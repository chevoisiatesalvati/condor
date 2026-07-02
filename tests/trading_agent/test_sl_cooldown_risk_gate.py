"""Engine-enforced SL symbol cooldown blocks executor creates."""

from __future__ import annotations

import pytest

from condor.trading_agent.risk import RiskEngine, RiskState, auto_approve_with_risk_check


@pytest.mark.asyncio
async def test_sl_cooldown_blocks_manage_executors_create():
    callback = auto_approve_with_risk_check(
        RiskEngine(),
        RiskState(),
        sl_cooldown_pairs=frozenset({"SPX-USD"}),
    )
    tool_call = {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "trading_pair": "SPX-USD",
            "executor_config": {"controller_id": "macdbb_scanner_aggressive_hl_78"},
        },
    }
    result = await callback(tool_call, [{"kind": "allow_once", "optionId": "ok"}])
    assert result["outcome"]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_sl_cooldown_allows_other_pairs():
    callback = auto_approve_with_risk_check(
        RiskEngine(),
        RiskState(),
        sl_cooldown_pairs=frozenset({"SPX-USD"}),
    )
    tool_call = {
        "tool": "manage_executors",
        "input": {
            "action": "create",
            "trading_pair": "BTC-USD",
            "executor_config": {"controller_id": "macdbb_scanner_aggressive_hl_78"},
        },
    }
    result = await callback(tool_call, [{"kind": "allow_once", "optionId": "ok"}])
    assert result["outcome"]["outcome"] == "selected"
