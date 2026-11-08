"""Классификация активов: канонический тикер, класс актива, контрагент.

Обёртки сводятся к базовому активу (WBTC -> BTC), но контрагентский риск
обёртки не теряется: он хранится отдельно (WRAPPER_COUNTERPARTY) и
попадает в оценку рисков как «BTC через кастодиана X».
"""

ASSET_ALIASES = {
    "WBTC": "BTC", "BTCB": "BTC", "TBTC": "BTC", "CBBTC": "BTC",
    "BTC.B": "BTC", "SOLVBTC": "BTC", "LBTC": "BTC", "UBTC": "BTC",
    "WETH": "ETH", "STETH": "ETH", "WSTETH": "ETH", "WEETH": "ETH",
    "RETH": "ETH", "CBETH": "ETH",
    "WBNB": "BNB", "WHYPE": "HYPE", "KHYPE": "HYPE", "STHYPE": "HYPE",
    "WNEAR": "NEAR", "WMATIC": "POL", "MATIC": "POL",
    "WAVAX": "AVAX", "WSOL": "SOL", "WBERA": "BERA",
    "PAXG": "GOLD", "XAUT": "GOLD",
}

# Не-стейблы, в тикере которых встречается "USD"
_NOT_STABLE = {"USUAL"}
_STABLE_EXTRA = {"DAI", "SDAI", "BUSD", "TUSD", "FRAX", "MIM", "LUSD", "GHO",
                 "CRVUSD", "EURC", "USDS"}

# Кто стоит за обёрткой: если он ломается — актив превращается в ноль,
# даже если сам BTC/ETH в порядке.
WRAPPER_COUNTERPARTY = {
    "WBTC": "BitGo/BiT Global (кастодиальный)",
    "BTCB": "Binance (кастодиальный, BSC)",
    "CBBTC": "Coinbase (кастодиальный)",
    "TBTC": "Threshold (децентрализованный мост)",
    "SOLVBTC": "Solv (кастодиальный)",
    "USD₮0": "Tether + мост LayerZero OFT",
    "USDT0": "Tether + мост LayerZero OFT",
    "USDT": "Tether (эмитент)",
    "USDC": "Circle (эмитент)",
    "XAUT": "Tether Gold (кастодиальный)",
    "PAXG": "Paxos (кастодиальный)",
    "WEETH": "ether.fi (рестейкинг)",
}

# Классы активов
BTC, ETH, STABLE, GOLD, STOCK, ALT = "BTC", "ETH", "STABLE", "GOLD", "STOCK", "ALT"

CLASS_LABELS = {
    BTC: "BTC", ETH: "ETH", STABLE: "Стейблы", GOLD: "Золото",
    STOCK: "Токен. акции", ALT: "Альткоины",
}

# Токенизированные акции: Robinhood chain ("hood") и xStocks/Backed с суффиксом "c"/"x"
_STOCK_TICKERS = {"MSTR", "TSLA", "MSFT", "SPCX", "SNDK", "NVDA", "AAPL", "GOOGL",
                  "AMZN", "META", "COIN", "HOOD", "SPY", "QQQ", "CRCL"}


def _clean(symbol: str) -> str:
    sym = (symbol or "???").strip()
    return sym.replace("₮", "T").replace("$", "")


def canonical_symbol(symbol: str, chain: str = "") -> str:
    """WBTC -> BTC, USD₮0 -> USD, MSTRc -> MSTR, XAUt -> GOLD."""
    raw = _clean(symbol)
    sym = raw.upper()
    stock = _stock_ticker(raw, chain)
    if stock:
        return stock
    sym = ASSET_ALIASES.get(sym, sym)
    if sym in _NOT_STABLE:
        return sym
    if "USD" in sym or sym in _STABLE_EXTRA:
        return "USD"
    return sym


def _stock_ticker(raw: str, chain: str) -> str:
    up = raw.upper()
    if chain == "hood" and up in _STOCK_TICKERS:
        return up
    for suffix in ("c", "x", "on", "rh"):
        if raw.endswith(suffix) and raw[: -len(suffix)].upper() in _STOCK_TICKERS:
            return raw[: -len(suffix)].upper()
    return ""


def asset_class(canonical: str) -> str:
    if canonical == "BTC":
        return BTC
    if canonical == "ETH":
        return ETH
    if canonical == "USD":
        return STABLE
    if canonical == "GOLD":
        return GOLD
    if canonical in _STOCK_TICKERS:
        return STOCK
    return ALT


def counterparty(symbol: str) -> str:
    """Контрагент обёртки или '' для нативного актива."""
    raw = _clean(symbol)
    return WRAPPER_COUNTERPARTY.get(raw.upper()) or WRAPPER_COUNTERPARTY.get(raw, "")
