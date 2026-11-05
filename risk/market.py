"""Тренды по дневным графикам монет портфеля (CoinGecko, бесплатный API).

Только то, что считается из цены: SMA50/SMA200, доходность 30д, волатильность,
просадка от максимума 200д, RSI14. Никаких прогнозов — это описание графика.
"""

import asyncio
import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin", "SOL": "solana",
    "HYPE": "hyperliquid", "ZEC": "zcash", "NEAR": "near", "TAO": "bittensor",
    "MORPHO": "morpho", "ASTER": "aster-2", "PUMP": "pump-fun", "ZRO": "layerzero",
    "AVAX": "avalanche-2", "POL": "polygon-ecosystem-token", "UNI": "uniswap",
    "CRV": "curve-dao-token", "MNT": "mantle", "BERA": "berachain-bera",
    "GOLD": "tether-gold",
}
STABLE_IDS = {"USDT": "tether", "USDC": "usd-coin"}

CG = "https://api.coingecko.com/api/v3"


@dataclass
class Trend:
    asset: str
    price: float
    sma50: float | None
    sma200: float | None
    ret_7d: float | None
    ret_30d: float | None
    vol_30d: float | None      # годовая, доля
    dd_from_high: float | None  # от максимума окна, доля (отрицательная)
    rsi14: float | None

    @property
    def label(self) -> str:
        """UP / DOWN / MIXED по положению цены относительно средних."""
        if self.sma50 is None:
            return "N/A"
        if self.sma200 is None:
            return "UP" if self.price > self.sma50 else "DOWN"
        if self.price > self.sma50 > self.sma200:
            return "UP"
        if self.price < self.sma50 < self.sma200:
            return "DOWN"
        return "MIXED"


def compute_trend(asset: str, closes: list) -> Trend | None:
    closes = [c for c in closes if c and c > 0]
    if len(closes) < 30:
        return None
    p = closes[-1]

    def sma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    def ret(n):
        return p / closes[-1 - n] - 1 if len(closes) > n else None

    lr = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 30, len(closes))]
    mu = sum(lr) / len(lr)
    vol = math.sqrt(sum((x - mu) ** 2 for x in lr) / (len(lr) - 1)) * math.sqrt(365)

    return Trend(asset, p, sma(50), sma(200), ret(7), ret(30), vol,
                 p / max(closes) - 1, rsi(closes, 14))


def rsi(closes: list, n: int = 14) -> float | None:
    """RSI по Уайлдеру."""
    if len(closes) <= n:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


async def _get_json(session, url, retries=3):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=30) as r:
                if r.status == 429:
                    await asyncio.sleep(20 * (attempt + 1))
                    continue
                r.raise_for_status()
                return await r.json()
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            logger.warning(f"{url}: {e}, retry")
            await asyncio.sleep(5)
    raise RuntimeError(f"{url}: rate limited")


async def fetch_trends(session, assets: list, pause: float = 4.0) -> tuple[dict, list]:
    """-> ({asset: Trend}, [ошибки]). Неизвестный CoinGecko-id — не ошибка, а пропуск."""
    out, errors = {}, []
    for a in assets:
        cid = COINGECKO_IDS.get(a)
        if not cid:
            continue
        try:
            d = await _get_json(session, f"{CG}/coins/{cid}/market_chart?vs_currency=usd&days=220&interval=daily")
            closes = [x[1] for x in d.get("prices", [])]
            t = compute_trend(a, closes)
            if t:
                out[a] = t
        except Exception as e:  # noqa: BLE001
            errors.append(f"график {a}: {e}")
        await asyncio.sleep(pause)
    return out, errors


async def fetch_stable_pegs(session) -> dict:
    ids = ",".join(STABLE_IDS.values())
    d = await _get_json(session, f"{CG}/simple/price?ids={ids}&vs_currencies=usd")
    return {sym: d.get(cid, {}).get("usd") for sym, cid in STABLE_IDS.items()}


async def fetch_fear_greed(session) -> int | None:
    try:
        d = await _get_json(session, "https://api.alternative.me/fng/?limit=1")
        return int(d["data"][0]["value"])
    except Exception:  # noqa: BLE001
        return None
