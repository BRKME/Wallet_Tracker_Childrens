import pytest

from risk.assets import asset_class, canonical_symbol
from risk.positions import parse_debank


@pytest.mark.parametrize("key", ["marta", "arkasha", "mama"])
def test_net_matches_debank_header(snapshots, key):
    """Сумма с вычетом долга должна сходиться с балансом DeBank (±2%).
    Старый скрейпер давал +50% — эта проверка его бы поймала."""
    s = snapshots[key]
    assert s.net_usd == pytest.approx(s.reported_total, rel=0.02)


def test_debt_is_negative_and_counted(snapshots):
    m = snapshots["marta"]
    assert m.debt_usd > 2500  # Aave arb ~1942 + bsc ~784
    assert m.gross_assets - m.debt_usd == pytest.approx(m.net_usd)


def test_health_factor_only_for_debt_positions(snapshots):
    a = snapshots["arkasha"]
    hfs = {p.protocol_id: p.health_rate for p in a.positions if p.health_rate}
    assert set(hfs) == {"arb_aave3", "bsc_aave3"}  # zerolend с 1e59 и без долга — не HF
    assert all(1.0 < v < 3 for v in hfs.values())


def test_dust_positions_dropped(snapshots):
    ids = {p.protocol_id for p in snapshots["arkasha"].positions}
    assert "arb_camelot" not in ids and "etherfi" not in ids


@pytest.mark.parametrize("sym,chain,canon,cls", [
    ("WBTC", "arb", "BTC", "BTC"), ("BTCB", "bsc", "BTC", "BTC"),
    ("cbBTC", "base", "BTC", "BTC"), ("USD₮0", "arb", "USD", "STABLE"),
    ("XAUt", "bsc", "GOLD", "GOLD"), ("MSTRc", "base", "MSTR", "STOCK"),
    ("MSTR", "hood", "MSTR", "STOCK"), ("SPCXc", "base", "SPCX", "STOCK"),
    ("ZEC", "bsc", "ZEC", "ALT"), ("WBNB", "bsc", "BNB", "ALT"),
    ("toby", "base", "TOBY", "ALT"), ("USUAL", "eth", "USUAL", "ALT"),
])
def test_classification(sym, chain, canon, cls):
    assert canonical_symbol(sym, chain) == canon
    assert asset_class(canon) == cls


def test_empty_payload_is_empty_not_crash():
    s = parse_debank("x", [], [], None)
    assert s.net_usd == 0 and s.positions == []
