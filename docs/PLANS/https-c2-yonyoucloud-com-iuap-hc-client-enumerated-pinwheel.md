# Plan: Add Detail Pages to Yonyou U8C Financial Accounting Learning Hub

## Context
The user has an interactive learning widget (rendered via `show_widget`) covering all 8 Yonyou U8C Financial Accounting modules and their complete 3-level sidebar hierarchy (~250+ menu items). They want each menu item to be clickable and show a detail panel explaining what the function does and how it's used — making it a proper reference tool, not just a tree explorer.

## Approach

Rebuild the widget with a **master-detail layout**:

- **Left pane**: existing module tree (tab → sections accordion → item list)
- **Right pane** (or slide-in drawer on click): detail card for the selected item

Each detail card will contain:
1. **Function name** (heading)
2. **Module / Section** breadcrumb
3. **What it does** — 1–2 sentence description
4. **Key inputs / fields** — bullet list of main data fields or options
5. **When to use** — practical business scenario
6. **Related items** — 2–3 linked items in same section

Since there is no external data source, descriptions will be authored directly in the widget JavaScript data structure, covering all ~250 items grouped by module and section.

## Implementation Steps

1. **Extend `DATA` structure** — add a `detail` object to each item entry:
   ```js
   { name: 'Voucher', detail: { desc: '...', fields: [...], when: '...', related: [...] } }
   ```

2. **Layout change** — switch from single-column to two-column flex layout:
   - Left: `width: 340px`, scrollable tree
   - Right: `flex:1`, sticky detail panel with placeholder when nothing selected

3. **Item click handler** — clicking any `item-row` calls `showDetail(modId, sectionName, item)` which populates the right panel

4. **Detail panel HTML** — breadcrumb + card with sections for description, fields, when-to-use, related links

5. **Mobile/narrow fallback** — below 600px, detail panel slides up as a bottom sheet overlay

6. **Quiz** — keep existing 15-question quiz tab unchanged

7. **Search** — keep existing search; clicking a search result also triggers `showDetail`

## Verification
- Render the widget and click several items across different modules
- Confirm detail panel updates correctly and breadcrumb is accurate
- Confirm accordion expand/collapse and search still work
- Confirm quiz tab is unaffected
- Test on narrow viewport (< 600px) for bottom-sheet behavior
