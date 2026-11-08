import time

import pytest

from risk.market import compute_trend, rsi
from risk.protocols import base_name, match_hacks, match_tvl

NOW = 1_800_000_000


def test_trend_up_and_down():
    up = compute_trend("X", [100 + i for i in range(220)])
    dn = compute_trend("X", [400 - i for i in range(220)])
    assert up.label == "UP" and dn.label == "DOWN"
    assert up.ret_30d > 0 > dn.ret_30d
    assert dn.dd_from_high < 0 and up.dd_from_high == 0


def test_trend_short_history():
    assert compute_trend("X", [1.0] * 10) is None
    t = compute_trend("X", [1 + i * 0.01 for i in range(60)])
    assert t.sma200 is None and t.label == "UP"


def test_rsi_bounds():
    assert rsi([float(i) for i in range(1, 40)]) == 100.0
    r = rsi([100 + (5 if i % 2 else -5) for i in range(60)])
    assert 40 < r < 60


def hack(name, days_ago, amount=1e6, desc=""):
    return {"name": name, "date": NOW - days_ago * 86400, "amount": amount,
            "technique": "x", "description": desc}


def test_hack_direct_protocol_match():
    hits = match_hacks([hack("Beefy", 3), hack("Beefy", 40), hack("Aavegotchi", 1)],
                       ["Beefy", "Aave V3"], [], now=NOW)
    assert [(h.name, h.direct) for h in hits] == [("Beefy", True)]


def test_hack_counterparty_keyword_and_background():
    hits = match_hacks([hack("LayerZero Bridge", 2),
                        hack("BigCEX", 2, amount=5e7), hack("SmallCEX", 2, amount=1e5)],
                       [], ["Tether + мост LayerZero OFT"], now=NOW)
    assert [(h.name, h.direct) for h in hits] == [("LayerZero Bridge", True), ("BigCEX", False)]


def test_base_name_and_tvl():
    assert base_name("Pendle V2") == "pendle"
    tvl = match_tvl([{"name": "Aave V3", "tvl": 1e9, "change_7d": -5},
                     {"name": "Pendle", "tvl": 1e8, "change_7d": -30}], ["Aave V3", "Pendle V2", "Nope"])
    assert set(tvl) == {"Aave V3", "Pendle V2"} and tvl["Pendle V2"].change_7d == -30


def test_hack_description_mention_is_not_a_match():
    """«Tether заморозил украденное» в описании чужого взлома — не взлом Tether."""
    hits = match_hacks([hack("Mercado Bitcoin", 2, amount=4e7, desc="Tether froze $4.5M USDT")],
                       [], ["Tether Gold (кастодиальный)"], now=NOW)
    assert [(h.name, h.direct) for h in hits] == [("Mercado Bitcoin", False)]


def test_hack_custodian_by_name():
    hits = match_hacks([hack("Binance CEX", 7, amount=6.36e9)], [], ["Binance (кастодиальный, BSC)"], now=NOW)
    assert hits[0].direct and hits[0].matched.startswith("Binance")
