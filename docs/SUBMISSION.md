# Tripwire: submission draft (NOT SUBMITTED)
Airwallex Agentic Banking Hackathon (HackerEarth) · Team: Shyam's Team 2 · Deadline Nov 13 2026, 2:29 PM ET

**Title:** Tripwire: a firewall for AI-agent payments on Airwallex and Metal

**Tagline:** Agents propose. Policy decides. Humans approve. Airwallex executes.

**Repo:** https://github.com/Cubiczan/tripwire · **Video:** docs/demo.mp4 (upload to YouTube/Drive and paste link)

## Description
Companies want AI agents to pay vendors, top up cards and move money, but handing an agent raw payment credentials is unsafe. Tripwire is a payment firewall that sits between any agent and Airwallex. The agent can only call `POST /pay`. Every request goes through a deterministic policy gate (amount limits, payee and country allowlists, velocity, time window, and deviation of the quoted FX rate from mid-market). An LLM verifier can only tighten a decision, never loosen it. The result is ALLOW (executed on Airwallex), HOLD (queued for a human approver), or DENY, with the exact rules that fired. Every decision is written to a hash-chained, tamper-evident audit log.

## The four judging requirements
- **End-to-end workflow for a clear user:** a finance team lets its accounts-payable agent pay vendors and spin up spend cards; Tripwire executes routine payments automatically and routes the rest to the finance lead.
- **Airwallex for a necessary financial action:** real sandbox transfers (`/api/v1/transfers/create`) and real Issuing virtual cards (`/api/v1/issuing/cards/create`) with per-transaction limits.
- **Financial context drives the decision:** live FX rates (`/api/v1/fx/rates/current`) are compared with mid-market before money moves; amount, velocity and payee country change the outcome.
- **Controls and exceptions:** under-threshold payments auto-execute; above-threshold payments HOLD until a human approves (`/holds/{id}/approve|deny`); blocked countries or over-max amounts DENY; Issuing errors fall back to a simulated card and record the reason; audit chain verification at `/audit/verify`.

## Metal (Kits 11 and 12, testnet) · for the Metal Award
Agents can also buy x402 resources. Tripwire reads the seller's 402 offer, runs the same policy and verifier, and only then pays on Metal testnet in one of three modes: `exact` (EIP-3009 transfer), `mandate` (the agent pays from its owner's MetalAccount under a mandate bound on chain: per-payment cap, total budget, payee allowlist, expiry), or Kit 12 `clearing` (deposit once, pay per call off chain, preconfirmed receipts). A held Metal payment settles only after a human approves. Built on @metalntwx/metal-agent 0.3.3 and @metalntwx/metal-pay 0.3.4.

## Human approval
Held payments post a Slack Block Kit card with Approve/Deny buttons (the team's own Slack app); clicks hit a signature-verified `/slack/interactions` endpoint. The dashboard offers the same buttons, plus the decision feed, recent Airwallex/Metal actions and audit-chain status.

## How it uses Airwallex
Authentication login → FX current rates → Transfers (create, unique request_id) → Issuing cardholders + virtual cards with authorization controls. Balances and beneficiaries are read for setup.

## Tech stack
Python 3.13, FastAPI, httpx, SQLite hash-chained audit log, pytest (23 tests); Node 22 with @metalntwx/metal-agent 0.3.3 and metal-pay 0.3.4; Slack Block Kit; Claude verifier running live with claude-haiku-4-5 (Anthropic Messages API, tighten-only, fail-closed); vanilla-JS dashboard; Airwallex demo sandbox and Metal testnet.

## Demo steps
1. `pip install -r requirements.txt`; copy `.env.example` to `.env` with sandbox keys.
2. `uvicorn tripwire.app:app`
3. POST /pay $25 → ALLOW + scheduled transfer; $250 with `buy_currency=GBP` → FX-checked ALLOW; `kind=card` $50 → ACTIVE Issuing card; $5,000 → HOLD; approve the hold → executed; `payee_country=KP` → DENY.
4. Metal: POST /pay `kind=x402`, `url=https://x402-demo-testnet.metalntwx.com/quote`, `metal_mode` exact / mandate / clearing → ALLOW and settled on testnet; a memo like "URGENT…" → HOLD → approve → settled.
5. Open `/` for the dashboard; `GET /audit/verify` → `{"ok": true}`; `pytest -q` → 23 passed.
