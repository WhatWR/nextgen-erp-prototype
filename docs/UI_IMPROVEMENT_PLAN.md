# UI Improvement Plan — NextGen ERP Prototype

> Audit date: 2026-08-19 · Scope: all frontend surfaces in `apps/nextgen_erp`
> Status: **proposed — awaiting approval before implementation**

## Surfaces audited

| Surface | Files | Overall state |
|---|---|---|
| Staff Chat (Typhoon copilot) | `public/js/staff_chat.js` (1,241 lines), `public/css/staff_chat.css` | Feature-rich; has real performance + accessibility defects |
| AI Cockpit dashboard | `page/ai_cockpit/ai_cockpit.{js,css}` | Visually strong; missing loading/error polish and a11y details |
| Public webshop pages | `www/nextgen-catalog.html`, `nextgen-shop-entry.html`, `nextgen-checkout-success.html`, `public/scss/nextgen_midnight.scss` | Most polished surface; small a11y gaps |
| Agent Workflow page | `page/agent_workflow/agent_workflow.js` | Thin Vue-app loader; blank while bundle loads |
| AI Order Intake form/list | `doctype/ai_order_intake/*.js` | Functional; raw JSON shown in success alerts |
| Procurement forecast controls | `public/js/*procurement*.js` | Clean; no changes needed beyond shared dialog polish |
| Invoice print format | `templates/print_formats/customer_invoice.{html,css}` | Clean and print-correct; no action |

---

## P0 — Defects worth fixing first

### 1. Staff Chat: full-list re-render on every streaming delta
`render()` rebuilds the entire message list with `innerHTML` each time a realtime `delta` arrives (staff_chat.js:410-412, 727-761).

- **Cost:** every token re-parses/re-renders all cards, tables and images; scroll is force-pinned to the bottom (`container.scrollTop = container.scrollHeight`), so users cannot scroll up to read during streaming.
- **Screen-reader impact:** the container has `role="log" aria-live="polite"` — replacing all children on each delta makes assistive tech re-announce the whole conversation repeatedly.
- **Fix:** keep a per-message DOM node map; on `delta`, append text only to the pending bubble's content node; only do a full render on structural changes (new message, card, done). Track "user is near bottom" and only auto-scroll then.

### 2. Staff Chat: focus management & keyboard access
- Header buttons (history `☰`, close `×`) have only `title` — no `aria-label`; the `☰`/`×` glyphs announce poorly.
- Opening the panel moves focus into the textarea, but closing never returns focus to the launcher — keyboard users lose their place.
- Agent menu uses `hidden` toggling with no Escape-to-close or outside-click dismissal, and no arrow-key navigation.
- **Fix:** aria-labels, focus restore on close, Escape/outside-click close for the agent menu, `role="menu"`/`menuitem` semantics.

### 3. Staff Chat: streaming UX gaps
- No `prefers-reduced-motion` handling for the `ng-pulse` thinking animation and panel slide transition (the webshop theme has it; the desk chat does not).
- Send button stays hardcoded `#2563eb` blue while the Procurement agent themes everything else green via `--ng-agent-color` — the composer and primary action buttons ignore the agent theme.
- `color-mix()` in `.ng-message-actions button` has no fallback; older WebKit/Firefox loses the tinted background silently.

## P1 — Polish with high perceived-quality payoff

### 4. AI Cockpit: loading, empty and error states
- First load shows an empty shell with a tiny "กำลังโหลดข้อมูล…" — add skeleton cards/charts (shimmer) so the layout doesn't pop.
- On fetch failure the only feedback is one line injected into the KPI grid and a thrown error — add an inline error panel with a **Retry** button.
- `refresh()` recreates three `frappe.Chart` instances every time without cleanup; on period switching this leaks SVG listeners. Destroy or reuse chart instances.

### 5. AI Cockpit: accessibility & touch targets
- KPI cards, attention rows and activity rows are custom `<button>`s with no `:focus-visible` style — keyboard focus is invisible (Desk themes don't cover custom classes).
- Period `<select>` is 28px tall with 11px text — below the 44px touch-target guideline, and 11px inputs trigger iOS auto-zoom.
- Donut/line charts render SVG with no text alternative; add `role="img"` + `aria-label` summarizing the data, or a visually-hidden data table.

### 6. AI Order Intake form: user-facing messages
Success alerts show `Done: {"status":"...raw JSON..."}` (ai_order_intake.js:14). Replace with human messages per action ("อนุมัติแล้ว · สร้าง Sales Order …"), keep JSON in console for debugging.

### 7. Public catalog (nextgen-catalog.html)
- Group filter chips use only a visual `.active` class — add `aria-pressed` and make the group row a `role="toolbar"` with arrow-key support.
- Search input has a placeholder but no `<label>` (add `aria-label` at minimum).
- Toast is not announced — add `role="status" aria-live="polite"`.
- Product images have no `width`/`height` — the aspect-ratio box limits CLS but explicit attrs + `decoding="async"` are cheap wins.

## P2 — Nice to have

### 8. Agent Workflow page: loading affordance
While `agent_workflow.bundle.js` downloads, the page is fully blank. Add a centered spinner/skeleton inside `.layout-main-section` before `frappe.require` resolves.

### 9. Consistency & maintainability
- Staff Chat and AI Cockpit hardcode Thai copy inline; Desk convention is `__()` for translatable strings. Wrap user-facing strings so Thai/English can be managed centrally.
- Two separate accent systems exist (Desk: blue/green agent colors; Web: violet "Midnight" theme). Document the split in one place (or introduce shared `--ng-*` tokens) so future surfaces don't invent a third palette.
- Staff Chat action/forecast/comparison card renderers repeat the same shell (`title / table / warnings / buttons`). Extract a small `cardShell()` helper to cut ~150 lines and unify styling.

---

## Suggested execution order

1. **Batch A (Staff Chat):** items 1–3 — biggest user-facing impact.
2. **Batch B (AI Cockpit + Intake):** items 4–6.
3. **Batch C (Public pages + workflow loader):** items 7–8.
4. **Batch D (refactor):** item 9, optional.

Each batch is independently shippable and testable in the browser without migrations.
