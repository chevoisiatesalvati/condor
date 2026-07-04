"""Tests for dual-instance prod + dev-local support."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from fastapi.testclient import TestClient

import main
from config_manager import ConfigManager
from condor.web.app import create_app


def test_config_manager_uses_condor_config_file(monkeypatch, tmp_path):
    ConfigManager.reset_instance()
    cfg = tmp_path / "custom.yml"
    cfg.write_text(
        yaml.dump(
            {
                "version": 1,
                "servers": {},
                "default_server": None,
                "admin_id": 1,
                "users": {},
                "server_access": {},
                "chat_defaults": {},
            }
        )
    )
    monkeypatch.setenv("CONDOR_CONFIG_FILE", str(cfg))
    cm = ConfigManager.instance()
    assert cm.config_path.resolve() == cfg.resolve()
    ConfigManager.reset_instance()


def test_reports_dir_env_override(monkeypatch, tmp_path):
    import importlib

    import condor.reports as reports_mod

    target = tmp_path / "reports-dev"
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(target))
    importlib.reload(reports_mod)
    assert reports_mod.CHARTS_DIR == target


def test_static_reports_mount_uses_charts_dir(monkeypatch, tmp_path):
    import importlib

    import condor.reports as reports_mod

    target = tmp_path / "reports-dev"
    target.mkdir()
    (target / "sample_report.html").write_text("<html><body>ok</body></html>")
    monkeypatch.setenv("CONDOR_REPORTS_DIR", str(target))
    importlib.reload(reports_mod)

    client = TestClient(create_app())
    response = client.get("/reports/sample_report.html")
    assert response.status_code == 200
    assert "ok" in response.text


def test_main_uses_web_only_when_env_set(monkeypatch):
    monkeypatch.setenv("CONDOR_WEB_ONLY", "1")
    ran = []

    async def stub():
        ran.append(True)

    monkeypatch.setattr(main, "_run_web_only", stub)

    def fake_run(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(main.asyncio, "run", fake_run)
    main.main()
    assert ran == [True]


def test_dev_login_not_available_without_flags(monkeypatch):
    monkeypatch.delenv("CONDOR_WEB_ONLY", raising=False)
    monkeypatch.delenv("CONDOR_DEV", raising=False)
    client = TestClient(create_app())
    response = client.post("/api/v1/auth/dev-login")
    assert response.status_code == 404


def test_dev_login_returns_jwt_for_admin(monkeypatch, tmp_path):
    ConfigManager.reset_instance()
    cfg = tmp_path / "config.yml"
    cfg.write_text(
        yaml.dump(
            {
                "version": 1,
                "servers": {},
                "default_server": None,
                "admin_id": 4242,
                "users": {"4242": {"role": "admin"}},
                "server_access": {},
                "chat_defaults": {},
            }
        )
    )
    monkeypatch.setenv("CONDOR_CONFIG_FILE", str(cfg))
    monkeypatch.setenv("CONDOR_DEV", "1")
    monkeypatch.setenv("CONDOR_WEB_ONLY", "1")
    monkeypatch.setenv("ADMIN_USER_ID", "4242")
    monkeypatch.setattr("utils.config.ADMIN_USER_ID", 4242)

    client = TestClient(create_app())
    response = client.post("/api/v1/auth/dev-login")
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["user"]["id"] == 4242
    assert body["user"]["role"] == "admin"
    ConfigManager.reset_instance()
