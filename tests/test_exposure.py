import pytest

from risk.exposure import build_exposure, combine, hhi, liquidation_drop


def test_liquidation_all_stable_debt():
    # HF 2, залог волатильный, долг в стейблах: ликвидация при −50%
    assert liquidation_drop(2.0, 1000, 0, 0, 400) == pytest.approx(0.5)


def test_liquidation_mixed_matches_definition():
    hf, cv, cs, dv, ds = 1.5, 3000, 600, 700, 1200
    s = liquidation_drop(hf, cv, cs, dv, ds)
    lt = hf * (dv + ds) / (cv + cs)
    after = lt * (cv * (1 - s) + cs) / (dv * (1 - s) + ds)
    assert after == pytest.approx(1.0)


def test_liquidation_volatile_debt_not_dangerous_on_drop():
    assert liquidation_drop(1.5, 100, 900, 600, 0) is None


def test_liquidation_no_debt():
    assert liquidation_drop(None, 100, 0, 0, 0) is None


def test_exposure_real_marta(snapshots):
    e = build_exposure(snapshots["marta"])
    assert e.leverage > 1.3
    assert len(e.loans) == 2
    for loan in e.loans:
        assert 0.2 < loan.drop_to_liquidation < 0.8
    assert sum(e.by_class.values()) == pytest.approx(e.net_usd)
    assert e.by_class["BTC"] > 0.3 * e.net_usd


def test_family_combined(snapshots):
    fam = build_exposure(combine("Семья", list(snapshots.values())))
    assert fam.net_usd == pytest.approx(sum(s.net_usd for s in snapshots.values()))


def test_hhi():
    assert hhi([1, 1, 1, 1]) == pytest.approx(0.25)
    assert hhi([5]) == 1.0
    assert hhi([]) == 0.0
