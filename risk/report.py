"""Telegram-отчёт (HTML). Разбит на секции, каждая укладывается в лимит 4096."""

from html import escape

from .assess import CATEGORIES, GREEN, ICON
from .assets import CLASS_LABELS

TG_LIMIT = 4000
ARROW = {"UP": "▲", "DOWN": "▼", "MIXED": "◆", "N/A": "·"}
ACTION = {"REPAY": "💳 Погасить", "SELL": "➖ Продать", "BUY": "➕ Докупить"}


def money(x: float) -> str:
    a = abs(x)
    s = f"${a / 1000:,.1f}k" if a >= 10_000 else f"${a:,.0f}"
    return ("−" if x < 0 else "") + s


def pct(x, signed=False):
    if x is None:
        return "н/д"
    v = round(x * 100) or 0  # без «-0%»
    return f"{v:+d}%" if signed else f"{v:d}%"


def header(date: str, family, prev: dict | None) -> str:
    s = f"🛡 <b>Риск-отчёт портфеля</b> · {date}\n\n"
    s += f"Чистыми: <b>{money(family.net_usd)}</b>"
    if prev and prev.get("family_net"):
        d = family.net_usd - prev["family_net"]
        s += f" ({'+' if d >= 0 else '−'}{money(abs(d))} за неделю)"
    s += "\n"
    if family.debt_usd > 0:
        s += f"Долг: {money(family.debt_usd)} · плечо {family.leverage:.2f}×\n"
    return s


def market_section(orac, fear_greed, pegs) -> str:
    s = "🌐 <b>Рынок (OracAI)</b>\n"
    if orac.error:
        return s + f"⚠️ {escape(orac.error)}\n"
    probs = " · ".join(f"{k} {v:.0%}" for k, v in sorted(orac.probabilities.items(), key=lambda kv: -kv[1]))
    s += f"Режим: <b>{orac.regime}</b> (уверенность {pct(orac.confidence)})\n"
    if probs:
        s += f"└ {probs}\n"
    s += f"Риск: {orac.risk_state}"
    if orac.exposure_cap is not None:
        s += f" · лимит экспозиции {pct(orac.exposure_cap)}"
    s += "\n"
    bits = []
    if orac.phase:
        bits.append(f"фаза {orac.phase}")
    if orac.mvrv:
        bits.append(f"MVRV {orac.mvrv:.2f}")
    if orac.btc_dd_from_ath is not None:
        bits.append(f"BTC от ATH {orac.btc_dd_from_ath:.0f}%")
    if bits:
        s += "Цикл: " + " · ".join(bits) + "\n"
    if orac.ladder_zone:
        s += f"Лестница: {orac.ladder_zone} → <b>{orac.ladder_action}</b>"
        if orac.ladder_rationale:
            s += f" — <i>{escape(orac.ladder_rationale)}</i>"
        s += "\n"
    if orac.is_stale():
        s += f"⚠️ данные OracAI старше {orac.age_hours() or 0:.0f} ч\n"
    extra = []
    if fear_greed is not None:
        extra.append(f"Fear&Greed {fear_greed}")
    for sym, p in (pegs or {}).items():
        if p:
            extra.append(f"{sym} ${p:.3f}")
    if extra:
        s += " · ".join(extra) + "\n"
    return s


def risk_section(a, max_per_cat: int = 4) -> str:
    s = "🚦 <b>Риски</b>\n"
    for cat in CATEGORIES:
        lvl = a.level(cat)
        flags = [f for f in a.of(cat) if f.level > GREEN] or a.of(cat)[:1]
        s += f"\n{ICON[lvl]} <b>{cat}</b>\n"
        if not flags:
            s += "└ замечаний нет\n"
            continue
        shown = flags[:max_per_cat]
        for i, f in enumerate(shown):
            br = "└" if i == len(shown) - 1 and len(flags) <= max_per_cat else "├"
            who = f"{escape(f.wallet)}: " if f.wallet else ""
            mark = "" if f.level == lvl or f.level == GREEN else ICON[f.level] + " "
            s += f"{br} {mark}{who}{escape(f.text)}\n"
        if len(flags) > max_per_cat:
            s += f"└ …и ещё {len(flags) - max_per_cat}\n"
    return s


def trend_section(trends: dict, family) -> str:
    if not trends:
        return "📈 <b>Тренды</b>\n└ графики не получены\n"
    s = "📈 <b>Тренды монет портфеля</b> (дневки)\n"
    items = sorted(trends.values(), key=lambda t: -abs(family.by_asset.get(t.asset, 0)))
    for t in items:
        w = family.share(family.by_asset.get(t.asset, 0))
        rsi = f" · RSI {t.rsi14:.0f}" if t.rsi14 is not None else ""
        s += (f"{ARROW[t.label]} <b>{t.asset}</b> {pct(w)} портф. · 30д {pct(t.ret_30d, True)}"
              f" · от макс. {pct(t.dd_from_high, True)}{rsi}\n")
    s += "<i>▲ цена > SMA50 > SMA200 · ▼ наоборот · ◆ смешанный</i>\n"
    return s


def wallet_section(exposures: list, prev: dict | None) -> str:
    s = "👛 <b>Кошельки</b>\n"
    prev_w = (prev or {}).get("wallets", {})
    for e in exposures:
        s += f"\n<b>{escape(e.name)}</b> — {money(e.net_usd)}"
        pw = prev_w.get(e.name, {}).get("net")
        if pw:
            s += f" ({pct(e.net_usd / pw - 1, True)} н/н)"
        s += "\n"
        cls = [f"{CLASS_LABELS[k]} {pct(e.share(v))}" for k, v in e.by_class.items()
               if abs(e.share(v)) >= 0.01]
        s += "├ " + " · ".join(cls) + "\n"
        top = [f"{escape(p.split(' (')[0] if p.startswith('Кошел') else p)} {pct(e.share(v))}"
               for p, v in list(e.by_protocol.items())[:4]]
        s += "├ Где: " + " · ".join(top) + "\n"
        if e.loans:
            for loan in e.loans:
                d = loan.drop_to_liquidation
                liq = f"ликв. при −{pct(d)}" if d is not None and d < 0.95 else "устойчив к падению"
                ph = prev_w.get(e.name, {}).get("hf", {}).get(f"{loan.protocol} ({loan.chain})")
                dh = f" (было {ph:.2f})" if ph and abs(ph - loan.health) >= 0.05 else ""
                s += f"├ 💳 {escape(loan.protocol)} {loan.chain}: HF {loan.health:.2f}{dh}, {liq}\n"
        s += f"└ Плечо {e.leverage:.2f}×\n"
    return s


def rebalance_section(per_wallet: list) -> str:
    """per_wallet: [(exposure, suggestions, targets, tilt_note)]"""
    s = "⚖️ <b>Ребалансировка</b> (предложения, не приказы)\n"
    if per_wallet:
        s += f"<i>{escape(per_wallet[0][3])}</i>\n"
    for e, sugg, targets, _ in per_wallet:
        s += f"\n<b>{escape(e.name)}</b>"
        tg = " · ".join(f"{CLASS_LABELS[k]} {pct(v)}" for k, v in targets.items() if v > 0)
        s += f" <i>(цель: {tg})</i>\n"
        if not sugg:
            s += "└ ✅ в пределах полос — ничего не делать\n"
            continue
        for i, x in enumerate(sugg):
            br = "└" if i == len(sugg) - 1 else "├"
            s += f"{br} {ACTION[x.action]} <b>{escape(x.what)}</b> ~{money(x.usd)} — {escape(x.reason)}\n"
    return s


def footer(errors: list, llm_text: str | None) -> str:
    s = ""
    if llm_text:
        s += f"🤖 <b>Комментарий</b>\n{escape(llm_text)}\n\n"
    if errors:
        s += "⚠️ <b>Проблемы со сбором данных</b>\n" + "".join(f"• {escape(e)}\n" for e in errors[:8])
    return s


def split_messages(sections: list) -> list:
    """Склеить секции в сообщения ≤ TG_LIMIT, не разрывая секцию."""
    msgs, cur = [], ""
    for sec in (x for x in sections if x and x.strip()):
        chunk = sec.rstrip() + "\n\n"
        if len(chunk) > TG_LIMIT:
            chunk = chunk[: TG_LIMIT - 20] + "\n…(обрезано)\n\n"
        if len(cur) + len(chunk) > TG_LIMIT and cur:
            msgs.append(cur.rstrip())
            cur = ""
        cur += chunk
    if cur.strip():
        msgs.append(cur.rstrip())
    return msgs
