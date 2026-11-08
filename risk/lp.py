"""LP-позиции: доходность (APR из API Beefy) против рисков.

Что считаем по каждой позиции:
- статус хранилища Beefy (active / eol / paused): eol — доход 0, стратегия
  выключена, деньги нужно забирать;
- APR — ПРОСТОЙ годовой доход (сумма компонент из /apy/breakdown). Beefy
  показывает ещё totalApy с ежедневным реинвестом — на волатильных CLM-парах
  он даёт 1000%+, это не ожидаемый доход, поэтому только справочно;
- IL — непостоянные потери от расхождения цен пары. Оценка для полного
  диапазона: σ²/8 в год, где σ — годовая волатильность отношения цен пары
  за 30 дней. У концентрированной ликвидности (Beefy CLM / Uniswap V3) потери
  в разы больше — это НИЖНЯЯ граница;
- риски Beefy (нет аудита, нет таймлока, не верифицирован), TVL хранилища
  и наша доля в нём.
"""

import json
import logging
import math
from dataclasses import dataclass, field

from .assets import STABLE, asset_class, canonical_symbol

logger = logging.getLogger(__name__)

API = "https://api.beefy.finance"

# Сети DeBank -> сети Beefy
CHAIN_MAP = {"arb": "arbitrum", "eth": "ethereum", "op": "optimism", "matic": "polygon",
             "hood": "robinhood", "era": "zksync", "mnt": "mantle", "bera": "berachain",
             "hyper": "hyperevm", "xdai": "gnosis", "avax": "avax"}

APR_FIELDS = ("vaultApr", "clmApr", "tradingApr", "rewardPoolApr", "merklApr",
              "liquidStakingApr", "composablePoolApr", "rewardPoolTradingApr")

RISK_LABELS = {
    "notAudited": "нет аудита",
    "notVerified": "контракт не верифицирован",
    "notTimelocked": "нет таймлока",
    "notBattleTested": "не проверен временем",
    "curated": None, "complex": None, "notCorrelated": None, "synthAsset": "синтетический актив",
}
LP_KINDS = {"Liquidity Pool", "Farming", "Yield"}


@dataclass
class BeefyVault:
    id: str
    name: str
    chain: str
    status: str
    assets: list
    risks: list          # человекочитаемые: «нет аудита»...
    apr: float           # простой
    apy: float | None    # компаунд, справочно
    tvl: float | None


@dataclass
class LpPosition:
    wallet: str
    protocol: str
    chain: str
    value: float
    assets: list                     # канонические тикеры пары
    vault: BeefyVault | None = None
    apr: float | None = None
    apy: float | None = None
    rel_vol: float | None = None
    il: float | None = None          # годовая оценка IL (нижняя граница)
    concentrated: bool = False
    notes: list = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.vault:
            return "/".join(self.vault.assets)
        return "/".join(self.assets) or self.protocol

    @property
    def tvl_share(self) -> float | None:
        if self.vault and self.vault.tvl:
            return self.value / self.vault.tvl
        return None

    @property
    def net_apr(self) -> float | None:
        if self.apr is None or self.il is None:
            return None
        return self.apr - self.il


def index_beefy(vaults: list, apy: dict, tvl: dict) -> dict:
    """{адрес контракта (lower): BeefyVault}. tvl — плоский {vault_id: usd}."""
    out = {}
    for v in vaults:
        addr = (v.get("earnContractAddress") or "").lower()
        if not addr:
            continue
        b = apy.get(v["id"]) or {}
        apr = sum(float(b[k]) for k in APR_FIELDS if b.get(k) is not None)
        risks_raw = v.get("risks") or {}
        if isinstance(risks_raw, dict):
            risks = [RISK_LABELS[k] for k, on in risks_raw.items() if on is True and RISK_LABELS.get(k)]
        else:
            risks = []
        out[addr] = BeefyVault(
            id=v["id"], name=v.get("name") or v["id"], chain=v.get("chain") or "",
            status=v.get("status") or "?", assets=list(v.get("assets") or []), risks=risks,
            apr=0.0 if v.get("status") == "eol" else apr,
            apy=b.get("totalApy"), tvl=tvl.get(v["id"]),
        )
    return out


def pair_rel_vol(ta, tb) -> float | None:
    """Годовая волатильность отношения цен пары за 30 дней. tb=None — второй актив стейбл."""
    if ta is None or not ta.closes:
        return None
    if tb is None:
        return ta.vol_30d
    if not tb.closes:
        return None
    n = min(len(ta.closes), len(tb.closes), 31)
    a, b = ta.closes[-n:], tb.closes[-n:]
    lr = [math.log((a[i] / b[i]) / (a[i - 1] / b[i - 1])) for i in range(1, n)]
    if len(lr) < 10:
        return None
    mu = sum(lr) / len(lr)
    return math.sqrt(sum((x - mu) ** 2 for x in lr) / (len(lr) - 1)) * math.sqrt(365)


def il_drag(rel_vol: float) -> float:
    """Ожидаемые потери LP 50/50 полного диапазона за год: σ²/8."""
    return rel_vol ** 2 / 8


def build_lp(snaps: list, beefy: dict, trends: dict) -> list:
    out = []
    for s in snaps:
        for p in s.positions:
            is_beefy = "beefy" in p.protocol_id
            if not is_beefy and p.kind not in LP_KINDS:
                continue
            assets = []
            for h in p.holdings:
                if h.kind == "supply" and h.canonical not in assets:
                    assets.append(h.canonical)
            lp = LpPosition(s.name, p.protocol, p.chain, p.net_usd, assets,
                            concentrated=is_beefy or "uniswap3" in p.protocol_id)
            if is_beefy:
                v = beefy.get((p.pool_id or "").lower())
                if v and v.chain not in (p.chain, CHAIN_MAP.get(p.chain, p.chain)):
                    v = None
                lp.vault = v
                if v:
                    lp.apr, lp.apy = v.apr, v.apy
                    lp.assets = [canonical_symbol(x) for x in v.assets] or assets
                else:
                    lp.notes.append("хранилище не найдено в API Beefy")
            # IL — только если оба актива имеют график или один из них стейбл
            vol_assets = [a for a in lp.assets if asset_class(a) != STABLE]
            if len(lp.assets) == 2 and vol_assets:
                if len(vol_assets) == 1:
                    lp.rel_vol = pair_rel_vol(trends.get(vol_assets[0]), None)
                else:
                    lp.rel_vol = pair_rel_vol(trends.get(vol_assets[0]), trends.get(vol_assets[1]))
                if lp.rel_vol is not None:
                    lp.il = il_drag(lp.rel_vol)
            elif len(lp.assets) == 2:
                lp.rel_vol, lp.il = 0.0, 0.0
            out.append(lp)
    return out


def summarize(lps: list) -> dict:
    priced = [x for x in lps if x.apr is not None]
    v = sum(x.value for x in priced)
    apr = sum(x.apr * x.value for x in priced) / v if v else 0.0
    with_il = [x for x in priced if x.il is not None]
    vi = sum(x.value for x in with_il)
    return {
        "value": sum(x.value for x in lps),
        "apr": apr,
        "income_year": apr * v,
        "il_year": sum(x.il * x.value for x in with_il),
        "il_covered_value": vi,
        "unpriced_value": sum(x.value for x in lps if x.apr is None),
    }


async def fetch_beefy(session) -> tuple[dict, list]:
    """-> (индекс хранилищ, ошибки)."""
    errors, parts = [], {}
    for key in ("vaults", "gov-vaults", "apy/breakdown", "tvl"):
        try:
            async with session.get(f"{API}/{key}", timeout=60) as r:
                r.raise_for_status()
                parts[key] = json.loads(await r.text())
        except Exception as e:  # noqa: BLE001
            errors.append(f"Beefy {key}: {e}")
            parts[key] = [] if "vaults" in key else {}
    tvl = {}
    for chain_tvl in (parts["tvl"] or {}).values():
        if isinstance(chain_tvl, dict):
            tvl.update(chain_tvl)
    idx = index_beefy(list(parts["vaults"]) + list(parts["gov-vaults"]), parts["apy/breakdown"] or {}, tvl)
    return idx, errors
