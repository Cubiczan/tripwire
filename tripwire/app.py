"""FastAPI service: POST /pay -> policy -> verifier -> audit -> (execute | hold for Slack approval)."""
import os
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel
from .policy import Policy, PaymentRequest, evaluate, Decision
from .verifier import verify
from .audit import AuditLog
from .airwallex import AirwallexClient

app = FastAPI(title="Tripwire")
policy, history, holds = Policy(), [], {}
audit = AuditLog(os.getenv("TRIPWIRE_DB", "tripwire.db"))
aw = AirwallexClient()

class PayIn(BaseModel):
    agent_id: str; kind: str = "transfer"; amount: float; currency: str = "USD"
    payee_id: str; payee_country: str; buy_currency: str | None = None; memo: str = ""

def notify_slack(hold_id, req):  # stub: real version posts Block Kit approve/deny buttons
    print(f"[slack-stub] approval needed {hold_id}: {req.agent_id} {req.amount} {req.currency} -> {req.payee_id}")

@app.post("/pay")
def pay(p: PayIn):
    fx = aw.fx_quote(p.currency, p.buy_currency, p.amount) if p.buy_currency else {}
    req = PaymentRequest(p.agent_id, p.kind, p.amount, p.currency, p.payee_id, p.payee_country, datetime.now(), fx.get("rate"), fx.get("mid"), p.memo)
    v = evaluate(req, policy, history); final, vr = verify(req, v)
    out = {"policy": v.decision.value, "reasons": v.reasons, "verifier": vr, "final": final.value}
    if final == Decision.ALLOW:
        history.append(req)
        out["execution"] = aw.issue_card_simulated(p.agent_id, p.amount) if p.kind == "card" else aw.create_transfer(p.payee_id, p.amount, p.currency)
    elif final == Decision.HOLD:
        hid = f"hold_{len(holds)+1}"; holds[hid] = req; out["hold_id"] = hid; notify_slack(hid, req)
    out["audit_hash"] = audit.append({"req": req.__dict__, **out}); return out

@app.post("/holds/{hid}/{action}")
def resolve(hid: str, action: str, approver: str = "human"):
    req = holds.pop(hid); out = {"hold_id": hid, "action": action, "approver": approver}
    if action == "approve": history.append(req); out["execution"] = aw.create_transfer(req.payee_id, req.amount, req.currency)
    out["audit_hash"] = audit.append(out); return out

@app.get("/audit/verify")
def audit_verify():
    ok, bad = audit.verify(); return {"ok": ok, "broken_at": bad}
