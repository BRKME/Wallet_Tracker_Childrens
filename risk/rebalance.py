"""Предложения по ребалансировке. Только предложения — ничего не исполняется.

Порядок важен:
  1. Плечо: если займ близок к ликвидации — сначала гасим долг.
  2. Точечные лимиты: один альт > 7%, вне белого списка, акции > 5%.
  3. Классы: отклонение от цели больше полосы — двигаем к цели.
Цель по стейблам наклоняется по режиму OracAI, но только когда сам OracAI
уверен (confidence ≥ порога) и его данные свежие.
"""

from dataclasses import dataclass

from . import policy as P
from .assets import ALT, BTC, CLASS_LABELS, ETH, STABLE, STOCK, asset_class


@dataclass
class Suggestion:
    wallet: str
    action: str     # REPAY | SELL | BUY
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


def deleverage_amount(health: float, collateral: float, debt: float,
                      target: float = P.HF_TARGET) -> float:
    """Сколько долга погасить ВЫВОДОМ ЗАЛОГА, чтобы HF стал target.
    lt = HF·D/C;  lt(C−R)/(D−R) = T  =>  R = (T·D − lt·C)/(T − lt)."""
    if debt <= 0 or collateral <= 0 or health >= target:
        return 0.0
    lt = health * debt / collateral
    if target <= lt:
        return debt
    return max(0.0, min(debt, (target * debt - lt * collateral) / (target - lt)))


def suggest(e, orac, whitelist: set) -> tuple[list, dict, str]:
    """-> (предложения, целевые доли, пояснение к наклону)."""
    out = []
    targets, tilt_note = tilted_targets(e.name, orac)
    net = e.net_usd
    if net <= 0:
        return out, targets, tilt_note

    # 1. Плечо
    for loan in e.loans:
        d = loan.drop_to_liquidation
        risky = loan.health < P.HF_YELLOW or (d is not None and d < P.LIQ_DROP_YELLOW)
        if risky:
            r = deleverage_amount(loan.health, loan.collateral_usd, loan.debt_usd)
            if r >= P.MIN_TRADE_USD:
                out.append(Suggestion(e.name, "REPAY", f"{loan.protocol} ({loan.chain})", r,
                                      f"HF {loan.health:.2f} → {P.HF_TARGET:.1f}"))

    # 2. Точечные лимиты (виртуально применяем, чтобы классы считались после них)
    cls_usd = {k: e.by_class.get(k, 0.0) for k in CLASS_LABELS}
    for asset, usd in e.by_asset.items():
        if usd <= 0:
            continue
        cls = asset_class(asset)
        cut, why = 0.0, ""
        if cls == ALT and asset.lower() not in whitelist:
            cut, why = usd, "вне белого списка"
        elif cls == ALT and usd / net > P.SINGLE_ALT_MAX:
            cut, why = usd - P.SINGLE_ALT_MAX * net, f"один альт > {P.SINGLE_ALT_MAX:.0%}"
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
            if cur < 0:
                why += f" (минус из-за долга в {CLASS_LABELS[cls]}: можно просто погасить этот долг)"
            elif cls == ALT and drift < 0:
                why += f" (только белый список, каждый ≤ {P.SINGLE_ALT_MAX:.0%})"
            out.append(Suggestion(e.name, "SELL" if drift > 0 else "BUY", CLASS_LABELS[cls], usd, why))

    order = {"REPAY": 0, "SELL": 1, "BUY": 2}
    out.sort(key=lambda s: (order[s.action], -s.usd))
    return out, targets, tilt_note
