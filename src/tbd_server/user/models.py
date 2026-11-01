"""Strict inputs shared by the API and founder commands."""

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str, Field(min_length=1, max_length=200)]
Revision = Annotated[int, Field(strict=True, ge=0)]


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("A valid email is required")
    return value


def normalize_phone(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"\+?[0-9 ().-]+", value):
        raise ValueError("A US phone is required")
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10:
        digits = "1" + digits
    if not re.fullmatch(r"1[2-9][0-9]{2}[2-9][0-9]{6}", digits):
        raise ValueError("A US phone is required")
    return "+" + digits


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Invitation(Input):
    name: Text
    phone: str
    email: str
    type: Literal["individual", "business"]

    _email = field_validator("email")(normalize_email)
    _phone = field_validator("phone")(normalize_phone)


class Seed(Input):
    seed_key: Text
    name: Text
    phone: str | None = None
    email: str | None = None

    @field_validator("email")
    @classmethod
    def email_value(cls, value):
        return normalize_email(value) if value is not None else None

    @field_validator("phone")
    @classmethod
    def phone_value(cls, value):
        return normalize_phone(value) if value is not None else None


class ProfilePatch(Input):
    revision: Revision
    name: Text


class Address(Input):
    label: Text | None = None
    recipient: Text
    line1: Text
    line2: Text | None = None
    city: Text
    region: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    postal_code: Annotated[str, Field(pattern=r"^[0-9]{5}(-[0-9]{4})?$")]
    country_code: Literal["US"] = "US"


class AddressWrite(Address):
    revision: Revision
    operation: Literal["save", "edit"] = "save"


class DefaultWrite(Input):
    revision: Revision
    address_id: UUID


class AddressDelete(Input):
    revision: Revision
    replacement_address_id: UUID | None = None


class Resolve(Input):
    phone: str
    _phone = field_validator("phone")(normalize_phone)


class UserResponse(BaseModel):
    id: UUID
    name: str
    type: Literal["individual", "business"]
    phone: str | None
    email: str | None
    revision: int
    created_at: datetime


class AddressResponse(Address):
    id: UUID
    user_id: UUID
    is_default: bool


class ProfileResponse(UserResponse):
    addresses: list[AddressResponse]


class DeliveryResponse(BaseModel):
    id: UUID
    outcome: Literal["sent", "already joined"]


class ErrorResponse(BaseModel):
    detail: str


class DeliveryUncertain(ErrorResponse):
    id: UUID
    outcome: Literal["uncertain"] = "uncertain"


class ResolvedUser(BaseModel):
    id: UUID


class HealthResponse(BaseModel):
    status: Literal["ok"]
