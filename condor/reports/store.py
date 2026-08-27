"""Report attribution and persistent report index storage."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

# reports/ is a repository-root output directory, not this source package.
CHARTS_DIR = Path(__file__).resolve().parents[2] / "reports"
SOURCE_INDEX_FILENAME = "reports_index.json"
INDEX_FILE = CHARTS_DIR / SOURCE_INDEX_FILENAME
MAX_REPORTS = int(os.environ.get("CONDOR_MAX_REPORTS", "100"))

_UNNAMED_SOURCE_DIR = "_unnamed"
_SOURCE_DIR_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RESERVED_SOURCE_DIRS = frozenset({SOURCE_INDEX_FILENAME, _UNNAMED_SOURCE_DIR})

_index_lock = asyncio.Lock()
_fs_lock = threading.RLock()
_last_report_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "last_report_id", default=None
)
_report_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "report_agent", default=None
)


def _charts_dir() -> Path:
    # Resolve through the public package so existing runtime overrides of
    # condor.reports.CHARTS_DIR keep working after the module-to-package split.
    from . import CHARTS_DIR as configured_dir

    return configured_dir


def _index_file() -> Path:
    from . import INDEX_FILE as configured_file

    return configured_file


def source_dir_name(source_name: str | None) -> str:
    """Return a path-safe folder name for a report source (usually a routine)."""
    slug = _SOURCE_DIR_UNSAFE.sub("_", (source_name or "").strip()).strip("._")
    if not slug or slug in {".", ".."} or slug in _RESERVED_SOURCE_DIRS:
        return _UNNAMED_SOURCE_DIR
    return slug[:80]


def source_index_path(source_name: str | None) -> Path:
    """Return the per-routine index file for ``source_name``."""
    return _charts_dir() / source_dir_name(source_name) / SOURCE_INDEX_FILENAME


def reset_last_report_id() -> None:
    """Clear the last-saved report ID for the current task (call before a run)."""
    _last_report_id.set(None)


def get_last_report_id() -> str | None:
    """Return the ID of the last report saved by the current task, if any."""
    return _last_report_id.get()


@contextmanager
def attribute_to(agent: str | None):
    """Attribute reports saved within this block to an assistant slug."""
    token = _report_agent.set(agent or None)
    try:
        yield
    finally:
        _report_agent.reset(token)


def get_report_raw_html(report_id: str) -> tuple[str, str] | None:
    """Return the report's raw HTML and filename exactly as saved on disk.

    The filename is never taken from the caller — it is read from the index
    entry for ``report_id`` — but the entry is still treated as untrusted:
    the resolved path must stay inside the reports directory and must be an
    ``.html`` file. This is what keeps the authenticated HTML route
    (``GET /api/v1/reports/{id}/html``) from being turned into an arbitrary
    file reader by a poisoned or hand-edited index.
    """
    entry = get_report(report_id)
    if not entry:
        return None
    charts_dir = _charts_dir().resolve()
    path = (charts_dir / entry["filename"]).resolve()
    if not path.is_relative_to(charts_dir):
        return None
    if path.suffix.lower() != ".html" or not path.is_file():
        return None
    return path.read_text(encoding="utf-8"), entry["filename"]


def _load_entries(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [entry for entry in payload if isinstance(entry, dict)]


def _dump_entries(path: Path, entries: list[dict]) -> None:
    if not entries:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(path.parent), suffix=".tmp", delete=False
    )
    try:
        json.dump(entries, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        os.replace(tmp.name, str(path))
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _iter_source_index_paths() -> list[Path]:
    charts_dir = _charts_dir()
    if not charts_dir.is_dir():
        return []
    paths: list[Path] = []
    for child in charts_dir.iterdir():
        if not child.is_dir():
            continue
        index_path = child / SOURCE_INDEX_FILENAME
        if index_path.is_file():
            paths.append(index_path)
    return paths


def _iter_source_groups() -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for index_path in _iter_source_index_paths():
        groups[index_path.parent.name] = _load_entries(index_path)
    return groups


def _split_legacy_index() -> None:
    """Move a leftover root catalog into per-routine index files.

    New reports live at ``reports/<routine>/reports_index.json``. A root
    ``reports/reports_index.json`` is only read so existing catalogs keep
    working until the next index access, which shards them in place.
    """
    legacy = _index_file()
    if not legacy.is_file():
        return
    entries = _load_entries(legacy)
    by_slug: dict[str, list[dict]] = {}
    for entry in entries:
        slug = source_dir_name(entry.get("source_name"))
        by_slug.setdefault(slug, []).append(entry)
    for slug, group in by_slug.items():
        path = _charts_dir() / slug / SOURCE_INDEX_FILENAME
        existing = _load_entries(path)
        existing_ids = {item.get("id") for item in existing}
        merged = existing + [item for item in group if item.get("id") not in existing_ids]
        _dump_entries(path, merged)
    try:
        legacy.unlink()
    except OSError:
        pass


def _read_index(source_names: list[str] | None = None) -> list[dict]:
    with _fs_lock:
        _split_legacy_index()
        if source_names:
            allowed = set(source_names)
            slugs = {source_dir_name(name) for name in source_names}
            entries: list[dict] = []
            for slug in slugs:
                path = _charts_dir() / slug / SOURCE_INDEX_FILENAME
                for entry in _load_entries(path):
                    if entry.get("source_name") in allowed:
                        entries.append(entry)
            return entries
        entries = []
        for group in _iter_source_groups().values():
            entries.extend(group)
        return entries


def _write_index(entries: list[dict]) -> None:
    """Replace the full catalog, sharded into per-routine index files."""
    with _fs_lock:
        by_slug: dict[str, list[dict]] = {}
        for entry in entries:
            slug = source_dir_name(entry.get("source_name"))
            by_slug.setdefault(slug, []).append(entry)
        existing_slugs = set(_iter_source_groups().keys())
        charts_dir = _charts_dir()
        charts_dir.mkdir(exist_ok=True)
        for slug, group in by_slug.items():
            _dump_entries(charts_dir / slug / SOURCE_INDEX_FILENAME, group)
        for slug in existing_slugs - set(by_slug):
            leftover = charts_dir / slug / SOURCE_INDEX_FILENAME
            if leftover.exists():
                leftover.unlink()
        legacy = _index_file()
        if legacy.exists():
            legacy.unlink()


def _upsert_entry(entry: dict) -> None:
    """Insert or replace one report in its routine's index file."""
    with _fs_lock:
        _split_legacy_index()
        path = source_index_path(entry.get("source_name"))
        entries = _load_entries(path)
        report_id = entry.get("id")
        replaced = False
        for index, item in enumerate(entries):
            if item.get("id") == report_id:
                entries[index] = entry
                replaced = True
                break
        if not replaced:
            entries.append(entry)
        _dump_entries(path, entries)


def _write_report_html(path: Path, content: str) -> None:
    """Atomically create or replace a report HTML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        tmp.write(content)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def list_reports(
    source_type: str | None = None,
    source_name: str | None = None,
    source_names: list[str] | None = None,
    tag: str | None = None,
    search: str | None = None,
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    scoped_names = source_names or ([source_name] if source_name else None)
    entries = _read_index(source_names=scoped_names)
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)

    if source_type:
        entries = [
            entry for entry in entries if entry.get("source_type") == source_type
        ]
    if source_names:
        allowed = set(source_names)
        entries = [
            entry for entry in entries if entry.get("source_name") in allowed
        ]
    elif source_name:
        entries = [
            entry for entry in entries if entry.get("source_name") == source_name
        ]
    if tag:
        entries = [entry for entry in entries if tag in entry.get("tags", [])]
    if agent:
        entries = [entry for entry in entries if entry.get("agent") == agent]
    if search:
        query = search.lower()
        entries = [
            entry
            for entry in entries
            if query in entry.get("title", "").lower()
            or query in entry.get("source_name", "").lower()
            or any(query in item.lower() for item in entry.get("tags", []))
        ]

    total = len(entries)
    return entries[offset : offset + limit], total


def list_reports_grouped() -> list[dict]:
    """Return the latest report per source name, with count."""
    entries = _read_index()
    entries.sort(key=lambda entry: entry.get("created_at", ""), reverse=True)
    groups: dict[str, dict] = {}
    for entry in entries:
        source_name = entry.get("source_name", "")
        if not source_name:
            continue
        if source_name not in groups:
            groups[source_name] = {
                "source_name": source_name,
                "source_type": entry.get("source_type", ""),
                "latest_report": entry,
                "total_count": 1,
                "all_tags": set(entry.get("tags", [])),
            }
        else:
            groups[source_name]["total_count"] += 1
            groups[source_name]["all_tags"].update(entry.get("tags", []))
    for group in groups.values():
        group["all_tags"] = sorted(group["all_tags"])
    return sorted(
        groups.values(),
        key=lambda group: group["latest_report"]["created_at"],
        reverse=True,
    )


def get_report(report_id: str) -> dict | None:
    for entry in _read_index():
        if entry["id"] == report_id:
            return entry
    return None


async def delete_report(report_id: str) -> bool:
    async with _index_lock:
        with _fs_lock:
            _split_legacy_index()
            found: dict | None = None
            found_slug: str | None = None
            for slug, group in _iter_source_groups().items():
                for entry in group:
                    if entry.get("id") == report_id:
                        found = entry
                        found_slug = slug
                        break
                if found is not None:
                    break
            if found is None or found_slug is None:
                return False
            path = _charts_dir() / found["filename"]
            if path.exists():
                path.unlink()
            remaining = [
                entry
                for entry in _load_entries(
                    _charts_dir() / found_slug / SOURCE_INDEX_FILENAME
                )
                if entry.get("id") != report_id
            ]
            _dump_entries(
                _charts_dir() / found_slug / SOURCE_INDEX_FILENAME, remaining
            )
            return True


def _cleanup_locked(
    max_reports: int = MAX_REPORTS, source_name: str | None = None
) -> None:
    """Prune oldest reports per source once that source exceeds ``max_reports``.

    ``CONDOR_MAX_REPORTS`` is a per-routine (per ``source_name``) cap, not a
    global one, so a high-frequency routine cannot rotate reports from another.
    ``max_reports <= 0`` retains everything.
    """
    if max_reports <= 0:
        return
    with _fs_lock:
        _split_legacy_index()
        if source_name is not None:
            slug = source_dir_name(source_name)
            groups = {slug: _load_entries(_charts_dir() / slug / SOURCE_INDEX_FILENAME)}
        else:
            groups = _iter_source_groups()
        if not any(groups.values()):
            return
        charts_dir = _charts_dir()
        for slug, group in groups.items():
            by_source: dict[str, list[dict]] = {}
            for entry in group:
                by_source.setdefault(entry.get("source_name") or "", []).append(entry)
            keep: list[dict] = []
            removed = False
            for named_group in by_source.values():
                named_group.sort(
                    key=lambda entry: entry.get("updated_at")
                    or entry.get("created_at", "")
                )
                if len(named_group) <= max_reports:
                    keep.extend(named_group)
                    continue
                removed = True
                overflow = named_group[: len(named_group) - max_reports]
                keep.extend(named_group[len(named_group) - max_reports :])
                for entry in overflow:
                    path = charts_dir / entry["filename"]
                    if path.exists():
                        path.unlink()
            if removed:
                _dump_entries(charts_dir / slug / SOURCE_INDEX_FILENAME, keep)
