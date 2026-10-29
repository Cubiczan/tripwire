# Tripwire: submission draft (NOT SUBMITTED)
Airwallex Agentic Banking Hackathon (HackerEarth) · Team: Shyam's Team 2 · Deadline Nov 13 2026, 2:29 PM ET

**Title:** Tripwire: a firewall for AI-agent payments on Airwallex

**Tagline:** Agents propose. Policy decides. Humans approve. Airwallex executes.

**Repo:** https://github.com/Cubiczan/tripwire · **Video:** docs/demo.mp4 (2:06; upload to YouTube/Drive and paste link)

## Description
Companies want AI agents to pay vendors, top up cards and move money, but handing an agent raw payment credentials is unsafe. Tripwire is a payment firewall that sits between any agent and Airwallex. The agent can only call `POST /pay`. Every request goes through a deterministic policy gate (amount limits, payee and country allowlists, velocity, time window, and deviation of the quoted FX rate from mid-market). An LLM verifier can only tighten a decision, never loosen it. The result is ALLOW (executed on Airwallex), HOLD (queued for a human approver), or DENY, with the exact rules that fired. Every decision is written to a hash-chained, tamper-evident audit log.

## The four judging requirements
- **End-to-end workflow for a clear user:** a finance team lets its accounts-payable agent pay vendors and spin up spend cards; Tripwire executes routine payments automatically and routes the rest to the finance lead.
- **Airwallex for a necessary financial action:** real sandbox transfers (`/api/v1/transfers/create`) and real Issuing virtual cards (`/api/v1/issuing/cards/create`) with per-transaction limits.
- **Financial context drives the decision:** live FX rates (`/api/v1/fx/rates/current`) are compared with mid-market before money moves; amount, velocity and payee country change the outcome.
- **Controls and exceptions:** under-threshold payments auto-execute; above-threshold payments HOLD until a human approves (`/holds/{id}/approve|deny`); blocked countries or over-max amounts DENY; Issuing errors fall back to a simulated card and record the reason; audit chain verification at `/audit/verify`.

## How it uses Airwallex
Authentication login → FX current rates → Transfers (create, unique request_id) → Issuing cardholders + virtual cards with authorization controls. Balances and beneficiaries are read for setup.

## Tech stack
Python 3.13, FastAPI, httpx, SQLite hash-chained audit log, pytest (17 tests), Claude verifier (tighten-only; stubbed when no key), Airwallex demo sandbox.

## Demo steps
1. `pip install -r requirements.txt`; copy `.env.example` to `.env` with sandbox keys.
2. `uvicorn tripwire.app:app`
3. POST /pay $25 → ALLOW + scheduled transfer; $250 with `buy_currency=GBP` → FX-checked ALLOW; `kind=card` $50 → ACTIVE Issuing card; $5,000 → HOLD; approve the hold → executed; `payee_country=KP` → DENY.
4. `GET /audit/verify` → `{"ok": true}`; `pytest -q` → 17 passed.
