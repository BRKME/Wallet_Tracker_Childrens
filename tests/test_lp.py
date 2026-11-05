"""LP-позиции: сопоставление с Beefy, APR, IL, флаги."""
import json
import math

import pytest

from risk.assess import LP, RED, YELLOW, assess
from risk.exposure import build_exposure
from risk.lp import build_lp, il_drag, index_beefy, pair_rel_vol, summarize
from risk.market import compute_trend
from risk.oracai import OracView

from .conftest import FIX

WL = {"btc", "eth", "bnb", "hype", "zec", "zro"}


@pytest.fixture
def beefy():
    d = json.loads((FIX / "beefy.json").read_text())
    return index_beefy(d["vaults"], d["apy"], d["tvl"])


def test_all_beefy_positions_matched(snapshots, beefy):
    lps = build_lp(list(snapshots.values()), beefy, {})
    bf = [x for x in lps if x.protocol == "Beefy"]
    assert len(bf) == 11  # ZEC/USDT есть у обоих детей
    assert all(x.vault is not None for x in bf)
    hood = [x for x in bf if x.chain == "hood"]
    assert hood and hood[0].vault.id == "uniswap-cow-robinhood-weth-mstr-rp"


def test_eol_vault_is_red(snapshots, beefy):
    lps = build_lp([snapshots["marta"]], beefy, {})
    eol = [x for x in lps if x.vault and x.vault.status == "eol"]
    assert {x.vault.id for x in eol} == {"aero-cow-tbtc-usdc-vault", "aerodrome-cow-base-usdc-spcxc-vault"}
    assert all(x.apr == 0 for x in eol)
    e = build_exposure(snapshots["marta"])
    a = assess([e], e, OracView(error="x"), {}, {}, None, [], {}, WL, lps=lps)
    assert a.level(LP) == RED
    assert any("eol" in f.text for f in a.of(LP))


def test_apr_is_simple_sum_not_compounded(snapshots, beefy):
    lps = build_lp([snapshots["marta"]], beefy, {})
    zec = next(x for x in lps if x.vault and x.vault.id == "uniswap-cow-bsc-zec-usdt-rp")
    assert zec.apr == pytest.approx(2.412, abs=0.01)   # clmApr
    assert zec.apy > 10                                  # Beefy totalApy (компаунд) — справочно


def test_uniswap_lp_without_apr(snapshots, beefy):
    lps = build_lp([snapshots["arkasha"]], beefy, {})
    uni = [x for x in lps if x.protocol.startswith("Uniswap")]
    assert len(uni) == 1 and uni[0].apr is None and uni[0].value > 1000


def series(g, n=60, start=100.0):
    return [start * (1 + g) ** i for i in range(n)]


def test_pair_rel_vol_and_il():
    import random
    random.seed(1)
    a = [100.0]
    for _ in range(59):
        a.append(a[-1] * math.exp(random.gauss(0, 0.03)))
    ta = compute_trend("ZEC", a)
    assert pair_rel_vol(ta, None) == pytest.approx(ta.vol_30d, rel=1e-6)  # пара со стейблом
    assert pair_rel_vol(ta, ta) == pytest.approx(0, abs=1e-9)          # пара сама с собой
    assert il_drag(0.8) == pytest.approx(0.08)                          # σ²/8


def test_flags_high_apr_small_tvl_unaudited(snapshots, beefy):
    lps = build_lp([snapshots["arkasha"], snapshots["marta"]], beefy, {})
    exps = [build_exposure(snapshots["arkasha"]), build_exposure(snapshots["marta"])]
    a = assess(exps, exps[0], OracView(error="x"), {}, {}, None, [], {}, WL, lps=lps)
    texts = " | ".join(f.text for f in a.of(LP))
    assert "APR" in texts and "TVL" in texts and "аудит" in texts


def test_summary_weighted_apr(snapshots, beefy):
    lps = build_lp(list(snapshots.values()), beefy, {})
    s = summarize(lps)
    priced = [x for x in lps if x.apr is not None]
    exp = sum(x.apr * x.value for x in priced) / sum(x.value for x in priced)
    assert s["apr"] == pytest.approx(exp)
    assert s["income_year"] == pytest.approx(exp * sum(x.value for x in priced))
    assert s["unpriced_value"] > 1000  # Uniswap без APR


def test_eol_becomes_first_exit_suggestion(snapshots, beefy):
    from datetime import datetime, timezone
    from risk.rebalance import suggest
    lps = build_lp([snapshots["marta"]], beefy, {})
    e = build_exposure(snapshots["marta"])
    o = OracView(regime="BEAR", confidence=0.1, timestamp=datetime.now(timezone.utc))
    s, _, _ = suggest(e, o, WL, [], lps)
    assert [x.action for x in s[:2]] == ["EXIT", "EXIT"]


def test_lp_section_renders(snapshots, beefy):
    from risk.report import lp_section
    lps = build_lp(list(snapshots.values()), beefy, {})
    txt = lp_section(lps, summarize(lps))
    assert "⛔" in txt and "EOL" in txt and len(txt) < 4000
    zec = next(l for l in txt.splitlines() if "ZEC/USDT" in l)
    assert "⚠️" in zec  # APR 241% — не «зелёный»
