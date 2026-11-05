"""Политика риск-отчёта: целевые доли и пороги.

ЭТО ЕДИНСТВЕННОЕ МЕСТО, КОТОРОЕ НУЖНО ПРАВИТЬ, чтобы поменять поведение.
Цели — по классам активов от чистой стоимости кошелька (залог − долг).
"""

# Базовые целевые доли (сумма = 1.0). Горизонт детских кошельков ~10 лет:
# ядро BTC+ETH, подушка стейблов, немного золота, альты — ограниченно.
BASE_TARGETS = {
    "BTC": 0.40,
    "ETH": 0.20,
    "ALT": 0.20,
    "STABLE": 0.15,
    "GOLD": 0.05,
    "STOCK": 0.00,
}

# Переопределения по кошелькам (если у кого-то другая цель)
WALLET_TARGETS = {
    # "Мама": {"BTC": 0.40, "ETH": 0.15, "ALT": 0.25, "STABLE": 0.20, "GOLD": 0.0, "STOCK": 0.0},
}

# Наклон по режиму OracAI. Применяется ТОЛЬКО если уверенность OracAI
# выше порога — уважаем его собственный confidence gate.
ORACAI_MIN_CONFIDENCE = 0.40
TILT_RISK_OFF_STABLE = +0.10   # медвежий режим / risk-off: +10 п.п. в стейблы
TILT_RISK_ON_STABLE = -0.05    # risk-on: −5 п.п. из стейблов в BTC

# Ребалансировка
DRIFT_BAND = 0.05              # не трогать класс, пока отклонение < 5 п.п.
MIN_TRADE_USD = 50             # меньше — комиссии съедят смысл
ALTS_TO_BUY = 2                # докупка альтов — в N сильнейших из белого списка
STOCK_MAX = 0.05               # токенизированные акции — не больше 5%

# Плечо и ликвидации
HF_RED, HF_YELLOW = 1.30, 1.60
HF_TARGET = 2.00               # к чему приводить при делевередже
LIQ_DROP_RED, LIQ_DROP_YELLOW = 0.30, 0.45
LEVERAGE_YELLOW = 1.50         # залог / чистая стоимость

# Концентрация
BTC_MAX_SHARE = 0.75           # BTC — ядро, порог выше
# Альткоины ведут себя как один класс — лимит на класс, не на монету
ALT_CLASS_YELLOW, ALT_CLASS_RED = 0.30, 0.40
PROTOCOL_MAX_SHARE = 0.50
COUNTERPARTY_YELLOW, COUNTERPARTY_RED = 0.30, 0.50
NON_WHITELIST_YELLOW = 0.05

# Протоколы: отток TVL за 7 дней, %
TVL_DROP_YELLOW, TVL_DROP_RED = -20.0, -40.0

# Тренды
TREND_WEIGHT_MIN = 0.10        # тренд важен, если доля актива ≥ 10%
CRASH_30D = -0.25

# Стейблы
DEPEG_YELLOW, DEPEG_RED = 0.005, 0.02


def targets_for(wallet: str) -> dict:
    return dict(WALLET_TARGETS.get(wallet, BASE_TARGETS))
