# Prototype architecture

## Product boundary

```text
LINE / simulator / spreadsheet
              |
              v
   verified channel intake
              |
              v
 Thai normalization + catalog matching
              |
              v
 confidence, stock and policy checks
              |
              v
       human review queue
              |
              v
 controlled adapter contract
        /             \
 CSV/Excel          ERPClaw action API
 customer system    (dry-run in prototype)
```

The AI/order-intake layer is the product. ERPClaw is one replaceable operating
substrate. Connectors do not write directly to ERPClaw tables.

## Decisions

1. **Human approval is mandatory.** Every inbound message creates a draft.
2. **Inbound text is untrusted data.** A customer message never receives general
   OpenClaw or ERP credentials.
3. **ERPClaw is pinned upstream.** Core remains an unmodified submodule. The web
   interface carries a small local branch.
4. **One customer instance first.** The prototype uses a merchant-scoped schema,
   but the production pilot should isolate each customer database and deployment.
5. **The legal ledger stays outside the prototype.** Approval creates CSV and a
   non-executed ERPClaw request artifact. Thai accounting posting follows only
   after CPA/tax validation.
6. **No autonomous module generation.** ERPClaw OS generation/deployment paths
   are not used.

## Production path

Replace the prototype SQLite database with PostgreSQL, introduce authenticated
staff roles, move write-back into a queued connector service, add signed LINE
webhooks and retries, then implement an ERPClaw action adapter using a narrowly
scoped service account. A separate `erpclaw-thailand` module should hold Thai
localization rather than patching upstream core.
