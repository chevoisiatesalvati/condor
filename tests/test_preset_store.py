"""Tests for presets.yaml CRUD helpers and admin-gated API routes."""

from __future__ import annotations

import yaml
import pytest
from fastapi import FastAPI
from pathlib import Path
from starlette.testclient import TestClient

import condor.web.routes.agents as agents_module
from condor.agents.preset_store import (
    PresetStoreError,
    create_strategy_preset,
    delete_strategy_preset,
    list_strategy_presets,
    load_presets_bundle,
    update_strategy_preset,
)
from condor.web.auth import get_current_user
from condor.web.models import WebUser

AGENT_SLUG = "macdbb_scanner_aggressive_hl"
ADMIN = WebUser(id=1, username="admin", first_name="Admin", role="admin")
USER = WebUser(id=2, username="user", first_name="User", role="user")
REAL_PRESETS_YAML = (
    Path(__file__).resolve().parents[1]
    / "strategies"
    / "macdbb_scanner_aggressive_hl"
    / "presets.yaml"
)


@pytest.fixture(autouse=True)
def _guard_real_presets_yaml(tmp_path, monkeypatch):
    isolated = tmp_path / "strategies"
    isolated.mkdir()
    monkeypatch.setenv("CONDOR_STRATEGIES_DIR", str(isolated))
    baseline = (
        REAL_PRESETS_YAML.read_text(encoding="utf-8")
        if REAL_PRESETS_YAML.is_file()
        else None
    )
    yield
    if baseline is not None:
        assert REAL_PRESETS_YAML.read_text(encoding="utf-8") == baseline


def _write_bundle(path, bundle):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")


def test_create_update_delete_private_preset(tmp_path, monkeypatch):
    presets_path = tmp_path / "strategies" / AGENT_SLUG / "presets.yaml"
    monkeypatch.setattr(
        "condor.agents.preset_store.resolve_presets_yaml",
        lambda slug: presets_path if slug == AGENT_SLUG else None,
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.presets_yaml_write_path",
        lambda slug: presets_path,
    )

    created = create_strategy_preset(
        AGENT_SLUG,
        preset_id="hl_dynamic_timeline_test_preset",
        label="Test preset",
        overrides={"sl_pct": 3.8, "preset": "custom", "range_start_utc": "2026-01-01"},
    )
    assert created["id"] == "hl_dynamic_timeline_test_preset"
    assert created["editable"] is True
    assert "preset" not in created["overrides"]
    assert "range_start_utc" not in created["overrides"]
    assert created["overrides"]["sl_pct"] == 3.8

    updated = update_strategy_preset(
        AGENT_SLUG,
        "hl_dynamic_timeline_test_preset",
        label="Renamed preset",
        overrides={"sl_pct": 4.0, "tp_pct": 6.0},
    )
    assert updated["label"] == "Renamed preset"
    assert updated["overrides"]["sl_pct"] == 4.0

    delete_strategy_preset(AGENT_SLUG, "hl_dynamic_timeline_test_preset")
    bundle = load_presets_bundle(AGENT_SLUG)
    assert "hl_dynamic_timeline_test_preset" not in (bundle.get("dynamic_preset_overrides") or {})


def test_delete_blocks_referenced_winner_preset(tmp_path, monkeypatch):
    presets_path = tmp_path / "presets.yaml"
    _write_bundle(
        presets_path,
        {
            "current_winner_preset": "hl_dynamic_timeline_keep_me",
            "dynamic_preset_overrides": {"hl_dynamic_timeline_keep_me": {"sl_pct": 1.0}},
            "labels": {"hl_dynamic_timeline_keep_me": "Keep"},
            "agent_strategy_preset_names": ["hl_dynamic_timeline_keep_me"],
        },
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.resolve_presets_yaml",
        lambda slug: presets_path,
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.presets_yaml_write_path",
        lambda slug: presets_path,
    )

    with pytest.raises(PresetStoreError, match="current_winner_preset"):
        delete_strategy_preset(AGENT_SLUG, "hl_dynamic_timeline_keep_me")


def test_list_includes_public_and_private(tmp_path, monkeypatch):
    presets_path = tmp_path / "presets.yaml"
    _write_bundle(
        presets_path,
        {
            "dynamic_preset_overrides": {
                "hl_dynamic_timeline_private_only": {"sl_pct": 2.0},
            },
            "labels": {"hl_dynamic_timeline_private_only": "Private only"},
            "agent_strategy_preset_names": ["hl_dynamic_timeline_private_only"],
        },
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.resolve_presets_yaml",
        lambda slug: presets_path,
    )

    items = list_strategy_presets(AGENT_SLUG)
    ids = {item["id"] for item in items}
    assert "custom" in ids
    assert "hl_dynamic_timeline_private_only" in ids

    private = next(i for i in items if i["id"] == "hl_dynamic_timeline_private_only")
    assert private["editable"] is True
    assert private["source"] == "private"


class FakeConfigManager:
    def __init__(self, admins: set[int]):
        self._admins = admins

    def is_admin(self, user_id: int) -> bool:
        return user_id in self._admins


@pytest.fixture
def preset_api_client(tmp_path, monkeypatch):
    presets_path = tmp_path / "presets.yaml"
    _write_bundle(
        presets_path,
        {
            "dynamic_preset_overrides": {
                "hl_dynamic_timeline_ui_delete_me": {"sl_pct": 1.1},
            },
            "labels": {"hl_dynamic_timeline_ui_delete_me": "Delete me"},
            "agent_strategy_preset_names": ["hl_dynamic_timeline_ui_delete_me"],
        },
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.resolve_presets_yaml",
        lambda slug: presets_path,
    )
    monkeypatch.setattr(
        "condor.agents.preset_store.presets_yaml_write_path",
        lambda slug: presets_path,
    )
    monkeypatch.setattr(
        "config_manager.get_config_manager",
        lambda: FakeConfigManager(admins={ADMIN.id}),
    )

    app = FastAPI()
    app.include_router(agents_module.router)
    return TestClient(app), presets_path


def test_preset_api_requires_admin_for_mutations(preset_api_client):
    client, _ = preset_api_client
    client.app.dependency_overrides[get_current_user] = lambda: USER

    resp = client.post(
        f"/agents/{AGENT_SLUG}/strategy-presets",
        json={"id": "hl_dynamic_timeline_new_one", "label": "New", "overrides": {"sl_pct": 2.0}},
    )
    assert resp.status_code == 403

    resp = client.delete(f"/agents/{AGENT_SLUG}/strategy-presets/hl_dynamic_timeline_ui_delete_me")
    assert resp.status_code == 403


def test_preset_api_admin_crud(preset_api_client):
    client, presets_path = preset_api_client
    client.app.dependency_overrides[get_current_user] = lambda: ADMIN

    resp = client.get(f"/agents/{AGENT_SLUG}/strategy-presets")
    assert resp.status_code == 200
    assert any(item["id"] == "hl_dynamic_timeline_ui_delete_me" for item in resp.json())

    resp = client.post(
        f"/agents/{AGENT_SLUG}/strategy-presets",
        json={
            "id": "hl_dynamic_timeline_created_via_api",
            "label": "API preset",
            "overrides": {"sl_pct": 3.3},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["overrides"]["sl_pct"] == 3.3

    resp = client.put(
        f"/agents/{AGENT_SLUG}/strategy-presets/hl_dynamic_timeline_created_via_api",
        json={"label": "Updated label"},
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "Updated label"

    resp = client.delete(f"/agents/{AGENT_SLUG}/strategy-presets/hl_dynamic_timeline_ui_delete_me")
    assert resp.status_code == 204

    bundle = yaml.safe_load(presets_path.read_text(encoding="utf-8"))
    assert "hl_dynamic_timeline_ui_delete_me" not in bundle["dynamic_preset_overrides"]
    assert "hl_dynamic_timeline_created_via_api" in bundle["dynamic_preset_overrides"]
