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
