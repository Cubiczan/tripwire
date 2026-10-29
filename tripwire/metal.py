"""Metal TESTNET agent payments (Kits 11/12) behind the Tripwire gate.
Modes: exact (x402 EIP-3009 transfer), mandate (owner-bound spending limits enforced on chain), clearing (Kit 12 bajillion, off-chain receipts).
Uses @metalntwx/metal-agent 0.3.3 CLI and @metalntwx/metal-pay 0.3.4 (metal/clearing.mjs). Mock when keys/node are missing."""
import base64, json, os, re, shutil, subprocess, uuid, httpx
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MDIR = os.path.join(ROOT, "metal"); KEYS = os.getenv("METAL_KEY_DIR", os.path.join(ROOT, ".metal-keys"))
CLI = os.path.join(MDIR, "node_modules", ".bin", "metal-agent")
NODE22 = os.getenv("METAL_NODE", shutil.which("node22") or "/tmp/n22/node_modules/.bin/node")
NETWORK = "testnet"  # hard-wired: never mainnet
USDZ_DECIMALS = 18

def _env():
    e = dict(os.environ); f = os.path.join(KEYS, "rpc.env")
    if "METAL_RPC_URL" not in e and os.path.exists(f):
        for l in open(f):
            if "=" in l: k, v = l.strip().split("=", 1); e[k] = v
    return e

def quote(url: str) -> dict:
    """Read the seller's x402 offer (402 PAYMENT-REQUIRED) without paying."""
    r = httpx.get(url, timeout=20)
    if r.status_code != 402: return {"payable": False, "status": r.status_code}
    j = json.loads(base64.b64decode(r.headers["payment-required"]))
    acc = {a["scheme"]: a for a in j["accepts"]}
    a = acc.get("exact") or next(iter(acc.values()))
    return {"payable": True, "amount_usdz": int(a["amount"]) / 10 ** USDZ_DECIMALS, "payTo": a["payTo"], "network": a["network"], "schemes": sorted(acc)}

def _clean(s: str) -> str:
    return "\n".join(l for l in s.splitlines() if "rpc-aas" not in l and "/v1/" not in l)

class MetalClient:
    def __init__(self, mock: bool | None = None):
        live = os.path.exists(CLI) and os.path.exists(os.path.join(KEYS, "agent.json"))
        self.mock = mock if mock is not None else not live
    def pay(self, url: str, mode: str = "exact", max_usdz: float = 1.0) -> dict:
        if mode not in ("exact", "mandate", "clearing"): raise ValueError(mode)
        if self.mock:
            return {"mode": mode, "network": f"metal-{NETWORK}", "status": 200, "transaction": f"mock_0x{uuid.uuid4().hex}", "mock": True}
        env = _env()
        if mode == "clearing":
            p = subprocess.run([NODE22, "--no-warnings", "clearing.mjs", "pay", url], cwd=MDIR, env={**env, "METAL_KEY_DIR": KEYS}, capture_output=True, text=True, timeout=240)
            try: j = json.loads(_clean(p.stdout).strip().splitlines()[-1])
            except Exception: j = {"ok": False, "error": _clean(p.stderr)[-300:]}
            return {"mode": "clearing", "network": f"metal-{NETWORK}", **j, "mock": False}
        cmd = [CLI, "pay", url, "--network", NETWORK]  # mandate: limits are enforced on chain by the bound mandate
        cmd += ["--mandate", os.path.join(KEYS, "mandate.json")] if mode == "mandate" else ["--key", os.path.join(KEYS, "agent.json"), "--max", str(max_usdz)]
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
        out = _clean(p.stdout + p.stderr); kv = dict(m.groups() for m in re.finditer(r"^(status|payer|payee|amount|transaction|explorer|error):? (.+)$", out, re.M))
        return {"mode": mode, "network": f"metal-{NETWORK}", "ok": p.returncode == 0, **kv, "mock": False}
