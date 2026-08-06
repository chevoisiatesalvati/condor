"""Promote manifests: pin a backtested preset for live Strategies starts."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROMOTIONS_DIR = REPO_ROOT / "data" / "strategy_promotions"

ENGINE_VERSION = "macdbb-decide-v1"
FEE_MODEL_VERSION = "fee_slippage_bps-v1"


@dataclass
class PromoteManifest:
    strategy_slug: str
    preset: str
    preset_hash: str
    engine_version: str
    fee_model_version: str
    venue: str
    promoted_at: float
    strategy_params: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromoteManifest":
        return cls(
            strategy_slug=str(data["strategy_slug"]),
            preset=str(data["preset"]),
            preset_hash=str(data["preset_hash"]),
            engine_version=str(data.get("engine_version") or ENGINE_VERSION),
            fee_model_version=str(data.get("fee_model_version") or FEE_MODEL_VERSION),
            venue=str(data.get("venue") or ""),
            promoted_at=float(data.get("promoted_at") or 0),
            strategy_params=dict(data.get("strategy_params") or {}),
            notes=str(data.get("notes") or ""),
        )


def _manifest_path(strategy_slug: str) -> Path:
    return PROMOTIONS_DIR / f"{strategy_slug}.json"


def hash_preset(preset: str, strategy_params: dict[str, Any]) -> str:
    payload = json.dumps(
        {"preset": preset, "params": strategy_params},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def load_manifest(strategy_slug: str) -> PromoteManifest | None:
    path = _manifest_path(strategy_slug)
    if not path.is_file():
        return None
    try:
        return PromoteManifest.from_dict(json.loads(path.read_text()))
    except Exception:
        return None


def write_manifest(manifest: PromoteManifest) -> Path:
    PROMOTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _manifest_path(manifest.strategy_slug)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n")
    return path


def promote(
    strategy_slug: str,
    *,
    preset: str,
    strategy_params: dict[str, Any],
    venue: str = "hyperliquid_perpetual",
    notes: str = "",
    require_params: bool = True,
) -> PromoteManifest:
    """Record a promote after research; live start reads this pin."""
    if require_params and not strategy_params:
        raise ValueError("strategy_params required to promote (parity pack must expand preset)")
    manifest = PromoteManifest(
        strategy_slug=strategy_slug,
        preset=preset,
        preset_hash=hash_preset(preset, strategy_params),
        engine_version=ENGINE_VERSION,
        fee_model_version=FEE_MODEL_VERSION,
        venue=venue,
        promoted_at=time.time(),
        strategy_params=dict(strategy_params),
        notes=notes,
    )
    write_manifest(manifest)
    return manifest


def assert_promoted_or_raise(
    strategy_slug: str,
    *,
    preset: str,
    strategy_params: dict[str, Any],
    require_promoted: bool,
) -> PromoteManifest | None:
    """Gate live start when the catalog marks require_promoted."""
    if not require_promoted:
        return load_manifest(strategy_slug)
    manifest = load_manifest(strategy_slug)
    if manifest is None:
        raise PermissionError(
            f"Strategy '{strategy_slug}' requires a promote manifest before live start. "
            "Run promote from Strategies after a passing parity backtest."
        )
    expected = hash_preset(preset, strategy_params)
    if manifest.preset != preset:
        raise PermissionError(
            f"Promoted preset is '{manifest.preset}', start requested '{preset}'."
        )
    if manifest.preset_hash != expected:
        raise PermissionError(
            f"Promoted preset hash mismatch for '{preset}' "
            f"(manifest {manifest.preset_hash}, start {expected}). Re-promote."
        )
    if manifest.engine_version != ENGINE_VERSION:
        raise PermissionError(
            f"Promoted engine_version {manifest.engine_version} != {ENGINE_VERSION}. Re-promote."
        )
    return manifest
