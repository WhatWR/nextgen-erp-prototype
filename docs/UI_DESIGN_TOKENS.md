# UI Design Tokens — NextGen ERP

> How the two NextGen accent systems work, and the rules for adding new UI
> without inventing a third palette.

## Two surfaces, two accent systems

### 1. Desk surfaces (Staff Chat, AI Cockpit, Agent Workflow)

Staff-facing UI follows the Frappe Desk theme and uses **agent-scoped accent
colors**, applied through a single CSS custom property:

```css
#nextgen-chat-panel { --ng-agent-color: #2563eb; }  /* sales: blue */
#nextgen-chat-panel { --ng-agent-color: #0e9f6e; }  /* procurement: green */
```

- `--ng-agent-color` is set at runtime by `applyAgentTheme()` in
  `public/js/staff_chat.js` from the agent record's `color` field.
- Any new chat widget styling should use `var(--ng-agent-color, #2563eb)`
  instead of hardcoding a hex — the fallback keeps the widget correct before
  an agent is selected.
- Everything else (surfaces, borders, text) uses Frappe's own variables:
  `--fg-color`, `--subtle-fg`, `--border-color`, `--text-color`,
  `--text-muted`, `--card-bg`. Always provide the fallback value, e.g.
  `var(--border-color, #e5e7eb)`.
- AI Cockpit additionally defines tone colors at `.ng-command-center`:
  `--ng-blue / --ng-green / --ng-violet / --ng-amber` for KPI and attention
  rows.

### 2. Public website surfaces (catalog, checkout, shop entry, portal)

Customer-facing pages use the **NextGen Midnight** theme
(`public/scss/nextgen_midnight.scss`), injected into the Website Theme's
Custom SCSS at provisioning time:

- Accent: violet (`--ng-accent: #8b5cf6`), dark surfaces
  (`--ng-midnight: #020617` family).
- The theme overrides Frappe's public variables (`--primary`, `--bg-color`,
  `--fg-color`, `--text-color`, …) so standard Frappe website components pick
  it up automatically.
- New public pages should reuse the shared `.ng-*` classes (`.ng-page`,
  `.ng-eyebrow`, `.ng-status-card`, `.ng-grid`, `.ng-card`) before writing
  page-specific CSS.

## Rules for new UI

1. **Desk → agent accent; Web → Midnight violet.** Never mix the two on one
   surface.
2. Prefer Frappe CSS variables with explicit fallbacks over hardcoded colors.
3. New component classes are prefixed `ng-` and live next to their feature
   (page CSS or feature CSS), not in the Midnight theme, unless the component
   is shared across public pages.
4. User-facing strings are wrapped in `__()` in Desk JS; Jinja templates keep
   Thai copy directly (Frappe web translations handle them).
5. Respect `prefers-reduced-motion` for any new transition or animation.
