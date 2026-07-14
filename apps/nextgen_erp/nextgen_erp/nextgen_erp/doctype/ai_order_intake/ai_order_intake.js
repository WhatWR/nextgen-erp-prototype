// Copyright (c) 2026, NextGen and contributors
// For license information, please see license.txt

// Desk review workspace for AI Order Intake.
// Status-driven action buttons call the whitelisted nextgen_erp.api methods.

frappe.ui.form.on("AI Order Intake", {
	refresh(frm) {
		if (frm.is_new()) return;
		const status = frm.doc.status;

		const call = (method, args, msg) => {
			frappe.call({ method: `nextgen_erp.api.${method}`, args, freeze: true, freeze_message: msg }).then((r) => {
				frappe.show_alert({ message: __("Done: {0}", [JSON.stringify(r.message)]), indicator: "green" });
				frm.reload_doc();
			});
		};

		if (["Needs Review", "Ready"].includes(status)) {
			frm.add_custom_button(__("Approve"), () =>
				call("approve_ai_order_intake", { name: frm.doc.name, reviewer: frappe.session.user }, __("Approving...")),
			).addClass("btn-primary");
			frm.add_custom_button(__("Reject"), () =>
				call("record_customer_confirmation", { name: frm.doc.name, confirmed: 0 }, __("Rejecting...")),
			);
		}

		if (status === "Awaiting Customer") {
			frm.add_custom_button(__("Customer Confirmed"), () =>
				call("record_customer_confirmation", { name: frm.doc.name, confirmed: 1 }, __("Creating Sales Order...")),
			).addClass("btn-primary");
			frm.add_custom_button(__("Customer Declined"), () =>
				call("record_customer_confirmation", { name: frm.doc.name, confirmed: 0 }, __("Rejecting...")),
			);
		}

		if (status === "Reserved") {
			frm.add_custom_button(__("Create Invoice"), () =>
				call("progress_delivery", { name: frm.doc.name }, __("Creating invoice...")),
			).addClass("btn-primary");
		}

		if (status === "Awaiting Payment") {
			frm.add_custom_button(__("Record Payment"), () => {
				frappe.prompt(
					[{ fieldname: "reference_no", label: __("Bank Reference"), fieldtype: "Data", reqd: 1 }],
					(v) => call("progress_payment", { name: frm.doc.name, reference_no: v.reference_no }, __("Recording payment...")),
					__("Record Payment"),
					__("Submit"),
				);
			}).addClass("btn-primary");
		}

		if (status === "Payment Review") {
			frm.add_custom_button(__("Approve Payment Slip"), () => {
				frappe.prompt(
					[{ fieldname: "reference_no", label: __("Bank Reference"), fieldtype: "Data", reqd: 1 }],
					(v) => call("approve_payment_slip", { name: frm.doc.name, reference_no: v.reference_no }, __("Approving payment...")),
					__("Approve Payment Slip"),
					__("Submit"),
				);
			}).addClass("btn-primary");
		}

		if (status === "Ready for Delivery") {
			frm.add_custom_button(__("Complete Delivery"), () =>
				call("complete_delivery", { name: frm.doc.name }, __("Completing delivery...")),
			).addClass("btn-primary");
		}

		const colours = {
			"Needs Review": "orange",
			Ready: "blue",
			"Awaiting Customer": "yellow",
			Reserved: "purple",
			"Awaiting Payment": "orange",
			"Payment Review": "orange",
			"Ready for Delivery": "blue",
			Delivered: "green",
			Paid: "green",
			Rejected: "red",
		};
		if (colours[status]) frm.page.set_indicator(status, colours[status]);
	},
});
