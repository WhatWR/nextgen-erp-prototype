// Copyright (c) 2026, NextGen and contributors
// For license information, please see license.txt

// Desk review workspace for AI Order Intake.
// Status-driven action buttons call the whitelisted nextgen_erp.api methods.

frappe.ui.form.on("AI Order Intake", {
	refresh(frm) {
		if (frm.is_new()) return;
		const status = frm.doc.status;

		const success_message = (method, args) => {
			const messages = {
				approve_ai_order_intake: () => __("อนุมัติออเดอร์แล้ว กำลังส่งให้ลูกค้ายืนยัน"),
				record_customer_confirmation: () =>
					args.confirmed
						? __("ลูกค้ายืนยันแล้ว สร้าง Sales Order และจองสต็อกเรียบร้อย")
						: __("บันทึกการปฏิเสธแล้ว"),
				progress_delivery: () => __("สร้างใบแจ้งหนี้เรียบร้อย"),
				progress_payment: () => __("บันทึกการชำระเงินเรียบร้อย"),
				approve_payment_slip: () => __("อนุมัติสลิปการชำระเงินแล้ว"),
				reject_payment_slip: () => __("ปฏิเสธสลิปแล้ว แจ้งลูกค้าผ่าน LINE"),
				complete_delivery: () => __("บันทึกการจัดส่งเรียบร้อย"),
			};
			return (messages[method] || (() => __("Done")))();
		};

		const call = (method, args, msg) => {
			frappe.call({ method: `nextgen_erp.api.${method}`, args, freeze: true, freeze_message: msg }).then((r) => {
				console.debug(`AI Order Intake · ${method}`, r.message);
				frappe.show_alert({ message: success_message(method, args), indicator: "green" });
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
			let extraction = {};
			try {
				extraction = JSON.parse(frm.doc.payment_extraction_json || "{}");
			} catch (_error) {
				extraction = {};
			}
			const flags = (extraction.flags || []).join(", ") || __("None");
			const amount = extraction.amount == null ? __("Not found") : format_currency(extraction.amount, "THB");
			const recipient = extraction.recipient || __("Not found");
			frm.set_intro(
				__(
					"OCR extracted amount: {0}; recipient: {1}; warnings: {2}. Compare with bank activity before approval.",
					[
						frappe.utils.escape_html(String(amount)),
						frappe.utils.escape_html(String(recipient)),
						frappe.utils.escape_html(String(flags)),
					],
				),
				"orange",
			);
			frm.add_custom_button(__("Approve Payment Slip"), () => {
				frappe.prompt(
					[{
						fieldname: "reference_no",
						label: __("Bank Reference"),
						fieldtype: "Data",
						reqd: 1,
						default: frm.doc.payment_reference || "",
					}],
					(v) => call("approve_payment_slip", { name: frm.doc.name, reference_no: v.reference_no }, __("Approving payment...")),
					__("Approve Payment Slip"),
					__("Submit"),
				);
			}).addClass("btn-primary");
			frm.add_custom_button(__("Reject Slip"), () => {
				frappe.prompt(
					[{ fieldname: "note", label: __("Reason"), fieldtype: "Small Text", reqd: 1 }],
					(v) => call("reject_payment_slip", { name: frm.doc.name, note: v.note }, __("Rejecting slip...")),
					__("Reject Payment Slip"),
					__("Submit"),
				);
			});
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
