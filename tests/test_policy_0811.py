"""Решения владельца 08.11:
1) допуск 3% к порогу HF 1.5: гасим только ниже 1.455;
2) уверенность OracAI < 40% — его сигналы не влияют ни на доли, ни на светофор."""
from datetime import datetime, timezone

import pytest

from risk import policy as P
from risk.assess import GREEN, LEVERAGE, RED, SYSTEMIC, YELLOW, assess
from risk.exposure import build_exposure
from risk.oracai import OracView
from risk.rebalance import suggest, tilted_targets

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


def orac(conf, risk="RISK_OFF", tail=True, regime="TRANSITION"):
    return OracView(regime=regime, risk_state=risk, confidence=conf, tail_risk=tail,
                    timestamp=datetime.now(timezone.utc))


def test_hf_tolerance_value():
    assert P.HF_TOLERANCE == 0.03
    assert P.hf_act_level() == pytest.approx(1.455)


def loans_at(snapshots, hf):
    e = build_exposure(snapshots["marta"])
    for loan in e.loans:
        loan.health = hf
    e.gross_usd = e.net_usd
    return e


@pytest.mark.parametrize("hf,repay,level", [
    (1.498, False, YELLOW),   # в пределах допуска — наблюдаем
    (1.46, False, YELLOW),
    (1.45, True, RED),        # ниже 1.455 — гасить
])
def test_tolerance(snapshots, hf, repay, level):
    e = loans_at(snapshots, hf)
    s, _, _ = suggest(e, orac(0.05), WL, [])
    assert any(x.action == "REPAY" for x in s) == repay
    a = assess([e], e, orac(0.05), {}, {}, None, [], {}, WL)
    assert a.level(LEVERAGE) == level


def test_low_confidence_oracai_is_info_only(snapshots):
    """5% уверенности: хвостовой риск / RISK_OFF — справка (зелёный), не красный."""
    e = build_exposure(snapshots["mama"])
    a = assess([e], e, orac(0.05), {}, {"USDT": 1.0}, 50, [], {}, WL)
    assert a.level(SYSTEMIC) == GREEN
    assert any("не ориентируемся" in f.text for f in a.of(SYSTEMIC))


def test_confident_tail_risk_still_red(snapshots):
    e = build_exposure(snapshots["mama"])
    a = assess([e], e, orac(0.6), {}, {}, None, [], {}, WL)
    assert a.level(SYSTEMIC) == RED


def test_low_confidence_no_tilt():
    t, note = tilted_targets("x", orac(0.05))
    assert t == P.targets_for("x") and "базов" in note
