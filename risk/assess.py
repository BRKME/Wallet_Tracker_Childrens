"""Оценка рисков: список флагов по категориям со светофором.

Без сводного «скора 0–100»: число без единиц измерения создаёт иллюзию
точности. Категория горит цветом своего худшего флага.
"""

from dataclasses import dataclass, field

from . import policy as P
from .assets import ALT, BTC, STABLE, STOCK, asset_class

RED, YELLOW, GREEN = 3, 2, 1
ICON = {RED: "🔴", YELLOW: "🟡", GREEN: "🟢"}

SYSTEMIC, LEVERAGE, PROTOCOL, LP, COUNTERPARTY, DIVERS, TREND = (
    "Системный риск", "Плечо и ликвидации", "Протоколы и взломы", "LP и доходность",
    "Контрагенты обёрток", "Диверсификация", "Тренды")
CATEGORIES = [SYSTEMIC, LEVERAGE, PROTOCOL, LP, COUNTERPARTY, DIVERS, TREND]


@dataclass
class Flag:
    category: str
    level: int
    text: str
    wallet: str = ""


@dataclass
class Assessment:
    flags: list = field(default_factory=list)

    def add(self, category, level, text, wallet=""):
        self.flags.append(Flag(category, level, text, wallet))

    def level(self, category) -> int:
        return max((f.level for f in self.flags if f.category == category), default=GREEN)

    def of(self, category) -> list:
        return sorted((f for f in self.flags if f.category == category), key=lambda f: -f.level)


def pct(x, signed=False):
    v = round(x * 100) or 0  # без «-0%»
    return f"{v:+d}%" if signed else f"{v:d}%"


def assess(exposures: list, family, orac, trends: dict, pegs: dict,
           fear_greed, hacks: list, tvl: dict, whitelist: set, lps: list | None = None) -> Assessment:
    a = Assessment()
    _systemic(a, orac, pegs, fear_greed)
    for e in exposures:
        _leverage(a, e)
        _concentration(a, e, whitelist)
        _counterparty(a, e)
        _protocol_concentration(a, e)
    _protocols(a, hacks, tvl)
    _trends(a, family, trends)
    _lp(a, lps or [])
    return a


def _lp(a, lps):
    for x in lps:
        where = f"{x.protocol} {x.name} ({x.chain}, ${x.value:,.0f})"
        v = x.vault
        if v and v.status != "active":
            a.add(LP, RED, f"{where}: хранилище {v.status} — доход 0%, вывести", x.wallet)
            continue
        for n in x.notes:
            a.add(LP, YELLOW, f"{where}: {n}", x.wallet)
        if v:
            bad = [r for r in v.risks if r in ("нет аудита", "контракт не верифицирован")]
            if bad:
                a.add(LP, YELLOW, f"{where}: {', '.join(bad)}", x.wallet)
            if v.tvl is not None and v.tvl < P.LP_SMALL_TVL:
                a.add(LP, YELLOW, f"{where}: TVL хранилища всего ${v.tvl:,.0f}", x.wallet)
            share = x.tvl_share
            if share is not None and share > P.LP_MAX_TVL_SHARE:
                a.add(LP, YELLOW, f"{where}: наша доля {pct(share)} хранилища", x.wallet)
        if x.apr is not None and x.apr > P.LP_APR_ANOMALY:
            a.add(LP, YELLOW, f"{where}: APR {pct(x.apr)} — аномально высокий, временный", x.wallet)
        if x.apr is not None and x.il is not None and x.il > x.apr:
            a.add(LP, YELLOW, f"{where}: IL ≥{pct(x.il)}/год больше APR {pct(x.apr)}", x.wallet)


def _systemic(a, orac, pegs, fg):
    if orac.error:
        a.add(SYSTEMIC, YELLOW, orac.error)
    else:
        conf = orac.confidence or 0
        if orac.is_stale():
            a.add(SYSTEMIC, YELLOW, f"Данные OracAI устарели ({orac.age_hours() or 0:.0f} ч)")
        if orac.tail_risk or orac.risk_state in ("CRISIS", "TAIL"):
            a.add(SYSTEMIC, RED, f"OracAI: хвостовой риск ({orac.risk_state})")
        elif orac.risk_state == "RISK_OFF":
            a.add(SYSTEMIC, RED if conf >= P.ORACAI_MIN_CONFIDENCE else YELLOW,
                  f"OracAI: RISK_OFF, уверенность {pct(conf)}")
        if orac.regime == "BEAR":
            lvl = YELLOW if conf >= P.ORACAI_MIN_CONFIDENCE else GREEN
            a.add(SYSTEMIC, lvl, f"OracAI: режим BEAR, уверенность {pct(conf)}"
                  + ("" if lvl == YELLOW else " — ниже порога, сигнала нет"))
    for sym, price in (pegs or {}).items():
        if price is None:
            continue
        dev = abs(price - 1)
        if dev >= P.DEPEG_RED:
            a.add(SYSTEMIC, RED, f"{sym} отвязался от доллара: ${price:.4f}")
        elif dev >= P.DEPEG_YELLOW:
            a.add(SYSTEMIC, YELLOW, f"{sym} колеблется у привязки: ${price:.4f}")
    if fg is not None and (fg <= 15 or fg >= 85):
        a.add(SYSTEMIC, YELLOW, f"Fear&Greed {fg} — экстремум настроений")


def _leverage(a, e):
    for loan in e.loans:
        where = f"{loan.protocol} ({loan.chain})"
        d = loan.drop_to_liquidation
        lvl = GREEN
        if loan.health < P.HF_FLOOR or (d is not None and d < P.LIQ_DROP_RED):
            lvl = RED
        elif loan.health < P.HF_WATCH or (d is not None and d < P.LIQ_DROP_YELLOW):
            lvl = YELLOW
        tail = (f"ликвидация при падении крипты на ~{pct(d)}" if d is not None and d < 0.95
                else "падение цены почти не опасно")
        below = loan.health < P.HF_FLOOR
        hf = f"{loan.health:.3f}" if below else f"{loan.health:.2f}"
        rule = f" (ниже порога {P.HF_FLOOR:.2f} — гасить)" if below else ""
        a.add(LEVERAGE, lvl, f"{where}: HF {hf}{rule}, долг ${loan.debt_usd:,.0f} — {tail}", e.name)
    if e.leverage >= P.LEVERAGE_YELLOW:
        a.add(LEVERAGE, YELLOW, f"Плечо {e.leverage:.2f}× (активы ${e.gross_usd:,.0f} при своих ${e.net_usd:,.0f})", e.name)


def _concentration(a, e, whitelist):
    if e.net_usd <= 0:
        return
    for asset, usd in e.by_asset.items():
        s = e.share(usd)
        if asset == "USD" or s <= 0:
            continue
        cls = asset_class(asset)
        if cls == BTC and s > P.BTC_MAX_SHARE:
            a.add(DIVERS, YELLOW, f"BTC {pct(s)} кошелька", e.name)
    alts = e.share(e.by_class.get(ALT, 0))
    if alts >= P.ALT_CLASS_YELLOW:
        a.add(DIVERS, RED if alts >= P.ALT_CLASS_RED else YELLOW,
              f"Альткоины {pct(alts)} кошелька (лимит {pct(P.ALT_CLASS_YELLOW)})", e.name)
    stable = e.share(e.by_class.get(STABLE, 0))
    if stable < 0.05:
        a.add(DIVERS, YELLOW, f"Стейблов {pct(stable)} — нет подушки на просадку", e.name)
    stock = e.share(e.by_class.get(STOCK, 0))
    if stock > P.STOCK_MAX:
        a.add(DIVERS, YELLOW, f"Токенизированные акции {pct(stock)}", e.name)
    off = {k: v for k, v in e.by_asset.items()
           if asset_class(k) == ALT and k.lower() not in whitelist and v > 0}
    off_share = e.share(sum(off.values()))
    if off_share >= P.NON_WHITELIST_YELLOW:
        names = ", ".join(list(off)[:4])
        a.add(DIVERS, YELLOW, f"Вне белого списка {pct(off_share)}: {names}", e.name)


def _counterparty(a, e):
    if e.gross_usd <= 0:
        return
    for cp, usd in e.by_counterparty.items():
        s = usd / e.gross_usd
        if s >= P.COUNTERPARTY_YELLOW:
            a.add(COUNTERPARTY, RED if s >= P.COUNTERPARTY_RED else YELLOW,
                  f"{cp}: {pct(s)} активов (${usd:,.0f})", e.name)


def _protocol_concentration(a, e):
    if e.net_usd <= 0:
        return
    for proto, usd in e.by_protocol.items():
        if proto.startswith("Кошелёк"):
            continue
        s = e.share(usd)
        if s >= P.PROTOCOL_MAX_SHARE:
            a.add(PROTOCOL, YELLOW, f"{proto}: {pct(s)} кошелька в одном протоколе", e.name)


def _protocols(a, hacks, tvl):
    for h in hacks:
        amt = f"${h.amount / 1e6:,.1f}M" if h.amount else "сумма н/д"
        if h.direct:
            a.add(PROTOCOL, RED, f"Взлом {h.date}: {h.name} ({amt}) — затрагивает: {h.matched}")
        else:
            a.add(PROTOCOL, GREEN, f"Фон: взлом {h.name} {h.date} ({amt}), не наш протокол")
    for proto, t in tvl.items():
        ch = t.change_7d
        if ch is None:
            continue
        if ch <= P.TVL_DROP_RED:
            a.add(PROTOCOL, RED, f"{proto}: отток TVL {ch:+.0f}% за 7д")
        elif ch <= P.TVL_DROP_YELLOW:
            a.add(PROTOCOL, YELLOW, f"{proto}: отток TVL {ch:+.0f}% за 7д")


def _trends(a, family, trends):
    for asset, t in trends.items():
        w = family.share(family.by_asset.get(asset, 0))
        important = w >= P.TREND_WEIGHT_MIN
        if t.ret_30d is not None and t.ret_30d <= P.CRASH_30D and w > 0.02:
            a.add(TREND, YELLOW, f"{asset}: {pct(t.ret_30d, True)} за 30 дней (доля {pct(w)})")
        if important and t.label == "DOWN":
            a.add(TREND, YELLOW, f"{asset}: нисходящий тренд (цена < SMA50 < SMA200), доля {pct(w)}")
        if asset == "BTC" and t.sma200 and t.price < t.sma200:
            a.add(TREND, YELLOW, f"BTC ниже SMA200 ({pct(t.price / t.sma200 - 1, True)})")
