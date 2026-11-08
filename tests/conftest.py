import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"

# Итоги из шапки DeBank на момент снятия фикстур (05.11.2026)
REPORTED = {"marta": 6841.0, "arkasha": 11739.0, "mama": 2736.0}


def load_snapshot(key, name=None):
    from risk.positions import parse_debank
    d = json.loads((FIX / f"debank_{key}.json").read_text())
    return parse_debank(name or key, d["token_cache_balance_list"],
                        d["portfolio_project_list"], REPORTED[key])


@pytest.fixture
def snapshots():
    return {k: load_snapshot(k) for k in REPORTED}
