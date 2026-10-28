"""Claude verifier: may only tighten the policy decision. Stub if no ANTHROPIC_API_KEY."""
import json, os, httpx
from .policy import Decision, tighten
MODEL = os.getenv("TRIPWIRE_CLAUDE_MODEL", "claude-sonnet-4-5")

def verify(req, verdict):
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:  # stub: heuristic red flags in memo
        sus = any(w in req.memo.lower() for w in ("urgent", "gift card", "ignore previous", "crypto"))
        return tighten(verdict.decision, Decision.HOLD if sus else Decision.ALLOW), "stub:" + ("suspicious_memo" if sus else "no_flags")
    prompt = (f"You audit AI-agent payments. Request: {req.__dict__}. Policy decision: {verdict.decision.value} {verdict.reasons}. "
              'Reply JSON only {"decision":"allow|hold|deny","reason":"..."}')
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=30, headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                       json={"model": MODEL, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]})
        j = json.loads(r.json()["content"][0]["text"]); d = Decision(j["decision"])
        return tighten(verdict.decision, d), "claude:" + j.get("reason", "")
    except Exception as e:  # fail closed
        return tighten(verdict.decision, Decision.HOLD), f"claude_error:{type(e).__name__}"
