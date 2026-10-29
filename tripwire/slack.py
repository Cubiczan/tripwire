"""Slack approvals for held payments: Block Kit approve/deny buttons -> POST /slack/interactions -> /holds/{id}/approve|deny.
Uses the user's OWN Slack app (SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET, SLACK_APPROVAL_CHANNEL). Stub (console) when unset."""
import hashlib, hmac, json, os, time, httpx

def configured() -> bool:
    return all(os.getenv(k) for k in ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_APPROVAL_CHANNEL"))

def blocks(hold_id, req, reasons):
    txt = f"*Tripwire hold* `{hold_id}`\nAgent `{req.agent_id}` wants to pay *{req.amount:,.2f} {req.currency}* to `{req.payee_id}` ({req.payee_country}) via {req.kind}.\nRules: {', '.join(reasons)}"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": txt}},
            {"type": "actions", "elements": [
                {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "Approve"}, "action_id": "tripwire_approve", "value": hold_id},
                {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "Deny"}, "action_id": "tripwire_deny", "value": hold_id}]}]

def notify(hold_id, req, reasons) -> dict:
    if not configured():
        print(f"[slack-stub] approval needed {hold_id}: {req.agent_id} {req.amount} {req.currency} -> {req.payee_id}")
        return {"slack": "stub"}
    r = httpx.post("https://slack.com/api/chat.postMessage", timeout=15, headers={"Authorization": f"Bearer {os.environ['SLACK_BOT_TOKEN']}"},
                   json={"channel": os.environ["SLACK_APPROVAL_CHANNEL"], "text": f"Tripwire hold {hold_id}", "blocks": blocks(hold_id, req, reasons)})
    j = r.json(); return {"slack": "posted" if j.get("ok") else "error", "error": j.get("error"), "ts": j.get("ts")}

def verify_signature(body: bytes, ts: str, sig: str, now: float | None = None) -> bool:
    secret = os.getenv("SLACK_SIGNING_SECRET", "")
    if not (secret and ts and sig) or abs((now or time.time()) - int(ts)) > 300: return False
    mine = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mine, sig)

def parse_action(payload: str):
    p = json.loads(payload); a = p["actions"][0]
    return a["value"], "approve" if a["action_id"] == "tripwire_approve" else "deny", p.get("user", {}).get("username") or p.get("user", {}).get("id", "slack")
