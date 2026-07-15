# Order Intake Agent — Demo Build Plan

## Context

Build a working demo of a LINE-based order intake agent for Thai SME merchants, structured so **onboarding is the hero** — it directly answers the panel objection "ลูกค้าต้อง set เอง = ไม่ซื้อ". The demo must prove: (1) a messy, real-world Thai inventory Excel works as-is with no reformatting, (2) large inventories are a strength (semantic matching at scale), and (3) a human checkpoint keeps the owner in control. Phase 2 shows the live order flow: free-text Thai customer messages → parsed order → inventory match → confirmation → delivery-team notification.

Decisions already made with the user:
- **Local chat simulator first** (LINE-like web UI for the stage demo, zero network risk) + a real LINE Messaging API webhook adapter kept ready behind a config flag (switch on later with ngrok).
- **Python** backend (FastAPI).
- **Local multilingual embeddings** (sentence-transformers) — no extra API key, offline-safe on demo day. Combined with fuzzy matching + Claude disambiguation.
- **I generate the messy sample Excel** (~1,000 rows, deliberately dirty Thai data) so the demo is reproducible; a real file can be swapped in later.

Greenfield: the project directory is empty.

## Architecture

Single FastAPI app, three browser surfaces + one webhook:

| Surface | Path | Purpose |
|---|---|---|
| Admin dashboard | `/admin` | Onboarding: upload Excel → review/confirm column mapping → build index → run test queries |
| Customer chat | `/chat` | LINE-like chat simulator (the customer side of the live demo) |
| Delivery feed | `/delivery` | Simulated LINE-group panel where confirmed orders appear for the delivery team |
| LINE webhook | `/webhook/line` | Real LINE adapter (signature verification + reply/push API), enabled via `LINE_ENABLED=true` |

Claude API usage (per current API reference — model `claude-opus-4-8`, adaptive thinking, structured outputs via `output_config.format` / `client.messages.parse()` with Pydantic):
1. **Column mapping** — send headers + first ~10 rows, get back a mapping schema JSON (`{name_col, sku_col, unit_col, price_col, stock_col, header_row, confidence, notes}`).
2. **Order parsing** — free-text Thai message → `[{item_text, quantity, unit}]` (Pydantic-validated).
3. **Match disambiguation** — when retrieval returns several close candidates, Claude picks the right SKU (or flags "ask customer").
4. **Confirmation / alternative-offer message generation** — natural Thai reply summarizing the order, total price, and stock-shortage alternatives.

Product matching pipeline (the "scale is a strength" story):
- Index build (once at onboarding): normalize product names → embed with `sentence-transformers` **`BAAI/bge-m3`** (strong Thai support; falls back to `paraphrase-multilingual-MiniLM-L12-v2` if download size is a concern) → store vectors as a numpy matrix. At ~1k items, plain numpy cosine similarity is enough — no FAISS dependency needed.
- Query time: embedding similarity **+** `rapidfuzz` partial-ratio (catches abbreviations/typos embeddings miss) → blended score → top-5 candidates → Claude disambiguates if the top hit isn't a clear winner.

Persistence: SQLite (stdlib `sqlite3`) — tables for `products`, `mapping`, `orders`, `chat_sessions`. Embedding matrix saved as `.npy` alongside.

## File layout

```
Order Intake Agent/
├── requirements.txt          # fastapi, uvicorn, anthropic, openpyxl, pandas,
│                             # sentence-transformers, rapidfuzz, numpy, python-dotenv,
│                             # line-bot-sdk (adapter only)
├── .env.example              # ANTHROPIC_API_KEY, LINE_ENABLED, LINE_CHANNEL_SECRET/TOKEN
├── app/
│   ├── main.py               # FastAPI app + routes (admin, chat, delivery, webhook)
│   ├── config.py             # env/config
│   ├── store.py              # SQLite schema + access
│   ├── claude.py             # Anthropic client: map_columns(), parse_order(),
│   │                         # disambiguate(), compose_reply() — Pydantic schemas here
│   ├── onboarding.py         # Excel ingest (openpyxl, handles merged cells/junk rows),
│   │                         # apply confirmed mapping, build embedding index
│   ├── matching.py           # embed + fuzzy blend, top-k retrieval, disambiguation hook
│   ├── orders.py             # order state machine: draft → awaiting_confirm → confirmed
│   ├── line_adapter.py       # LINE webhook verify + reply/push (flag-gated)
│   ├── templates/            # admin.html, chat.html, delivery.html (Jinja2)
│   └── static/               # minimal CSS/JS (LINE-like chat bubbles, polling or SSE)
├── scripts/
│   ├── generate_sample_excel.py   # produces data/inventory_messy.xlsx
│   └── test_onboarding.py         # fires the 5 checkpoint queries, prints pass/fail
└── data/                     # sample xlsx, sqlite db, embeddings .npy (gitignored)
```

## Implementation steps

### Step 1 — Scaffold + sample data
- `requirements.txt`, `.env.example`, config, SQLite store.
- `scripts/generate_sample_excel.py`: ~1,000-row Thai wholesale inventory (drinks/snacks/household), deliberately messy: header not on row 1, merged category-band cells, inconsistent column names ("ชื่อของ", "ราคา/หน่วย", "เหลือ"), mixed units (ลัง/แพ็ค/ขวด/โหล), abbreviations ("นน.แดง 12x325"), inconsistent spelling, some blank/junk rows, prices as text with "บาท" suffixes.

### Step 2 — Onboarding pipeline (Phase 1, the demo hero)
- `onboarding.py`: robust Excel reader → raw grid; send headers + samples to `claude.map_columns()`; persist proposed mapping as `pending`.
- `/admin` UI: upload → show detected mapping with sample-value preview per column → **owner confirms/edits (the human checkpoint)** → ingest all rows through the mapping (skip junk rows, normalize prices/units) → build embedding index with progress indicator → show "Onboarding success checkpoint": run 5 canned misspelled/abbreviated test queries and show matched products + scores live.
- `scripts/test_onboarding.py`: same checkpoint runnable headless.

### Step 3 — Matching engine
- `matching.py`: blended scorer `0.65 * cosine + 0.35 * fuzzy` (tune during testing); returns top-5 with scores; margin rule (top1 − top2 < threshold → call `claude.disambiguate()`).

### Step 4 — Live order flow (Phase 2)
- `/chat` simulator: LINE-styled UI, messages POST to the same handler the LINE adapter uses (`handle_incoming(user_id, text)`).
- Flow: `parse_order()` → match each item → stock check → `compose_reply()` (order summary + total; shortage → offer reduced qty / nearest alternative) → customer replies "ยืนยัน" (or taps a confirm button) → order saved as confirmed, stock decremented → notification pushed to `/delivery` feed (and via LINE push to a group when `LINE_ENABLED`).
- `orders.py` keeps per-user draft state so the confirm turn works.

### Step 5 — LINE adapter (flag-gated, not needed for stage demo)
- `line_adapter.py`: `POST /webhook/line` with `X-Line-Signature` verification, maps LINE events into `handle_incoming()`, replies via Messaging API. Documented ngrok setup in README. Untested paths clearly marked until credentials exist.

### Step 6 — Polish for the demo script
- README with demo runbook: generate Excel → start server → onboard on `/admin` → checkpoint slide moment → live order on `/chat` → `/delivery` notification.

## Verification

1. `python scripts/generate_sample_excel.py` → open the file, confirm it's convincingly messy.
2. Start the server with the preview tool (`.claude/launch.json` entry: `uvicorn app.main:app --port 8000`), walk `/admin` end-to-end: upload → mapping looks right → confirm → index builds → all 5 checkpoint queries match the correct SKU.
3. `/chat`: send `"เอาน้ำแดง 2 ลัง กะมาม่าต้มยำ 3 แพ๊ค ส่งพรุ่งนี้เช้า"` (typos intended) → verify parsed items, correct matches, price total, confirm turn, and the order appearing on `/delivery`.
4. Shortage path: order more than stock → verify the agent offers an alternative instead of failing.
5. `scripts/test_onboarding.py` exits 0.

Notes:
- API key: resolve via env/`ant auth status` at runtime; the app should fail with a clear message if no credential is present, not crash mid-demo.
- First run downloads the embedding model (~1–2 GB for bge-m3) — do this before demo day; everything after is offline except Claude API calls.
