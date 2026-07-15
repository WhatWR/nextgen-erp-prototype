# Restyle generated PDFs to match the reference "Purchase Invoice" layout

## Context

The user provided a reference PDF (an ERP-style **Purchase Invoice**, A4, iText-generated)
and asked to make the app's generated PDFs look "like this." The app generates two PDFs
via `Pdf::BaseDocument` subclasses:
- **Invoice/Receipt** — `app/services/pdf/invoice_document.rb` (rendered at `InvoicesController#pdf`, route `app/controllers/invoices_controller.rb:52`)
- **Rental Contract** — `app/services/pdf/contract_document.rb` (rendered at `ContractsController#pdf`, route `app/controllers/contracts_controller.rb:62`)

Both currently use a vertical, section-based layout with embedded Thai font, **fillable
AcroForm fields**, and (invoice only) a **PromptPay QR**.

### Decoded reference layout (A4 595×842, Helvetica, small fonts, thin grid rules, flat)
1. **Centered title** at top ("Purchase Invoice").
2. **Metadata grid** — a compact block of `label : value` pairs in two columns
   (Invoice No · Doc Date · Counterpart No/Date · Invoice Type · Billing Party · Currency ·
   Exchange Rate · Dept · Buyer).
3. **Bordered line-items table** — multi-line column header (Line No · Code · Name · Spec ·
   Qty · Unit · Unit Price · Price Excl/Incl Tax · Amount Excl/Incl Tax · Tax Amt), thin
   cell borders, **right-aligned numeric columns**, one row per item.
4. **Approval footer** — "Approved By: <name>" / "Approver / Date: <date>".

### User decisions
- **Scope:** Invoice **and** Contract.
- **Content:** keep the Thai/bilingual **dormitory data**; adopt only the reference's
  visual structure (metadata grid + bordered table + approval footer). Do NOT import
  ERP fields (Supplier/VAT/Exchange Rate/Buyer).
- **Flat:** drop the PromptPay QR and all fillable AcroForm fields — print-style document
  like the reference. Keep the embedded Thai font (content is Thai).

## Implementation

### 1. `app/services/pdf/base_document.rb` — add flat layout helpers, drop AcroForm
- Remove AcroForm usage: in `initialize` stop calling `@doc.acro_form(create: true)` /
  default-appearance setup; in `render` drop the `create_appearances` / `NeedAppearances`
  block (just `@doc.write`). Keeps font embedding and the top-down cursor engine.
- Delete now-unused field helpers (`fill_line`, `value_or_field`, `fill_box`,
  `add_text_field`, `printed_line`) — or keep `printed_line` only if convenient. Callers
  in both documents are being rewritten, so prefer removing them.
- Add reusable primitives + components (new colors: `HEADER_FILL ≈ [0.95,0.96,0.97]`, reuse `RULE`/`MUTED`/`INK`):
  - `box(x, y, w, h, fill: nil, stroke: RULE, line_width: 0.6)` — fill/stroke a rectangle.
  - `meta_grid(pairs, cols: 2, label_w:, row_h: 18)` — draws a bordered metadata block;
    each cell renders a muted small label and the value beside/under it; auto-rows. Pairs
    are `[[label, value], ...]`. Used for both documents' header info.
  - `data_table(columns, rows, header_fill: HEADER_FILL)` where `columns` is
    `[{title:, width:, align: :left|:right}, ...]` and `rows` is an array of cell-string
    arrays. Draws: shaded bold header row, thin vertical/horizontal cell rules,
    right-aligned numerics, and calls `ensure_space`/`new_page` re-drawing the header on
    overflow. Reuse `width_of`/`wrap`/`draw_text`.
  - `signature_boxes(blocks)` — replaces the AcroForm signature widgets in the contract:
    draws an outlined rectangle per signer (embeds captured signature image via the
    existing `embed_data_uri_image` when signed), then a ruled name line + `(name)` + date.

### 2. `app/services/pdf/invoice_document.rb` — rebuild around grid + table (flat)
`build` becomes: `header → meta_grid → items_table → totals → approval_footer → footer`.
- **header:** centered property name + address, centered title (`ใบเสร็จรับเงิน/RECEIPT`
  when `paid?`, else `ใบแจ้งหนี้/INVOICE`), then a `hr`.
- **meta_grid:** two columns — left: เลขที่/Invoice No, สถานะ/Status, วันที่ออก/Issue date,
  ครบกำหนด/Due date; right: ผู้ชำระเงิน/Bill-to (tenant name, phone, LINE), ห้อง/Room,
  รอบบิล/Billing period. (Replaces current `parties`.)
- **items_table:** `data_table` with columns `รายการ/Description` (left, wide),
  `อัตรา/Rate` (right), `จำนวน/Qty·Unit` (right), `จำนวนเงิน/Amount` (right). Rows: rent,
  water (rate ฿/unit), electricity (rate ฿/unit), late fee when positive. Reuse
  `ThaiFormat.money`.
- **totals:** right-aligned subtotal / late fee / **grand total** rows in a small boxed
  block (keep current `amount_due` logic).
- **approval_footer:** printed ruled lines "ผู้รับเงิน / Received by ____" and
  "วันที่ / Date ____" (printed value when `paid? && paid_at`), mirroring the reference's
  Approved-By/Date block. **No QR, no form fields.**
- Remove `promptpay_block` and `payment_block`; `footer` keeps the thank-you/notice line +
  printed timestamp.

### 3. `app/services/pdf/contract_document.rb` — matching header/footer + tabled terms (flat)
- **header:** same centered style + `meta_grid` (เลขที่สัญญา/Contract No, วันเริ่ม/Start,
  วันสิ้นสุด/End, ผู้ให้เช่า/Landlord, ผู้เช่า/Tenant, โทร/Phone).
- Keep the legal **preamble** and the **numbered conditions** as `paragraph`s (legal text
  stays — it doesn't tabularize).
- **room_and_terms + financials:** render as `meta_grid`/`data_table` blocks instead of
  `fill_line`/`row_two` (room no, floor, duration; monthly rent, deposit, water/electric
  rate, due day, move-in total) — bordered, right-aligned amounts.
- **signatures:** use new `signature_boxes` (Tenant/Landlord + two witnesses) — drawn
  boxes, embed captured signature image when `@contract.signed?`, else blank ruled line.
  **No AcroForm fields.**
- **footer:** keep two-copies note + printed `เลขที่สัญญา … · พิมพ์เมื่อ … · Sundance`.

### Reuse
- `Pdf::ThaiFormat` (`money`, `long_date`, `month_year`, `to_thai_digits`) — unchanged.
- `embed_data_uri_image` / `embed_image_bytes` (base) — keep for signatures.
- `PromptpayQr` — no longer called by the invoice (leave the class in place; it's still
  used by LINE billing flows elsewhere — verify before removing).

## Verification
The local bundle now runs (`bin/rails` works). After implementing:
1. Generate samples and probe structure with a runner script:
   ```ruby
   inv = Invoice.first; ct = Contract.first
   File.binwrite("/tmp/inv.pdf", Pdf::InvoiceDocument.new(inv).render)
   File.binwrite("/tmp/ct.pdf",  Pdf::ContractDocument.new(ct).render)
   require "hexapdf"
   [ "/tmp/inv.pdf", "/tmp/ct.pdf" ].each do |f|
     d = HexaPDF::Document.open(f)
     puts "#{f}: pages=#{d.pages.count} acroform=#{d.acro_form ? 'YES(should be no)' : 'none'}"
   end
   ```
   Expect: both render without error, **no AcroForm**, invoice ~1 page, contract multi-page.
2. Re-run the content-stream decoder used during research (offset +29 / BT-reset) on the
   generated invoice to confirm the metadata grid, table columns, and approval footer
   appear in the right positions.
3. Manual check in a viewer via the running app: download `/invoices/:id/pdf` and
   `/contracts/:id/pdf`, confirm the tabular look matches the reference, Thai renders, and
   amounts are right-aligned. (My sandbox can't render to image — poppler isn't installed —
   so the visual confirmation is the user's.)

## Out of scope / notes
- Keeping content Thai/bilingual and dormitory-specific (not the ERP fields) per decision.
- Dropping fillable fields means contracts are signed in-app (existing `signature_data`
  flow) — unsigned PDFs get a wet-signature ruled line instead of an editable field.
