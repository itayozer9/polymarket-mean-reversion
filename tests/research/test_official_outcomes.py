import pytest
from research.dataset.official_outcomes import parse_official_outcome


def test_parse_up():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Up", "Down"], "outcomePrices": ["1", "0"]}) == "UP"

def test_parse_down():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Up", "Down"], "outcomePrices": ["0", "1"]}) == "DOWN"

def test_parse_yes_no_aliases():
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Yes", "No"], "outcomePrices": ["1", "0"]}) == "UP"
    assert parse_official_outcome(
        {"closed": True, "outcomes": ["Yes", "No"], "outcomePrices": ["0", "1"]}) == "DOWN"

def test_parse_unresolved_or_missing():
    assert parse_official_outcome({"closed": True, "outcomes": ["Up", "Down"],
                                   "outcomePrices": ["0.5", "0.5"]}) is None
    assert parse_official_outcome({"closed": False, "outcomes": ["Up", "Down"],
                                   "outcomePrices": ["1", "0"]}) is None
    assert parse_official_outcome(None) is None
    assert parse_official_outcome({}) is None

def test_parse_json_string_fields():
    assert parse_official_outcome(
        {"closed": True, "outcomes": '["Up", "Down"]', "outcomePrices": '["1", "0"]'}) == "UP"


def test_cl_outcomes_uses_official(monkeypatch, tmp_path):
    import pandas as pd
    import research.analysis.edge_lab as el
    import research.dataset.official_outcomes as oo
    # recon says UP(1) for both; official cache overrides slugA to DOWN(0), leaves slugB missing
    monkeypatch.setattr(oo, "chainlink_outcome_by_slug",
                        lambda: pd.DataFrame({"slug": ["A", "B"], "cl_up": [1, 1]}))
    cache = tmp_path / "off.parquet"
    pd.DataFrame({"slug": ["A"], "official_up": [0.0]}).to_parquet(cache, index=False)
    monkeypatch.setattr(oo, "OUT", str(cache))
    el.cl_outcomes.cache_clear()
    out = el.cl_outcomes().set_index("slug")["cl_up"].to_dict()
    assert out["A"] == 0     # official overrides recon
    assert out["B"] == 1     # missing official -> reconstructed fallback
    el.cl_outcomes.cache_clear()
