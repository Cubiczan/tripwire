"""Deterministic policy gate. Pure functions, no I/O."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum

class Decision(str, Enum):
    ALLOW = "allow"; HOLD = "hold"; DENY = "deny"
RANK = {Decision.ALLOW: 0, Decision.HOLD: 1, Decision.DENY: 2}

def tighten(a: Decision, b: Decision) -> Decision:
    return a if RANK[a] >= RANK[b] else b

@dataclass
class PaymentRequest:
    agent_id: str; kind: str; amount: float; currency: str
    payee_id: str; payee_country: str; at: datetime
    fx_rate: float | None = None; fx_mid: float | None = None
    memo: str = ""

@dataclass
class Policy:
    max_single: float = 5000; hold_above: float = 1000
    allowed_payees: set[str] = field(default_factory=set)
    allowed_countries: set[str] = field(default_factory=lambda: {"US", "GB", "AU", "SG", "HK"})
    velocity_window_min: int = 60; velocity_max_count: int = 5; velocity_max_total: float = 10000
    fx_max_deviation_pct: float = 1.0
    window_start: time = time(6, 0); window_end: time = time(22, 0)

@dataclass
class Verdict:
    decision: Decision; reasons: list[str]

def evaluate(req: PaymentRequest, pol: Policy, history: list[PaymentRequest]) -> Verdict:
    d, why = Decision.ALLOW, []
    def flag(x, r): nonlocal d; d = tighten(d, x); why.append(r)
    if req.amount <= 0: flag(Decision.DENY, "non_positive_amount")
    if req.amount > pol.max_single: flag(Decision.DENY, "over_max_single")
    elif req.amount > pol.hold_above: flag(Decision.HOLD, "over_hold_threshold")
    if pol.allowed_payees and req.payee_id not in pol.allowed_payees: flag(Decision.HOLD, "payee_not_allowlisted")
    if req.payee_country not in pol.allowed_countries: flag(Decision.DENY, "country_not_allowed")
    since = req.at - timedelta(minutes=pol.velocity_window_min)
    recent = [h for h in history if h.agent_id == req.agent_id and since <= h.at <= req.at]
    if len(recent) + 1 > pol.velocity_max_count: flag(Decision.HOLD, "velocity_count")
    if sum(h.amount for h in recent) + req.amount > pol.velocity_max_total: flag(Decision.HOLD, "velocity_total")
    if req.fx_rate is not None and req.fx_mid:
        dev = abs(req.fx_rate - req.fx_mid) / req.fx_mid * 100
        if dev > pol.fx_max_deviation_pct: flag(Decision.DENY, f"fx_deviation_{dev:.2f}pct")
    if not (pol.window_start <= req.at.time() <= pol.window_end): flag(Decision.HOLD, "outside_time_window")
    return Verdict(d, why or ["ok"])
