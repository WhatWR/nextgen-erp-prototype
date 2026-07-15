# Document Extraction Service — Implementation Plan

## Context

We're building the technical core of an **Order Intake agent** for the Thai wholesale market: one service that ingests any document (handwriting, scanned PDF, native PDF, Word, image, plain text), classifies it (PO / contract / invoice / transfer slip / other), and returns clean, validated JSON ready for an ERP. The hard, de-risking problem is **Thai handwriting → structured JSON** — Thai has no word boundaries, looped/loopless font variation, and messy real-world layouts that break the old OCR→regex pipeline.

The decision already made: **VLM-first, not OCR-first.** Feed the document image directly to a vision model and ask for schema-matching JSON in one pass, skipping the brittle "raw text → parse" middle step. The architecture keeps the extraction call behind an interface so the provider is swappable (hosted VLM now → self-hosted Typhoon later for cost/data-residency).

This is a **greenfield project** — the working directory `/Users/santi/Documents/1-Projects/OCR` is empty. This plan covers the full roadmap (Phase 0 spike → Phase 4 self-host), but **most of Phases 2–4 is provisional until the Phase 0 spike validates the core.** Phase 0 is the only thing to do this week.

### Decisions locked for this plan
- **Language:** Python end-to-end (best doc/ML ecosystem: `anthropic`, `google-genai`, `pdfplumber`, `python-docx`, `pillow`; and the eventual Typhoon path is Python). Rails-as-orchestrator remains an option later if you want to reuse Rails strengths, but a single-language service avoids a cross-process hop and is simpler to ship.
- **Spike providers:** **Claude and Gemini, compared side by side** on the same 3 real Thai documents — this directly answers "which model for Thai handwriting" before we commit.
- **Claude model:** `claude-opus-4-8` for the quality bake-off (most capable; native vision + PDF input + structured outputs). For production at volume, drop to `claude-sonnet-4-6` ($3/$15 per 1M tok) or `claude-haiku-4-5` ($1/$5) once accuracy is proven — model choice is a per-route cost decision, not a quality compromise to make now.
- **Gemini model:** `gemini-2.5-pro` (cited ~93% handwriting accuracy, strong Thai).

---

## Phase 0 — Spike (this week). Prove the hard part.

**Goal:** One hardcoded path: photo of a Thai handwritten PO → both VLMs with a PO schema → JSON printed to terminal, Claude vs Gemini side by side. No API, no DB, no pipeline. If the JSON is usable on 3 real documents, the core is validated. If the handwriting one is a mess, we've learned exactly where the review loop and real engineering effort go — before writing pipeline code.

### Test set (get these first)
Three real documents from a target Thai wholesaler:
1. One clean **printed PO**
2. One **handwritten order**
3. One **transfer slip**

Drop them in `spike/samples/`. Ugly real documents only — not clean synthetic samples (see Hard Parts #2).

### Files
```
spike/
  samples/            # the 3 real docs (jpg/png/pdf)
  schema.py           # PurchaseOrder Pydantic model (the contract)
  extract_claude.py   # Claude Opus 4.8 vision + structured output
  extract_gemini.py   # Gemini 2.5 Pro vision + JSON mode
  run.py              # loop samples × providers, print JSON + timing side by side
  requirements.txt    # anthropic, google-genai, pydantic, pillow, pdfplumber
  .env                # ANTHROPIC_API_KEY, GEMINI_API_KEY  (gitignored)
```

### Schema (the contract — define this first)
`spike/schema.py` — a Pydantic model so validation is automatic and reusable across both providers and all later phases. Mirror the PO sketch from the brief: `po_number`, `order_date`, `buyer{name,tax_id,address}`, `supplier{name,tax_id}`, `line_items[]{description,sku,quantity,unit,unit_price,amount}`, `subtotal`, `vat`, `total`, `currency`, `notes`, plus `_meta{confidence, source_file, needs_review}`. Make optional fields `Optional[...] = None` — VLMs return `null` for missing fields, and structured-output schemas reject unknown numeric/string constraints, so keep the schema flat and permissive.

### Claude extraction (`extract_claude.py`)
- SDK: `pip install anthropic`; `client = anthropic.Anthropic()` (reads `ANTHROPIC_API_KEY`).
- Use **`client.messages.parse(...)`** with `output_config={"format": <pydantic schema>}` (recommended path — validates the response against the schema automatically; `response.parsed_output` is a typed `PurchaseOrder`).
- **Images:** base64 content block — `{"type":"image","source":{"type":"base64","media_type":"image/jpeg","data":<b64>}}` + a tight text instruction.
- **Native PDFs:** Claude takes PDF directly — `{"type":"document","source":{"type":"base64","media_type":"application/pdf","data":<b64>}}` — no client-side rasterization needed for the spike (handy for the printed-PO case if it's a PDF).
- `model="claude-opus-4-8"`, `max_tokens=8000`. Do **not** use assistant prefill (400 on 4.8) — structured outputs replace it.
- Tight prompt: "Transcribe and extract this Thai purchase order into the schema. Use null for anything not present. Do not guess values you cannot read. Output Thai text verbatim."

### Gemini extraction (`extract_gemini.py`)
- SDK: `pip install google-genai`; pass the image bytes + `response_mime_type="application/json"` and `response_schema` (the same Pydantic model) so Gemini returns schema-shaped JSON. Parse defensively (strip code fences) as a fallback.
- `model="gemini-2.5-pro"`.

### Compare (`run.py`)
For each sample × each provider: print the parsed JSON, wall-clock latency, and a quick human-eyeball diff of the two providers' output for the same doc. Manually score the handwritten one — that's the decision-maker.

### Verification (Phase 0 done when)
- All 3 docs produce parseable JSON from both providers.
- The **handwritten** doc's line items (description + quantity) are mostly correct from at least one provider — this is the go/no-go signal.
- You have a documented gut call on Claude vs Gemini for Thai handwriting, and a rough per-doc latency + token cost for each.

---

## Phase 1 — Thin vertical slice (1–2 weeks)

**Goal:** One document type (PO), one input path (image), a real async API, store original + result, schema validation. No classifier, no review UI yet. Get one real client document flowing end to end.

### Service shape (Python, FastAPI + a worker)
```
app/
  api.py              # FastAPI: POST /v1/documents, GET /v1/documents/{job_id}
  models.py           # SQLAlchemy: Job(id, status, original_uri, result_json, created_at)
  storage.py          # S3-compatible put/get (boto3 / minio)
  worker.py           # async job: download → extract → validate → persist
  extraction/
    base.py           # Extractor interface (THE swap point for Phase 4)
    claude.py         # promote spike/extract_claude.py behind the interface
    gemini.py         # promote spike/extract_gemini.py behind the interface
  schemas/
    purchase_order.py # promote spike/schema.py
  validation.py       # schema check + business rules + confidence
```

### Key design points
- **Extractor interface (`extraction/base.py`)** is the most important architectural seam: `extract(file_bytes, mime_type, schema) -> dict`. Both Claude and Gemini implement it; Phase 4's Typhoon slots in here with zero pipeline changes. Pick the Phase 0 winner as the default; keep both registered so you can A/B per document.
- **API contract** (versioned `/v1/`):
  - `POST /v1/documents` (multipart file + optional `{"hint_type":"purchase_order"}`) → `202 {"job_id","status":"processing"}`. Return immediately; process async.
  - `GET /v1/documents/{job_id}` → `{"status":"done|processing|needs_review|failed","result":{...}}`.
- **Async:** start with FastAPI `BackgroundTasks` or a small RQ/Celery + Redis worker. Extraction takes seconds — never block the request.
- **Store:** original file → S3-compatible object storage; `Job` record → Postgres.
- **Validation + one-retry repair:** validate VLM output against the Pydantic schema; on failure, do **one** retry feeding the validation error back to the model, then mark `failed` if still bad.

### Verification (Phase 1 done when)
- `curl -F file=@samples/handwritten.jpg POST /v1/documents` returns a `job_id`; polling `GET` returns validated PO JSON.
- Original file is in object storage; `Job` row has the result.
- A malformed-output case triggers the one-retry repair path (test by forcing a bad response).

---

## Phase 2 — Breadth

Add the other input paths and document types, and the routing intelligence.

- **Preprocessing** (`app/preprocessing.py`) — normalize everything into one of two paths:
  - **File-type detection by magic bytes** (`python-magic`), not extension.
  - **Native-text docs** (`.docx`, `.txt`, text-layer PDFs): extract text directly — `python-docx`/`mammoth` for Word, `pdfplumber`/`pypdf` for native PDF. No VLM image call; send text + schema to the model.
  - **Image docs** (photos, scanned PDFs, handwriting): rasterize each PDF page (`pdf2image`), light cleanup (deskew, denoise, auto-orient via `pillow`/`opencv`), send as images.
  - **Native-vs-scanned PDF test:** extract the text layer; if empty/junk, treat as image.
- **Classifier** (`app/classifier.py`) — cheap VLM/LLM call on the first page → `purchase_order | contract | invoice | transfer_slip | other`. Drives which schema the extractor targets. (Start with the LLM call, not keyword rules — fewer edge cases.)
- **More schemas** — `invoice.py`, `transfer_slip.py`, `contract.py`, plus an **`other` fallback** that returns key-value pairs + full text so nothing is ever a hard failure. Every schema carries `_meta{confidence, source_file, needs_review}`.
- **Confidence + threshold routing** — per-field scores + business-rule results (line items sum to total? dates sane? currency present? supplier in a known list?) → document-level confidence → above threshold auto-returns, below queues for review (`status: needs_review`).

---

## Phase 3 — Trust + learning loop

- **Human review UI** — document image next to extracted fields, low-confidence fields highlighted, editable.
- **Corrections endpoint** — `POST /v1/documents/{job_id}/corrections` stores the corrected JSON and marks reviewed.
- **Log every correction** — this is the future fine-tuning dataset and the client trust mechanism. Don't skip it.
- **Webhooks** — push the result to callers (important for the LINE bot flow) instead of polling.

---

## Phase 4 — Cost + sovereignty

- Swap the `Extractor` interface to **self-hosted Typhoon OCR V1.5** for high-volume / data-residency-sensitive clients. Because everything sits behind `extraction/base.py`, this is an implementation swap, not a pipeline rewrite.
- **Route by client:** hosted Claude/Gemini for low volume; self-hosted Typhoon for the rest. This is the entire reason the interface exists — build it in Phase 1 even though it's only one implementation then.

---

## Hard parts to budget for (don't underestimate)

1. **Thai handwriting** is the genuinely hard input — print-style ~10–15% more accurate than cursive, quality varies wildly. This is where the human-review loop earns its keep. Phase 0 tells you how bad it is.
2. **Layout chaos** — real POs/slips aren't clean forms. VLMs beat rules here but tables and multi-column still trip them. Test on ugly real documents early (Phase 0), not clean samples.
3. **Confidence calibration** — the auto-accept vs review threshold is what makes this trustworthy. Too loose → bad data hits the ERP; too tight → humans review everything and you've saved no time. Tune against the Phase 3 corrections log.
4. **Schema drift** — every client's PO looks different. Resist a custom schema per client; converge on one flexible schema with optional fields.
5. **Cost at volume** — per-document VLM calls add up. This is the whole reason Phase 4 exists. Track per-doc token cost from Phase 0 onward.

---

## First move

Do **Phase 0 this week** with the three real documents. The whole plan downstream is provisional until that bake-off comes back usable. If the handwriting JSON is good, the core is validated and Phase 1 is mechanical. If it's a mess, you've found where the real engineering effort and the review loop have to go — before writing a line of pipeline code.
