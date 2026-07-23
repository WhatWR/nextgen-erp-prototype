import frappe

from nextgen_erp.api import make_invoice_download_url, make_promptpay_qr_url
from nextgen_erp.webshop import _current_shop_session


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw("Please open the catalog from LINE", frappe.PermissionError)
	session = _current_shop_session(frappe.session.user)
	name = frappe.form_dict.get("order")
	doc = frappe.get_doc("AI Order Intake", name)
	if doc.customer != session.customer or doc.line_ref != session.line_ref:
		frappe.throw("This order does not belong to this shop session", frappe.PermissionError)
	context.no_cache = 1
	context.title = "ยืนยันออเดอร์แล้ว"
	context.order = doc
	context.invoice_url = (
		make_invoice_download_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else None
	)
	context.qr_url = (
		make_promptpay_qr_url(doc.sales_invoice, doc.line_ref) if doc.sales_invoice else None
	)
	return context
