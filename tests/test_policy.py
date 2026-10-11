from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tripwire.intent import IntentKind, PaymentIntent
from tripwire.policy import (
    AgentLimits,
    Decision,
    History,
    PayeeRecord,
    PolicyConfig,
    PolicyEngine,
    tighten,
)

SECRET = b"test-secret"
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def make_intent(**kw: object) -> PaymentIntent:
    base: dict[str, object] = {
        "agent_id": "payables",
        "payee_id": "acme",
        "payee_name": "Acme Supplies Ltd",
        "bank_details_fingerprint": "fp-acme-1",
        "amount": Decimal("500"),
        "currency": "USD",
        "purpose": "Invoice 1042",
        "created_at": NOW,
    }
    base.update(kw)
    return PaymentIntent(**base).signed(SECRET)  # type: ignore[arg-type]


@pytest.fixture
def engine() -> PolicyEngine:
    cfg = PolicyConfig(
        agents={
            "payables": AgentLimits(
                currency="USD",
                max_per_payment=Decimal("5000"),
                approval_threshold=Decimal("1000"),
                daily_budget=Decimal("6000"),
                max_payments_per_window=3,
            )
        }
    )
    history = History(payees={"acme": PayeeRecord("Acme Supplies Ltd", "fp-acme-1")})
    return PolicyEngine(cfg, history, {"payables": SECRET})


def test_clean_payment_allowed(engine: PolicyEngine) -> None:
    assert engine.evaluate(make_intent()).decision is Decision.ALLOW


def test_tampered_intent_denied(engine: PolicyEngine) -> None:
    forged = make_intent().model_copy(update={"amount": Decimal("1")})
    v = engine.evaluate(forged)
    assert v.decision is Decision.DENY
    assert v.findings[0].rule == "signature"


def test_unsigned_intent_denied(engine: PolicyEngine) -> None:
    unsigned = make_intent().model_copy(update={"signature": ""})
    assert engine.evaluate(unsigned).decision is Decision.DENY


def test_bank_details_change_holds(engine: PolicyEngine) -> None:
    v = engine.evaluate(make_intent(bank_details_fingerprint="fp-attacker"))
    assert v.decision is Decision.HOLD
    assert any("bank_details_change" in r for r in v.reasons)


def test_unknown_payee_holds(engine: PolicyEngine) -> None:
    v = engine.evaluate(make_intent(payee_id="new", payee_name="Globex Corp"))
    assert v.decision is Decision.HOLD


def test_lookalike_payee_holds(engine: PolicyEngine) -> None:
    v = engine.evaluate(make_intent(payee_id="acme2", payee_name="Acme Supplies Ltd."))
    assert any(f.rule == "lookalike_payee" and f.decision is Decision.HOLD for f in v.findings)


def test_duplicate_key_denied_after_release(engine: PolicyEngine) -> None:
    first = make_intent(idempotency_key="k1")
    assert engine.evaluate(first).decision is Decision.ALLOW
    engine.release(first)
    assert engine.evaluate(make_intent(idempotency_key="k1")).decision is Decision.DENY


def test_over_max_denied_over_threshold_held(engine: PolicyEngine) -> None:
    assert engine.evaluate(make_intent(amount=Decimal("5001"))).decision is Decision.DENY
    assert engine.evaluate(make_intent(amount=Decimal("1500"))).decision is Decision.HOLD


def test_daily_budget_denied(engine: PolicyEngine) -> None:
    for i in range(2):
        engine.release(make_intent(amount=Decimal("1000"), idempotency_key=f"b{i}"))
    # 2000 spent; 4500 would take it to 6500 > 6000 (and is held for threshold, denied overall)
    assert engine.evaluate(make_intent(amount=Decimal("4500"))).decision is Decision.DENY


def test_velocity_holds(engine: PolicyEngine) -> None:
    for i in range(3):
        engine.release(make_intent(amount=Decimal("10"), idempotency_key=f"v{i}"))
    v = engine.evaluate(make_intent(amount=Decimal("10")))
    assert v.decision is Decision.HOLD
    assert any(f.rule == "velocity" for f in v.findings)


def test_currency_mismatch_holds(engine: PolicyEngine) -> None:
    assert engine.evaluate(make_intent(currency="EUR")).decision is Decision.HOLD


def test_fx_slippage_denied(engine: PolicyEngine) -> None:
    ok = make_intent(
        kind=IntentKind.FX_CONVERSION, quoted_rate=Decimal("1.0010"), reference_rate=Decimal("1.0")
    )
    bad = make_intent(
        kind=IntentKind.FX_CONVERSION, quoted_rate=Decimal("1.02"), reference_rate=Decimal("1.0")
    )
    assert engine.evaluate(ok).decision is Decision.ALLOW
    assert engine.evaluate(bad).decision is Decision.DENY


def test_unknown_agent_denied() -> None:
    eng = PolicyEngine(PolicyConfig(agents={}), History(), {"payables": SECRET})
    assert eng.evaluate(make_intent()).decision is Decision.DENY


def test_verifier_only_tightens(engine: PolicyEngine) -> None:
    v = engine.evaluate(make_intent())
    assert tighten(v, Decision.HOLD, "evidence mismatch").decision is Decision.HOLD
    held = engine.evaluate(make_intent(bank_details_fingerprint="x"))
    assert tighten(held, Decision.ALLOW, "looks fine").decision is Decision.HOLD
