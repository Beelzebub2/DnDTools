import os

from src.models.market_model import extra_roll_share, load_model, model_from_report, pair_bonuses, save_model

PAIRS = [
    {"pair": "PhysicalPower + PhysicalWeaponDamageAdd", "synergy": 18.0, "support": 33},
    {"pair": "Agility + Knowledge", "synergy": 14.0, "support": 9},        # too few listings
    {"pair": "Luck + Vigor", "synergy": -6.0, "support": 40},             # worth less together
    {"pair": "broken", "synergy": 5.0, "support": 50},                    # malformed
]


def test_model_keeps_what_pricing_needs():
    report = {"stat_premiums": {}, "good_roll_counts": {}, "extra_good_roll_factor": 0.2, "roll_ranges": {},
              "pair_synergies": PAIRS, "below_vendor": [], "listings": 10}
    assert set(model_from_report(report)) == {"stat_premiums", "good_roll_counts", "extra_good_roll_factor",
                                              "roll_ranges", "pair_synergies"}


def test_save_replaces_the_model_atomically(tmp_path):
    path = str(tmp_path / "market_model.json")
    save_model(path, {"extra_good_roll_factor": 0.1})
    save_model(path, {"extra_good_roll_factor": 0.3})
    assert load_model(path) == {"extra_good_roll_factor": 0.3}
    assert os.listdir(tmp_path) == ["market_model.json"]   # no temporary files left behind


def test_missing_or_broken_model_loads_empty(tmp_path):
    assert load_model(str(tmp_path / "nope.json")) == {}
    (tmp_path / "bad.json").write_text("{half a fi", encoding="utf-8")
    assert load_model(str(tmp_path / "bad.json")) == {}
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert load_model(str(tmp_path / "list.json")) == {}


def test_pair_bonuses_keep_only_positive_well_supported_pairs():
    assert pair_bonuses({"pair_synergies": PAIRS}) == {
        frozenset({"PhysicalPower", "PhysicalWeaponDamageAdd"}): 18.0}
    assert pair_bonuses({}) == {}


def test_extra_roll_share_is_clamped_with_a_default():
    assert extra_roll_share({"extra_good_roll_factor": 0.4}, 0.25) == 0.4
    assert extra_roll_share({"extra_good_roll_factor": 7}, 0.25) == 1.0
    assert extra_roll_share({"extra_good_roll_factor": -1}, 0.25) == 0.0
    assert extra_roll_share({"extra_good_roll_factor": "x"}, 0.25) == 0.25
    assert extra_roll_share({}, 0.25) == 0.25
