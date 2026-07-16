/* global frappe */

frappe.listview_settings["NextGen Procurement Forecast"] = {
	onload(listview) {
		const generate = () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Generate Procurement Forecast"),
				fields: [
					{
						fieldname: "item",
						fieldtype: "Link",
						options: "Item",
						label: __("Item (optional)"),
						get_query: () => ({ filters: { disabled: 0, is_stock_item: 1, is_purchase_item: 1 } }),
					},
					{
						fieldname: "warehouse",
						fieldtype: "Link",
						options: "Warehouse",
						label: __("Warehouse (optional)"),
						get_query: () => ({ filters: { disabled: 0, is_group: 0 } }),
					},
					{ fieldname: "horizon_days", fieldtype: "Int", label: __("Horizon (Days)"), default: 30, reqd: 1 },
					{ fieldname: "limit", fieldtype: "Int", label: __("Maximum Items"), default: 50 },
				],
				primary_action_label: __("Generate Forecast"),
				primary_action: async (values) => {
					const button = dialog.get_primary_btn();
					button.prop("disabled", true);
					try {
						const response = await frappe.call({
							method: "nextgen_erp.forecast.generate_forecasts",
							args: {
								item_codes: values.item ? JSON.stringify([values.item]) : null,
								warehouse: values.warehouse || null,
								horizon_days: values.horizon_days,
								limit: values.item ? 1 : values.limit,
							},
							freeze: true,
							freeze_message: __("Generating forecasts from ERP data..."),
						});
						const result = response.message || {};
						dialog.hide();
						listview.refresh();
						frappe.show_alert({
							message: __(`Generated ${result.generated || 0} forecast(s)${result.failed ? `, ${result.failed} failed` : ""}`),
							indicator: result.failed ? "orange" : "green",
						});
					} catch (error) {
						frappe.msgprint(error?.message || __("Could not generate forecast"));
					} finally {
						button.prop("disabled", false);
					}
				},
			});
			dialog.show();
		};
		// Frappe hides a List primary action when the role cannot manually create
		// the DocType. Forecast snapshots are intentionally API-generated, so use
		// a dedicated visible toolbar button without granting manual Create access.
		const button = listview.page.add_inner_button(__("Generate Forecast"), generate);
		button.addClass("btn-primary").removeClass("btn-default hide");
		// Keep the command visible at every Desk breakpoint. Frappe's default
		// custom-actions wrapper hides labels on medium/mobile widths.
		button.detach().prependTo(listview.page.wrapper.find(".page-actions"));
	},
};
