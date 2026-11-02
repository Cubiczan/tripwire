"""FastAPI service: POST /pay -> policy -> verifier -> audit -> (execute on Airwallex / Metal testnet | hold for Slack approval | deny)."""
import os
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .policy import Policy, PaymentRequest, evaluate, Decision
from .verifier import verify
from .audit import AuditLog
from .airwallex import AirwallexClient
from .metal import MetalClient, quote as metal_quote
from . import slack
from .t0 import T0Client
from .policy import tighten

app = FastAPI(title="Tripwire")
policy, history, holds = Policy(), [], {}
hold_ctx = {}
decisions, actions = [], []
import itertools; _hold_seq = itertools.count(1)
audit = AuditLog(os.getenv("TRIPWIRE_DB", "tripwire.db"))
aw, metal = AirwallexClient(), MetalClient()
t0 = T0Client()
def t0_on(): return (not t0.mock) or bool(t0.fx)
def to_usd(amount, cur):
    if cur.upper() in ("USD", "USDZ"): return amount, None
    q = aw.fx_quote(cur.upper(), "USD", amount); return amount * (q.get("rate") or 1.0), q
DASH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard", "index.html")

class PayIn(BaseModel):
    agent_id: str; kind: str = "transfer"; amount: float = 0; currency: str = "USD"
    payee_id: str = ""; payee_country: str = "US"; buy_currency: str | None = None; memo: str = ""
    url: str | None = None; metal_mode: str = "exact"  # kind="x402": pay an x402 URL on Metal testnet

def execute(p: PayIn, t0ctx: dict | None = None):
    if p.kind == "card": r = aw.issue_card(p.agent_id, p.amount); rail = "airwallex:issuing"
    elif p.kind == "x402": r = metal.pay(p.url, p.metal_mode, max_usdz=max(p.amount, 0.01)); rail = f"metal:{p.metal_mode}"
    else: r = aw.create_transfer(p.payee_id, p.amount, p.currency); rail = "airwallex:transfer"
    if p.kind == "transfer" and t0_on() and not (r.get("mock") and not t0.mock):  # never book a mock transfer into live T:0
        usd, _ = to_usd(p.amount, p.currency)
        try: r = {**r, "t0_journal_entry": t0.post_transfer_je(r, p.payee_id, p.amount, p.currency, usd, (t0ctx or {}).get("matched_bill"))}
        except Exception as e: r = {**r, "t0_journal_entry": {"error": f"{type(e).__name__}: {e}"}}
    actions.insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "rail": rail, "agent_id": p.agent_id, "amount": p.amount, "currency": p.currency, "result": r})
    return r

@app.post("/pay")
def pay(p: PayIn):
    if p.kind == "x402":
        if not p.url: raise HTTPException(400, "url required for x402")
        q = {"payable": True, "amount_usdz": p.amount or 0.01, "payTo": urlparse(p.url).hostname} if metal.mock else metal_quote(p.url)
        if not q.get("payable"): raise HTTPException(400, f"not an x402 resource: {q}")
        p.amount, p.currency, p.payee_id = q["amount_usdz"], "USDZ", p.payee_id or q["payTo"]
    fx = aw.fx_quote(p.currency, p.buy_currency, p.amount) if p.buy_currency else {}
    req = PaymentRequest(p.agent_id, p.kind, p.amount, p.currency, p.payee_id, p.payee_country, datetime.now(), fx.get("rate"), fx.get("mid"), p.memo)
    v = evaluate(req, policy, history); t0ctx = None
    if t0_on() and p.kind in ("transfer", "card"):
        usd, _ = to_usd(p.amount, p.currency)
        t0ctx = t0.context(p.payee_id, p.amount, p.currency, usd)
        if p.kind == "card": t0ctx["flags"] = [f for f in t0ctx["flags"] if f[1] in ("t0_insufficient_cash", "t0_cash_buffer", "t0_unavailable")]
        for d, r in t0ctx["flags"]:
            v.decision = tighten(v.decision, d); v.reasons = [x for x in v.reasons if x != "ok"] + [r]
        t0ctx["amount_usd"] = round(usd, 2)
    final, vr = verify(req, v)
    out = {"policy": v.decision.value, "reasons": v.reasons, "verifier": vr, "final": final.value}
    if t0ctx is not None: out["t0_context"] = {k: v_ for k, v_ in t0ctx.items() if k != "flags"} | {"flags": [r for _, r in t0ctx["flags"]]}
    if final == Decision.ALLOW:
        history.append(req); out["execution"] = execute(p, t0ctx)
    elif final == Decision.HOLD:
        hid = f"hold_{next(_hold_seq)}"; holds[hid] = (p, v.reasons); hold_ctx[hid] = t0ctx
        out["hold_id"] = hid; out["approval"] = slack.notify(hid, req, v.reasons)
    out["audit_hash"] = audit.append({"req": {**req.__dict__, "at": req.at.isoformat()}, **out})
    decisions.insert(0, {"at": req.at.isoformat(timespec="seconds"), "agent_id": p.agent_id, "kind": p.kind, "amount": p.amount, "currency": p.currency, **{k: out[k] for k in ("final", "reasons", "verifier")}, **({"hold_id": out["hold_id"]} if "hold_id" in out else {})})
    return out

@app.post("/holds/{hid}/{action}")
def resolve(hid: str, action: str, approver: str = "human"):
    if action not in ("approve", "deny"): raise HTTPException(400, "approve|deny")
    if hid not in holds: raise HTTPException(404, "unknown or already resolved hold")
    p, _ = holds.pop(hid); hc = hold_ctx.pop(hid, None); out = {"hold_id": hid, "action": action, "approver": approver}
    if action == "approve":
        history.append(PaymentRequest(p.agent_id, p.kind, p.amount, p.currency, p.payee_id, p.payee_country, datetime.now())); out["execution"] = execute(p, hc)
    out["audit_hash"] = audit.append(out); return out

@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    body = await request.body()
    if not slack.verify_signature(body, request.headers.get("x-slack-request-timestamp", ""), request.headers.get("x-slack-signature", "")):
        raise HTTPException(401, "bad slack signature")
    hid, action, user = slack.parse_action(parse_qs(body.decode())["payload"][0])
    r = resolve(hid, action, approver=f"slack:{user}")
    return {"response_type": "in_channel", "replace_original": True, "text": f"{hid} {'approved' if action == 'approve' else 'denied'} by {user}"}

@app.get("/holds")
def list_holds(): return [{"hold_id": h, "agent_id": p.agent_id, "kind": p.kind, "amount": p.amount, "currency": p.currency, "payee_id": p.payee_id, "reasons": r} for h, (p, r) in holds.items()]
@app.get("/decisions")
def list_decisions(): return decisions[:100]
@app.get("/actions")
def list_actions(): return actions[:100]
@app.get("/airwallex/probe")
def airwallex_probe(): return aw.probe()

@app.get("/airwallex/webhooks")
def airwallex_webhooks(): return aw.list_webhooks()

@app.get("/status")
def status():
    return {"airwallex": "mock" if aw.mock else "live-sandbox", "metal": "mock" if metal.mock else "live-testnet",
            "slack": "live" if slack.configured() else "stub", "claude": "live" if os.getenv("ANTHROPIC_API_KEY") else "stub",
            "t0": "live" if not t0.mock else ("mock" if t0.fx else "off")}
@app.get("/t0/calls")
def t0_calls(): return [{"method": m, "path": pth, "status": c} for m, pth, c in t0.calls[-50:]]
@app.get("/audit/verify")
def audit_verify():
    ok, bad = audit.verify(); return {"ok": ok, "broken_at": bad}
@app.get("/")
def dashboard(): return FileResponse(DASH)
