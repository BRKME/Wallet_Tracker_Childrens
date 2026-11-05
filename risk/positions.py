"""Позиции кошелька: токены на балансе + DeFi-позиции с залогом и долгом.

Источник — два эндпоинта DeBank, которые страница профиля грузит сама:
  token/cache_balance_list  — токены на кошельке (все сети разом)
  portfolio/project_list    — DeFi-позиции (supply / borrow / reward)

Других эндпоинтов НЕ берём: token/balance_list (по сетям) дублирует
cache_balance_list, portfolio/list дублирует project_list. Старый скрейпер
main.py собирает всё подряд и не вычитает долг — отсюда сумма токенов
в history.json больше реального баланса в 1.5 раза.
"""

import logging
from dataclasses import dataclass, field

from .assets import asset_class, canonical_symbol, counterparty

logger = logging.getLogger(__name__)

DUST_USD = 1.0


@dataclass
class Holding:
    symbol: str          # как в кошельке: WBTC, USD₮0
    canonical: str       # BTC, USD
    chain: str
    value: float         # USD, со знаком: долг отрицательный
    source: str          # "wallet" | id протокола (arb_aave3)
    kind: str = "hold"   # hold | supply | borrow | reward

    @property
    def asset_class(self) -> str:
        return asset_class(self.canonical)

    @property
    def counterparty(self) -> str:
        return counterparty(self.symbol)


@dataclass
class Position:
    protocol_id: str     # arb_aave3
    protocol: str        # Aave V3
    chain: str
    kind: str            # Lending, Liquidity Pool, Farming...
    asset_usd: float
    debt_usd: float
    health_rate: float | None = None
    holdings: list = field(default_factory=list)

    @property
    def net_usd(self) -> float:
        return self.asset_usd - self.debt_usd


@dataclass
class WalletSnapshot:
    name: str
    holdings: list = field(default_factory=list)   # все Holding, включая долг
    positions: list = field(default_factory=list)  # Position
    reported_total: float | None = None            # итог из шапки DeBank
    errors: list = field(default_factory=list)

    @property
    def net_usd(self) -> float:
        return sum(h.value for h in self.holdings)

    @property
    def gross_assets(self) -> float:
        return sum(h.value for h in self.holdings if h.value > 0)

    @property
    def debt_usd(self) -> float:
        return -sum(h.value for h in self.holdings if h.value < 0)


def _token_value(t: dict) -> float:
    return float(t.get("amount") or 0) * float(t.get("price") or 0)


def _symbol(t: dict) -> str:
    return (t.get("optimized_symbol") or t.get("symbol") or t.get("display_symbol") or "???").strip()


def parse_debank(name: str, cache_balance_list: list, project_list: list,
                 reported_total: float | None = None) -> WalletSnapshot:
    snap = WalletSnapshot(name=name, reported_total=reported_total)

    for t in cache_balance_list or []:
        v = _token_value(t)
        if v < DUST_USD:
            continue
        sym = _symbol(t)
        chain = t.get("chain") or ""
        snap.holdings.append(Holding(sym, canonical_symbol(sym, chain), chain, v, "wallet"))

    for pr in project_list or []:
        pid, pname, pchain = pr.get("id", "?"), pr.get("name", "?"), pr.get("chain", "")
        for item in pr.get("portfolio_item_list") or []:
            stats = item.get("stats") or {}
            detail = item.get("detail") or {}
            pos = Position(
                protocol_id=pid, protocol=pname, chain=pchain,
                kind=item.get("name") or "",
                asset_usd=float(stats.get("asset_usd_value") or 0),
                debt_usd=float(stats.get("debt_usd_value") or 0),
                health_rate=_health(detail.get("health_rate"), stats),
            )
            for key, sign, kind in (("supply_token_list", 1, "supply"),
                                    ("reward_token_list", 1, "reward"),
                                    ("borrow_token_list", -1, "borrow")):
                for t in detail.get(key) or []:
                    v = _token_value(t)
                    if v < DUST_USD:
                        continue
                    sym = _symbol(t)
                    chain = t.get("chain") or pchain
                    h = Holding(sym, canonical_symbol(sym, chain), chain, sign * v, pid, kind)
                    pos.holdings.append(h)
                    snap.holdings.append(h)
            if pos.asset_usd >= DUST_USD or pos.debt_usd >= DUST_USD:
                snap.positions.append(pos)
    return snap


def _health(hr, stats) -> float | None:
    """DeBank отдаёт 1e59 при нулевом долге — это не HF, а «долга нет»."""
    if hr is None or not (stats.get("debt_usd_value") or 0):
        return None
    try:
        hr = float(hr)
    except (TypeError, ValueError):
        return None
    return hr if hr < 1e6 else None


def merge(name: str, parts: list) -> WalletSnapshot:
    """Склеить несколько адресов одного человека (EVM + BTC) в один снимок."""
    out = WalletSnapshot(name=name)
    total = 0.0
    for p in parts:
        out.holdings.extend(p.holdings)
        out.positions.extend(p.positions)
        out.errors.extend(p.errors)
        total += p.reported_total if p.reported_total is not None else p.net_usd
    out.reported_total = total
    return out


# ============ СБОР ДАННЫХ ============

async def scrape_debank(name: str, address: str, timeout_ms: int = 60000) -> WalletSnapshot:
    """Открыть профиль DeBank и перехватить два нужных эндпоинта."""
    from playwright.async_api import async_playwright

    captured = {"cache": None, "projects": None}

    async def on_response(resp):
        url = resp.url
        if "api.debank.com" not in url:
            return
        ep = url.split("api.debank.com", 1)[1].split("?", 1)[0]
        key = {"/token/cache_balance_list": "cache",
               "/portfolio/project_list": "projects"}.get(ep)
        if not key:
            return
        try:
            data = (await resp.json()).get("data")
        except Exception:
            return
        if isinstance(data, list):
            captured[key] = data

    total = None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on("response", on_response)
        try:
            await page.goto(f"https://debank.com/profile/{address}",
                            wait_until="networkidle", timeout=timeout_ms)
            el = await page.wait_for_selector('[class*="HeaderInfo_totalAssetInner"]', timeout=30000)
            txt = (await el.inner_text()).split("\n")[0]
            total = float(txt.replace("$", "").replace(",", "").strip())
            for _ in range(10):
                if captured["cache"] is not None and captured["projects"] is not None:
                    break
                await page.wait_for_timeout(1000)
        finally:
            await browser.close()

    snap = parse_debank(name, captured["cache"] or [], captured["projects"] or [], total)
    for key, label in (("cache", "токены кошелька"), ("projects", "DeFi-позиции")):
        if captured[key] is None:
            snap.errors.append(f"{name}: DeBank не отдал {label}")
    return snap


async def fetch_btc(session, name: str, address: str, btc_price: float) -> WalletSnapshot:
    snap = WalletSnapshot(name=name)
    try:
        async with session.get(f"https://mempool.space/api/address/{address}", timeout=20) as r:
            d = await r.json()
        cs, ms = d["chain_stats"], d["mempool_stats"]
        sats = cs["funded_txo_sum"] - cs["spent_txo_sum"] + ms["funded_txo_sum"] - ms["spent_txo_sum"]
        v = sats / 1e8 * btc_price
        if v >= DUST_USD:
            snap.holdings.append(Holding("BTC", "BTC", "bitcoin", v, "wallet"))
        snap.reported_total = v
    except Exception as e:  # noqa: BLE001
        snap.errors.append(f"{name}: BTC-адрес не прочитан ({e})")
    return snap
