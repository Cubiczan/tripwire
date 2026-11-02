"""T:0 (t0.ai) books client: financial context for the policy gate + journal-entry write-back.

Live when T0_API_KEY is set; otherwise mock (mock data is labeled `mock: True` in every result).
Reads: /v1/vendors, /v1/vendor-bills, /v1/reports/balance-sheet, /v1/gl-accounts. Write: POST /v1/journal-entries.
"""
from __future__ import annotations
import json, os
from datetime import date
import httpx
from .policy import Decision

BASE = os.getenv("T0_BASE_URL", "https://api.t0.ai")
AMOUNT_TOL_PCT = 1.0

class T0Client:
    def __init__(self, mock: bool | None = None, fixtures: dict | None = None):
        self.key = os.getenv("T0_API_KEY")
        self.mock = mock if mock is not None else not self.key
        self.fx = fixtures or {}
        self.vendor_map = json.loads(os.getenv("T0_VENDOR_MAP", "{}"))  # Airwallex beneficiary_id -> T:0 vendor external_id
        self.calls: list[tuple[str, str, int]] = []
        self.posted: list[dict] = []
    def _get(self, path, **params):
        r = httpx.get(f"{BASE}{path}", params=params, headers={"Authorization": f"Bearer {self.key}"}, timeout=20)
        self.calls.append(("GET", path, r.status_code)); r.raise_for_status(); return r.json()

    # ---- reads ----
    def open_bills(self, vendor_id):
        if self.mock: return [b for b in self.fx.get("bills", []) if b["vendor"] == vendor_id and b["status"] == "open"]
        j = self._get("/v1/vendor-bills", status="open", expand="vendor", limit=100)
        out = []
        for b in j["data"]:
            vids = [l["externalId"] for l in b.get("links", []) if l.get("dataType") == "Vendor"]
            if vendor_id in vids:
                d = b["data"]; out.append({"id": b["id"]["externalId"], "vendor": vendor_id, "status": d["status"], "total": d["total"],
                                           "currency": d["currencyCode"], "bill_number": (d.get("customFields") or {}).get("bill_number")})
        return out
    def vendor(self, vendor_id):
        if self.mock: return self.fx.get("vendors", {}).get(vendor_id)
        try: j = self._get(f"/v1/vendors/{vendor_id}"); return {"id": vendor_id, "name": j["data"]["name"]}
        except httpx.HTTPStatusError: return None
    def cash(self):
        """Bank balance (USD) from the T:0 balance-sheet report, today."""
        if self.mock: return self.fx.get("cash_usd", 0.0)
        t = date.today().isoformat(); j = self._get("/v1/reports/balance-sheet", start_date=t[:8] + "01", end_date=t)
        apu = j.get("atoms_per_unit", 100)
        for s in j["sections"]:
            for c in s.get("children", []):
                if c.get("account_id") == "type_group_bank": return list(c["balances"].values())[-1] / apu
        return 0.0
    def gl(self):
        if self.mock: return self.fx.get("gl", {})
        j = self._get("/v1/gl-accounts", postable="true")
        return {a["account_number"]: a["external_id"] for a in j["data"]}

    def context(self, payee_id, amount, currency, amount_usd):
        """Pull T:0 context and turn it into (decision, reason) flags. Fails closed (HOLD) if T:0 is unreachable."""
        ctx = {"source": "mock" if self.mock else "t0-live", "flags": []}
        def flag(d, r): ctx["flags"].append((d, r))
        try:
            vid = self.vendor_map.get(payee_id)
            v = self.vendor(vid) if vid else None
            ctx["vendor"] = v
            ctx["cash_usd"] = cash = self.cash()
            if amount_usd > cash: flag(Decision.DENY, "t0_insufficient_cash")
            elif cash - amount_usd < float(os.getenv("T0_MIN_CASH_BUFFER", "0")): flag(Decision.HOLD, "t0_cash_buffer")
            if not v: flag(Decision.HOLD, "t0_vendor_unknown"); return ctx
            bills = self.open_bills(vid); ctx["open_bills"] = bills
            if not bills: flag(Decision.HOLD, "t0_no_open_bill"); return ctx
            def amt(b): return amount if b["currency"].upper() == currency.upper() else amount_usd
            match = next((b for b in bills if abs(amt(b) - b["total"]) <= b["total"] * AMOUNT_TOL_PCT / 100), None)
            if match: ctx["matched_bill"] = match
            else: flag(Decision.HOLD, "t0_amount_mismatch")
        except Exception as e:
            ctx["error"] = f"{type(e).__name__}: {getattr(getattr(e, 'response', None), 'status_code', '') or e}"
            flag(Decision.HOLD, "t0_unavailable")
        return ctx

    # ---- write-back ----
    def post_transfer_je(self, transfer: dict, payee_id: str, amount: float, currency: str, amount_usd: float, matched_bill: dict | None):
        """Book the executed Airwallex transfer. Dr Accounts Payable (vendor) if a bill matched, else Dr Uncategorized; Cr Transfer Clearing."""
        gl = self.gl(); vid = self.vendor_map.get(payee_id)
        dr = {"gl_account_id": gl["21100"], "counterparty_type": "vendor", "counterparty_external_id": vid} if matched_bill and vid else {"gl_account_id": gl["90000"]}
        usd = round(amount_usd * 100); txn = round(amount * 100); foreign = currency.upper() != "USD"
        memo = f"Tripwire: Airwallex transfer {transfer.get('id')} {amount:.2f} {currency} to {payee_id}" + (f" for bill {matched_bill['id']}" if matched_bill else " (approved by human, no matching bill)")
        lines = [{**dr, "line_type": "debit", "amount_atoms": usd, "memo": memo}, {"gl_account_id": gl["11900"], "line_type": "credit", "amount_atoms": usd, "memo": memo}]
        if foreign:
            for l in lines: l["transaction_amount_atoms"] = txn
        body = {"entry_date": date.today().isoformat(), "currency": "USD", "memo": memo[:1000], "manual_entry_reason": "Posted by Tripwire after Airwallex execution", "lines": lines}
        if foreign: body["transaction_currency"] = currency.upper()
        if self.mock:
            je = {"id": f"mock_je_{len(self.posted)+1}", "mock": True, "body": body}; self.posted.append(je); return je
        r = httpx.post(f"{BASE}/v1/journal-entries", json=body, timeout=20,
                       headers={"Authorization": f"Bearer {self.key}", "X-Idempotency-Key": f"tripwire-{transfer.get('id')}"})
        self.calls.append(("POST", "/v1/journal-entries", r.status_code))
        try: j = r.json()
        except Exception: j = {}
        return {"status_code": r.status_code, "id": j.get("id"), "je_number": j.get("je_number"), "mock": False,
                **({"error": j.get("error_code") or j.get("public_message") or str(j)[:200]} if r.status_code >= 400 else {})}
