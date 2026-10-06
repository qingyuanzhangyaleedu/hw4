"""Run from backend/: uvicorn main:app --reload --port 8000."""

from contextlib import asynccontextmanager, closing
import asyncio
import logging
import secrets
import sqlite3
from threading import Lock
from time import monotonic

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAIError
from pydantic_ai.exceptions import UsageLimitExceeded, UnexpectedModelBehavior

# Supports the required backend/ command and existing root-level test imports.
# No package-relative imports or sys.path modifications are needed.
if __package__:
    from backend import agent as shop_agent, tools
    from backend.models import (AuthResponse, ChatHistoryResponse, ChatRequest, ChatResponse, CustomerContext, LoginRequest,
                                ProductDetail, ProductSummary, PublicUser, SignupRequest)
else:
    import agent as shop_agent
    import tools
    from models import (AuthResponse, ChatHistoryResponse, ChatRequest, ChatResponse, CustomerContext, LoginRequest,
                        ProductDetail, ProductSummary, PublicUser, SignupRequest)

PROJECT_ROOT = tools.PROJECT_ROOT
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "campus_customs.db"
PRODUCTS_DIR = DATA_DIR / "products"
PASSWORD_HASHER = PasswordHasher(
    type=Type.ID, memory_cost=65536, time_cost=3, parallelism=4,
    salt_len=16, hash_len=32,
)
# The supplied three-part PBKDF2 format omits its work factor. This value was
# verified against the seed account BEFORE making any database changes.
LEGACY_PBKDF2_ROUNDS = 120_000
DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
SESSION_COOKIE = "campus_customs_session"
SESSION_SECONDS = 8 * 60 * 60
SESSIONS: dict[str, tuple[int, float]] = {}
SESSION_LOCK = Lock()
AUTH_ORIGINS = {
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8000", "http://127.0.0.1:8000",
}
USER_FIELDS = "id, name, email, first_name, last_name, created_at"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        shop_agent.get_agent()  # Read the prompt and configure the provider at startup.
    except shop_agent.MissingConfiguration:
        logging.getLogger(__name__).warning("Chat needs PORTKEY_API_KEY; product/account routes remain available.")
    yield
    await shop_agent.close_client()


app = FastAPI(title="Campus Customs", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
# Only the product directory is public; the database and user records are not served.
app.mount("/images", StaticFiles(directory=PRODUCTS_DIR), name="images")


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default errors can contain the rejected request values. Never
    # echo a password (including malformed or too-long input) back to a client.
    messages = {
        "email": "Enter a valid email address (up to 254 characters).",
        "first_name": "Enter your first name (1–80 characters).",
        "last_name": "Enter your last name (1–80 characters).",
        "password": "Enter a password of 8–128 characters." if request.url.path.endswith("/signup") else "Enter your password (up to 128 characters).",
        "confirm_password": "Confirm your password; both entries must match.",
    }
    errors = {}
    for error in exc.errors():
        field = error["loc"][-1] if error["loc"] else None
        if field in messages:
            errors[field] = messages[field]
    return JSONResponse(status_code=422, content={
        "detail": "Please check the form fields.", "errors": errors,
    })


def connect_db() -> sqlite3.Connection:
    """Keep catalogue and login lookups read-only."""
    return tools.connect_db(DB_PATH)


def connect_accounts() -> sqlite3.Connection:
    """Only account endpoints use this writable connection; never create a DB."""
    connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def verify_password(stored_hash: str, password: str) -> bool:
    """Use library verification for both Argon2id and the supplied legacy format."""
    try:
        if stored_hash.startswith("$argon2"):
            return PASSWORD_HASHER.verify(stored_hash, password)
        parts = stored_hash.split("$")
        if len(parts) == 3 and parts[0] == "pbkdf2_sha256" and len(parts[2]) == 64:
            PBKDF2HMAC(
                algorithm=hashes.SHA256(), length=32,
                salt=parts[1].encode("utf-8"), iterations=LEGACY_PBKDF2_ROUNDS,
            ).verify(password.encode("utf-8"), bytes.fromhex(parts[2]))
            return True
    except (VerificationError, InvalidHashError, InvalidKey, ValueError, TypeError):
        return False
    # No plaintext comparison or permissive fallback for unknown hash formats.
    return False


def check_auth_origin(request: Request) -> None:
    # SameSite cookies plus JSON-only bodies and an Origin check protect local
    # cookie-authenticated actions. Requests without Origin also permit CLI tests.
    origin = request.headers.get("origin")
    if origin is not None and origin not in AUTH_ORIGINS:
        raise HTTPException(403, "This request is not allowed from that site.")


def start_session(user_id: int, request: Request, response: Response) -> None:
    reset_chat(request, response)
    token = secrets.token_urlsafe(32)
    now = monotonic()
    with SESSION_LOCK:
        for expired in [key for key, (_, until) in SESSIONS.items() if until <= now]:
            del SESSIONS[expired]
        SESSIONS.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        SESSIONS[token] = (user_id, now + SESSION_SECONDS)
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_SECONDS,
        httponly=True, samesite="lax", secure=request.url.scheme == "https", path="/api",
    )
    response.headers["Cache-Control"] = "no-store"


@app.post("/api/auth/signup", response_model=AuthResponse, status_code=201)
def signup(payload: SignupRequest, request: Request, response: Response) -> AuthResponse:
    check_auth_origin(request)
    password_hash = PASSWORD_HASHER.hash(payload.password.get_secret_value())
    try:
        with closing(connect_accounts()) as connection, connection:
            # Serialize duplicate-check + insert, including differently cased
            # emails, without changing the provided schema or adding columns.
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM users WHERE lower(trim(email)) = ?", (payload.email,),
            ).fetchone()
            if existing:
                raise HTTPException(409, "An account with this email already exists. Please log in.")
            cursor = connection.execute(
                "INSERT INTO users (name, first_name, last_name, email, password_hash) VALUES (?, ?, ?, ?, ?)",
                (f"{payload.first_name} {payload.last_name}", payload.first_name,
                 payload.last_name, payload.email, password_hash),
            )
            row = connection.execute(
                "SELECT " + USER_FIELDS + " FROM users WHERE id = ?", (cursor.lastrowid,),
            ).fetchone()
        user = PublicUser(**dict(row))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "An account with this email already exists. Please log in.") from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "Accounts are temporarily unavailable. Please try again.") from exc
    start_session(user.id, request, response)
    return AuthResponse(user=user)


@app.post("/api/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
    check_auth_origin(request)
    try:
        with closing(connect_db()) as connection:
            row = connection.execute(
                "SELECT " + USER_FIELDS + ", password_hash FROM users WHERE lower(trim(email)) = ?",
                (payload.email,),
            ).fetchone()
        password = payload.password.get_secret_value()
        # An unknown email still runs password verification, rather than skipping
        # all hashing. Both credential failures return the same status and text.
        valid = verify_password(row["password_hash"] if row else DUMMY_HASH, password)
        if row is None or not valid:
            raise HTTPException(401, "Incorrect email or password.")
        stored_hash = row["password_hash"]
        if not stored_hash.startswith("$argon2id$") or PASSWORD_HASHER.check_needs_rehash(stored_hash):
            upgraded_hash = PASSWORD_HASHER.hash(password)
            with closing(connect_accounts()) as connection, connection:
                connection.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ? AND password_hash = ?",
                    (upgraded_hash, row["id"], stored_hash),
                )
        user = PublicUser(**{field: row[field] for field in PublicUser.model_fields})
    except sqlite3.Error as exc:
        raise HTTPException(503, "Accounts are temporarily unavailable. Please try again.") from exc
    start_session(user.id, request, response)
    return AuthResponse(user=user)


def authenticated_user(request: Request) -> PublicUser | None:
    """Resolve identity exclusively from the server's authenticated session."""
    token = request.cookies.get(SESSION_COOKIE, "")
    with SESSION_LOCK:
        session = SESSIONS.get(token)
        if session and session[1] <= monotonic():
            SESSIONS.pop(token, None)
            session = None
    if session is None:
        return None
    try:
        with closing(connect_db()) as connection:
            row = connection.execute("SELECT " + USER_FIELDS + " FROM users WHERE id = ?", (session[0],)).fetchone()
        if row is None:
            return None
        return PublicUser(**dict(row))
    except sqlite3.Error as exc:
        raise HTTPException(503, "Accounts are temporarily unavailable. Please try again.") from exc


@app.get("/api/auth/me", response_model=AuthResponse)
def current_user(request: Request, response: Response) -> AuthResponse:
    response.headers["Cache-Control"] = "no-store"
    user = authenticated_user(request)
    if user is None:
        raise HTTPException(401, "Please log in.")
    return AuthResponse(user=user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    check_auth_origin(request)
    reset_chat(request, response)
    with SESSION_LOCK:
        SESSIONS.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    response.delete_cookie(SESSION_COOKIE, path="/api", httponly=True, samesite="lax", secure=request.url.scheme == "https")
    response.headers["Cache-Control"] = "no-store"
    return {"message": "You have been logged out."}


# Existing helper names remain available to the regression checks.
image_url = tools.image_url
product_summary = tools.product_summary


@app.get("/api/products", response_model=list[ProductSummary])
def list_products(category: str | None = Query(default=None, max_length=80),
                  max_price: float | None = Query(default=None, ge=0, allow_inf_nan=False),
                  size: str | None = Query(default=None, pattern="^(XS|S|M|L|XL|XXL)$")) -> list[ProductSummary]:
    try:
        return tools.list_catalogue(DB_PATH, category=category, max_price=max_price, size=size)
    except sqlite3.Error as exc:
        raise HTTPException(503, "The catalogue is temporarily unavailable. Please try again.") from exc


@app.get("/api/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: str) -> ProductDetail:
    try:
        product = tools.read_product(product_id, DB_PATH)
        if product is None:
            raise HTTPException(404, "Product not found.")
        return product
    except sqlite3.Error as exc:
        raise HTTPException(503, "Product details are temporarily unavailable. Please try again.") from exc


def reset_chat(request: Request, response: Response) -> None:
    shop_agent.discard_conversation(request.cookies.get(shop_agent.CONVERSATION_COOKIE))
    response.delete_cookie(shop_agent.CONVERSATION_COOKIE, path="/api", httponly=True, samesite="lax")


@app.get("/api/chat/history", response_model=ChatHistoryResponse)
def chat_history(request: Request, response: Response) -> ChatHistoryResponse:
    response.headers["Cache-Control"] = "no-store"
    user = authenticated_user(request)
    if user is None:
        return ChatHistoryResponse(messages=[])
    try:
        return ChatHistoryResponse(messages=tools.load_chat_history(user.id, DB_PATH))
    except sqlite3.Error as exc:
        raise HTTPException(503, "Your conversation could not be loaded. Please try again.") from exc


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request, response: Response) -> ChatResponse:
    check_auth_origin(request)
    user = await asyncio.to_thread(authenticated_user, request)
    customer = CustomerContext(id=user.id, name=user.name, email=user.email) if user else None
    try:
        result, token = await shop_agent.answer(
            payload.message, request.cookies.get(shop_agent.CONVERSATION_COOKIE), customer, payload.page_context, DB_PATH,
        )
    except shop_agent.MissingConfiguration as exc:
        raise HTTPException(503, "The shop assistant is not configured yet. Please try again later.") from exc
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "The assistant took too long. Please try a shorter question.") from exc
    except UsageLimitExceeded as exc:
        raise HTTPException(429, "This question reached the assistant's request limit. Please narrow it down.") from exc
    except shop_agent.ConversationBusy as exc:
        raise HTTPException(503, "The shop assistant is busy. Please try again shortly.") from exc
    except (OpenAIError, UnexpectedModelBehavior, sqlite3.Error, tools.AuditLogError) as exc:
        # Log only an error class, never prompts, keys, provider bodies, or history.
        logging.getLogger(__name__).warning("Shop assistant request failed (%s)", type(exc).__name__)
        raise HTTPException(503, "The shop assistant is temporarily unavailable. Please try again.") from exc
    response.set_cookie(
        shop_agent.CONVERSATION_COOKIE, token, max_age=shop_agent.CONVERSATION_SECONDS,
        httponly=True, secure=request.url.scheme == "https", samesite="lax", path="/api",
    )
    response.headers["Cache-Control"] = "no-store"
    return result
