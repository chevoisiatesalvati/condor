"""Tests for extended signals_1h journal telemetry parsing."""

from routines.macdbb_replay.journal import _parse_decision_line, _parse_signals_1h, parse_dt


def test_parse_signals_1h_legacy_format():
    raw = (
        "BTC-USD:bb=50.00,macd=1.0,sig=0.5,hist=0.5,gap=1.0,hr=0.5,"
        "tr=bull,mom=inc,fL=0,fS=0,aL=0,aS=0,sL=1.0,sS=2.0"
    )
    signals = _parse_signals_1h(raw)
    sig = signals["BTC-USD"]
    assert sig.bb_mid is None
    assert sig.bullish_cross is None
    assert not sig.has_replay_bands()


def test_parse_signals_1h_extended_format():
    raw = (
        "LIT-USD:bb=40.80,macd=-0.0166,sig=-0.0197,hist=0.0032,gap=0.1596,hr=0.1899,"
        "tr=bear,mom=dec,fL=0,fS=0,aL=0,aS=0,sL=0.0000,sS=0.0000,"
        "mid=1.4500,up=1.5200,bX=0,sX=1,p=1.4911"
    )
    signals = _parse_signals_1h(raw)
    sig = signals["LIT-USD"]
    assert sig.bb_mid == 1.45
    assert sig.bb_upper == 1.52
    assert sig.bullish_cross is False
    assert sig.bearish_cross is True
    assert sig.price == 1.4911
    assert sig.has_replay_bands()


def test_parse_macd_pairs_with_hip3_k_prefix():
    tick_time_map = {1: parse_dt("2026-06-17 20:32")}
    line = (
        "- **#1** tick=1 | entry_class=formal | macd_reviewed=5 | "
        "macd_pairs=kPEPE-USD,LIT-USD,PUMP-USD,ASTER-USD,ADA-USD | "
        "queue_total=kPEPE-USD,LIT-USD,PUMP-USD,ASTER-USD,ADA-USD,AERO-USD | "
        "signals_1h=kPEPE-USD:bb=7.85,macd=-0.0000,sig=0.0000,hist=-0.0000,"
        "gap=5.0924,hr=1.0821,tr=bear,mom=inc,fL=0,fS=0,aL=0,aS=0,sL=3.1,sS=2.02,"
        "mid=0.0030,up=0.0030,bX=0,sX=0,p=0.0029|"
        "ADA-USD:bb=13.28,macd=-0.0018,sig=-0.0017,hist=-0.0001,gap=0.0305,hr=0.0296,"
        "tr=bear,mom=inc,fL=0,fS=1,aL=0,aS=0,sL=1.5602,sS=0.4802,"
        "mid=0.1703,up=0.1748,bX=0,sX=1,p=0.1670"
    )
    meta = _parse_decision_line(line, tick_time_map)
    assert meta is not None
    assert meta.macd_pairs == [
        "kPEPE-USD",
        "LIT-USD",
        "PUMP-USD",
        "ASTER-USD",
        "ADA-USD",
    ]
    assert "ADA-USD" in meta.queue_total
    assert meta.signals_1h["kPEPE-USD"].pair == "kPEPE-USD"
    assert meta.signals_1h["ADA-USD"].formal_short is True


def test_parse_scanner_telemetry_on_decision_line():
    tick_time_map = {3: parse_dt("2026-06-12 12:00")}
    line = (
        "- **#3** tick=3 | entry_class=hold | scanner_regime=mature | "
        "tradeable_count=5 | natr_floor_used=0.08 | best_score=1.75 | "
        "macd_pairs=SOL-USD"
    )
    meta = _parse_decision_line(line, tick_time_map)
    assert meta is not None
    assert meta.scanner_regime == "mature"
    assert meta.natr_floor_used == 0.08
    assert meta.best_score == 1.75


def test_parse_create_plan_from_decision_line():
    tick_time_map = {1: parse_dt("2026-06-17 20:32")}
    line = (
        "- **#1** tick=1 | entry_class=formal | pair=ADA-USD | "
        "create_plan=ADA-USD:side=short,entry_class=formal,notional_req=500,"
        "notional_cap=550,eff_sl=1.4,eff_tp=6.5,vol=0.19,size_mult=1.0,"
        "amount=2994,attempt=1/3 | macd_pairs=ADA-USD"
    )
    meta = _parse_decision_line(line, tick_time_map)
    assert meta is not None
    plan = meta.create_plans["ADA-USD"]
    assert plan.side == "short"
    assert plan.entry_class == "formal"
    assert plan.notional_req == 500.0
    assert plan.eff_sl == 1.4
    assert plan.eff_tp == 6.5
    assert plan.vol == 0.19
    assert plan.size_mult == 1.0
