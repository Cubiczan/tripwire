# tripwire — context

## Purpose

Agents never hold payment keys. They call `POST /pay`; Tripwire decides **ALLOW / HOLD / DENY** and only then moves money on **Airwallex** (transfers, FX, Issuing virtual cards) or **Metal testnet** (x402 `exact`, on-chain **mandates**, **Kit 12 clearing**).

## Architecture

Stack: Python.

Top-level layout:

- `dashboard/`
- `docs/`
- `metal/`
- `tests/`
- `tripwire/`
- `README.md`
- `coverage.xml`
- `requirements.txt`

## How to run

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # Airwallex sandbox keys; empty = mock
cd metal && npm install && cd ..  # Metal packages (Node 22.13+ needed for clearing)
# Metal testnet wallet (keys stay in .metal-keys/, gitignored)
M=metal/node_modules/.bin/metal-agent
$M key create --out .metal-keys/agent.json && $M fund <address> --network testnet
$M pay https://rpc-testnet.metalntwx.com/v1 --network testnet --key .metal-keys/agent.json --method POST \
   --body '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'   # header rpc-aas-endpoint -> METAL_RPC_URL in .metal-keys/rpc.env
```

## Tests

```bash
python -m pytest -q
```

Coverage report: `coverage.xml` (generated 2026-10-31).

## Data note

Synthetic data only. Fixtures, samples and docs use fictional names and companies, example.com / .example emails, 555 phone numbers and placeholder keys (REPLACE_ME or env-var references). Statutes, regulations and public filings are cited as public record. No customer data is included.

See README.md for full details.
