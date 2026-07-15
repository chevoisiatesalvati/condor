from condor.agents.performance import controller_ids_for_lookup


def test_controller_id_lookup_exact_only_for_dotted_session():
    assert controller_ids_for_lookup(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_78"
    ) == ["macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_78"]


def test_controller_id_lookup_passthrough_flat_id():
    assert controller_ids_for_lookup("macdbb_scanner_aggressive_hl_78") == [
        "macdbb_scanner_aggressive_hl_78"
    ]


def test_controller_id_lookup_exact_only_for_experiment():
    assert controller_ids_for_lookup(
        "macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_e3"
    ) == ["macdbb_scanner_aggressive_hl.macdbb_scanner_aggressive_hl_e3"]
