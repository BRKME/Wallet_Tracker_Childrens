"""Мама (08.11): отдельный, более рисковый план — без ETH и золота."""
from datetime import datetime, timezone

import pytest

from risk import policy as P
from risk.assess import DIVERS, GREEN, RED, YELLOW, assess
from risk.exposure import build_exposure
from risk.oracai import OracView
from risk.rebalance import suggest

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


def orac():
    return OracView(regime="BEAR", confidence=0.05, timestamp=datetime.now(timezone.utc))


def test_mama_targets():
    t = P.targets_for("Мама")
    assert t == {"BTC": 0.45, "ETH": 0.0, "ALT": 0.45, "STABLE": 0.10, "GOLD": 0.0, "STOCK": 0.0}
    assert sum(t.values()) == pytest.approx(1.0)
    assert P.targets_for("Марта") == P.BASE_TARGETS  # детей не трогаем


def test_mama_no_eth_no_gold_buys(snapshots):
    e = build_exposure(snapshots["mama"])
    e.name = "Мама"
    s, targets, _ = suggest(e, orac(), WL, [])
    assert targets["ETH"] == 0
    assert not any(x.action == "BUY" and x.what in ("ETH", "Золото") for x in s)


@pytest.mark.parametrize("wallet,alts,level", [
    ("Мама", 0.50, GREEN),    # цель 45%: +5 п.п. — норма
    ("Мама", 0.58, YELLOW),   # > цели +10 п.п.
    ("Мама", 0.68, RED),      # > цели +20 п.п.
    ("Марта", 0.32, YELLOW),  # у детей цель 20%: прежние 30%/40%
    ("Марта", 0.42, RED),
])
def test_alt_flag_relative_to_wallet_target(snapshots, wallet, alts, level):
    e = build_exposure(snapshots["mama"])
    e.name = wallet
    e.by_class = {"ALT": alts * e.net_usd, "BTC": (1 - alts) * e.net_usd}
    e.by_asset = {}
    a = assess([e], e, orac(), {}, {}, None, [], {}, WL)
    alt_flags = [f for f in a.of(DIVERS) if f.text.startswith("Альткоины")]
    got = max((f.level for f in alt_flags), default=GREEN)
    assert got == level
