from condor.agents.performance import controller_ids_for_lookup


def test_legacy_controller_id_for_macdbb_session():
    assert controller_ids_for_lookup(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_78"
    ) == [
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_78",
        "macdbb_scanner_aggressive_hl_78",
    ]


def test_legacy_controller_id_passthrough_flat_id():
    assert controller_ids_for_lookup("macdbb_scanner_aggressive_hl_78") == [
        "macdbb_scanner_aggressive_hl_78"
    ]


def test_legacy_controller_id_experiment_suffix():
    ids = controller_ids_for_lookup(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_e3"
    )
    assert "macdbb_scanner_aggressive_hl_e3" in ids
