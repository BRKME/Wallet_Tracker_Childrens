"""Политика займов: держим HF ≥ 1.5. Ниже — гасим часть долга до 1.65."""
from datetime import datetime, timezone

import pytest

from risk import policy as P
from risk.assess import GREEN, LEVERAGE, RED, YELLOW, assess
from risk.exposure import build_exposure
from risk.oracai import OracView
from risk.rebalance import repay_amount, suggest

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


def orac():
    return OracView(regime="BEAR", confidence=0.1, timestamp=datetime.now(timezone.utc))


def test_policy_values():
    assert P.HF_FLOOR == 1.5 and P.HF_RESTORE == 1.65


def test_repay_from_outside_restores_target():
    hf, c, d = 1.45, 3000.0, 1600.0
    r = repay_amount(hf, c, d, 1.65)
    lt = hf * d / c
    assert lt * c / (d - r) == pytest.approx(1.65)


def test_repay_amount_zero_at_or_above_restore():
    """Формула: уже ≥ 1.65 — гасить нечего. Решение «гасить ли» (HF < 1.5) — в suggest."""
    assert repay_amount(1.7, 3000, 1000) == 0.0


def with_hf(snapshots, hf):
    e = build_exposure(snapshots["marta"])
    for loan in e.loans:
        loan.health = hf
    return e


def repays(e):
    s, _, _ = suggest(e, orac(), WL, [])
    return [x for x in s if x.action == "REPAY"]


def test_below_floor_repays_to_restore(snapshots):
    e = with_hf(snapshots, 1.45)
    r = repays(e)
    assert len(r) == 2 and r[0].action == "REPAY"
    assert "1.455" in r[0].reason and "1.65" in r[0].reason  # порог с допуском 3% (08.11)


def test_between_floor_and_restore_no_repay(snapshots):
    """1.55 — выше порога: ничего не гасим, только жёлтое предупреждение."""
    e = with_hf(snapshots, 1.55)
    assert repays(e) == []
    a = assess([e], e, orac(), {}, {}, None, [], {}, WL)
    assert a.level(LEVERAGE) == YELLOW


@pytest.mark.parametrize("hf,level", [(1.45, RED), (1.55, YELLOW), (1.75, GREEN)])
def test_levels(snapshots, hf, level):
    e = with_hf(snapshots, hf)
    e.gross_usd = e.net_usd  # плечо кошелька не влияет на цвет займа
    a = assess([e], e, orac(), {}, {}, None, [], {}, WL)
    assert a.level(LEVERAGE) == level
