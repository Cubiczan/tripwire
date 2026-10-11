from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from tripwire.airwallex import AirwallexClient, AirwallexError


def make_client(calls: list[httpx.Request], transfer_status: int = 201) -> AirwallexClient:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if req.url.path == "/api/v1/authentication/login":
            return httpx.Response(
                201, json={"token": "tok-1", "expires_at": "2099-01-01T00:00:00+0000"}
            )
        if req.url.path == "/api/v1/transfers/create":
            if transfer_status >= 400:
                return httpx.Response(
                    transfer_status, json={"code": "request_id_duplicate", "message": "dup"}
                )
            return httpx.Response(201, json={"id": "tr-1", "status": "PROCESSING"})
        return httpx.Response(404, json={"code": "not_found", "message": "nope"})

    return AirwallexClient("cid", "key", transport=httpx.MockTransport(handler))


async def test_login_headers_and_token_reuse() -> None:
    calls: list[httpx.Request] = []
    c = make_client(calls)
    for i in range(2):
        await c.create_transfer(
            request_id=f"r{i}",
            beneficiary_id="b1",
            transfer_currency="USD",
            transfer_amount=Decimal("10.50"),
            source_currency="USD",
            reason="supplier",
            reference="INV-1",
        )
    logins = [r for r in calls if r.url.path.endswith("/login")]
    assert len(logins) == 1  # token cached, not re-fetched
    assert logins[0].headers["x-client-id"] == "cid"
    assert logins[0].headers["x-api-key"] == "key"
    create = [r for r in calls if r.url.path.endswith("/transfers/create")][0]
    assert create.headers["authorization"] == "Bearer tok-1"
    body = json.loads(create.content)
    assert body["request_id"] == "r0"
    assert body["transfer_amount"] == "10.50"


async def test_error_mapped() -> None:
    c = make_client([], transfer_status=400)
    with pytest.raises(AirwallexError) as ei:
        await c.create_transfer(
            request_id="r",
            beneficiary_id="b",
            transfer_currency="USD",
            transfer_amount=Decimal("1"),
            source_currency="USD",
            reason="x",
            reference="y",
        )
    assert ei.value.code == "request_id_duplicate"
