# Tripwire

**A firewall for agent payments on Airwallex.**
Any AI agent can *propose* a payment. Only Tripwire can *release* one.

Built for the [Airwallex Agentic Banking Hackathon](https://www.hackerearth.com/community/challenges/hackathon/agentic-banking-hackathon/).

## Why

Everyone is building agents that move money. Almost nobody can answer the question that stops companies from switching them on:

> *What happens when the agent is wrong, tricked, or retried?*

Prompt-injected invoices that swap bank details, double-pays after a delayed transfer, runaway spend, and no record of why a payment happened. The agent is not the product. **The control layer is.**

## How it works

```
 Agent (LLM)                     Tripwire                              Airwallex
 ───────────                     ────────                              ─────────
 proposes ──► signed PaymentIntent ─► 1. signature check
                                      2. deterministic policy engine ──┐
                                      3. independent AI verifier  ─────┤ can only tighten
                                      4. decision: ALLOW / HOLD / DENY ┘
                                                │
                                   ALLOW ───────┼──► idempotent transfer / scoped card ──► sandbox API
                                   HOLD  ───────┼──► human approves in one tap
                                   DENY  ───────┘
                                      5. webhook reconciliation + hash-chained audit log
```

Agents never hold Airwallex credentials. They hold only a per-agent signing key for intents.

## Status

This is a hackathon build in progress. Be precise about what exists:

| Component | State |
|---|---|
| Signed `PaymentIntent` schema (`intent.py`) | Done, tested |
| Deterministic policy engine, 9 rules (`policy.py`) | Done, tested |
| Verifier "can only tighten" combinator (`tighten`) | Done, tested. The AI verifier itself is not built yet |
| Airwallex client: login, token cache, create/get transfer (`airwallex.py`) | Done, tested against a mock transport. **Not yet run against a live sandbox** |
| Virtual card issuing, FX quotes, beneficiaries, webhooks | Planned |
| Treasury / purchase / payables demo agents | Planned |
| Red-team attack suite + scoreboard | Planned |
| Hash-chained audit log, dashboard, Slack approvals | Planned |

## Policy rules

Every rule runs. The final decision is the most severe finding (`DENY > HOLD > ALLOW`).

| Rule | Triggers | Result |
|---|---|---|
| `signature` | Missing, invalid or tampered intent signature | DENY |
| `known_agent` | Agent has no policy configured | DENY |
| `duplicate_key` | Idempotency key already released | DENY |
| `payee_allowlist` | First payment to an unknown payee | HOLD |
| `bank_details_change` | Bank details differ from the last verified ones for this payee | HOLD |
| `lookalike_payee` | New payee name closely resembles a known payee | HOLD |
| `amount_limits` | Over approval threshold / over per-payment max / currency the limits don't cover | HOLD / DENY / HOLD |
| `daily_budget` | Rolling 24h spend would exceed the agent's budget | DENY |
| `velocity` | Too many payments inside the window | HOLD |
| `fx_slippage` | Quoted FX rate too far from the reference rate (default 50 bps) | DENY |

The policy path contains no LLM. When uncertain, it holds.

## Quickstart

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd tripwire
uv sync
uv run python -m pytest        # 16 tests
uv run ruff check .
```

Airwallex sandbox access:

```bash
cp .env.example .env           # then fill in your sandbox client id and API key
```

```python
import asyncio
from decimal import Decimal
from tripwire.airwallex import AirwallexClient


async def main() -> None:
    aw = AirwallexClient.from_env()
    transfer = await aw.create_transfer(
        request_id="intent-123",  # use the intent's idempotency_key
        beneficiary_id="<beneficiary uuid>",
        transfer_currency="USD",
        transfer_amount=Decimal("250.00"),
        source_currency="USD",
        reason="supplier payment",
        reference="INV-1042",
    )
    print(transfer["status"])
    await aw.aclose()


asyncio.run(main())
```

Gating an intent:

```python
from decimal import Decimal
from tripwire.intent import PaymentIntent
from tripwire.policy import AgentLimits, History, PolicyConfig, PolicyEngine

secret = b"per-agent-signing-key"
engine = PolicyEngine(
    PolicyConfig(
        agents={
            "payables": AgentLimits(
                currency="USD",
                max_per_payment=Decimal("5000"),
                approval_threshold=Decimal("1000"),
                daily_budget=Decimal("6000"),
            )
        }
    ),
    History(),
    {"payables": secret},
)
intent = PaymentIntent(
    agent_id="payables",
    payee_id="acme",
    payee_name="Acme Supplies Ltd",
    bank_details_fingerprint="fp-1",
    amount=Decimal("500"),
    currency="USD",
    purpose="Invoice 1042",
).signed(secret)

verdict = engine.evaluate(intent)
print(verdict.decision.name, verdict.reasons)  # HOLD: first payment to unknown payee
```

## Design notes

- **Intent, not action.** An intent is signed with HMAC-SHA256 over its canonical JSON, so a forged or edited intent is denied before any rule runs.
- **Idempotency is a first-class rule.** The intent's idempotency key is the Airwallex `request_id`. Airwallex rejects a reused `request_id` within 7 days (`request_id_duplicate`), and its docs say to look a transfer up by `request_id` before retrying after a timeout. Tripwire's retry path is built around that.
- **Fail closed.** Unknown agent, unknown payee, unsupported currency and missing FX rates all hold or deny instead of passing.
- **Money is `Decimal`.** Amounts are serialised as strings to the API, never floats.

## Known gaps and unverified assumptions

- The sandbox host differs between doc versions (`api.sandbox.airwallex.com` in the latest docs, `api-demo.airwallex.com` in older ones). It is configurable via `AIRWALLEX_BASE_URL`; confirm against your account.
- The login response field name `token` and the transfer-by-`request_id` lookup have not been confirmed against a live sandbox.
- `History` is in memory. It needs a database before the daily-budget and duplicate rules survive a restart.
- Card issuing may need allowlisted access. If so, cards will be simulated behind the same interface.

## Roadmap

1. Live sandbox smoke test (login, beneficiary, transfer, webhook).
2. Claude-based verifier that sees only raw evidence plus the intent.
3. Three demo agents behind the gate: treasury, SaaS purchase, supplier payables.
4. Open red-team suite with published block rate, false-positive rate and added latency.
5. Hash-chained flight recorder, decision timeline dashboard, one-tap Slack approvals.

## License

Copyright Shyam Desigan. License to be decided before the build-phase submission.
