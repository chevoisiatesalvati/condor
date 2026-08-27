"""Tests for report index retention policy."""

import json
from pathlib import Path

import pytest

import condor.reports as reports_mod
from condor.reports import store


@pytest.fixture
def charts_dir(tmp_path: Path, monkeypatch):
    directory = tmp_path / "reports"
    directory.mkdir()
    monkeypatch.setattr(reports_mod, "CHARTS_DIR", directory)
    monkeypatch.setattr(reports_mod, "INDEX_FILE", directory / "reports_index.json")
    return directory


def _entry(report_id: str, filename: str, day: int, source_name: str) -> dict:
    return {
        "id": report_id,
        "filename": filename,
        "created_at": f"2026-01-{day:02d}T00:00:00+00:00",
        "source_type": "routine",
        "source_name": source_name,
    }


def test_cleanup_skipped_when_max_reports_zero(charts_dir: Path):
    entries = []
    for i in range(5):
        fname = f"r{i}.html"
        (charts_dir / fname).write_text("<html></html>")
        entries.append(_entry(f"r{i}", fname, i + 1, "macd_bb_analysis"))
    store._write_index(entries)
    store._cleanup_locked(max_reports=0)
    assert len(store._read_index()) == 5
    assert all((charts_dir / f"r{i}.html").exists() for i in range(5))


def test_cleanup_prunes_oldest_when_over_limit(charts_dir: Path):
    entries = []
    for i in range(5):
        fname = f"r{i}.html"
        (charts_dir / fname).write_text("<html></html>")
        entries.append(_entry(f"r{i}", fname, i + 1, "macd_bb_analysis"))
    store._write_index(entries)
    store._cleanup_locked(max_reports=2)
    kept = store._read_index()
    assert len(kept) == 2
    assert {e["id"] for e in kept} == {"r3", "r4"}
    assert not (charts_dir / "r0.html").exists()
    assert (charts_dir / "r4.html").exists()


def test_cleanup_applies_limit_per_source(charts_dir: Path):
    entries = []
    for i in range(4):
        fname = f"frequent/f{i}.html"
        path = charts_dir / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>")
        entries.append(_entry(f"f{i}", fname, i + 1, "frequent_routine"))
    for i in range(2):
        fname = f"rare/r{i}.html"
        path = charts_dir / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>")
        entries.append(_entry(f"r{i}", fname, i + 1, "rare_routine"))
    store._write_index(entries)
    store._cleanup_locked(max_reports=2)

    kept = store._read_index()
    kept_ids = {e["id"] for e in kept}
    assert kept_ids == {"f2", "f3", "r0", "r1"}
    assert not (charts_dir / "frequent" / "f0.html").exists()
    assert not (charts_dir / "frequent" / "f1.html").exists()
    assert (charts_dir / "rare" / "r0.html").exists()
    assert (charts_dir / "rare" / "r1.html").exists()


def test_list_reports_filters_by_source_name_before_limit(charts_dir: Path):
    entries = []
    for i in range(5):
        entries.append(
            _entry(f"noise{i}", f"noise{i}.html", i + 1, "macd_bb_analysis")
        )
        entries[-1]["created_at"] = f"2026-07-0{i + 1}T12:00:00+00:00"
    for i in range(3):
        entries.append(
            _entry(
                f"bt{i}",
                f"bt{i}.html",
                i + 1,
                "macdbb_scanner_aggressive_hl_backtest",
            )
        )
        entries[-1]["created_at"] = f"2026-06-2{i + 1}T12:00:00+00:00"
    store._write_index(entries)

    matched, total = store.list_reports(
        source_type="routine",
        source_names=["macdbb_scanner_aggressive_hl_backtest"],
        limit=2,
    )
    assert total == 3
    assert len(matched) == 2
    assert all(
        e["source_name"] == "macdbb_scanner_aggressive_hl_backtest" for e in matched
    )
    assert matched[0]["id"] == "bt2"
    assert matched[1]["id"] == "bt1"


def test_source_dir_name_rejects_path_traversal():
    assert store.source_dir_name("macd_bb_analysis") == "macd_bb_analysis"
    assert store.source_dir_name("../escape") == "escape"
    assert store.source_dir_name("") == "_unnamed"
    assert store.source_dir_name("reports_index.json") == "_unnamed"
    assert store.source_dir_name("a/b") == "a_b"


def test_write_index_shards_by_source(charts_dir: Path):
    store._write_index(
        [
            _entry("a1", "alpha/a1.html", 1, "alpha"),
            _entry("b1", "beta/b1.html", 1, "beta"),
        ]
    )
    assert not (charts_dir / "reports_index.json").exists()
    alpha_index = charts_dir / "alpha" / "reports_index.json"
    beta_index = charts_dir / "beta" / "reports_index.json"
    assert alpha_index.is_file()
    assert beta_index.is_file()
    assert {entry["id"] for entry in json.loads(alpha_index.read_text())} == {"a1"}
    assert {entry["id"] for entry in json.loads(beta_index.read_text())} == {"b1"}


def test_legacy_root_index_splits_into_source_indexes(charts_dir: Path):
    entries = [
        _entry("a1", "old_a.html", 1, "alpha"),
        _entry("b1", "old_b.html", 1, "beta"),
    ]
    (charts_dir / "reports_index.json").write_text(
        json.dumps(entries), encoding="utf-8"
    )
    loaded = store._read_index()
    assert {entry["id"] for entry in loaded} == {"a1", "b1"}
    assert not (charts_dir / "reports_index.json").exists()
    assert (charts_dir / "alpha" / "reports_index.json").is_file()
    assert (charts_dir / "beta" / "reports_index.json").is_file()


def test_missing_source_index_hides_tabs(charts_dir: Path):
    store._write_index([_entry("gone", "alpha/gone.html", 1, "alpha")])
    (charts_dir / "alpha" / "reports_index.json").unlink()
    matched, total = store.list_reports(source_names=["alpha"])
    assert total == 0
    assert matched == []
