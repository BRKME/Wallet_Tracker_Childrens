"""Экспозиция: на что на самом деле завязан кошелёк.

Всё считается от ЧИСТОЙ стоимости (залог − долг). Занял USDT под WBTC —
значит, экспозиция на BTC больше 100% собственных денег по этому кусочку,
и это видно в доле BTC > 100% × (доля залога).
"""

from collections import defaultdict
from dataclasses import dataclass, field

from .assets import STABLE

STABLE_CANON = "USD"


@dataclass
class Exposure:
    name: str
    net_usd: float
    gross_usd: float
    debt_usd: float
    by_asset: dict = field(default_factory=dict)        # BTC -> usd (net)
    by_class: dict = field(default_factory=dict)        # BTC/ETH/STABLE... -> usd
    by_protocol: dict = field(default_factory=dict)     # "Aave V3 (arb)" -> net usd
    by_chain: dict = field(default_factory=dict)        # gross usd
    by_counterparty: dict = field(default_factory=dict)  # gross usd
    loans: list = field(default_factory=list)           # LoanRisk

    def share(self, usd: float) -> float:
        return usd / self.net_usd if self.net_usd > 0 else 0.0

    @property
    def leverage(self) -> float:
        return self.gross_usd / self.net_usd if self.net_usd > 0 else 0.0


@dataclass
class LoanRisk:
    wallet: str
    protocol: str
    chain: str
    health: float
    collateral_usd: float
    debt_usd: float
    drop_to_liquidation: float | None  # 0.35 = крипта −35% -> ликвидация; None = падение не ликвидирует


def liquidation_drop(health: float, coll_volatile: float, coll_stable: float,
                     debt_volatile: float, debt_stable: float) -> float | None:
    """На какую долю должны синхронно упасть все волатильные активы, чтобы HF стал 1.

    Средневзвешенный порог ликвидации lt выводится из текущего HF:
        HF = lt·C / D  =>  lt = HF·D / C
    После падения на s:  lt·(Cv(1−s) + Cs) = Dv(1−s) + Ds
        => (1−s) = (Ds − lt·Cs) / (lt·Cv − Dv)
    Если знаменатель ≤ 0 — долг волатильнее залога, падение не опасно
    (опасен рост — это отдельный случай, возвращаем None).
    """
    C = coll_volatile + coll_stable
    D = debt_volatile + debt_stable
    if not health or C <= 0 or D <= 0:
        return None
    lt = health * D / C
    denom = lt * coll_volatile - debt_volatile
    if denom <= 0:
        return None
    k = (debt_stable - lt * coll_stable) / denom  # = 1 − s
    s = 1 - k
    return max(0.0, min(1.0, s))


def build_exposure(snap) -> Exposure:
    by_asset, by_class = defaultdict(float), defaultdict(float)
    by_chain, by_cp = defaultdict(float), defaultdict(float)
    by_protocol = defaultdict(float)

    for h in snap.holdings:
        by_asset[h.canonical] += h.value
        by_class[h.asset_class] += h.value
        if h.value > 0:
            by_chain[h.chain] += h.value
            if h.counterparty:
                by_cp[h.counterparty] += h.value

    for p in snap.positions:
        by_protocol[f"{p.protocol} ({p.chain})"] += p.net_usd
    wallet_usd = sum(h.value for h in snap.holdings if h.source == "wallet")
    if wallet_usd:
        by_protocol["Кошелёк (без протоколов)"] += wallet_usd

    loans = []
    for p in snap.positions:
        if not p.health_rate or p.debt_usd <= 0:
            continue
        cv = cs = dv = ds = 0.0
        for h in p.holdings:
            stable = h.asset_class == STABLE
            if h.value > 0:
                cs, cv = (cs + h.value, cv) if stable else (cs, cv + h.value)
            else:
                ds, dv = (ds - h.value, dv) if stable else (ds, dv - h.value)
        loans.append(LoanRisk(snap.name, p.protocol, p.chain, p.health_rate,
                              cv + cs, dv + ds,
                              liquidation_drop(p.health_rate, cv, cs, dv, ds)))

    return Exposure(
        name=snap.name, net_usd=snap.net_usd, gross_usd=snap.gross_assets,
        debt_usd=snap.debt_usd,
        by_asset=_sorted(by_asset), by_class=_sorted(by_class),
        by_protocol=_sorted(by_protocol), by_chain=_sorted(by_chain),
        by_counterparty=_sorted(by_cp), loans=loans,
    )


def combine(name: str, snaps: list):
    """Семейный портфель целиком: один снимок из всех кошельков."""
    from .positions import WalletSnapshot
    s = WalletSnapshot(name=name)
    for x in snaps:
        s.holdings.extend(x.holdings)
        s.positions.extend(x.positions)
    return s


def hhi(shares) -> float:
    """Индекс Херфиндаля по долям (0..1). 1/HHI = «эффективное число активов»."""
    pos = [max(0.0, x) for x in shares]
    tot = sum(pos)
    return sum((x / tot) ** 2 for x in pos) if tot else 0.0


def _sorted(d: dict) -> dict:
    return dict(sorted(((k, v) for k, v in d.items() if abs(v) >= 0.5),
                       key=lambda kv: -abs(kv[1])))
