"""Риск протоколов: взломы (DefiLlama hacks) и отток TVL (DefiLlama protocols).

Взлом протокола, где лежат деньги, или эмитента обёртки (BitGo, Tether,
LayerZero...) — прямой сигнал. Резкий отток TVL — ранний индикатор: деньги
бегут раньше, чем выходит пост-мортем.
"""

import time
from dataclasses import dataclass

HACKS_URL = "https://api.llama.fi/hacks"
PROTOCOLS_URL = "https://api.llama.fi/protocols"

# Контрагенты обёрток -> ключевые слова для поиска во взломах
COUNTERPARTY_KEYWORDS = {
    "BitGo": ["bitgo", "wbtc", "bit global"],
    "Binance": ["binance", "bnb chain", "bsc bridge"],
    "Tether": ["tether", "usdt0"],
    "LayerZero": ["layerzero", "stargate"],
    "Circle": ["circle", "usdc"],
    "Coinbase": ["coinbase", "cbbtc"],
    "Threshold": ["threshold", "tbtc"],
    "Hyperliquid": ["hyperliquid", "hyperevm"],
    "Robinhood": ["robinhood"],
}

BIG_HACK_USD = 10_000_000


@dataclass
class HackHit:
    date: str
    name: str
    amount: float | None
    technique: str
    matched: str     # что из портфеля задело
    direct: bool     # True — наш протокол/эмитент, False — фоновый крупный взлом


@dataclass
class TvlInfo:
    protocol: str
    tvl: float
    change_7d: float | None
    change_1d: float | None


def base_name(protocol: str) -> str:
    """'Aave V3' -> 'aave', 'Uniswap V3' -> 'uniswap', 'Pendle V2' -> 'pendle'."""
    return (protocol or "").split(" ")[0].lower()


def match_hacks(hacks: list, protocols: list, counterparties: list,
                days: int = 30, now: float | None = None) -> list:
    now = now or time.time()
    since = now - days * 86400
    since_bg = now - 7 * 86400
    proto_keys = {base_name(p): p for p in protocols if p}
    cp_keys = {}
    for cp in counterparties:
        for brand, kws in COUNTERPARTY_KEYWORDS.items():
            if brand.lower() in cp.lower():
                for kw in kws:
                    cp_keys[kw] = cp

    hits = []
    for h in hacks:
        if (h.get("date") or 0) < since:
            continue
        name = (h.get("name") or "")
        # Только по имени: описания упоминают Tether/USDC почти в каждом
        # взломе («Tether заморозил...»), это давало ложные срабатывания.
        nm = name.lower()
        matched = None
        for key, proto in proto_keys.items():
            if key and (nm == key or nm.startswith(key + " ")):
                matched = proto
                break
        if not matched:
            for kw, cp in cp_keys.items():
                if kw in nm:
                    matched = cp
                    break
        date = time.strftime("%d.%m", time.gmtime(h["date"]))
        if matched:
            hits.append(HackHit(date, name, h.get("amount"), h.get("technique") or "", matched, True))
        elif (h.get("amount") or 0) >= BIG_HACK_USD and h["date"] >= since_bg:
            hits.append(HackHit(date, name, h.get("amount"), h.get("technique") or "", "", False))
    hits.sort(key=lambda x: (not x.direct, -(x.amount or 0)))
    return hits


def match_tvl(llama_protocols: list, protocols: list) -> dict:
    """{'Aave V3': TvlInfo}. Сопоставление по точному имени, иначе по базовому."""
    by_name = {p.get("name", "").lower(): p for p in llama_protocols}
    out = {}
    for proto in protocols:
        p = by_name.get(proto.lower()) or by_name.get(base_name(proto))
        if p:
            out[proto] = TvlInfo(proto, p.get("tvl") or 0, p.get("change_7d"), p.get("change_1d"))
    return out


async def fetch_llama(session):
    """-> (hacks, protocols, errors)."""
    hacks, protos, errors = [], [], []
    for url, sink in ((HACKS_URL, hacks), (PROTOCOLS_URL, protos)):
        try:
            async with session.get(url, timeout=60) as r:
                r.raise_for_status()
                sink.extend(await r.json(content_type=None))
        except Exception as e:  # noqa: BLE001
            errors.append(f"DefiLlama {url.rsplit('/', 1)[-1]}: {e}")
    return hacks, protos, errors
