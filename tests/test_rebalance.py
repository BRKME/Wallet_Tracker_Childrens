import pytest
from datetime import datetime, timezone

from risk import policy as P
from risk.exposure import build_exposure
from risk.oracai import OracView, parse_oracai
from risk.rebalance import deleverage_amount, suggest, tilted_targets

WL = {"btc", "eth", "bnb", "hype", "aster", "pump", "zro", "morpho", "tao", "zec"}


def orac(regime="BEAR", risk="RISK_OFF", conf=0.6):
    return OracView(regime=regime, risk_state=risk, confidence=conf,
                    timestamp=datetime.now(timezone.utc))


def test_deleverage_reaches_target():
    hf, c, d = 1.4, 1000.0, 500.0
    r = deleverage_amount(hf, c, d, 2.0)
    lt = hf * d / c
    assert lt * (c - r) / (d - r) == pytest.approx(2.0)


def test_no_deleverage_when_safe():
    assert deleverage_amount(2.5, 1000, 100) == 0.0


def test_tilt_respects_confidence_gate():
    t, note = tilted_targets("x", orac(conf=0.1))
    assert t == P.targets_for("x") and "уверенность" in note


def test_tilt_risk_off_adds_stables_and_keeps_sum():
    base = P.targets_for("x")
    t, _ = tilted_targets("x", orac())
    assert t["STABLE"] == pytest.approx(base["STABLE"] + P.TILT_RISK_OFF_STABLE)
    assert sum(t.values()) == pytest.approx(1.0)


def test_stale_oracai_no_tilt():
    o = orac()
    o.timestamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    t, note = tilted_targets("x", o)
    assert t == P.targets_for("x") and "устарели" in note


def test_suggestions_real_mama(snapshots):
    """Мама: ZEC ~41% и стейблы 32%, BTC/ETH нет в EVM-части -> продать ZEC сверх лимита,
    NEAR вне белого списка, докупить BTC/ETH."""
    e = build_exposure(snapshots["mama"])
    s, _, _ = suggest(e, orac(conf=0.1), WL)
    acts = {(x.action, x.what) for x in s}
    assert ("SELL", "ZEC") in acts
    assert ("SELL", "NEAR") in acts
    assert ("BUY", "BTC") in acts
    assert all(x.usd >= P.MIN_TRADE_USD for x in s)


def test_suggestions_order_repay_first(snapshots):
    e = build_exposure(snapshots["marta"])
    for loan in e.loans:
        loan.health = 1.2  # искусственно опасно
    s, _, _ = suggest(e, orac(conf=0.1), WL)
    assert s[0].action == "REPAY"


def test_parse_real_oracai_shape():
    lo = {"regime": "BEAR", "probabilities": {"BEAR": 0.24}, "confidence": {"base": 0.19, "quality_adjusted": 0.14},
          "risk": {"risk_state": "RISK_NEUTRAL", "risk_exposure_cap": 0.7},
          "cycle": {"phase": "EARLY_BULL", "action": "BUY"},
          "metadata": {"timestamp": "2026-11-05T16:19:08.194217", "mvrv": 1.49},
          "asset_allocation": {"meta": {"tail_risk_active": False}}}
    v = parse_oracai(lo, {"zone": "NEUTRAL", "signal": {"action": "HOLD", "rationale": "держим"}})
    assert v.regime == "BEAR" and v.confidence == 0.14 and v.ladder_action == "HOLD"
    assert v.age_hours(datetime(2026, 11, 6, 16, 19, 8, tzinfo=timezone.utc)) == pytest.approx(24, abs=0.01)
    assert not v.risk_off


def test_point_sales_not_followed_by_stable_sale(snapshots):
    """Продали ZEC/NEAR — не должно тут же появиться «продать стейблы» сверх реального избытка."""
    e = build_exposure(snapshots["mama"])
    s, targets, _ = suggest(e, orac(conf=0.1), WL)
    stable_sell = sum(x.usd for x in s if x.action == "SELL" and x.what == "Стейблы")
    real_excess = e.by_class["STABLE"] - targets["STABLE"] * e.net_usd
    assert stable_sell <= real_excess + 1


def test_sells_and_buys_roughly_balance(snapshots):
    for k in ("marta", "arkasha", "mama"):
        e = build_exposure(snapshots[k])
        s, _, _ = suggest(e, orac(conf=0.1), WL)
        sells = sum(x.usd for x in s if x.action == "SELL")
        buys = sum(x.usd for x in s if x.action == "BUY")
        # Полосы ±5 п.п. дают небаланс, но не больше ~30% кошелька
        assert abs(sells - buys) < 0.3 * e.net_usd, k


def test_pct_no_negative_zero():
    from risk.report import pct
    assert pct(-0.001) == "0%" and pct(0.256) == "26%" and pct(-0.11, True) == "-11%"
