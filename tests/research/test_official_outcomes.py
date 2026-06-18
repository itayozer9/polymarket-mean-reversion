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


def test_official_matches_real_money_settlements():
    """official outcome must equal the booked outcome for every traded slug (real-money truth)."""
    import json, os, pandas as pd
    from research.dataset.official_outcomes import fetch_official_outcome
    path = "data/live/settlements.jsonl"
    if not os.path.exists(path):
        import pytest; pytest.skip("no settlements.jsonl")
    rows = [json.loads(l) for l in open(path) if l.strip()]
    s = pd.DataFrame(rows)
    s = s[s.get("backfill") != True] if "backfill" in s.columns else s
    s = s.dropna(subset=["slug", "outcome"])
    booked = {sl: str(g["outcome"].iloc[0]).upper() for sl, g in s.groupby("slug")}
    mism = []
    for slug, want in booked.items():
        got = fetch_official_outcome(slug)
        if got is None:
            continue                      # transient/unresolved at fetch time — skip, don't fail
        if got != ("UP" if want in ("UP", "YES") else "DOWN"):
            mism.append((slug, got, want))
    assert not mism, f"official-vs-booked mismatches ({len(mism)}): {mism[:5]}"
