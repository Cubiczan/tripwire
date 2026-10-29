import hashlib, hmac, json, time, importlib
from urllib.parse import urlencode
from fastapi.testclient import TestClient
from tripwire import slack, verifier, metal
from tripwire.policy import Policy, PaymentRequest, evaluate, Decision
from datetime import datetime

def app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("TRIPWIRE_DB", str(tmp_path / "t.db"))
    for k in ("ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_APPROVAL_CHANNEL", "AIRWALLEX_CLIENT_ID", "AIRWALLEX_API_KEY"): monkeypatch.delenv(k, raising=False)
    for k, v in env.items(): monkeypatch.setenv(k, v)
    import tripwire.app as m; importlib.reload(m); m.metal = metal.MetalClient(mock=True); return m, TestClient(m.app)

def test_metal_mock_and_mode_guard():
    c = metal.MetalClient(mock=True); r = c.pay("https://x", "clearing"); assert r["mock"] and r["network"] == "metal-testnet"
    try: c.pay("https://x", "mainnet"); assert False
    except ValueError: pass
    assert metal.NETWORK == "testnet"

def test_x402_through_gate(tmp_path, monkeypatch):
    m, c = app(tmp_path, monkeypatch)
    r = c.post("/pay", json={"agent_id": "a", "kind": "x402", "url": "https://seller.test/q", "amount": 0.01, "metal_mode": "mandate"}).json()
    assert r["final"] == "allow" and r["execution"]["mode"] == "mandate"
    r = c.post("/pay", json={"agent_id": "a", "kind": "x402", "url": "https://seller.test/q", "amount": 2000}).json()
    assert r["final"] == "hold" and "execution" not in r
    assert c.get("/actions").json()[0]["rail"] == "metal:mandate"

def test_slack_stub_hold_and_dashboard_endpoints(tmp_path, monkeypatch):
    m, c = app(tmp_path, monkeypatch)
    r = c.post("/pay", json={"agent_id": "a", "amount": 1500, "payee_id": "p", "payee_country": "US"}).json()
    assert r["approval"] == {"slack": "stub"}; assert c.get("/holds").json()[0]["hold_id"] == r["hold_id"]
    assert c.get("/decisions").json()[0]["final"] == "hold"
    assert c.get("/status").json()["slack"] == "stub"
    assert "Tripwire" in c.get("/").text
    assert c.post(f"/holds/{r['hold_id']}/approve").json()["execution"]["mock"]
    assert c.post(f"/holds/{r['hold_id']}/approve").status_code == 404

def _signed(secret, payload):
    body = urlencode({"payload": json.dumps(payload)}).encode(); ts = str(int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return body, {"x-slack-request-timestamp": ts, "x-slack-signature": sig, "content-type": "application/x-www-form-urlencoded"}

def test_slack_interaction_signed(tmp_path, monkeypatch):
    m, c = app(tmp_path, monkeypatch, SLACK_SIGNING_SECRET="s3cret")
    hid = c.post("/pay", json={"agent_id": "a", "amount": 1500, "payee_id": "p", "payee_country": "US"}).json()["hold_id"]
    p = {"user": {"username": "lead"}, "actions": [{"action_id": "tripwire_deny", "value": hid}]}
    body, h = _signed("wrong", p); assert c.post("/slack/interactions", content=body, headers=h).status_code == 401
    body, h = _signed("s3cret", p); r = c.post("/slack/interactions", content=body, headers=h)
    assert r.status_code == 200 and "denied by lead" in r.json()["text"]
    assert c.get("/holds").json() == []

def test_slack_blocks_have_buttons():
    req = PaymentRequest("a", "transfer", 1500, "USD", "p", "US", datetime.now())
    ids = [e["action_id"] for e in slack.blocks("hold_1", req, ["over_hold_threshold"])[1]["elements"]]
    assert ids == ["tripwire_approve", "tripwire_deny"]

class _R:
    def __init__(self, t): self.t = t
    def raise_for_status(self): pass
    def json(self): return {"content": [{"text": self.t}]}

def test_claude_live_path_tightens_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    req = PaymentRequest("a", "transfer", 100, "USD", "p", "US", datetime(2026, 11, 2, 12))
    v = evaluate(req, Policy(), [])
    monkeypatch.setattr(verifier.httpx, "post", lambda *a, **k: _R('```json\n{"decision":"hold","reason":"new payee"}\n```'))
    d, why = verifier.verify(req, v); assert d == Decision.HOLD and why.startswith("claude:")
    monkeypatch.setattr(verifier.httpx, "post", lambda *a, **k: _R('{"decision":"allow","reason":"fine"}'))
    d, _ = verifier.verify(req, evaluate(PaymentRequest("a", "transfer", 9000, "USD", "p", "US", datetime(2026, 11, 2, 12)), Policy(), [])); assert d == Decision.DENY
    def boom(*a, **k): raise RuntimeError
    monkeypatch.setattr(verifier.httpx, "post", boom); d, why = verifier.verify(req, v); assert d == Decision.HOLD and "error" in why
