"""Live end-to-end: Airwallex sandbox + T:0 sandbox through Tripwire (in-process FastAPI TestClient). Prints JSON; never prints secrets."""
import json, os, sys
os.environ.setdefault("T0_VENDOR_MAP", json.dumps({"68460101-9070-454c-9fc6-12388b475447": "canon_vendor_prod_ZhvCz6AhYlcKcdfk"}))  # sandbox EUR beneficiary -> T:0 vendor "Due Ltd." (demo mapping)
os.environ["TRIPWIRE_DB"] = "/tmp/tripwire_t0_demo.db"
from fastapi.testclient import TestClient
import tripwire.app as m
c = TestClient(m.app)
def show(title, r):
    print(f"\n### {title}"); print(json.dumps(r, indent=1, default=str)[:2500])
show("status", c.get("/status").json())
BEN = "68460101-9070-454c-9fc6-12388b475447"
r1 = c.post("/pay", json={"agent_id": "ap-agent", "amount": 25, "currency": "EUR", "payee_id": BEN, "payee_country": "GB", "memo": "Due Ltd. services"}).json()
show("1. EUR 25 to Due Ltd. (T:0 vendor) - T:0 shows no OPEN bill", r1)
if r1.get("hold_id"):
    show("2. CFO approves hold -> Airwallex executes -> journal entry POST to T:0 attempted", c.post(f"/holds/{r1['hold_id']}/approve", params={"approver": "cfo"}).json())
show("3. EUR 25 to a beneficiary not mapped to any T:0 vendor", c.post("/pay", json={"agent_id": "ap-agent", "amount": 25, "currency": "EUR", "payee_id": "0f5a34e8-6427-42e7-a786-202ce87a6ee3", "payee_country": "GB"}).json())
show("4. USD 50 card - T:0 cash check only", c.post("/pay", json={"agent_id": "ap-agent", "kind": "card", "amount": 50, "currency": "USD", "payee_id": "saas"}).json())
show("T:0 calls (method, path, HTTP status)", c.get("/t0/calls").json())
