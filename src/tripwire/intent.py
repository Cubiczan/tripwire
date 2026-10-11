"""Typed, signed payment intents. Agents emit these; they never call Airwallex."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class IntentKind(StrEnum):
    TRANSFER = "transfer"
    CARD_PURCHASE = "card_purchase"
    FX_CONVERSION = "fx_conversion"


class PaymentIntent(BaseModel, frozen=True):
    intent_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_id: str
    kind: IntentKind = IntentKind.TRANSFER
    payee_id: str
    payee_name: str
    # Hash of the payee's bank details as presented in the evidence (e.g. invoice).
    bank_details_fingerprint: str
    amount: Decimal = Field(gt=0)
    currency: str
    purpose: str
    evidence_urls: tuple[str, ...] = ()
    # FX only: rate the agent was quoted vs an independent reference rate.
    quoted_rate: Decimal | None = Field(default=None, gt=0)
    reference_rate: Decimal | None = Field(default=None, gt=0)
    # Doubles as the Airwallex `request_id` (unique for 7 days per their docs).
    idempotency_key: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    signature: str = ""

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v: str) -> str:
        if len(v) != 3 or not v.isalpha() or not v.isupper():
            raise ValueError("currency must be a 3-letter uppercase ISO-4217 code")
        return v

    def canonical_bytes(self) -> bytes:
        """Stable serialisation of every field except the signature."""
        body = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()

    def signed(self, secret: bytes) -> PaymentIntent:
        return self.model_copy(update={"signature": _mac(secret, self.canonical_bytes())})

    def verify(self, secret: bytes) -> bool:
        if not self.signature:
            return False
        return hmac.compare_digest(self.signature, _mac(secret, self.canonical_bytes()))


def _mac(secret: bytes, data: bytes) -> str:
    return hmac.new(secret, data, hashlib.sha256).hexdigest()
