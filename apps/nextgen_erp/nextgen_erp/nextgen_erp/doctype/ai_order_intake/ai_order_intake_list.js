// List-view quick intake: paste a Thai LINE order, extract via the external
// service, and open the created AI Order Intake.
frappe.listview_settings["AI Order Intake"] = {
	onload(listview) {
		listview.page.add_inner_button(__("New from LINE text"), () => {
			frappe.prompt(
				[
					{ fieldname: "text", label: __("LINE order text"), fieldtype: "Small Text", reqd: 1 },
					{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
				],
				(v) => {
					frappe
						.call({
							method: "nextgen_erp.api.quick_intake",
							args: { text: v.text, customer: v.customer || null },
							freeze: true,
							freeze_message: __("Extracting order..."),
						})
						.then((r) => {
							if (r.message && r.message.name) {
								frappe.set_route("Form", "AI Order Intake", r.message.name);
							} else {
								frappe.msgprint(__("No intake was created."));
							}
						});
				},
				__("New Order from LINE Text"),
				__("Create"),
			);
		});
	},
};
