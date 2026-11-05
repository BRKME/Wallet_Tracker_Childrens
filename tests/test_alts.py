"""Альткоины: лимит на класс целиком, докупка — 2 сильнейших из белого списка."""
from datetime import datetime, timezone

import pytest

from risk import policy as P
from risk.assess import DIVERS, RED, YELLOW, assess
from risk.exposure import build_exposure
from risk.market import compute_trend, rank_alts
from risk.oracai import OracView
from risk.rebalance import suggest

WL = {"btc", "eth", "bnb", "hype", "aster", "pump", "zro", "morpho", "tao", "zec"}


def orac():
    return OracView(regime="BEAR", risk_state="RISK_NEUTRAL", confidence=0.1,
                    timestamp=datetime.now(timezone.utc))


def series(daily_growth, n=220):
    return [100 * (1 + daily_growth) ** i for i in range(n)]


def test_trend_has_90d_return():
    t = compute_trend("X", series(0.01))
    assert t.ret_90d == pytest.approx(1.01 ** 90 - 1)


def test_rank_alts_strongest_first_downtrend_last():
    trends = {a: compute_trend(a, series(g)) for a, g in
              [("HYPE", 0.004), ("TAO", 0.002), ("ZEC", -0.003), ("BNB", 0.001), ("BTC", 0.003)]}
    ranked = rank_alts(trends, WL)
    assert [r.asset for r in ranked] == ["HYPE", "TAO", "BNB", "ZEC"]  # BTC не альт
    assert ranked[-1].trend.label == "DOWN"


def test_rank_alts_ignores_non_whitelist():
    trends = {"SOL": compute_trend("SOL", series(0.01)), "TAO": compute_trend("TAO", series(0.001))}
    assert [r.asset for r in rank_alts(trends, WL)] == ["TAO"]


def ranking(*assets):
    return rank_alts({a: compute_trend(a, series(0.005 - i * 0.002)) for i, a in enumerate(assets)}, WL)


def test_no_single_alt_cap_sales(snapshots):
    """У Мамы ZEC 41% — больше нет «продать ZEC: один альт > 7%»."""
    e = build_exposure(snapshots["mama"])
    s, _, _ = suggest(e, orac(), WL, ranking("HYPE", "TAO", "ZEC"))
    assert not any("один альт" in x.reason for x in s)
    assert not hasattr(P, "SINGLE_ALT_MAX")


def test_buy_alts_names_two_strongest(snapshots):
    e = build_exposure(snapshots["marta"])  # альтов ~5% из белого списка при цели 20%
    s, _, _ = suggest(e, orac(), WL, ranking("HYPE", "TAO", "MORPHO"))
    buys = [x for x in s if x.action == "BUY" and x.what in ("HYPE", "TAO", "MORPHO")]
    assert [x.what for x in buys] == ["HYPE", "TAO"]
    assert buys[0].usd == pytest.approx(buys[1].usd)


def test_sell_alts_takes_weakest_held_first(snapshots):
    """Мама: альтов ~68% при цели 20% -> продавать сначала самый слабый из имеющихся."""
    e = build_exposure(snapshots["mama"])
    s, _, _ = suggest(e, orac(), WL, ranking("HYPE", "ZEC"))  # ZEC слабее HYPE
    sells = [x for x in s if x.action == "SELL" and x.what in ("HYPE", "ZEC")]
    assert sells and sells[0].what == "ZEC"
    assert "№2 из 2" in sells[0].reason
    total_alt_sell = sum(x.usd for x in s if x.action == "SELL" and x.what in ("HYPE", "ZEC", "NEAR", "UNI", "BNB"))
    excess = e.by_class["ALT"] - P.targets_for("Мама")["ALT"] * e.net_usd
    assert total_alt_sell == pytest.approx(excess, rel=0.05)


def test_non_whitelist_still_sold(snapshots):
    e = build_exposure(snapshots["marta"])
    s, _, _ = suggest(e, orac(), WL, ranking("HYPE", "TAO"))
    assert ("SELL", "SOL") in {(x.action, x.what) for x in s}


def test_no_ranking_falls_back_to_class(snapshots):
    e = build_exposure(snapshots["marta"])
    s, _, _ = suggest(e, orac(), WL, [])
    assert any(x.action == "BUY" and x.what == "Альткоины" for x in s)


def test_alt_class_flag_not_single_alt(snapshots):
    exps = [build_exposure(snapshots["mama"])]
    a = assess(exps, exps[0], orac(), {}, {}, None, [], {}, WL)
    texts = [f.text for f in a.of(DIVERS)]
    assert not any("один альткоин" in t for t in texts)
    assert any(t.startswith("Альткоины") for t in texts)
    assert a.level(DIVERS) == RED  # ~68% > ALT_CLASS_RED


def test_alt_class_thresholds():
    assert P.targets_for("x")["ALT"] < P.ALT_CLASS_YELLOW < P.ALT_CLASS_RED
