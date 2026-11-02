"""T:0 integration tests with a MOCKED T:0 (fixtures) and mock Airwallex."""
import importlib
from fastapi.testclient import TestClient
from tripwire import metal
from tripwire.t0 import T0Client

V = "canon_vendor_due"
FIX = {"vendors": {V: {"id": V, "name": "Due Ltd."}}, "cash_usd": 10000.0,
       "gl": {"21100": "gl_ap", "90000": "gl_uncat", "11900": "gl_clearing"},
       "bills": [{"id": "bill_1", "vendor": V, "status": "open", "total": 250.0, "currency": "USD", "bill_number": "INV-1"},
                 {"id": "bill_paid", "vendor": V, "status": "paid", "total": 400.0, "currency": "USD"}]}

def mk(tmp_path, monkeypatch, fix=FIX):
    monkeypatch.setenv("TRIPWIRE_DB", str(tmp_path / "t.db"))
    for k in ("ANTHROPIC_API_KEY", "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "AIRWALLEX_CLIENT_ID", "AIRWALLEX_API_KEY", "T0_API_KEY"): monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("T0_VENDOR_MAP", '{"ben_due": "%s"}' % V)
    import tripwire.app as m; importlib.reload(m); m.metal = metal.MetalClient(mock=True)
    m.t0 = T0Client(mock=True, fixtures=fix); return m, TestClient(m.app)

def pay(c, amt, payee="ben_due", **kw): return c.post("/pay", json={"agent_id": "ap", "amount": amt, "currency": "USD", "payee_id": payee, "payee_country": "GB", **kw}).json()

def test_matching_open_bill_allows_and_books_ap(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch); r = pay(c, 250)
    assert r["final"] == "allow" and r["t0_context"]["matched_bill"]["id"] == "bill_1"
    je = r["execution"]["t0_journal_entry"]; assert je["mock"]
    dr, cr = je["body"]["lines"]; assert dr["gl_account_id"] == "gl_ap" and dr["counterparty_external_id"] == V and cr["gl_account_id"] == "gl_clearing"
    assert dr["amount_atoms"] == cr["amount_atoms"] == 25000

def test_amount_mismatch_holds(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch); r = pay(c, 300)
    assert r["final"] == "hold" and "t0_amount_mismatch" in r["reasons"] and "execution" not in r

def test_no_open_bill_holds_then_approve_books_uncategorized(tmp_path, monkeypatch):
    fix = {**FIX, "bills": [FIX["bills"][1]]}; m, c = mk(tmp_path, monkeypatch, fix); r = pay(c, 400)
    assert r["final"] == "hold" and "t0_no_open_bill" in r["reasons"]
    a = c.post(f"/holds/{r['hold_id']}/approve").json()
    assert a["execution"]["t0_journal_entry"]["body"]["lines"][0]["gl_account_id"] == "gl_uncat"

def test_unknown_vendor_holds(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch); r = pay(c, 250, payee="ben_other")
    assert r["final"] == "hold" and "t0_vendor_unknown" in r["reasons"]

def test_insufficient_cash_denies(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch, {**FIX, "cash_usd": 100.0}); r = pay(c, 250)
    assert r["final"] == "deny" and "t0_insufficient_cash" in r["reasons"]

def test_t0_outage_fails_closed(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch)
    def boom(): raise RuntimeError("down")
    m.t0.cash = boom; r = pay(c, 250)
    assert r["final"] == "hold" and "t0_unavailable" in r["reasons"]

def test_foreign_currency_je_balances_both(tmp_path, monkeypatch):
    t = T0Client(mock=True, fixtures=FIX); t.vendor_map = {"ben_due": V}
    je = t.post_transfer_je({"id": "tr1"}, "ben_due", 25.0, "EUR", 29.0, None)["body"]
    assert je["transaction_currency"] == "EUR" and all(l["transaction_amount_atoms"] == 2500 and l["amount_atoms"] == 2900 for l in je["lines"])

def test_t0_off_without_key_keeps_old_behaviour(tmp_path, monkeypatch):
    m, c = mk(tmp_path, monkeypatch); m.t0 = T0Client(mock=True); r = pay(c, 250, payee="anything")
    assert r["final"] == "allow" and "t0_context" not in r and c.get("/status").json()["t0"] == "off"
