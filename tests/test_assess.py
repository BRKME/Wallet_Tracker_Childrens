from datetime import datetime, timezone

from risk.assess import DIVERS, LEVERAGE, PROTOCOL, RED, SYSTEMIC, YELLOW, assess
from risk.exposure import build_exposure, combine
from risk.oracai import OracView
from risk.protocols import HackHit, TvlInfo

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


def run(snapshots, **kw):
    exps = [build_exposure(s) for s in snapshots.values()]
    fam = build_exposure(combine("Семья", list(snapshots.values())))
    args = dict(orac=OracView(regime="BEAR", risk_state="RISK_NEUTRAL", confidence=0.14,
                              timestamp=datetime.now(timezone.utc)),
                trends={}, pegs={"USDT": 1.0, "USDC": 1.0}, fear_greed=50, hacks=[], tvl={})
    args.update(kw)
    return assess(exps, fam, whitelist=WL, **args)


def test_real_portfolio_flags(snapshots):
    a = run(snapshots)
    texts = " | ".join(f.text for f in a.flags)
    assert "ZEC" in texts  # у Мамы ZEC ~41%
    assert a.level(DIVERS) == RED
    assert len(a.of(LEVERAGE)) >= 4  # 4 займа Aave
    assert a.level(SYSTEMIC) < YELLOW  # BEAR при уверенности 14% — не сигнал


def test_depeg_and_hack_are_red(snapshots):
    a = run(snapshots, pegs={"USDT": 0.97},
            hacks=[HackHit("01.11", "Beefy", 2e6, "x", "Beefy", True)],
            tvl={"Aave V3": TvlInfo("Aave V3", 1e9, -45, -10)})
    assert a.level(SYSTEMIC) == RED and a.level(PROTOCOL) == RED


def test_dangerous_hf_is_red(snapshots):
    exps = [build_exposure(snapshots["marta"])]
    exps[0].loans[0].health = 1.1
    a = assess(exps, exps[0], OracView(error="нет"), {}, {}, None, [], {}, WL)
    assert a.level(LEVERAGE) == RED
