"""Minimal async Airwallex client: login, create transfer, get transfer.

Verified against Airwallex docs (2026-10): POST /api/v1/authentication/login with
x-client-id + x-api-key headers returns a token (30 min) and `expires_at`; later calls
send `Authorization: Bearer <token>`. POST /api/v1/transfers/create requires `reason`,
`reference`, `request_id`, `transfer_currency` plus `beneficiary` or `beneficiary_id`.
`request_id` is rejected as `request_id_duplicate` if reused within 7 days.

Not yet verified: exact sandbox host (see .env.example), token response field name
(`token` assumed), and the transfer-by-request_id lookup used for "query before retry".
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.sandbox.airwallex.com"


class AirwallexError(Exception):
    def __init__(self, status: int, code: str | None, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code


class AirwallexClient:
    def __init__(
        self,
        client_id: str,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        login_as: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._api_key = api_key
        self._login_as = login_as
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30.0, transport=transport)
        self._token: str | None = None
        self._expires_at: datetime = datetime.min.replace(tzinfo=UTC)

    @classmethod
    def from_env(cls) -> AirwallexClient:
        return cls(
            client_id=os.environ["AIRWALLEX_CLIENT_ID"],
            api_key=os.environ["AIRWALLEX_API_KEY"],
            base_url=os.environ.get("AIRWALLEX_BASE_URL") or DEFAULT_BASE_URL,
            login_as=os.environ.get("AIRWALLEX_LOGIN_AS") or None,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _ensure_token(self) -> str:
        # Refresh a minute early; docs say not to log in before every request.
        if self._token and datetime.now(UTC) < self._expires_at - timedelta(minutes=1):
            return self._token
        headers = {"x-client-id": self._client_id, "x-api-key": self._api_key}
        if self._login_as:
            headers["x-login-as"] = self._login_as
        data = await self._send("POST", "/api/v1/authentication/login", headers=headers)
        self._token = data["token"]
        self._expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        if self._expires_at.tzinfo is None:
            self._expires_at = self._expires_at.replace(tzinfo=UTC)
        return self._token

    async def _send(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = await self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {}
            raise AirwallexError(resp.status_code, body.get("code"), body.get("message", resp.text))
        return resp.json()

    async def _authed(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._ensure_token()
        headers = {"Authorization": f"Bearer {token}", **kwargs.pop("headers", {})}
        return await self._send(method, path, headers=headers, **kwargs)

    async def create_transfer(
        self,
        *,
        request_id: str,
        beneficiary_id: str,
        transfer_currency: str,
        transfer_amount: Decimal,
        source_currency: str,
        reason: str,
        reference: str,
    ) -> dict[str, Any]:
        """Create a payout. `request_id` must be the intent's idempotency key.

        On timeout or network failure the outcome is unknown: look the transfer up by
        the same request_id before retrying (never blindly re-POST).
        """
        body = {
            "request_id": request_id,
            "beneficiary_id": beneficiary_id,
            "transfer_currency": transfer_currency,
            "transfer_amount": str(transfer_amount),
            "source_currency": source_currency,
            "reason": reason,
            "reference": reference,
        }
        return await self._authed("POST", "/api/v1/transfers/create", json=body)

    async def get_transfer(self, transfer_id: str) -> dict[str, Any]:
        return await self._authed("GET", f"/api/v1/transfers/{transfer_id}")
