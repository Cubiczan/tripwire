"""Airwallex client wrapper. Mock mode unless AIRWALLEX_CLIENT_ID/API_KEY are set."""
import os, uuid, httpx
BASE = os.getenv("AIRWALLEX_BASE_URL", "https://api-demo.airwallex.com")

class AirwallexClient:
    def __init__(self, mock: bool | None = None):
        self.cid, self.key = os.getenv("AIRWALLEX_CLIENT_ID"), os.getenv("AIRWALLEX_API_KEY")
        self.mock = mock if mock is not None else not (self.cid and self.key)
        self._token = None; self.ledger = []
    def _auth(self):
        if not self._token:
            r = httpx.post(f"{BASE}/api/v1/authentication/login", headers={"x-client-id": self.cid, "x-api-key": self.key}); r.raise_for_status()
            self._token = r.json()["token"]
        return {"Authorization": f"Bearer {self._token}"}
    def fx_quote(self, sell, buy, amount):
        if self.mock:
            mid = {"USDGBP": 0.79, "USDAUD": 1.52, "USDSGD": 1.35}.get(sell + buy, 1.0)
            return {"rate": mid, "mid": mid, "mock": True}
        r = httpx.get(f"{BASE}/api/v1/fx/rates/current", params={"sell_currency": sell, "buy_currency": buy, "sell_amount": amount}, headers=self._auth()); r.raise_for_status()
        j = r.json(); return {"rate": j.get("client_rate"), "mid": j.get("mid_rate", j.get("client_rate")), "mock": False}
    def create_transfer(self, beneficiary_id, amount, currency, reason="agent payment"):
        if self.mock:
            t = {"id": f"mock_tr_{uuid.uuid4().hex[:10]}", "status": "SUBMITTED", "amount": amount, "currency": currency, "mock": True}
            self.ledger.append(t); return t
        body = {"beneficiary_id": beneficiary_id, "transfer_amount": amount, "transfer_currency": currency, "source_currency": currency,
                "reason": reason, "reference": "tripwire", "request_id": str(uuid.uuid4()), "transfer_method": "LOCAL"}
        r = httpx.post(f"{BASE}/api/v1/transfers/create", json=body, headers=self._auth()); r.raise_for_status(); return r.json()
    def issue_card_simulated(self, agent_id, limit):  # Kit 02: sandbox issuing down -> always simulated
        return {"id": f"sim_card_{uuid.uuid4().hex[:8]}", "agent_id": agent_id, "limit": limit, "simulated": True}
