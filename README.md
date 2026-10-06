# Campus Customs — hw4

The React + Vite + TypeScript storefront uses FastAPI for products, accounts, and the PydanticAI shopping assistant. Problems 5–9 add real model replies, focused product lookups, clickable search cards, saved customer conversations, page context, catalogue filters, suggested questions, more search results, and product comparisons. Problem 10 adds a consistent campus print-studio design in black, paper, and pink. Problem 12 adds a persistent audit trail, shop-specific safety rules, and the complete current reference in [output/harness.md](output/harness.md). The Home and About text is original. Prices, product descriptions, images, and stock come from the local data. See [output/usability.md](output/usability.md) for the four Problem 9 features and [output/design.md](output/design.md) for the finished design and regression checks.

## Fresh-clone setup

Use **Python 3.13** (the tested version) and **Node 24**. The pinned Vite version also supports Node 20.19+ or 22.12+. Clone into the required folder name:

```bash
git clone https://github.com/qingyuanzhangyaleedu/hw4.git hw4
cd hw4
```

The instructor's data pack is intentionally **not in GitHub**. Put it in this layout before starting the backend:

```text
hw4/
  data/
    campus_customs.db
    products/
      ... supplied product images ...
  backend/
  frontend/
```

Do not accidentally nest it as `data/data/`. Keep the original schema and product filenames. The account/history routes need a writable local copy of the database. Product images and the database stay local; the screenshots in `output/app_check_images/` are the required grading evidence.

From `hw4`, create a new environment and install the pinned runtime dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` locally and replace `your-portkey-api-key-here` with your own **`PORTKEY_API_KEY`**. Keep `MODEL_NAME=gpt-6-astra` and `PORTKEY_BASE_URL=https://api.portkey.ai/v1` unless your course configuration says otherwise. The key needs access to that model through Portkey. Do not paste a key into source code or the README. All three environment variables read by the app are documented in `.env.example`.

`backend/agent.py` loads settings once. Existing process environment values win, followed by a `.env` one folder above `hw4` (the original course workspace), then `hw4/.env`. For a standalone clone, use `hw4/.env` and avoid an unrelated parent `.env` overriding it. Credentials never go to the browser. Without a configured key, products and accounts still work; chat returns a clear configuration error.

Install the frontend from the same `hw4` folder:

```bash
cd frontend
npm ci
cd ..
```

`npm ci` uses the included `package-lock.json`. `requirements.txt` pins the direct Python runtime packages actually imported, plus Uvicorn for the documented server command; `pydantic-ai-slim[openai]` includes the OpenAI provider. Test-only `httpx` is pinned separately in `requirements-dev.txt`.

## Run in two terminals

Terminal 1, from `hw4`:

```bash
source .venv/bin/activate
cd backend
uvicorn main:app --reload --port 8000
```

Terminal 2, from `hw4`:

```bash
cd frontend
npm run dev -- --port 5173
```

Open **http://localhost:5173**. API documentation is at **http://localhost:8000/docs**. Stop each server with Ctrl+C in its terminal. Both servers are required; Vite forwards `/api` and `/images` to FastAPI. The strict frontend port setting reports an error instead of silently switching ports.

Keep `data/campus_customs.db` and `data/products/` inside `hw4`. Paths are derived from the project location, not a hard-coded user directory. `catalogue.product_id` is exposed as `id` in the API. Image paths such as `products/example.jpg` are interpreted relative to `data/`.

## Files

| File or folder | Role |
| --- | --- |
| `backend/main.py` | FastAPI product/account/chat endpoints, cookies, images, CORS, and safe errors. |
| `backend/agent.py` | Environment/model wiring, prompt loading, customer/page deps, persistent or guest history, and run limits. |
| `backend/tools.py` | Product search/lookups, pagination/comparison, saved-history helpers, and safe audit summaries/appends. |
| `backend/models.py` | Pydantic API and agent types, including product cards and chat replies. |
| `backend/prompts/prompt.md` | Editable Campus Customs voice, lookup/search instructions, and shop-specific safety rules. |
| `frontend/src/App.tsx` | Routes, navigation, pages, search/sort, and working account forms. |
| `frontend/src/api.ts` | Central API calls and response types. |
| `frontend/src/ChatWidget.tsx` | Floating chat panel, model replies, and structured results rendered with the shared product card. |
| `frontend/src/components.tsx` | Shared product cards, image fallback, and loading/error states. |
| `frontend/src/format.ts` | USD price formatting. |
| `frontend/src/index.css` | Campus print-studio theme, responsive layouts, motion, and visible focus states. |
| `frontend/vite.config.ts` | Development ports and API/image proxy. |
| `requirements.txt` | Exact versions of the backend runtime dependencies. |
| `.env.example`, `.gitignore` | Placeholder-only settings and exclusions for local secrets, data, environments, dependencies, and build files. |
| `requirements-dev.txt`, `tests/test_api.py`, `tests/test_auth.py` | Test dependencies and shop/account/chat checks; auth tests use temporary database copies. |
| `inspect_db.py` | Problem 2 inspection script. |
| `output/harness.md` | Actual database schema, findings, and shop implementation notes. |
| `output/audit_trail.json` | Persistent activity from real agent message history; preserves prior runs and excludes raw prompts and private payloads. |
| `output/usability.md` | The four Problem 9 improvements, why they help, and tested click/type demonstrations. |
| `output/design.md` | Problem 10's visual direction, concrete design choices, and live regression checks. |
| `output/app_check.html`, `output/app_check_images/` | Problem 11's offline report and three actual live screenshots; keep them together and double-click the HTML file. |
| `AI_prompts.md` | Prompt log, organized by homework problem. |

## Quick checklist

- [ ] Try the four Problem 9 demonstrations in `output/usability.md`: filters, shortcuts, more matches, and comparisons. Search position is temporary and resets after logout, expiry, or backend restart; account chat history remains saved.
- [ ] Open every navigation link: Home, Products, About Us, Log in, Create account.
- [ ] Home shows four real featured products. Products shows **102** entries with photos and database prices.
- [ ] Search for `Champion Reverse Weave Hoodie 1`; check the price-sort dropdown and open its card.
- [ ] Its detail page shows **$68.00**, sizes XS through XXL, and **Out of stock** for XS and XL in the supplied dataset. Other counts match the database. Use the back link.
- [ ] Visit `/products/not-a-real-product`; see the friendly not-found page.
- [ ] Sign up with first name, last name, email, password (8–128 characters), and matching confirmation. See a welcome message and Log out in the navigation.
- [ ] Log out, then log in with that email and password; refresh and confirm the session remains.
- [ ] Confirm the supplied seed account still logs in. Incorrect email and password attempts both show “Incorrect email or password.” Duplicate email and mismatched confirmation are rejected.
- [ ] Open “Let’s talk Yale” and ask “What hoodies do you have?” Confirm multiple cards with images, names, prices, and short descriptions. Search returns at most six.
- [ ] Ask “What bomber jackets do you have?” Confirm one card. Click it and see the existing item page with its large photo, full description, and size stock.
- [ ] Ask “What spacesuits do you have?” Confirm a no-match reply and no new cards; older results remain attached to their earlier messages.
- [ ] Ask “What is the price of the Champion Reverse Weave Hoodie 1?” Confirm a model reply with **$68**.
- [ ] Ask “Is it available in XL?” without repeating the product name. Confirm **out of stock**, using the retained conversation and a fresh database lookup. Close using ×, the toggle, or Escape.
- [ ] While logged in, state a shopping preference, reload, and reopen chat. Confirm the exchange returns. Log out and back in to verify it survives a new login session too.
- [ ] Open the Brooks Brothers Bomber Jacket Yale page and ask “Do you have this in pink?” Confirm the answer refers to that jacket and checks its recorded navy/white colors.
- [ ] Log out: the old account's transcript disappears. Guest chat still works. On About Us, “Do you have this in pink?” should ask which product you mean.
- [ ] Resize to phone width; navigation, two-column product cards, detail stock, and chat remain usable.

Accounts are real and stored in the existing SQLite users table. Login sessions last up to eight hours in this server process; a backend restart requires logging in again. Passwords are hashed with Argon2id, and the seed account’s older PBKDF2 format is verified and upgraded on successful login. No password reset or email verification is implemented. The site does not take orders.

Chat sends `{ "message": "...", "page_context": { "product_id": "..." } }` to `POST /api/chat` and receives `{ "reply": "...", "products": [...] }`. The product ID is null on other pages; requests omitting page context remain valid. Identity comes only from the server's authenticated session, not the request body. `ShopDeps` gives the agent the signed-in shopper's name/email and current product ID, or an explicit guest identity.

Signed-in conversations are saved in the existing `chat_messages` table as an atomic user/assistant pair after each successful answer. The widget fetches `GET /api/chat/history` on login and reload. The model receives up to 12 recent saved messages per turn and refreshes product facts with tools. Saved cards support both the older supplied format and current cards; their displayed fields are refreshed from SQLite. Logout or server restart ends the temporary session, but the conversation returns after logging back in. Guests retain temporary six-turn conversation memory for up to 30 minutes of inactivity and never read/write account history. Account changes clear the widget and cancel its pending requests. Guest text disappears on full page refresh, though its temporary server context may still exist. Tabs in the same browser guest session share that temporary conversation.

Each turn allows four model requests and six tool calls, with a 25-second timeout for agent/history work (the widget waits 30 seconds). Audit saving follows the timed work. A model response allows up to 2,500 output tokens for full card objects; the run also has a 6,000-output-token usage check. Limits bound retries too. Catalogue search returns at most six products. PydanticAI validates the `ProductMatch` list, and Python verifies its IDs and re-reads its card fields from SQLite. A prose list cannot replace the search's structured matches. Prose answers still depend on the model following its grounding instructions. The prompt is read when the agent initializes; restart the backend after editing the Markdown prompt, since the default Python auto-reloader may not watch `.md` files.

`output/audit_trail.json` accumulates actual model-request/response metadata, tool calls/returns, and final run outcomes. Summaries are capped at 300 characters and omit raw chat text, customer deps, and model thinking. Each completed or handled failed turn adds to the existing validated list under a lock, using an atomic temporary-file replacement; malformed existing history is never reset. The lock is for this single-process app. An abrupt process kill can lose an unfinished turn, but previously committed entries remain. The live restart check preserved all 34 earlier entries and grew the file to 48 entries across four chats. Details and the code-versus-prompt safety distinction are in the harness.

## Automated checks

From `hw4`:

```bash
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
cd frontend
npm run build
npm run lint
```

Product tests read the supplied data and confirm they do not change the database. Authentication and persistent-history tests use isolated temporary database copies; all chat tests use temporary audit files. Chat tests use PydanticAI's offline FunctionModel with real local product tools: no paid API requests. The 59 tests cover restored history, guest/account isolation, deps/page changes, atomic saving, legacy cards, bounded loops, timeouts, invalid matches, audit persistence/concurrency/privacy, and the existing shop/account behavior. Problem 4's live browser verification added one demo account and upgraded only the seed user’s hash; Problem 8's live signed-in chats intentionally add history rows to the seed account. `npm run build` creates `frontend/dist/`. The app's Vite proxy is configured for the development server; deploying the build requires an API/image reverse proxy and a fallback to `index.html` for frontend routes. Multiple workers would need shared login sessions, per-user request coordination, and shared audit locking/storage; public access also needs rate limiting.

## Submission and local-only files

The repository includes the five backend code/prompt files, full frontend source and lockfile, `AI_prompts.md`, the completed harness/design/usability notes, the offline screenshot report, and the real audit trail. `inspect_db.py` and the regression tests are included to make the work reproducible.

`.gitignore` excludes real `.env` variants (but keeps `.env.example`), the entire `data/` directory, database files, data archives, Python environments/caches, `node_modules`, and frontend builds. Do not use `git add -f` for the data pack. The audit file contains bounded product/event summaries, not customer transcripts or model prompts; the screenshots show guest product checks. Each local chat adds audit entries, so seeing that tracked file change after testing is expected.

The local project had no `.git` directory or prior commits when submission preparation began. That means there was no earlier local history to clean; it does not make a claim about any unrelated repository. Staged content and the complete newly created history are checked before publishing.

For a quick first-run check, open Products and a detail page, then ask the chat: “What is the price of Champion Reverse Weave Hoodie 1, and how many are in stock in size M?” With the supplied data and a working key, it should report $68 and 20 in M. Ask for hoodies to see clickable cards. The full test commands above use temporary databases/audit files where needed and no paid model calls.

### Verified clean installation — October 5, 2026

The public repository at https://github.com/qingyuanzhangyaleedu/hw4 was cloned without credentials into a new temporary `hw4` folder. All 40 tracked files were present; no real `.env`, data pack, virtual environment, dependency folder, or build output arrived in the clone. After supplying the local data pack/key, the install and server commands above worked using a new Python environment and a fresh `npm ci`.

The clean site's live chat returned the Champion hoodie's $68 price and M stock of 20, displayed six hoodie cards, and opened the $45 Ua Gameday Double Knit Hood detail page from a chat card. All 59 backend tests, frontend build, lint, and `pip check` passed. All offline-report relative links resolved. Staged files and the newly created Git history were checked for excluded data and real keys before publication; only the intentional placeholder matched the generic key-assignment scan. No earlier local Git history existed.
