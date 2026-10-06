"""Shared API and agent types; product field names match the existing widget."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationInfo, field_validator

AUDIT_SUMMARY_LIMIT = 300


class AuditEntry(BaseModel):
    run_id: str = Field(description="Random ID grouping one chat turn's real agent activity; not a customer or session identifier.")
    timestamp: datetime = Field(description="UTC time of the recorded message/event, or turn start when no event time is exposed.")
    step: int = Field(ge=1, description="Event order within this chat turn.")
    event: Literal["model_request", "model_response", "tool_call", "tool_return", "retry", "run_end"] = Field(description="Kind of recorded agent activity; run_end is the application-observed outcome.")
    tool_name: str | None = Field(default=None, description="Recorded catalogue/output tool name, or null for a non-tool event.")
    tool_call_id: str | None = Field(default=None, description="Recorded call identifier connecting a tool call to its return.")
    args_summary: str | None = Field(default=None, max_length=AUDIT_SUMMARY_LIMIT, description="Truncated summary of allowed product-lookup arguments; excludes credentials and final reply text.")
    result_summary: str | None = Field(default=None, max_length=AUDIT_SUMMARY_LIMIT, description="Truncated product result or event metadata; never full messages, system prompts, or model thinking.")
    stop_reason: Literal["final_output", "timeout", "usage_limit", "cancelled", "error"] = Field(description="Application-observed final outcome of this run, repeated on its entries.")
    model_finish_reason: str | None = Field(default=None, description="Provider finish reason from this recorded model response when available; distinct from the whole-run outcome.")


class ProductSummary(BaseModel):
    id: str = Field(description="The exact catalogue.product_id used in the product page URL.")
    name: str = Field(description="Product name from the catalogue, without invented wording.")
    price: float = Field(description="Current catalogue price in US dollars.")
    short_description: str = Field(description="A short excerpt of the catalogue description for a product card.")
    image_url: str | None = Field(description="Local /images URL, or null when the photo is unavailable.")
    garment_type: str = Field(description="Garment type as stored in the catalogue.")


class ProductMatch(ProductSummary):
    """A catalogue-backed chat card with the same fields as an existing shop card."""


class SizeStock(BaseModel):
    size: str = Field(description="Inventory size label, such as XS, S, M, L, XL, or XXL.")
    quantity: int = Field(description="Current available units for this size; zero means out of stock.")


class ProductDetail(ProductSummary):
    description: str = Field(description="Full visual/product description stored in the catalogue.")
    sizes: list[SizeStock] = Field(description="Inventory counts for this product, ordered by size.")


class CataloguePage(BaseModel):
    status: Literal["matches", "end", "no_search"] = Field(description="Whether more matches were found, all matching items have been shown, or a new search is needed.")
    products: list[ProductMatch] = Field(description="Next unseen matching cards; empty at the end or without an active search.")


class ProductComparison(BaseModel):
    products: list[ProductDetail] = Field(description="Up to two exact products with current prices, recorded descriptions, and inventory; no guessed facts.")
    missing_ids: list[str] = Field(description="Requested IDs not found in the database; never silently replace these.")
    requested_size: str | None = Field(description="Normalized size if requested; otherwise inventory includes all sizes.")
    price_difference: Decimal | None = Field(description="Exact absolute price difference in US dollars, or null unless both products exist.")


class ProductDescription(BaseModel):
    product_id: str = Field(description="Exact catalogue.product_id identifying the product that was found.")
    name: str = Field(description="Stored product name, to confirm which item this description belongs to.")
    description: str | None = Field(description="Full stored description, or null if unavailable; never an invented description.")
    colors: list[str] | None = Field(description="Recorded catalogue color labels; null means unavailable. These are product colors, not color-specific inventory quantities.")


class ProductPrice(BaseModel):
    product_id: str = Field(description="Exact catalogue.product_id identifying the product that was found.")
    name: str = Field(description="Stored product name, to confirm which item this price belongs to.")
    price: Decimal | None = Field(description="Exact stored price in US dollars, represented as a decimal without rounding; null means unavailable, not free.")


class ProductStock(BaseModel):
    product_id: str = Field(description="Exact catalogue.product_id identifying the product that was found.")
    name: str = Field(description="Stored product name, to confirm which item this stock belongs to.")
    requested_size: str | None = Field(description="Normalized size requested by the shopper, or null when returning all recorded sizes.")
    sizes: list[SizeStock] = Field(description="Only the requested size's exact quantity, or all recorded sizes when no size was requested. An empty list means no inventory record; zero quantity means out of stock.")


class PageContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    product_id: str | None = Field(default=None, min_length=1, max_length=200, description="Product ID from the current detail-page route; null on other pages. This is a product reference, never an account identifier.")


class CustomerContext(BaseModel):
    id: int = Field(description="Authenticated users.id, resolved by the server from the session cookie.")
    name: str = Field(description="Authenticated shopper's stored display name.")
    email: str = Field(description="Authenticated shopper's stored email; never supplied by the chat request.")


@dataclass
class ShopDeps:
    """Per-request context; guests explicitly have customer=None."""
    customer: CustomerContext | None = None
    page_product_id: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message: str = Field(min_length=1, max_length=2000, description="The shopper's latest message; history is managed by the server.")
    page_context: PageContext | None = Field(default=None, description="Current product page, if any; never accepted as proof of identity.")


class ChatResponse(BaseModel):
    reply: str = Field(description="A concise, friendly answer to the shopper, grounded in shop information.")
    products: list[ProductMatch] = Field(description="Database-backed product matches rendered as cards; empty when nothing matches or no cards apply.")


class SavedChatMessage(BaseModel):
    id: int = Field(description="Existing chat_messages.id used to order and render saved messages.")
    role: Literal["user", "assistant"] = Field(description="Who authored this saved message.")
    content: str = Field(description="The saved shopper message or final assistant reply.")
    products: list[ProductMatch] = Field(description="Saved product references refreshed from the catalogue; empty for user messages or unavailable products.")
    created_at: str = Field(description="SQLite timestamp for when the message was stored.")


class ChatHistoryResponse(BaseModel):
    messages: list[SavedChatMessage] = Field(description="Only the authenticated shopper's saved messages, in order; empty for guests.")


class AgentReply(ChatResponse):
    """PydanticAI validates full product matches; Python verifies their database source."""
    reply: str = Field(min_length=1, max_length=3000, description="Plain-text reply, usually under 100 words. Quote prices/stock only after checking tools in this turn. State uncertainty honestly.")
    products: list[ProductMatch] = Field(max_length=6, description="Structured product cards, copied from this turn's tools. For catalogue browsing, include all matches from the latest search_catalogue call, even one match. Use [] if no matches or no cards apply. Never replace cards with a prose list or invent fields.")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    email: str = Field(max_length=254, description="Account email address, normalized by the server.")
    password: SecretStr = Field(min_length=1, max_length=128, description="Password to verify; never returned or logged.")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("password")
    @classmethod
    def password_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Enter a password.")
        return value


class SignupRequest(LoginRequest):
    password: SecretStr = Field(min_length=8, max_length=128, description="New password, hashed before storage and never silently trimmed.")
    first_name: str = Field(min_length=1, max_length=80, description="Customer's first name.")
    last_name: str = Field(min_length=1, max_length=80, description="Customer's last name.")
    confirm_password: SecretStr = Field(min_length=8, max_length=128, description="Matching password confirmation, checked and discarded.")

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, value: SecretStr, info: ValidationInfo) -> SecretStr:
        password = info.data.get("password")
        if password is not None and value.get_secret_value() != password.get_secret_value():
            raise ValueError("Passwords do not match.")
        return value


class PublicUser(BaseModel):
    id: int = Field(description="Existing users.id account identifier.")
    name: str = Field(description="Stored display name.")
    email: str = Field(description="Account email address.")
    first_name: str | None = Field(description="First name, if recorded on this existing account.")
    last_name: str | None = Field(description="Last name, if recorded on this existing account.")
    created_at: str = Field(description="Creation timestamp assigned by SQLite.")


class AuthResponse(BaseModel):
    user: PublicUser = Field(description="Signed-in customer's public account fields; excludes credentials.")
