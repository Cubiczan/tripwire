"""Deterministic policy engine. No LLM in this path.

Every rule runs; the final decision is the most severe finding. An optional
verifier verdict can only tighten the result via `tighten`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher
from enum import IntEnum

from tripwire.intent import IntentKind, PaymentIntent


class Decision(IntEnum):
    """Ordered by severity so `max()` combines decisions."""

    ALLOW = 0
    HOLD = 1  # needs a human
    DENY = 2


@dataclass(frozen=True)
class Finding:
    rule: str
    decision: Decision
    reason: str


@dataclass(frozen=True)
class Verdict:
    intent_id: str
    decision: Decision
    findings: tuple[Finding, ...]

    @property
    def reasons(self) -> list[str]:
        return [f"{f.rule}: {f.reason}" for f in self.findings if f.decision > Decision.ALLOW]


@dataclass(frozen=True)
class AgentLimits:
    currency: str
    max_per_payment: Decimal  # above this: DENY
    approval_threshold: Decimal  # above this: HOLD for a human
    daily_budget: Decimal  # cumulative spend above this: DENY
    max_payments_per_window: int = 5
    velocity_window: timedelta = timedelta(minutes=10)


@dataclass(frozen=True)
class PolicyConfig:
    agents: Mapping[str, AgentLimits]
    max_fx_slippage_bps: Decimal = Decimal("50")
    lookalike_threshold: float = 0.85


@dataclass
class PayeeRecord:
    name: str
    bank_details_fingerprint: str


@dataclass
class History:
    """In-memory ledger of what Tripwire already released. Swap for Postgres later."""

    payees: dict[str, PayeeRecord] = field(default_factory=dict)
    seen_keys: set[str] = field(default_factory=set)
    released: list[tuple[str, datetime, Decimal]] = field(default_factory=list)  # agent, ts, amount

    def spent_since(self, agent_id: str, since: datetime) -> Decimal:
        return sum((a for ag, ts, a in self.released if ag == agent_id and ts >= since), Decimal(0))

    def count_since(self, agent_id: str, since: datetime) -> int:
        return sum(1 for ag, ts, _ in self.released if ag == agent_id and ts >= since)

    def record_release(self, intent: PaymentIntent) -> None:
        self.seen_keys.add(intent.idempotency_key)
        self.released.append((intent.agent_id, intent.created_at, intent.amount))
        self.payees.setdefault(
            intent.payee_id, PayeeRecord(intent.payee_name, intent.bank_details_fingerprint)
        )


Rule = Callable[[PaymentIntent, PolicyConfig, History], Finding | None]


def _ok(rule: str) -> Finding:
    return Finding(rule, Decision.ALLOW, "ok")


def duplicate_key(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    if i.idempotency_key in h.seen_keys:
        return Finding("duplicate_key", Decision.DENY, "idempotency key already released")
    return _ok("duplicate_key")


def known_agent(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    if i.agent_id not in cfg.agents:
        return Finding("known_agent", Decision.DENY, f"agent {i.agent_id!r} has no policy")
    return _ok("known_agent")


def payee_allowlist(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    if i.payee_id not in h.payees:
        return Finding("payee_allowlist", Decision.HOLD, "first payment to unknown payee")
    return _ok("payee_allowlist")


def bank_details_change(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    known = h.payees.get(i.payee_id)
    if known and known.bank_details_fingerprint != i.bank_details_fingerprint:
        return Finding(
            "bank_details_change",
            Decision.HOLD,
            "bank details differ from the last verified details for this payee",
        )
    return _ok("bank_details_change")


def lookalike_payee(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    if i.payee_id in h.payees:
        return _ok("lookalike_payee")
    for pid, rec in h.payees.items():
        ratio = SequenceMatcher(None, i.payee_name.casefold(), rec.name.casefold()).ratio()
        if ratio >= cfg.lookalike_threshold:
            return Finding(
                "lookalike_payee",
                Decision.HOLD,
                f"{i.payee_name!r} resembles known payee {rec.name!r} ({pid})",
            )
    return _ok("lookalike_payee")


def amount_limits(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    lim = cfg.agents.get(i.agent_id)
    if lim is None:
        return None  # known_agent already denies
    if i.currency != lim.currency:
        return Finding(
            "amount_limits",
            Decision.HOLD,
            f"limits are in {lim.currency}; cannot evaluate a {i.currency} payment",
        )
    if i.amount > lim.max_per_payment:
        return Finding(
            "amount_limits",
            Decision.DENY,
            f"{i.amount} exceeds per-payment max {lim.max_per_payment}",
        )
    if i.amount > lim.approval_threshold:
        return Finding(
            "amount_limits",
            Decision.HOLD,
            f"{i.amount} exceeds approval threshold {lim.approval_threshold}",
        )
    return _ok("amount_limits")


def daily_budget(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    lim = cfg.agents.get(i.agent_id)
    if lim is None or i.currency != lim.currency:
        return None
    spent = h.spent_since(i.agent_id, i.created_at - timedelta(days=1))
    if spent + i.amount > lim.daily_budget:
        return Finding(
            "daily_budget",
            Decision.DENY,
            f"{spent} already spent + {i.amount} exceeds daily budget {lim.daily_budget}",
        )
    return _ok("daily_budget")


def velocity(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    lim = cfg.agents.get(i.agent_id)
    if lim is None:
        return None
    n = h.count_since(i.agent_id, i.created_at - lim.velocity_window)
    if n >= lim.max_payments_per_window:
        return Finding(
            "velocity",
            Decision.HOLD,
            f"{n} payments in the last {lim.velocity_window}; cap is {lim.max_payments_per_window}",
        )
    return _ok("velocity")


def fx_slippage(i: PaymentIntent, cfg: PolicyConfig, h: History) -> Finding | None:
    if i.kind is not IntentKind.FX_CONVERSION:
        return None
    if i.quoted_rate is None or i.reference_rate is None:
        return Finding("fx_slippage", Decision.HOLD, "FX intent without quoted and reference rates")
    bps = abs(i.quoted_rate - i.reference_rate) / i.reference_rate * Decimal(10_000)
    if bps > cfg.max_fx_slippage_bps:
        return Finding(
            "fx_slippage",
            Decision.DENY,
            f"quoted rate is {bps:.1f} bps from reference; max {cfg.max_fx_slippage_bps}",
        )
    return _ok("fx_slippage")


DEFAULT_RULES: tuple[Rule, ...] = (
    known_agent,
    duplicate_key,
    payee_allowlist,
    bank_details_change,
    lookalike_payee,
    amount_limits,
    daily_budget,
    velocity,
    fx_slippage,
)


class PolicyEngine:
    def __init__(
        self,
        config: PolicyConfig,
        history: History,
        agent_secrets: Mapping[str, bytes],
        rules: tuple[Rule, ...] = DEFAULT_RULES,
    ) -> None:
        self._cfg = config
        self._history = history
        self._secrets = agent_secrets
        self._rules = rules

    def evaluate(self, intent: PaymentIntent) -> Verdict:
        secret = self._secrets.get(intent.agent_id)
        if secret is None or not intent.verify(secret):
            return Verdict(
                intent.intent_id,
                Decision.DENY,
                (Finding("signature", Decision.DENY, "missing or invalid intent signature"),),
            )
        findings = tuple(
            f for rule in self._rules if (f := rule(intent, self._cfg, self._history)) is not None
        )
        decision = max((f.decision for f in findings), default=Decision.ALLOW)
        return Verdict(intent.intent_id, decision, findings)

    def release(self, intent: PaymentIntent) -> None:
        """Call only after the money action was actually dispatched."""
        self._history.record_release(intent)


def tighten(verdict: Verdict, verifier: Decision, reason: str) -> Verdict:
    """Apply an AI verifier's result. It can raise severity, never lower it."""
    if verifier <= verdict.decision:
        return verdict
    extra = Finding("verifier", verifier, reason)
    return Verdict(verdict.intent_id, verifier, (*verdict.findings, extra))
