"""Read-only catalogue helpers and plain, typed functions exposed to the agent."""
from contextlib import closing
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import Decimal
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from threading import Lock
from urllib.parse import quote
from pydantic import BaseModel, TypeAdapter
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart

if __package__:
    from backend.models import AUDIT_SUMMARY_LIMIT, AuditEntry
    from backend.models import CataloguePage, ProductComparison, ChatResponse, ProductDescription, ProductDetail, ProductMatch, ProductPrice, ProductStock, ProductSummary, SavedChatMessage, SizeStock
else:
    from models import AUDIT_SUMMARY_LIMIT, AuditEntry
    from models import CataloguePage, ProductComparison, ChatResponse, ProductDescription, ProductDetail, ProductMatch, ProductPrice, ProductStock, ProductSummary, SavedChatMessage, SizeStock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "campus_customs.db"
PRODUCTS_DIR = DATA_DIR / "products"
SIZE_ORDER = {size: i for i, size in enumerate(("XS", "S", "M", "L", "XL", "XXL"))}
PRODUCT_COLUMNS = "product_id, name, price, description, image_file_path, garment_type"
MAX_RESULTS = 6
AUDIT_PATH = PROJECT_ROOT / "output" / "audit_trail.json"
_AUDIT_LOCK = Lock()
_AUDIT_ADAPTER = TypeAdapter(list[AuditEntry])
_AUDIT_TOOLS = {"search_products", "search_catalogue", "get_product_description", "get_product_price",
                "get_product_stock", "show_more_products", "compare_products"}
_AUDIT_FIELDS = {"id", "product_id", "first_product_id", "second_product_id", "name", "price", "size",
                 "quantity", "requested_size", "sizes", "colors", "status", "missing_ids", "price_difference",
                 "products", "query", "category", "keyword", "max_price", "limit"}


class AuditLogError(RuntimeError):
    """An audit append could not be safely committed; never reset the existing file."""


def _audit_text(value: str) -> str:
    """Redact common credential/email patterns before truncating public product text."""
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email omitted]", value)
    value = re.sub(r"(?i)\b(?:bearer\s+\S+|sk-[A-Za-z0-9_-]+)", "[credential omitted]", value)
    value = re.sub(r"(?i)\b(?:password|secret|api[_ -]?key|token)\s*[:=]\s*\S+", "[credential omitted]", value)
    return value if len(value) <= 100 else value[:99] + "…"


def _audit_preview(value: object, depth: int = 0) -> object:
    """Keep a small allowlist of public product fields, never arbitrary payloads."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if depth > 4:
        return "[nested data omitted]"
    if isinstance(value, dict):
        result = {key: _audit_preview(item, depth + 1) for key, item in value.items() if key in _AUDIT_FIELDS}
        if "description" in value:
            result["description"] = "present (text omitted)" if value["description"] else "unavailable"
        if "reply" in value:
            result["reply"] = "[text omitted]"
        return result
    if isinstance(value, (list, tuple)):
        return [_audit_preview(item, depth + 1) for item in value[:3]] + ([f"… {len(value)-3} more"] if len(value) > 3 else [])
    if isinstance(value, str):
        return _audit_text(value)
    if isinstance(value, float) and not math.isfinite(value):
        return "[non-finite number omitted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    return "[unsupported data omitted]"


def _audit_summary(value: object) -> str:
    text = json.dumps(_audit_preview(value), ensure_ascii=False, allow_nan=False)
    return text if len(text) <= AUDIT_SUMMARY_LIMIT else text[:AUDIT_SUMMARY_LIMIT - 1] + "…"


def audit_entries(messages: list[ModelMessage], run_id: str, stop_reason: str,
                  started_at: datetime, error_type: str | None = None) -> list[AuditEntry]:
    """Summarize this turn's actual PydanticAI messages; never ask the model to audit itself."""
    entries: list[AuditEntry] = []

    def add(event: str, timestamp: datetime, **fields: object) -> None:
        entries.append(AuditEntry(run_id=run_id, timestamp=timestamp.astimezone(timezone.utc),
                                  step=len(entries) + 1, event=event, stop_reason=stop_reason, **fields))

    for message in messages:
        timestamp = message.timestamp or started_at
        counts = dict(Counter(part.part_kind for part in message.parts))
        # Metadata only: no customer prompts, instructions, assistant prose, or thinking parts.
        metadata = json.dumps({"parts": counts, "state": message.state})[:AUDIT_SUMMARY_LIMIT]
        if isinstance(message, ModelResponse):
            add("model_response", timestamp, result_summary=metadata, model_finish_reason=message.finish_reason)
        elif isinstance(message, ModelRequest):
            add("model_request", timestamp, result_summary=metadata)
        for part in message.parts:
            if not isinstance(part, (ToolCallPart, ToolReturnPart, RetryPromptPart)):
                continue
            name = part.tool_name if part.tool_name in _AUDIT_TOOLS | {"final_result"} else ("unrecognized_tool" if part.tool_name else None)
            fields = {"tool_name": name, "tool_call_id": _audit_text(part.tool_call_id)}
            if isinstance(part, ToolCallPart):
                try:
                    args = part.args_as_dict()
                    summary = _audit_summary(args) if name in _AUDIT_TOOLS else "Output or unrecognized tool arguments omitted."
                except (ValueError, TypeError):
                    summary = "Malformed tool arguments omitted."
                add("tool_call", timestamp, args_summary=summary, **fields)
            elif isinstance(part, ToolReturnPart):
                summary = _audit_summary(part.content) if name in _AUDIT_TOOLS and not isinstance(part.content, str) else "Tool returned text; content omitted."
                add("tool_return", part.timestamp, result_summary=summary, **fields)
            else:
                add("retry", part.timestamp, result_summary="Validation/tool retry requested; raw feedback omitted.", **fields)
    end = "Chat turn completed." if stop_reason == "final_output" else f"Chat turn ended: {error_type or stop_reason}."
    add("run_end", datetime.now(timezone.utc), result_summary=end)
    return entries


def append_audit_entries(entries: list[AuditEntry]) -> None:
    """Atomically append a completed/failed run while preserving every existing entry.

    A thread lock covers concurrent requests in this single-process homework app.
    Invalid existing JSON is an error, never permission to replace history.
    """
    with _AUDIT_LOCK:
        temporary: str | None = None
        try:
            AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                existing = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            except FileNotFoundError:
                existing = []
            _AUDIT_ADAPTER.validate_python(existing)
            combined = existing + [entry.model_dump(mode="json") for entry in entries]
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=AUDIT_PATH.parent,
                                             prefix=".audit-", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                json.dump(combined, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, AUDIT_PATH)
        except (OSError, ValueError, TypeError) as exc:
            raise AuditLogError("The audit history could not be safely appended.") from exc
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
# Per-run mutable set follows async/thread tool execution without mixing users.
# Plain tool signatures remain simple: the model never receives this state.
SEEN_PRODUCTS: ContextVar[set[str] | None] = ContextVar("seen_products", default=None)


@dataclass
class BrowseCursor:
    """Server-owned search constraints and IDs already shown in this conversation."""
    filters: dict = field(default_factory=dict)
    shown: set[str] = field(default_factory=set)


@dataclass
class CatalogueSearchState:
    """Per-turn state shared with worker-thread tools; None means no browse call."""
    matches: list[ProductMatch] | None = None
    cursor: BrowseCursor | None = None


CATALOGUE_SEARCH: ContextVar[CatalogueSearchState | None] = ContextVar("catalogue_search", default=None)


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    """Open the supplied SQLite file read-only; never create a missing database."""
    connection = sqlite3.connect((path or DB_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def image_url(stored_path: str) -> str | None:
    """Turn an existing path inside data/products into a safe public image URL."""
    path = Path(stored_path.replace("\\", "/"))
    if path.is_absolute():
        candidate = path
    elif path.parts[:2] == ("data", "products"):
        candidate = PROJECT_ROOT / path
    elif path.parts[:1] == ("products",):
        candidate = DATA_DIR / path
    else:
        candidate = PRODUCTS_DIR / path
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(PRODUCTS_DIR.resolve())
        if not resolved.is_file():
            return None
    except (OSError, ValueError, RuntimeError):
        return None
    return "/images/" + quote(relative.as_posix(), safe="/")


def product_summary(row: sqlite3.Row) -> ProductSummary:
    """Map existing database column names into the unchanged frontend contract."""
    description = " ".join(row["description"].split())
    short = description if len(description) <= 155 else description[:152].rsplit(" ", 1)[0] + "…"
    return ProductSummary(
        id=row["product_id"], name=row["name"], price=row["price"],
        short_description=short, image_url=image_url(row["image_file_path"]),
        garment_type=row["garment_type"],
    )


def list_catalogue(path: Path | None = None, category: str | None = None,
                   max_price: float | None = None, size: str | None = None) -> list[ProductSummary]:
    """Return the complete catalogue for the shop grid, not for the model context."""
    conditions, params = [], []
    if category:
        conditions.append("instr(lower(garment_type), ?) > 0")
        params.append(category.strip().lower())
    if max_price is not None:
        conditions.append("price <= ?")
        params.append(max_price)
    if size:
        conditions.append("EXISTS (SELECT 1 FROM inventory WHERE inventory.product_id = catalogue.product_id AND upper(size) = ? AND quantity > 0)")
        params.append(size.strip().upper())
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with closing(connect_db(path)) as connection:
        rows = connection.execute("SELECT " + PRODUCT_COLUMNS + " FROM catalogue" + where + " ORDER BY name COLLATE NOCASE, product_id", params).fetchall()
    return [product_summary(row) for row in rows]


def read_product(product_id: str, path: Path | None = None) -> ProductDetail | None:
    """Read one product and its stock in one consistent transaction."""
    with closing(connect_db(path)) as connection:
        connection.execute("BEGIN")
        row = connection.execute("SELECT " + PRODUCT_COLUMNS + " FROM catalogue WHERE product_id = ?", (product_id,)).fetchone()
        if row is None:
            return None
        stock = connection.execute("SELECT size, quantity FROM inventory WHERE product_id = ?", (product_id,)).fetchall()
    sizes = sorted(stock, key=lambda item: (SIZE_ORDER.get(item["size"], 99), item["size"]))
    return ProductDetail(**product_summary(row).model_dump(), description=row["description"],
                         sizes=[SizeStock(size=item["size"], quantity=item["quantity"]) for item in sizes])


def _remember_product(product_id: str) -> None:
    """Allow a successfully retrieved product to appear in this turn's cards."""
    seen = SEEN_PRODUCTS.get()
    if seen is not None:
        seen.add(product_id)


def get_product(product_id: str) -> ProductDetail | None:
    """Internal helper for full search results; not registered as an agent tool."""
    product = read_product(product_id)
    if product is not None:
        _remember_product(product.id)
    return product


def get_product_description(product_id: str) -> ProductDescription | None:
    """Read the full recorded description for one exact catalogue product ID.

    Call for any question about a product's description, appearance, or features.
    Resolve the ID with search_products or the conversation first; do not guess
    an ID or substitute a similarly named item. Returns null if the product is
    not found. A found product with description=null has no usable description.
    """
    with closing(connect_db()) as connection:
        row = connection.execute(
            "SELECT product_id, name, description, colors FROM catalogue WHERE product_id = ?", (product_id,),
        ).fetchone()
    if row is None:
        return None
    _remember_product(row["product_id"])
    description = row["description"]
    try:
        colors = json.loads(row["colors"])
        if not isinstance(colors, list) or not all(isinstance(value, str) for value in colors):
            colors = None
    except (ValueError, TypeError):
        colors = None
    return ProductDescription(product_id=row["product_id"], name=row["name"],
                              description=description if description and description.strip() else None,
                              colors=colors or None)


def get_product_price(product_id: str) -> ProductPrice | None:
    """Read the exact stored US-dollar price for one exact catalogue product ID.

    Call for any price question, including follow-ups; never reuse an old price.
    Resolve the ID with search_products or the conversation first. Return the
    decimal price without rounding, estimating, or inventing a sale price. Null
    return means product not found; price=null means the product has no price.
    """
    with closing(connect_db()) as connection:
        row = connection.execute(
            "SELECT product_id, name, price FROM catalogue WHERE product_id = ?", (product_id,),
        ).fetchone()
    if row is None:
        return None
    _remember_product(row["product_id"])
    return ProductPrice(product_id=row["product_id"], name=row["name"],
                        price=Decimal(str(row["price"])) if row["price"] is not None else None)


def get_product_stock(product_id: str, size: str | None = None) -> ProductStock | None:
    """Read exact stock quantities for one product, optionally limited to one size.

    Resolve the exact ID with search_products or the conversation first. Pass
    size when asked about a specific size (XS, S, M, L, XL, XXL); omit it for all
    recorded sizes. Letter case and surrounding spaces are ignored. Null return
    means product not found. An empty sizes list means no inventory record for
    the requested size/product, NOT zero stock. Quantity 0 means out of stock;
    a positive quantity is the exact available count. Never estimate quantities.
    """
    requested_size = size.strip().upper() if size is not None else None
    with closing(connect_db()) as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT product_id, name FROM catalogue WHERE product_id = ?", (product_id,),
        ).fetchone()
        if row is None:
            return None
        if requested_size is None:
            stock = connection.execute(
                "SELECT size, quantity FROM inventory WHERE product_id = ?", (product_id,),
            ).fetchall()
        else:
            stock = connection.execute(
                "SELECT size, quantity FROM inventory WHERE product_id = ? AND upper(size) = ?",
                (product_id, requested_size),
            ).fetchall()
    _remember_product(row["product_id"])
    ordered = sorted(stock, key=lambda item: (SIZE_ORDER.get(item["size"], 99), item["size"]))
    return ProductStock(product_id=row["product_id"], name=row["name"], requested_size=requested_size,
                        sizes=[SizeStock(size=item["size"], quantity=item["quantity"]) for item in ordered])


def search_products(query: str = "", max_price: float | None = None,
                    size: str | None = None, limit: int = 6,
                    category: str | None = None) -> list[ProductDetail]:
    """Resolve a named product before a focused description, price, or stock lookup.

    For browse questions such as 'what hoodies do you have?', use search_catalogue
    instead so all matches are rendered as cards.
    Use concise product words such as 'navy hoodie' or 'baseball', not a whole
    sentence. max_price is an inclusive dollar ceiling. size restricts results
    to a size with quantity above zero (XS, S, M, L, XL, XXL). An empty query
    browses the catalogue. Returns at most six products with current prices,
    full descriptions, and all size quantities; [] means no matching products.
    """
    return _search_products(query, max_price, size, limit, category)


def _search_products(query: str = "", max_price: float | None = None,
                     size: str | None = None, limit: int = 6,
                     category: str | None = None, exclude: set[str] | None = None) -> list[ProductDetail]:
    """Shared SQL implementation; excluded IDs come only from server-owned state."""
    if max_price is not None and (not math.isfinite(max_price) or max_price < 0):
        return []
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())))[:10]
    plural = {"hoodies": "hoodie", "shirts": "shirt", "sweatshirts": "sweatshirt", "jackets": "jacket"}
    terms = [plural.get(term, term) for term in terms if term not in {"a", "an", "the", "please", "show", "me"}]
    conditions = []
    params: list[str | float | int] = []
    if category is not None:
        category_terms = re.findall(r"[a-z0-9]+", category.lower())
        if not category_terms:
            return []
        plural_categories = {**plural, "hats": "hat", "sweaters": "sweater"}
        for term in category_terms:
            term = plural_categories.get(term, term)
            # Both 'hoodie' and 'hooded sweatshirt' occur in the supplied data.
            term = "hood" if term in {"hoodie", "hooded"} else term
            conditions.append("lower(garment_type) LIKE ?")
            params.append("%" + term + "%")
    for term in terms:
        conditions.append("lower(name || ' ' || garment_type || ' ' || description || ' ' || colors || ' ' || search_tags || ' ' || product_id) LIKE ?")
        params.append("%" + term + "%")
    if max_price is not None:
        conditions.append("price <= ?")
        params.append(max_price)
    if size:
        conditions.append("EXISTS (SELECT 1 FROM inventory WHERE inventory.product_id = catalogue.product_id AND upper(inventory.size) = ? AND inventory.quantity > 0)")
        params.append(size.strip().upper())
    if exclude:
        conditions.append("product_id NOT IN (" + ",".join("?" for _ in exclude) + ")")
        params.extend(sorted(exclude))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(max(1, min(limit, MAX_RESULTS)))
    with closing(connect_db()) as connection:
        rows = connection.execute("SELECT product_id FROM catalogue" + where + " ORDER BY price, name, product_id LIMIT ?", params).fetchall()
    return [product for row in rows if (product := get_product(row["product_id"])) is not None]


def search_catalogue(category: str | None = None, keyword: str = "",
                     max_price: float | None = None, size: str | None = None,
                     limit: int = 6) -> list[ProductMatch]:
    """Find cards to display for 'what do you have', browse, or recommendation requests.

    category matches garment_type (e.g. 'hoodies', 'bomber jacket'); keyword
    matches product names, descriptions, colors, and search tags. If both are
    supplied, both must match. Optional max_price is an inclusive USD ceiling;
    optional size requires positive stock in that size. Returns at most six
    database-backed cards, not necessarily the full catalogue. Copy ALL returned
    cards to the final products list; introduce them briefly instead of listing
    products in prose. [] means no matches: return products=[] and say so, without
    silently broadening the search or inventing alternatives.
    """
    found = search_products(query=keyword, category=category, max_price=max_price, size=size, limit=limit)
    matches = [ProductMatch.model_validate(product.model_dump()) for product in found]
    state = CATALOGUE_SEARCH.get()
    if state is not None:
        state.matches = matches
        state.cursor = BrowseCursor(
            filters=dict(query=keyword, category=category, max_price=max_price, size=size, limit=limit),
            shown={item.id for item in matches},
        )
    return matches


def show_more_products() -> CataloguePage:
    """Continue the shopper's latest browse search without repeating products.

    Call for 'show more', 'next', or 'other matches'. The server retains the exact
    category, keywords, budget, and size; do not start search_catalogue again.
    Copy every returned product to the final cards. status=end means all matches
    were shown; no_search means ask the shopper to start a new browse search.
    Never broaden constraints or claim unshown stock when there are no results.
    """
    state = CATALOGUE_SEARCH.get()
    if state is None or state.cursor is None:
        if state is not None:
            state.matches = []
        return CataloguePage(status="no_search", products=[])
    found = _search_products(**state.cursor.filters, exclude=state.cursor.shown)
    matches = [ProductMatch.model_validate(item.model_dump()) for item in found]
    state.cursor.shown.update(item.id for item in matches)
    state.matches = matches
    return CataloguePage(status="matches" if matches else "end", products=matches)


def compare_products(first_product_id: str, second_product_id: str,
                     size: str | None = None) -> ProductComparison:
    """Compare two EXACT products using current database facts in one tool call.

    Resolve each full name with search_products, or use exact IDs from shown cards
    or page context. Ask which items if unclear. Pass size for a size-specific
    comparison; omit for all sizes. This replaces separate price, description,
    and stock calls for comparisons. Copy all found items to final product cards.
    Explain only recorded differences; never invent fit, quality or material.
    An absent size record is unknown; quantity zero is out of stock. Missing IDs
    are explicit and must not be silently replaced. Price difference is computed
    in Python, not guessed. The same ID twice compares one unique product only.
    """
    requested_size = size.strip().upper() if size else None
    products, missing = [], []
    # An outer read transaction gives both items one consistent DB snapshot.
    with closing(connect_db()) as connection:
        connection.execute("BEGIN")
        for product_id in dict.fromkeys([first_product_id, second_product_id]):
            row = connection.execute("SELECT " + PRODUCT_COLUMNS + " FROM catalogue WHERE product_id = ?", (product_id,)).fetchone()
            if row is None:
                missing.append(product_id)
                continue
            sql = "SELECT size, quantity FROM inventory WHERE product_id = ?"
            params = [product_id]
            if requested_size:
                sql += " AND upper(size) = ?"
                params.append(requested_size)
            stock = connection.execute(sql, params).fetchall()
            sizes = sorted(stock, key=lambda item: (SIZE_ORDER.get(item["size"], 99), item["size"]))
            products.append(ProductDetail(**product_summary(row).model_dump(), description=row["description"],
                                          sizes=[SizeStock(**dict(item)) for item in sizes]))
            _remember_product(product_id)
    difference = abs(Decimal(str(products[0].price)) - Decimal(str(products[1].price))) if len(products) == 2 else None
    state = CATALOGUE_SEARCH.get()
    if state is not None:
        state.matches = [ProductMatch.model_validate(item.model_dump()) for item in products]
    return ProductComparison(products=products, missing_ids=missing,
                             requested_size=requested_size, price_difference=difference)


def load_chat_history(user_id: int, path: Path | None = None, limit: int | None = None) -> list[SavedChatMessage]:
    """Read only this server-authenticated user's messages; not an agent tool."""
    with closing(connect_db(path)) as connection:
        if limit is None:
            rows = connection.execute(
                "SELECT id, role, content, products_json, created_at FROM chat_messages "
                "WHERE user_id = ? AND role IN ('user', 'assistant') ORDER BY id", (user_id,),
            ).fetchall()
        else:
            rows = list(reversed(connection.execute(
                "SELECT id, role, content, products_json, created_at FROM chat_messages "
                "WHERE user_id = ? AND role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?", (user_id, limit),
            ).fetchall()))
    messages = []
    for row in rows:
        # Older supplied rows use product_id; current cards use id. Refresh
        # either format from SQLite, never trust historical image URLs/prices.
        products = []
        try:
            saved = json.loads(row["products_json"] or "[]")
        except (ValueError, TypeError):
            saved = []
        seen = set()
        for item in saved[:MAX_RESULTS] if isinstance(saved, list) else []:
            product_id = item.get("id", item.get("product_id")) if isinstance(item, dict) else item
            if not isinstance(product_id, str) or product_id in seen:
                continue
            product = read_product(product_id, path)
            if product is not None:
                products.append(ProductMatch.model_validate(product.model_dump()))
                seen.add(product_id)
        messages.append(SavedChatMessage(id=row["id"], role=row["role"], content=row["content"],
                                         products=products, created_at=row["created_at"]))
    return messages


def save_chat_turn(user_id: int, message: str, result: ChatResponse, path: Path | None = None) -> None:
    """Atomically save a successful exchange for the authenticated user only."""
    with closing(sqlite3.connect((path or DB_PATH).resolve().as_uri() + "?mode=rw", uri=True, timeout=5)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO chat_messages (user_id, role, content, products_json) VALUES (?, ?, ?, ?)",
            (user_id, "user", message, None),
        )
        connection.execute(
            "INSERT INTO chat_messages (user_id, role, content, products_json) VALUES (?, ?, ?, ?)",
            (user_id, "assistant", result.reply, json.dumps([p.model_dump(mode="json") for p in result.products])),
        )
