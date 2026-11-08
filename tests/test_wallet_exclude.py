"""Мама (08.11): ZRO исключён из её плана — не докупать, если есть — продать."""
from datetime import datetime, timezone

from risk import policy as P
from risk.exposure import build_exposure
from risk.market import compute_trend, rank_alts
from risk.oracai import OracView
from risk.rebalance import suggest

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


def orac():
    return OracView(regime="BEAR", confidence=0.05, timestamp=datetime.now(timezone.utc))


def ranking(*assets):
    return rank_alts({a: compute_trend(a, [100 * (1 + 0.005 - i * 0.001) ** k for k in range(220)])
                      for i, a in enumerate(assets)}, WL)


def test_policy_has_exclusion():
    assert P.excluded_alts("Мама") == {"ZRO"}
    assert P.excluded_alts("Марта") == set()


def mama(snapshots, alt_share_shift=0.0):
    e = build_exposure(snapshots["mama"])
    e.name = "Мама"
    return e


def test_mama_buys_skip_zro(snapshots):
    e = mama(snapshots)
    # альтов ниже цели 45% -> докупка
    e.by_class["ALT"] = 0.30 * e.net_usd
    s, _, _ = suggest(e, orac(), WL, ranking("ZEC", "ZRO", "HYPE"))
    bought = [x.what for x in s if x.action == "BUY" and x.what in ("ZEC", "ZRO", "HYPE")]
    assert bought == ["ZEC", "HYPE"]


def test_children_still_buy_zro(snapshots):
    e = build_exposure(snapshots["marta"])
    e.name = "Марта"
    s, _, _ = suggest(e, orac(), WL, ranking("ZEC", "ZRO", "HYPE"))
    assert "ZRO" in [x.what for x in s if x.action == "BUY"]


def test_mama_held_zro_is_sold(snapshots):
    e = mama(snapshots)
    e.by_asset["ZRO"] = 300.0
    e.by_class["ALT"] += 300.0
    s, _, _ = suggest(e, orac(), WL, ranking("ZEC", "ZRO", "HYPE"))
    zro = [x for x in s if x.what == "ZRO"]
    assert zro and zro[0].action == "SELL" and "исключ" in zro[0].reason
