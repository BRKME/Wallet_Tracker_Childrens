"""Предложения по ребалансировке. Только предложения — ничего не исполняется.

Порядок важен:
  1. Плечо: если займ близок к ликвидации — сначала гасим долг.
  2. Точечные лимиты: вне белого списка, акции > 5%.
  3. Классы: отклонение от цели больше полосы — двигаем к цели.
     Альты докупаем в 2 сильнейших из белого списка, продаём самые слабые
     из имеющихся (рейтинг — market.rank_alts).
Цель по стейблам наклоняется по режиму OracAI, но только когда сам OracAI
уверен (confidence ≥ порога) и его данные свежие.
"""

from dataclasses import dataclass

from . import policy as P
from .assets import ALT, BTC, CLASS_LABELS, ETH, STABLE, STOCK, asset_class


@dataclass
class Suggestion:
    wallet: str
    action: str     # EXIT | REPAY | SELL | BUY
    what: str       # актив, класс или протокол
    usd: float
    reason: str


def tilted_targets(wallet: str, orac) -> tuple[dict, str]:
    t = P.targets_for(wallet)
    if orac is None or orac.error or orac.is_stale():
        return t, "наклона нет: данных OracAI нет или они устарели"
    conf = orac.confidence or 0
    if conf < P.ORACAI_MIN_CONFIDENCE:
        return t, f"наклона нет: уверенность OracAI {conf:.0%} < {P.ORACAI_MIN_CONFIDENCE:.0%}"
    if orac.risk_off or orac.regime == "BEAR":
        return _shift_to_stable(t, P.TILT_RISK_OFF_STABLE), \
            f"OracAI {orac.regime}/{orac.risk_state}: +{P.TILT_RISK_OFF_STABLE:.0%} в стейблы"
    if orac.risk_on and orac.regime == "BULL":
        take = min(-P.TILT_RISK_ON_STABLE, max(0.0, t[STABLE] - 0.05))
        t[STABLE] -= take
        t[BTC] += take
        return t, f"OracAI BULL/RISK_ON: −{take:.0%} из стейблов в BTC"
    return t, f"OracAI {orac.regime}/{orac.risk_state}: базовые цели"


def _shift_to_stable(t: dict, amount: float) -> dict:
    t = dict(t)
    need = amount
    take_alt = min(need, t.get(ALT, 0))
    t[ALT] -= take_alt
    need -= take_alt
    core = t[BTC] + t[ETH]
    if need > 0 and core > 0:
        for k in (BTC, ETH):
            t[k] -= need * t[k] / core
    t[STABLE] += amount
    return t


def repay_amount(health: float, collateral: float, debt: float,
                 target: float = P.HF_RESTORE) -> float:
    """Сколько долга погасить СВОИМИ ДЕНЬГАМИ (стейблы с кошелька), чтобы HF стал target.
    lt = HF·D/C;  lt·C/(D−R) = T  =>  R = D − lt·C/T."""
    if debt <= 0 or collateral <= 0 or health >= target:
        return 0.0
    lt = health * debt / collateral
    return max(0.0, min(debt, debt - lt * collateral / target))


def deleverage_amount(health: float, collateral: float, debt: float,
                      target: float = P.HF_RESTORE) -> float:
    """Сколько долга погасить ВЫВОДОМ ЗАЛОГА, чтобы HF стал target.
    lt = HF·D/C;  lt(C−R)/(D−R) = T  =>  R = (T·D − lt·C)/(T − lt)."""
    if debt <= 0 or collateral <= 0 or health >= target:
        return 0.0
    lt = health * debt / collateral
    if target <= lt:
        return debt
    return max(0.0, min(debt, (target * debt - lt * collateral) / (target - lt)))


def lp_exits(wallet: str, lps: list) -> list:
    """Закрытые хранилища (eol/paused) — забрать: доход 0%, стратегия выключена."""
    return [Suggestion(wallet, "EXIT", f"{x.protocol} {x.name} ({x.chain})", x.value,
                       f"хранилище {x.vault.status}, доход 0%")
            for x in lps if x.wallet == wallet and x.vault and x.vault.status != "active"
            and x.value >= P.MIN_TRADE_USD]


def suggest(e, orac, whitelist: set, alt_rank: list | None = None,
            lps: list | None = None) -> tuple[list, dict, str]:
    """-> (предложения, целевые доли, пояснение к наклону)."""
    out = lp_exits(e.name, lps or [])
    targets, tilt_note = tilted_targets(e.name, orac)
    net = e.net_usd
    if net <= 0:
        return out, targets, tilt_note

    # 1. Займы: правило HF ≥ HF_FLOOR
    for loan in e.loans:
        if loan.health >= P.HF_FLOOR:
            continue
        r = repay_amount(loan.health, loan.collateral_usd, loan.debt_usd)
        rc = deleverage_amount(loan.health, loan.collateral_usd, loan.debt_usd)
        if r > 0:
            out.append(Suggestion(e.name, "REPAY", f"{loan.protocol} ({loan.chain})", max(r, 1.0),
                                  f"HF {loan.health:.3f} < {P.HF_FLOOR:.2f} → {P.HF_RESTORE:.2f};"
                                  f" стейблами ~${r:,.0f} или выводом залога ~${rc:,.0f}"))

    # 2. Точечные лимиты (виртуально применяем, чтобы классы считались после них)
    cls_usd = {k: e.by_class.get(k, 0.0) for k in CLASS_LABELS}
    for asset, usd in e.by_asset.items():
        if usd <= 0:
            continue
        cls = asset_class(asset)
        cut, why = 0.0, ""
        if cls == ALT and asset.lower() not in whitelist:
            cut, why = usd, "вне белого списка"
        if cut >= P.MIN_TRADE_USD:
            out.append(Suggestion(e.name, "SELL", asset, cut, f"{why} ({usd / net:.0%})"))
            cls_usd[cls] -= cut
    stock_cut = cls_usd[STOCK] - P.STOCK_MAX * net
    if stock_cut >= P.MIN_TRADE_USD:
        out.append(Suggestion(e.name, "SELL", CLASS_LABELS[STOCK], stock_cut,
                              f"акции > {P.STOCK_MAX:.0%}"))
        cls_usd[STOCK] -= stock_cut
    # Деньги от точечных продаж не приписываем ни к какому классу:
    # они идут на докупки ниже (иначе «продай ZEC → продай стейблы»).

    # 3. Классы к цели
    for cls in CLASS_LABELS:
        if cls == STOCK:
            continue  # акции только ограничиваем сверху, докупать не предлагаем
        cur = cls_usd.get(cls, 0.0) / net
        tgt = targets.get(cls, 0.0)
        drift = cur - tgt
        usd = abs(drift) * net
        if abs(drift) >= P.DRIFT_BAND and usd >= P.MIN_TRADE_USD:
            why = f"{cur:.0%} → цель {tgt:.0%}"
            if cls == ALT and alt_rank:
                out.extend(_alt_trades(e, drift, usd, why, alt_rank, whitelist))
                continue
            if cur < -0.005:  # заметный минус, а не шум округления
                why += f" (минус из-за долга в {CLASS_LABELS[cls]}: можно просто погасить этот долг)"
            elif cls == ALT and drift < 0:
                why += " (только белый список)"
            out.append(Suggestion(e.name, "SELL" if drift > 0 else "BUY", CLASS_LABELS[cls], usd, why))

    order = {"EXIT": 0, "REPAY": 1, "SELL": 2, "BUY": 3}
    out.sort(key=lambda s: (order[s.action], -s.usd))
    return out, targets, tilt_note


def _mom(r) -> str:
    t = r.trend
    return f"30д {t.ret_30d:+.0%}, 90д {t.ret_90d:+.0%}" if t.ret_90d is not None else f"30д {t.ret_30d:+.0%}"


def _alt_trades(e, drift, usd, why, alt_rank, whitelist) -> list:
    """Докупка — поровну в ALTS_TO_BUY сильнейших; продажа — с самых слабых из имеющихся."""
    if drift < 0:
        top = alt_rank[: P.ALTS_TO_BUY]
        part = usd / len(top)
        if part < P.MIN_TRADE_USD:
            top, part = top[:1], usd
        return [Suggestion(e.name, "BUY", r.asset, part,
                           f"альты {why}; сильнейший в белом списке ({_mom(r)})") for r in top]
    order = {r.asset: i for i, r in enumerate(alt_rank)}
    held = [(a, v) for a, v in e.by_asset.items()
            if v > 0 and asset_class(a) == ALT and a.lower() in whitelist]
    # Без графика — считаем самым слабым: продаём первым
    held.sort(key=lambda av: -order.get(av[0], len(order)))
    out, left = [], usd
    for a, v in held:
        if left < P.MIN_TRADE_USD:
            break
        cut = min(v, left)
        if cut < P.MIN_TRADE_USD:
            continue
        r = next((x for x in alt_rank if x.asset == a), None)
        note = (f"№{order[a] + 1} из {len(alt_rank)} в рейтинге ({_mom(r)})" if r
                else "нет графика — продаём первым")
        out.append(Suggestion(e.name, "SELL", a, cut, f"альты {why}; {note}"))
        left -= cut
    return out
