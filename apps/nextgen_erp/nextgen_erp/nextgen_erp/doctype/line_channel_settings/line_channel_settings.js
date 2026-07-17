// Copyright (c) 2026, NextGen and contributors
// For license information, please see license.txt

frappe.ui.form.on("LINE Channel Settings", {
	setup(frm) {
		frm.set_query("selling_warehouse", () => ({
			filters: {
				company: frm.doc.company || "",
				is_group: 0,
				disabled: 0,
			},
		}));
	},
	company(frm) {
		if (frm.doc.selling_warehouse) frm.set_value("selling_warehouse", null);
	},
});
