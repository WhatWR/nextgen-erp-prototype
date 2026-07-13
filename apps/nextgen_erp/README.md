# NextGen ERP

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

## License

MIT
