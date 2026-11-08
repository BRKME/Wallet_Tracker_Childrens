"""Комментарий Claude к отчёту (опционально, нужен секрет ANTHROPIC_API_KEY).

Claude получает ТОЛЬКО уже посчитанные факты (JSON) и может поискать в
интернете свежие новости о взломах/проблемах протоколов и эмитентов из
портфеля. Цифры он не пересчитывает и не придумывает — это прописано в
промпте. Без ключа или при ошибке отчёт уходит без комментария.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

MODEL = os.getenv("RISK_LLM_MODEL", "claude-opus-5-5")

SYSTEM = """Ты — риск-аналитик семейного крипто-портфеля (кошельки детей и жены, горизонт ~10 лет).
Тебе дают JSON с уже посчитанными фактами: экспозиция, займы и health factor, флаги рисков,
режим рынка от модели OracAI, тренды монет, предложения ребалансировки.

Задача:
1. Через веб-поиск проверь новости за последние 7 дней о взломах, эксплойтах, депегах,
   проблемах с выводом у протоколов и эмитентов из списка `watchlist`. Ищи коротко и по делу.
2. Напиши комментарий по-русски, 5–8 предложений, простым текстом без markdown:
   - главный риск недели и почему;
   - есть ли новости, меняющие картину (с датой и источником), или «значимых новостей нет»;
   - согласен ли ты с предложениями ребалансировки и что сделать в первую очередь.

Правила: не придумывай цифр — используй только числа из JSON или из найденных новостей.
Не давай гарантий доходности. Если OracAI не уверен (confidence < 40%), не строй выводов на его режиме."""


def build_facts(family, exposures, assessment, orac, trends, per_wallet, hacks) -> dict:
    watch = set()
    for e in exposures:
        for p in e.by_protocol:
            if not p.startswith("Кошел"):
                watch.add(p.split(" (")[0])
        watch.update(e.by_counterparty)
    return {
        "family": {"net_usd": round(family.net_usd), "debt_usd": round(family.debt_usd),
                   "class_shares": {k: round(family.share(v), 3) for k, v in family.by_class.items()}},
        "wallets": [{
            "name": e.name, "net_usd": round(e.net_usd), "leverage": round(e.leverage, 2),
            "top_assets": {k: round(e.share(v), 3) for k, v in list(e.by_asset.items())[:6]},
            "protocols": {k: round(e.share(v), 3) for k, v in e.by_protocol.items()},
            "loans": [{"protocol": f"{x.protocol} ({x.chain})", "health": round(x.health, 2),
                       "debt_usd": round(x.debt_usd),
                       "drop_to_liquidation": None if x.drop_to_liquidation is None else round(x.drop_to_liquidation, 2)}
                      for x in e.loans],
        } for e in exposures],
        "oracai": {"regime": orac.regime, "confidence": orac.confidence, "risk_state": orac.risk_state,
                   "phase": orac.phase, "ladder": orac.ladder_action, "stale": orac.is_stale(),
                   "error": orac.error},
        "trends": {a: {"label": t.label, "ret_30d": None if t.ret_30d is None else round(t.ret_30d, 3),
                       "rsi": None if t.rsi14 is None else round(t.rsi14)} for a, t in trends.items()},
        "flags": [{"category": f.category, "level": f.level, "wallet": f.wallet, "text": f.text}
                  for f in assessment.flags if f.level > 1],
        "rebalance": [{"wallet": s.wallet, "action": s.action, "what": s.what, "usd": round(s.usd),
                       "reason": s.reason} for _, sugg, _, _ in per_wallet for s in sugg],
        "known_hacks_30d": [{"name": h.name, "date": h.date, "matched": h.matched} for h in hacks if h.direct],
        "watchlist": sorted(watch),
    }


async def commentary(facts: dict) -> str | None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic не установлен — комментарий пропущен")
        return None

    client = anthropic.AsyncAnthropic()
    messages = [{"role": "user", "content": "Факты недели:\n" + json.dumps(facts, ensure_ascii=False)}]
    params = dict(
        model=MODEL, max_tokens=16000, system=SYSTEM,
        output_config={"effort": "medium"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 6}],
    )
    try:
        resp = None
        for _ in range(4):  # pause_turn: серверный поиск может прерваться, продолжаем
            resp = await client.messages.create(messages=messages, **params)
            if resp.stop_reason != "pause_turn":
                break
            messages.append({"role": "assistant", "content": resp.content})
        if resp is None or resp.stop_reason == "refusal":
            logger.warning("Claude отказался комментировать")
            return None
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or None
    except anthropic.APIStatusError as e:
        logger.warning(f"Claude API {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        logger.warning(f"Claude API недоступен: {e}")
    return None
