# NextGen ERP

> **Document status:** Current application capability and installation notes. See the [canonical AI handoff](../../docs/AI_HANDOFF/README.md) for target architecture and implementation phases.

Frappe app for confidence-gated Thai/LINE order intake on ERPNext. It provides
the review queue, LINE configuration/customer mapping, durable event receipts,
stock reservation and the Sales Order → Delivery Note → invoice → payment
lifecycle.

## Install

From a Frappe v16.20 bench with ERPNext v16.20 installed:

```bash
bench get-app /absolute/path/to/nextgen-erp-prototype/apps/nextgen_erp
bench --site your-site install-app nextgen_erp
bench --site your-site migrate
```

The install hooks create the custom fields and scoped service role/user. Generate
the service user's API credentials in ERPNext and keep them outside source
control. See the repository's `docs/ERPNEXT_MIGRATION.md` for full setup.

Migration also creates the `NextGen Agent Runtime` role and one disabled
`NextGen Automation Policy` per company, then backfills existing chat actions
into generic Agent Run and Action Proposal records. Both patches are safe to
rerun and delete nothing.

## Agent gateway

`agent_gateway`, `agent_records`, `action_proposals` and `domain_tools` are the
Frappe side of the boundary to the separately deployed
[Agent Runtime microservice](../../services/agent-runtime/README.md). Frappe
keeps authentication context, company policy, the durable audit, approvals and
every ERP write; the runtime owns model calls and the tool loop only. Everything
ships disabled — see [docs/AGENT_RUNTIME.md](../../docs/AGENT_RUNTIME.md).

## License

MIT
