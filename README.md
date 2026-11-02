# Tripwire: a firewall for AI-agent payments (Airwallex + Metal)
Airwallex Agentic Banking Hackathon (HackerEarth) · Shyam's Team 2

Agents never hold payment keys. They call `POST /pay`; Tripwire decides **ALLOW / HOLD / DENY** and only then moves money on
**Airwallex** (transfers, FX, Issuing virtual cards) or **Metal testnet** (x402 `exact`, on-chain **mandates**, **Kit 12 clearing**).

```
agent → POST /pay → deterministic policy gate (limits, payee/country allowlists, velocity, FX deviation, time window)
      → Claude verifier (can only tighten; fails closed) → hash-chained audit log
      → ALLOW: execute on Airwallex sandbox / Metal testnet
      → HOLD : Slack Approve/Deny card (or dashboard) → /holds/{id}/approve|deny → execute
      → DENY : nothing moves, rules recorded
```

## Integrations
| Integration | What it does | Code |
|---|---|---|
| Airwallex sandbox | login, live FX rates, transfers, Issuing cardholders + virtual cards (simulated fallback on error) | `tripwire/airwallex.py` |
| Metal testnet, Kit 11 | x402 `exact` payments; mandate (owner's MetalAccount, cap 1 / budget 5 USDZ, payee allowlist) bound on chain; agent pays under it | `tripwire/metal.py`, `@metalntwx/metal-agent@0.3.3` |
| Metal testnet, Kit 12 | clearing (`bajillion`): deposit once, pay per call off chain, preconfirmed receipt | `metal/clearing.mjs`, `@metalntwx/metal-pay@0.3.4` |
| Slack approvals | Block Kit Approve/Deny buttons, signed `/slack/interactions` (HMAC v0) → hold resolve; console stub if unset | `tripwire/slack.py` |
| Claude verifier | **Live** with `claude-haiku-4-5` via Anthropic Messages API, tighten-only, fail-closed; heuristic stub if no key | `tripwire/verifier.py` |
| T:0 (t0.ai) books | vendor, open bills, cash (balance sheet), GL accounts drive the decision; journal-entry write-back after execution | `tripwire/t0.py` |
| Dashboard | decisions, holds with Approve/Deny, audit-chain status, recent Airwallex/Metal actions | `dashboard/index.html` at `/` |

Metal is hard-wired to **testnet** (`eip155:63825`); there is no mainnet path.

## T:0 books as financial context (live, Nov 2 2026)
Before the policy gate decides on a transfer, Tripwire pulls the company's books from [T:0](https://t0.ai) (`tripwire/t0.py`, read with the T:0 sandbox key) and adds these rules. T:0 rules can only tighten a decision.

| T:0 data (endpoint) | Rule | Outcome |
|---|---|---|
| `GET /v1/vendors/{id}` (Airwallex beneficiary to T:0 vendor via `T0_VENDOR_MAP`) | payee isn't a known vendor | HOLD `t0_vendor_unknown` |
| `GET /v1/vendor-bills?status=open` | vendor has no open bill | HOLD `t0_no_open_bill` |
| same | open bill exists but no amount within 1% | HOLD `t0_amount_mismatch` |
| `GET /v1/reports/balance-sheet` (Bank group) | amount (USD via Airwallex FX) is more than cash | DENY `t0_insufficient_cash` (optional HOLD below `T0_MIN_CASH_BUFFER`) |
| any T:0 error | fail closed | HOLD `t0_unavailable` |

After an Airwallex transfer executes, Tripwire calls `POST /v1/journal-entries` with an `X-Idempotency-Key` set to the transfer id. The entry debits Accounts Payable (21100, with the vendor as counterparty) when a bill matched, or Uncategorized (90000) when a person approved it with no bill. It credits Transfer Clearing (11900), using GL ids from `GET /v1/gl-accounts`. For an EUR transfer it books USD functional plus EUR transaction amounts.

**Live run results** (`scripts/t0_live_demo.py`, output in `docs/t0_live_demo_nov2.txt`), against the live Airwallex sandbox and the live T:0 sandbox entity:
- T:0 reads returned **200**: vendors, vendor-bills, balance-sheet (Bank $216,929.30) and gl-accounts.
- €25 to the sandbox EUR beneficiary, mapped to the T:0 vendor "Due Ltd.": all 14 T:0 bills are `paid`, with no open bill, so it was **HELD** (`t0_no_open_bill`). The CFO approved it, Airwallex created transfer `468de7d9…` (**SCHEDULED**), and the journal-entry POST returned **403 `CAPI71`**. The T:0 key we were given doesn't have the Create journal entries permission, so **no entry was written to T:0**.
- €25 to an unmapped beneficiary was **HELD** (`t0_vendor_unknown`).
- A $50 card passed the T:0 cash check and Airwallex Issuing returned an **ACTIVE** card.
- Not live: seeding an open bill (`POST /v1/records`) returned **403 `CAPI72`** because the key has no Custom API connection. So the ALLOW-on-matching-bill path and `t0_amount_mismatch` are shown **only in tests with a mocked T:0** (`tests/test_t0.py`).
- The beneficiary-to-vendor mapping is demo config. Metal was mock and Slack was a stub in this run.

![T:0 no open bill hold](docs/screenshots/30_t0_no_open_bill_hold.png)
![Approve, execute, JE attempt](docs/screenshots/31_t0_approve_execute_je.png)
![Vendor unknown and card](docs/screenshots/32_t0_vendor_unknown_and_card.png)
![T:0 call log](docs/screenshots/33_t0_call_log.png)
![32 tests passed](docs/screenshots/34_tests_32_passed.png)

## Setup
```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # Airwallex sandbox keys; empty = mock
cd metal && npm install && cd ..  # Metal packages (Node 22.13+ needed for clearing)
# Metal testnet wallet (keys stay in .metal-keys/, gitignored)
M=metal/node_modules/.bin/metal-agent
$M key create --out .metal-keys/agent.json && $M fund <address> --network testnet
$M pay https://rpc-testnet.metalntwx.com/v1 --network testnet --key .metal-keys/agent.json --method POST \
   --body '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'   # header rpc-aas-endpoint -> METAL_RPC_URL in .metal-keys/rpc.env
$M account create --out .metal-keys/owner.json --network testnet && $M fund <account> --network testnet
$M mandate credential create --principal <account> --payee <seller> --cap 1 --budget 5 --key .metal-keys/owner.json --out .metal-keys/mandate.json --network testnet
$M mandate bind --credential .metal-keys/mandate.json --key .metal-keys/owner.json --network testnet
(cd metal && node clearing.mjs deposit 0.2)
pytest -q && uvicorn tripwire.app:app     # dashboard at http://localhost:8000/
```
Endpoints: `POST /pay` (`kind`: transfer | card | x402 with `url`, `metal_mode`: exact | mandate | clearing), `POST /holds/{id}/approve|deny`,
`POST /slack/interactions`, `GET /holds /decisions /actions /status /audit/verify`.

## Status as of Nov 2 2026, 3:00 PM ET (honest)
The sandbox key (scoped key `tripwire`, demo env) now has account-scoped permissions. Live probe (`GET /airwallex/probe`):
balances, FX rates, beneficiaries, transfers, payment intents, Issuing cardholders, webhooks → **200**; conversions list → 400 (not used).

Full flow run live against the Airwallex demo sandbox (`mock:false`, real Airwallex IDs):
| Step | Result |
|---|---|
| 25 EUR transfer | ALLOW → real sandbox transfer, SCHEDULED |
| 250 EUR + live FX check (EUR→GBP rate vs mid) | ALLOW → real sandbox transfer, SCHEDULED |
| 50 USD card | ALLOW → real Issuing virtual card, ACTIVE |
| 5,000 EUR | HOLD → CFO approves → real sandbox transfer, SCHEDULED |
| Payee in KP | DENY, nothing moves |
| Audit chain | intact |

Mock fallback stays: no keys → mock; any Airwallex 4xx on transfers/FX → executes as mock with `mock:true` and `fallback_reason`; Issuing errors → simulated card with reason.
Known limits: transfers use a pre-existing sandbox EUR beneficiary (LOCAL method); Metal ran in mock in this run (testnet keys not loaded); Slack is a console stub.

| | |
|---|---|
| Key permission probe | ![](docs/screenshots/20_live_probe.png) |
| 25 EUR transfer, live | ![](docs/screenshots/21_live_transfer_25_allow.png) |
| 250 EUR, live FX check | ![](docs/screenshots/22_live_fx_checked_250.png) |
| Live Issuing card | ![](docs/screenshots/23_live_issuing_card.png) |
| 5,000 EUR HOLD | ![](docs/screenshots/24_live_hold_5000.png) |
| CFO approves → executed live | ![](docs/screenshots/25_live_hold_approved.png) |
| KP DENY | ![](docs/screenshots/26_kp_denied.png) |
| Audit verify | ![](docs/screenshots/27_audit_verify.png) |
| Dashboard after live run | ![](docs/screenshots/28_dashboard_live_nov2.png) |

Demo video (live sandbox run, 1:39): [docs/demo_live_nov2.mp4](docs/demo_live_nov2.mp4). Tests: `pytest -q` → 24 passed.

Earlier (Nov 2, 2:50 PM ET) the key had only Webhooks Read; that run is kept below for the record.

### Earlier scoped-key run (Webhooks Read only)
The current sandbox key (scoped key `tripwire`, demo env) logs in (201) but only has **Webhooks Read**. Live probe (`GET /airwallex/probe`):

| Endpoint | Status |
|---|---|
| Webhooks list | **200, live** |
| Balances, FX rates, Beneficiaries, Transfers, Payment Intents, Issuing cardholders/cards, Conversions | 401 "Insufficient permissions" |

On a 401/403 Tripwire still runs the policy gate, verifier and audit log, then executes in **mock** and labels it (`mock: true`, `fallback_reason`).
The Oct 29 screenshots below came from an earlier, broader sandbox key. To re-enable live execution, add these scopes to the key: FX rates read,
Transfers read/write, Beneficiaries read, Balances read, Issuing cardholders/cards read/write (optional: Conversions write, Payment Acceptance).

| Nov 2 dashboard, scoped key (transfer allowed → labeled mock fallback) | ![](docs/screenshots/16_dashboard_nov2_scoped_key.png) |
|---|---|
| Live permission probe | ![](docs/screenshots/17_airwallex_permission_probe.png) |

Tests: `pytest -q` → 24 passed.

## Live demo (Airwallex demo sandbox + Metal testnet, Oct 29 2026)
| | |
|---|---|
| Dashboard | ![](docs/screenshots/09_dashboard.png) |
| $25 transfer → ALLOW, executed | ![](docs/screenshots/01_pay_25_transfer_allowed.png) |
| $250 with live FX check → ALLOW | ![](docs/screenshots/02_pay_250_fx_checked.png) |
| $50 card → real Issuing virtual card ACTIVE | ![](docs/screenshots/03_pay_50_real_card_active.png) |
| $5,000 → HOLD | ![](docs/screenshots/04_pay_5000_held_for_approval.png) |
| Held payment approved → executed | ![](docs/screenshots/08_hold_approved_executed.png) |
| Payee in KP → DENY | ![](docs/screenshots/05_pay_kp_denied.png) |
| Metal x402 exact | ![](docs/screenshots/10_metal_x402_exact.png) |
| Metal mandate payment | ![](docs/screenshots/11_metal_mandate_pay.png) |
| Metal Kit 12 clearing | ![](docs/screenshots/12_metal_kit12_clearing.png) |
| Verifier HOLD on Metal, human approves | ![](docs/screenshots/13_metal_hold_then_approve.png) |
| Slack approval card | ![](docs/screenshots/14_slack_approval_card.png) |
| Audit chain verification | ![](docs/screenshots/06_audit_verify.png) |
| Tests 23/23 | ![](docs/screenshots/07_tests_passed.png) |
| API docs | ![](docs/screenshots/00_api_docs.png) |

Demo video: [docs/demo.mp4](docs/demo.mp4) · Submission draft: [docs/SUBMISSION.md](docs/SUBMISSION.md)
