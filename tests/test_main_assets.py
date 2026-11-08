"""Единая политика (08.11): строка 💼 недельного отчёта считается так же, как в 💎Atlas —
чистыми (залог − долг), без дублей, с классификацией risk.assets."""
import pytest

import main
from tests.conftest import load_snapshot


def tokens_of(snap):
    return main.snapshot_tokens(snap)


def test_marta_net_composition():
    line = main.format_assets(tokens_of(load_snapshot("marta")))
    parts = dict(p.rsplit(" ", 1) for p in line.split(" · "))
    assert list(parts)[0] == "BTC"
    # единая политика: та же доля, что в риск-отчёте 💎Atlas (не 51% старого скрейпера)
    from risk.exposure import build_exposure
    e = build_exposure(load_snapshot("marta"))
    assert int(parts["BTC"].rstrip("%")) == round(e.share(e.by_asset["BTC"]) * 100)
    assert parts["ETH"] == "-7%"                          # займ WETH виден
    assert int(parts["USD"].rstrip("%")) <= 15            # не 24%: стейбл-долг вычтен


def test_shares_sum_to_100_of_net():
    toks = tokens_of(load_snapshot("arkasha"))
    net = sum(t["value"] for t in toks)
    snap = load_snapshot("arkasha")
    assert net == pytest.approx(snap.net_usd)
    assert net == pytest.approx(snap.reported_total, rel=0.02)


def test_quality_gate():
    snap = load_snapshot("marta")
    assert main.positions_reliable(snap)
    snap.reported_total = snap.net_usd * 1.5   # расхождение 50% — не доверяем составу
    assert not main.positions_reliable(snap)
    snap.reported_total = None
    assert not main.positions_reliable(snap)


def test_empty():
    assert main.format_assets([]) == ""
