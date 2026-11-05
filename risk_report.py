#!/usr/bin/env python3
"""Еженедельный риск-отчёт по семейным кошелькам.

    python risk_report.py              # собрать, отправить в Telegram, сохранить state/risk/
    python risk_report.py --dry-run    # напечатать, ничего не отправлять и не сохранять
    python risk_report.py --fixtures   # офлайн-демо на tests/fixtures (без DeBank)

Секреты: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (как у недельного отчёта),
ANTHROPIC_API_KEY — опционально, для комментария Claude.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from config import WALLETS, WHITELIST_FLAT
from risk import report as R
from risk.assess import assess
from risk.exposure import build_exposure, combine
from risk.llm import build_facts, commentary
from risk.market import fetch_fear_greed, fetch_stable_pegs, fetch_trends
from risk.oracai import fetch_oracai
from risk.positions import fetch_btc, merge, parse_debank, scrape_debank
from risk.protocols import fetch_llama, match_hacks, match_tvl
from risk.rebalance import suggest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("risk")

STATE = Path("state/risk")
DATA_MISMATCH = 0.05      # расхождение с итогом DeBank > 5% — данные неполные
TREND_MIN_SHARE = 0.02    # графики только для монет с долей ≥ 2%
FIXTURE_MAP = {"Марта": "marta", "Аркаша": "arkasha", "Мама": "mama"}


async def collect_wallets(session, use_fixtures: bool) -> list:
    snaps = []
    btc_price = None
    for name, addr in WALLETS.items():
        if use_fixtures:
            from tests.conftest import load_snapshot
            snaps.append(load_snapshot(FIXTURE_MAP[name], name))
            continue
        evm = addr if isinstance(addr, str) else addr.get("evm")
        parts = []
        if evm:
            snap = None
            for attempt in range(2):
                try:
                    snap = await scrape_debank(name, evm)
                    if not snap.errors:
                        break
                except Exception as e:  # noqa: BLE001
                    log.warning(f"{name}: DeBank попытка {attempt + 1}: {e}")
                    snap = None
            parts.append(snap or parse_debank(name, [], []))
            if snap is None:
                parts[-1].errors.append(f"{name}: DeBank не открылся")
        if isinstance(addr, dict) and addr.get("btc"):
            if btc_price is None:
                btc_price = await _btc_price(session)
            parts.append(await fetch_btc(session, name, addr["btc"], btc_price))
        snaps.append(merge(name, parts) if len(parts) != 1 else parts[0])
    return snaps


async def _btc_price(session) -> float:
    async with session.get("https://mempool.space/api/v1/prices", timeout=15) as r:
        return float((await r.json())["USD"])


def data_quality(snaps) -> list:
    errs = []
    for s in snaps:
        errs.extend(s.errors)
        if s.reported_total and abs(s.net_usd / s.reported_total - 1) > DATA_MISMATCH:
            errs.append(f"{s.name}: позиции ${s.net_usd:,.0f} ≠ баланс DeBank ${s.reported_total:,.0f}"
                        " — разбивка неполная, выводы по этому кошельку осторожно")
    return errs


def load_prev() -> dict | None:
    p = STATE / "latest.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, ValueError):
        return None


def save_state(date, family, exposures, assessment, per_wallet, orac):
    from risk.assess import CATEGORIES
    STATE.mkdir(parents=True, exist_ok=True)
    rec = {
        "date": date,
        "family_net": round(family.net_usd, 2),
        "family_debt": round(family.debt_usd, 2),
        "oracai": {"regime": orac.regime, "confidence": orac.confidence, "risk_state": orac.risk_state},
        "levels": {c: assessment.level(c) for c in CATEGORIES},
        "wallets": {e.name: {
            "net": round(e.net_usd, 2), "leverage": round(e.leverage, 3),
            "classes": {k: round(e.share(v), 4) for k, v in e.by_class.items()},
            "hf": {f"{x.protocol} ({x.chain})": round(x.health, 3) for x in e.loans},
        } for e in exposures},
        "suggestions": [{"wallet": s.wallet, "action": s.action, "what": s.what, "usd": round(s.usd)}
                        for _, sugg, _, _ in per_wallet for s in sugg],
    }
    (STATE / "latest.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    hist = STATE / "history.jsonl"
    with hist.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def send_telegram(session, text: str) -> bool:
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("TELEGRAM_TOKEN/TELEGRAM_CHAT_ID не заданы")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with session.post(url, json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                       "disable_web_page_preview": True}) as r:
        if r.status != 200:
            log.error(f"Telegram {r.status}: {await r.text()}")
            return False
    return True


async def run(args) -> int:
    date = datetime.now(timezone.utc).strftime("%d.%m.%Y")
    async with aiohttp.ClientSession() as session:
        snaps = await collect_wallets(session, args.fixtures)
        errors = data_quality(snaps)

        exposures = [build_exposure(s) for s in snaps]
        family = build_exposure(combine("Семья", snaps))

        orac = await fetch_oracai(session)
        hacks_raw, llama_protos, llama_err = await fetch_llama(session)
        errors += llama_err

        protocols = sorted({p.protocol for s in snaps for p in s.positions})
        counterparties = sorted({cp for e in exposures for cp in e.by_counterparty})
        hacks = match_hacks(hacks_raw, protocols, counterparties)
        tvl = match_tvl(llama_protos, protocols)

        assets = [a for a, v in family.by_asset.items()
                  if a != "USD" and family.share(abs(v)) >= TREND_MIN_SHARE]
        for core in ("BTC", "ETH"):
            if core not in assets:
                assets.append(core)
        trends, trend_err = await fetch_trends(session, assets)
        errors += trend_err
        try:
            pegs = await fetch_stable_pegs(session)
        except Exception as e:  # noqa: BLE001
            pegs, _ = {}, errors.append(f"курсы стейблов: {e}")
        fg = await fetch_fear_greed(session)

        assessment = assess(exposures, family, orac, trends, pegs, fg, hacks, tvl, WHITELIST_FLAT)
        per_wallet = []
        for e in exposures:
            sugg, targets, note = suggest(e, orac, WHITELIST_FLAT)
            per_wallet.append((e, sugg, targets, note))

        llm_text = None
        if not args.no_llm:
            llm_text = await commentary(build_facts(family, exposures, assessment, orac,
                                                    trends, per_wallet, hacks))

        prev = load_prev()
        sections = [
            R.header(date, family, prev) + "\n" + R.market_section(orac, fg, pegs),
            R.risk_section(assessment),
            R.wallet_section(exposures, prev),
            R.trend_section(trends, family),
            R.rebalance_section(per_wallet),
            R.footer(errors, llm_text),
        ]
        messages = R.split_messages(sections)

        if args.dry_run or args.fixtures:
            for m in messages:
                print(m, "\n" + "─" * 40)
            return 0

        ok = True
        for m in messages:
            ok = await send_telegram(session, m) and ok
        save_state(date, family, exposures, assessment, per_wallet, orac)
        # Шумно падаем, если данных по кошелькам нет вовсе — workflow станет красным
        if all(s.net_usd <= 0 for s in snaps):
            log.error("Ни по одному кошельку нет данных")
            return 2
        return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    sys.exit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
