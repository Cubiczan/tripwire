from datetime import datetime, timedelta
from tripwire.policy import Policy, PaymentRequest, evaluate, Decision, tighten
from tripwire.audit import AuditLog
from tripwire.airwallex import AirwallexClient
from tripwire import verifier
T = datetime(2026, 11, 2, 12, 0)
def R(**k):
    b = dict(agent_id="a1", kind="transfer", amount=100, currency="USD", payee_id="p1", payee_country="US", at=T); b.update(k); return PaymentRequest(**b)
P = Policy()
def test_allow(): assert evaluate(R(), P, []).decision == Decision.ALLOW
def test_hold_threshold(): assert evaluate(R(amount=2000), P, []).decision == Decision.HOLD
def test_deny_max(): assert evaluate(R(amount=9000), P, []).decision == Decision.DENY
def test_deny_nonpositive(): assert evaluate(R(amount=0), P, []).decision == Decision.DENY
def test_country(): assert evaluate(R(payee_country="KP"), P, []).decision == Decision.DENY
def test_payee_allowlist(): assert evaluate(R(), Policy(allowed_payees={"x"}), []).decision == Decision.HOLD
def test_velocity_count():
    h = [R(at=T - timedelta(minutes=i)) for i in range(1, 6)]
    assert "velocity_count" in evaluate(R(), P, h).reasons
def test_velocity_old_ignored():
    h = [R(at=T - timedelta(hours=3)) for _ in range(10)]
    assert evaluate(R(), P, h).decision == Decision.ALLOW
def test_velocity_total(): assert "velocity_total" in evaluate(R(amount=900), P, [R(amount=4900, at=T-timedelta(minutes=5))]*2).reasons
def test_fx(): assert evaluate(R(fx_rate=0.81, fx_mid=0.79), P, []).decision == Decision.DENY
def test_fx_ok(): assert evaluate(R(fx_rate=0.791, fx_mid=0.79), P, []).decision == Decision.ALLOW
def test_window(): assert evaluate(R(at=T.replace(hour=3)), P, []).decision == Decision.HOLD
def test_tighten_monotonic():
    for a in Decision:
        for b in Decision: assert tighten(a, b) != Decision.ALLOW or (a == b == Decision.ALLOW)
def test_verifier_cannot_loosen(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    d, _ = verifier.verify(R(), evaluate(R(amount=9000), P, [])); assert d == Decision.DENY
    d, _ = verifier.verify(R(memo="URGENT gift card"), evaluate(R(), P, [])); assert d == Decision.HOLD
def test_audit_chain_and_tamper():
    a = AuditLog(); [a.append({"n": i}) for i in range(5)]; assert a.verify() == (True, None)
    a.db.execute("UPDATE audit SET payload='{\"n\": 99}' WHERE id=3"); assert a.verify() == (False, 3)
def test_mock_client():
    c = AirwallexClient(mock=True); assert c.create_transfer("b", 10, "USD")["mock"]; assert c.issue_card_simulated("a", 50)["simulated"]
def test_api(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIPWIRE_DB", str(tmp_path / "t.db")); monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import importlib, tripwire.app as m; importlib.reload(m)
    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    r = c.post("/pay", json={"agent_id": "a", "amount": 9000, "payee_id": "p", "payee_country": "US"}).json(); assert r["final"] == "deny"
    assert c.get("/audit/verify").json()["ok"]
