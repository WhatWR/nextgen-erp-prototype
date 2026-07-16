/* global frappe */

frappe.ui.form.on("NextGen Procurement Forecast", {
	refresh(frm) {
		if (!frm.doc.item) return;
		frm.add_custom_button(__("Generate New Forecast"), async () => {
			const response = await frappe.call({
				method: "nextgen_erp.forecast.generate_forecasts",
				args: {
					item_codes: JSON.stringify([frm.doc.item]),
					warehouse: frm.doc.warehouse || null,
					horizon_days: frm.doc.horizon_days || 30,
					limit: 1,
				},
				freeze: true,
				freeze_message: __("Generating forecast from ERP data..."),
			});
			const created = response.message?.forecasts?.[0];
			if (created?.snapshot) frappe.set_route("Form", "NextGen Procurement Forecast", created.snapshot);
		}, __("Actions"));
	},
});
