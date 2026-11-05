"""Режим рынка из OracAI (публичный репозиторий BRKME/OracAI).

Берём то, что OracAI уже посчитал и закоммитил: state/last_output.json
(режим, вероятности, уверенность, risk_state, cap экспозиции, фаза цикла)
и state/cycle_ladder.json (зона MVRV, сигнал лестницы). Сами ничего
не пересчитываем: если OracAI не обновлялся — честно пишем «устарело».
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

RAW = "https://raw.githubusercontent.com/BRKME/OracAI/main/state"
STALE_HOURS = 48


@dataclass
class OracView:
    regime: str = "UNKNOWN"
    probabilities: dict = field(default_factory=dict)
    confidence: float | None = None
    risk_state: str = "UNKNOWN"
    exposure_cap: float | None = None
    phase: str | None = None
    cycle_action: str | None = None
    ladder_zone: str | None = None
    ladder_action: str | None = None
    ladder_rationale: str | None = None
    mvrv: float | None = None
    btc_dd_from_ath: float | None = None
    tail_risk: bool = False
    timestamp: datetime | None = None
    error: str | None = None

    def age_hours(self, now: datetime | None = None) -> float | None:
        if not self.timestamp:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.timestamp).total_seconds() / 3600

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.age_hours(now)
        return age is None or age > STALE_HOURS

    @property
    def risk_off(self) -> bool:
        return self.risk_state in ("RISK_OFF", "CRISIS", "TAIL") or self.tail_risk

    @property
    def risk_on(self) -> bool:
        return self.risk_state == "RISK_ON"


def parse_oracai(last_output: dict, ladder: dict | None) -> OracView:
    lo = last_output or {}
    risk = lo.get("risk") or {}
    conf = lo.get("confidence") or {}
    cyc = lo.get("cycle") or {}
    meta = lo.get("metadata") or {}
    alloc_meta = (lo.get("asset_allocation") or {}).get("meta") or {}

    ts = None
    if meta.get("timestamp"):
        try:
            ts = datetime.fromisoformat(meta["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            ts = None

    v = OracView(
        regime=lo.get("regime") or "UNKNOWN",
        probabilities=lo.get("probabilities") or {},
        confidence=conf.get("quality_adjusted", conf.get("base")),
        risk_state=risk.get("risk_state") or "UNKNOWN",
        exposure_cap=risk.get("risk_exposure_cap"),
        phase=cyc.get("phase"),
        cycle_action=cyc.get("action"),
        mvrv=meta.get("mvrv"),
        btc_dd_from_ath=meta.get("drawdown_from_ath"),
        tail_risk=bool(alloc_meta.get("tail_risk_active")),
        timestamp=ts,
    )
    if ladder:
        sig = ladder.get("signal") or {}
        v.ladder_zone = ladder.get("zone")
        v.ladder_action = sig.get("action")
        v.ladder_rationale = sig.get("rationale") or ladder.get("rationale")
        v.mvrv = v.mvrv or ladder.get("mvrv")
    return v


async def fetch_oracai(session) -> OracView:
    try:
        async with session.get(f"{RAW}/last_output.json", timeout=20) as r:
            r.raise_for_status()
            lo = await r.json(content_type=None)
        ladder = None
        try:
            async with session.get(f"{RAW}/cycle_ladder.json", timeout=20) as r:
                if r.status == 200:
                    ladder = await r.json(content_type=None)
        except Exception:  # noqa: BLE001
            pass
        return parse_oracai(lo, ladder)
    except Exception as e:  # noqa: BLE001
        return OracView(error=f"OracAI недоступен: {e}")
