"""CRUD helpers for agent-owned presets.yaml bundles."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import yaml

from condor.agents.agent_presets import AGENT_PRESET_LOADERS
from condor.agents.strategy_paths import agent_dir, private_strategy_dir, resolve_presets_yaml

PRESET_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
RESERVED_PRESET_IDS = frozenset({"custom"})


class PresetStoreError(ValueError):
    """Validation or state error for preset mutations."""


def agent_supports_presets(slug: str) -> bool:
    return slug in AGENT_PRESET_LOADERS


def _import_agent_preset_module(slug: str):
    module_path = AGENT_PRESET_LOADERS.get(slug)
    if not module_path:
        raise PresetStoreError(f"Agent {slug!r} has no preset module")
    return importlib.import_module(module_path)


def _public_preset_ids(slug: str) -> frozenset[str]:
    try:
        module = _import_agent_preset_module(slug)
    except PresetStoreError:
        return frozenset()
    public_dynamic = getattr(module, "PUBLIC_DYNAMIC_PRESET_OVERRIDES", {}) or {}
    public_labels = getattr(module, "PUBLIC_PRESET_LABELS", {}) or {}
    ids = {str(name) for name in public_dynamic.keys()}
    ids.update(str(name) for name in public_labels.keys())
    return frozenset(ids - RESERVED_PRESET_IDS)


def presets_yaml_write_path(slug: str) -> Path:
    """Return the writable presets.yaml path, creating parent dirs when needed."""
    existing = resolve_presets_yaml(slug)
    if existing is not None:
        return existing
    strategies_root = private_strategy_dir(slug).parent
    if strategies_root.is_dir():
        path = private_strategy_dir(slug) / "presets.yaml"
    else:
        path = agent_dir(slug) / "presets.private.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def load_presets_bundle(slug: str) -> dict[str, Any]:
    path = resolve_presets_yaml(slug)
    if path is None or not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def save_presets_bundle(slug: str, bundle: dict[str, Any]) -> Path:
    path = presets_yaml_write_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(bundle, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    invalidate_agent_preset_cache(slug)
    return path


def invalidate_agent_preset_cache(slug: str) -> None:
    module_path = AGENT_PRESET_LOADERS.get(slug)
    if not module_path:
        return
    module = importlib.import_module(module_path)
    invalidate = getattr(module, "invalidate_preset_cache", None)
    if callable(invalidate):
        invalidate()


def _strip_keys_for_storage(overrides: dict[str, Any]) -> dict[str, Any]:
    from routines.macdbb_scanner_aggressive_hl_replay.timeline_sweep import PRESET_STRIP_KEYS

    return {
        key: value
        for key, value in overrides.items()
        if key not in PRESET_STRIP_KEYS
    }


def _validate_preset_id(preset_id: str) -> str:
    preset_id = preset_id.strip()
    if preset_id in RESERVED_PRESET_IDS:
        raise PresetStoreError(f"Preset id {preset_id!r} is reserved")
    if not PRESET_ID_RE.fullmatch(preset_id):
        raise PresetStoreError(
            "Preset id must start with a letter and contain only lowercase letters, digits, and underscores"
        )
    return preset_id


def _assert_private_mutable(slug: str, preset_id: str) -> None:
    if preset_id in _public_preset_ids(slug):
        raise PresetStoreError(f"Preset {preset_id!r} is built-in and cannot be changed")


def _assert_deletable(bundle: dict[str, Any], preset_id: str) -> None:
    for key in ("default_agent_strategy_preset", "current_winner_preset"):
        if bundle.get(key) == preset_id:
            raise PresetStoreError(
                f"Cannot delete preset {preset_id!r}: it is referenced by {key!r}"
            )


def list_strategy_presets(slug: str) -> list[dict[str, Any]]:
    """Return preset catalog entries with metadata for the UI."""
    if not agent_supports_presets(slug):
        return []

    module = _import_agent_preset_module(slug)
    catalog_fn = getattr(module, "agent_preset_catalog", None)
    labels_fn = getattr(module, "preset_labels", None)
    private_overrides = load_presets_bundle(slug).get("dynamic_preset_overrides") or {}
    public_ids = _public_preset_ids(slug)

    catalog = catalog_fn() if callable(catalog_fn) else []
    labels = labels_fn() if callable(labels_fn) else {}

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for entry in catalog:
        preset_id = str(entry.get("id") or "")
        if not preset_id or preset_id in seen:
            continue
        seen.add(preset_id)
        is_public = preset_id in public_ids
        is_private = preset_id in private_overrides
        items.append(
            {
                "id": preset_id,
                "label": str(entry.get("label") or labels.get(preset_id) or preset_id),
                "source": "public" if is_public and not is_private else "private",
                "editable": preset_id not in public_ids and preset_id not in RESERVED_PRESET_IDS,
                "override_count": len(private_overrides.get(preset_id) or {}),
            }
        )

    for preset_id, overrides in private_overrides.items():
        if preset_id in seen:
            continue
        seen.add(preset_id)
        items.append(
            {
                "id": preset_id,
                "label": str(labels.get(preset_id) or preset_id),
                "source": "private",
                "editable": preset_id not in public_ids and preset_id not in RESERVED_PRESET_IDS,
                "override_count": len(overrides or {}),
            }
        )
    return items


def _preset_detail_from_bundle(
    slug: str,
    preset_id: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    private_overrides = bundle.get("dynamic_preset_overrides") or {}
    labels = bundle.get("labels") or {}
    if preset_id in private_overrides:
        overrides = dict(private_overrides[preset_id])
        source = "private"
        editable = preset_id not in _public_preset_ids(slug)
    elif preset_id in _public_preset_ids(slug):
        module = _import_agent_preset_module(slug)
        public = getattr(module, "PUBLIC_DYNAMIC_PRESET_OVERRIDES", {}) or {}
        overrides = dict(public.get(preset_id) or {})
        source = "public"
        editable = False
    else:
        raise PresetStoreError(f"Preset {preset_id!r} not found")

    label = str(labels.get(preset_id) or preset_id)
    try:
        module = _import_agent_preset_module(slug)
        all_labels = getattr(module, "preset_labels", lambda: {})()
        label = str(all_labels.get(preset_id) or label)
    except PresetStoreError:
        pass

    return {
        "id": preset_id,
        "label": label,
        "source": source,
        "editable": editable,
        "overrides": overrides,
    }


def get_strategy_preset(slug: str, preset_id: str) -> dict[str, Any]:
    preset_id = _validate_preset_id(preset_id)
    bundle = load_presets_bundle(slug)
    return _preset_detail_from_bundle(slug, preset_id, bundle)


def create_strategy_preset(
    slug: str,
    *,
    preset_id: str,
    label: str,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    preset_id = _validate_preset_id(preset_id)
    _assert_private_mutable(slug, preset_id)

    bundle = load_presets_bundle(slug)
    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_id in dynamic_overrides:
        raise PresetStoreError(f"Preset {preset_id!r} already exists")

    dynamic_overrides[preset_id] = _strip_keys_for_storage(dict(overrides))
    labels = bundle.setdefault("labels", {})
    labels[preset_id] = label.strip() or preset_id

    names = list(bundle.get("agent_strategy_preset_names") or [])
    if preset_id not in names:
        names.append(preset_id)
    bundle["agent_strategy_preset_names"] = names

    save_presets_bundle(slug, bundle)
    return _preset_detail_from_bundle(slug, preset_id, bundle)


def update_strategy_preset(
    slug: str,
    preset_id: str,
    *,
    label: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preset_id = _validate_preset_id(preset_id)
    _assert_private_mutable(slug, preset_id)

    bundle = load_presets_bundle(slug)
    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_id not in dynamic_overrides:
        raise PresetStoreError(f"Preset {preset_id!r} not found")

    if overrides is not None:
        dynamic_overrides[preset_id] = _strip_keys_for_storage(dict(overrides))
    if label is not None:
        bundle.setdefault("labels", {})[preset_id] = label.strip() or preset_id

    save_presets_bundle(slug, bundle)
    return _preset_detail_from_bundle(slug, preset_id, bundle)


def delete_strategy_preset(slug: str, preset_id: str) -> None:
    preset_id = _validate_preset_id(preset_id)
    _assert_private_mutable(slug, preset_id)

    bundle = load_presets_bundle(slug)
    dynamic_overrides = bundle.get("dynamic_preset_overrides") or {}
    if preset_id not in dynamic_overrides:
        raise PresetStoreError(f"Preset {preset_id!r} not found")

    _assert_deletable(bundle, preset_id)

    dynamic_overrides = dict(dynamic_overrides)
    dynamic_overrides.pop(preset_id, None)
    bundle["dynamic_preset_overrides"] = dynamic_overrides

    labels = dict(bundle.get("labels") or {})
    labels.pop(preset_id, None)
    bundle["labels"] = labels

    names = [name for name in (bundle.get("agent_strategy_preset_names") or []) if name != preset_id]
    bundle["agent_strategy_preset_names"] = names

    save_presets_bundle(slug, bundle)


def upsert_strategy_preset(
    slug: str,
    *,
    preset_id: str,
    label: str,
    overrides: dict[str, Any],
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Create or replace a private preset (used by sweep automation)."""
    preset_id = _validate_preset_id(preset_id)
    bundle = load_presets_bundle(slug)
    dynamic_overrides = bundle.setdefault("dynamic_preset_overrides", {})
    if preset_id in dynamic_overrides and not replace_existing:
        raise PresetStoreError(f"Preset {preset_id!r} already exists")

    dynamic_overrides[preset_id] = _strip_keys_for_storage(dict(overrides))
    bundle.setdefault("labels", {})[preset_id] = label.strip() or preset_id

    names = list(bundle.get("agent_strategy_preset_names") or [])
    if preset_id not in names:
        names.append(preset_id)
    bundle["agent_strategy_preset_names"] = names

    save_presets_bundle(slug, bundle)
    return _preset_detail_from_bundle(slug, preset_id, bundle)
