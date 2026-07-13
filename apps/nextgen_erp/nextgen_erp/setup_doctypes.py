"""Idempotent creation of the NextGen ERP DocTypes.

Run once (developer_mode on) to materialise the DocTypes and export their JSON
into the app:

    bench --site nextgen.localhost execute nextgen_erp.setup_doctypes.run

After that the JSON files under nextgen_erp/nextgen_erp/doctype/ are the source
of truth and `bench migrate` recreates them on any site.
"""

import json

import frappe

MODULE = "NextGen ERP"


def run():
    from nextgen_erp.install import after_migrate

    after_migrate()
    _order_intake_item()
    _order_intake()
    _line_customer_map()
    _automation_settings()
    _line_settings()
    card_map = _number_cards()
    _workspace(card_map)
    _branding()
    _desk_tile()
    frappe.db.commit()
    return "ok"


def _desk_tile():
    """Add an 'Order Agent' tile to the /desk launcher that opens the workspace.

    The legacy /desk desktop shows Desktop Icons whose is_permitted() requires a
    matching Workspace Sidebar (keyed by label). Create both.
    """
    if frappe.db.exists("Workspace Sidebar", "Order Agent"):
        frappe.delete_doc("Workspace Sidebar", "Order Agent", force=True, ignore_permissions=True)
    sidebar = frappe.get_doc(
        {
            "doctype": "Workspace Sidebar",
            "title": "Order Agent",
            "module": MODULE,
            "header_icon": "sell",
            "items": [
                {"type": "Link", "label": "Order Agent", "link_type": "Workspace", "link_to": "Order Agent", "icon": "sell", "idx": 1},
                {"type": "Link", "label": "AI Order Intake", "link_type": "DocType", "link_to": "AI Order Intake", "idx": 2},
                {"type": "Link", "label": "LINE Customer Map", "link_type": "DocType", "link_to": "LINE Customer Map", "idx": 3},
            ],
        }
    )
    sidebar.flags.ignore_links = True
    sidebar.insert(ignore_permissions=True)

    existing = frappe.db.get_value("Desktop Icon", {"label": "Order Agent"})
    if existing:
        frappe.delete_doc("Desktop Icon", existing, force=True, ignore_permissions=True)
    icon = frappe.get_doc(
        {
            "doctype": "Desktop Icon",
            "label": "Order Agent",
            "icon_type": "Link",
            "link_type": "Workspace Sidebar",
            "link_to": "Order Agent",
            "app": "erpnext",
            "parent_icon": "ERPNext",
            "icon": "sell",
            "logo_url": ICON,
            "standard": 1,
            "hidden": 0,
        }
    )
    icon.flags.ignore_links = True
    icon.insert(ignore_permissions=True)

    from frappe.desk.doctype.desktop_icon.desktop_icon import clear_desktop_icons_cache

    clear_desktop_icons_cache("Administrator")


LOGO = "/assets/nextgen_erp/images/nextgen-logo.svg"
ICON = "/assets/nextgen_erp/images/nextgen-icon.svg"


def _branding():
    """Point ERPNext's app logo / favicon at the NextGen Order AI brand.

    get_app_logo() checks Website + Navbar Settings before the (order-fragile)
    app_logo_url hook, so set them explicitly for a deterministic result.
    """
    ws = frappe.get_single("Website Settings")
    ws.app_logo = LOGO
    ws.app_name = "NextGen Order AI"
    if not ws.get("favicon"):
        ws.favicon = ICON
    ws.save(ignore_permissions=True)

    nb = frappe.get_single("Navbar Settings")
    nb.app_logo = LOGO
    nb.save(ignore_permissions=True)


def _line_settings():
    _make(
        {
            "name": "LINE Channel Settings",
            "module": MODULE,
            "issingle": 1,
            "fields": [
                {"fieldname": "enabled", "label": "Enable Incoming LINE Orders", "fieldtype": "Check", "default": 0},
                {"fieldname": "merchant", "label": "Merchant", "fieldtype": "Data", "default": "demo"},
                {"fieldname": "channel_id", "label": "LINE Channel ID", "fieldtype": "Data"},
                {"fieldname": "channel_secret", "label": "Channel Secret", "fieldtype": "Password", "description": "Verifies the X-Line-Signature on incoming events"},
                {"fieldname": "channel_access_token", "label": "Channel Access Token", "fieldtype": "Password", "description": "Required to push confirmations/updates back to customers"},
                {"fieldname": "webhook_url", "label": "Webhook URL", "fieldtype": "Data", "default": "http://127.0.0.1:8200/webhooks/line", "description": "Public HTTPS address ending in /webhooks/line"},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            ],
        }
    )


CARD_STATUSES = ["Needs Review", "Awaiting Customer", "Reserved", "Paid"]


def _number_cards() -> dict:
    """Create one count card per status; return {status: card_name}."""
    card_map = {}
    for status in CARD_STATUSES:
        label = f"NextGen {status}"
        existing = frappe.db.get_value(
            "Number Card", {"label": label, "document_type": "AI Order Intake"}, "name"
        )
        if existing:
            card_map[status] = existing
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Number Card",
                "label": label,
                "module": MODULE,
                "document_type": "AI Order Intake",
                "type": "Document Type",
                "function": "Count",
                "is_public": 1,
                "filters_json": json.dumps([["AI Order Intake", "status", "=", status, False]]),
                "color": "#f59e0b" if status == "Needs Review" else "#5e64ff",
            }
        ).insert(ignore_permissions=True)
        card_map[status] = doc.name
    frappe.db.commit()
    return card_map


def _workspace(card_map: dict):
    # Force-recreate so re-runs pick up new cards/links (drop legacy + current name).
    for legacy in ("Order Agent", "NextGen Orders"):
        if frappe.db.exists("Workspace", legacy):
            frappe.delete_doc("Workspace", legacy, ignore_permissions=True, force=True)
    content = [
        {"id": "logo", "type": "paragraph", "data": {"text": '<img src="/assets/nextgen_erp/images/nextgen-logo.svg" alt="NextGen Order AI" style="height:44px">', "col": 12}},
        {"id": "hdr", "type": "header", "data": {"text": '<span class="h4">Order Intake</span>', "col": 12}},
        {"id": "sc1", "type": "shortcut", "data": {"shortcut_name": "Review Queue", "col": 4}},
        {"id": "sc2", "type": "shortcut", "data": {"shortcut_name": "LINE Channel Settings", "col": 4}},
        {"id": "sc3", "type": "shortcut", "data": {"shortcut_name": "Automation Settings", "col": 4}},
    ]
    content += [
        {"id": f"nc{i}", "type": "number_card", "data": {"number_card_name": name, "col": 3}}
        for i, name in enumerate(card_map.values())
    ]
    frappe.get_doc(
        {
            "doctype": "Workspace",
            "name": "Order Agent",
            "title": "Order Agent",
            "label": "Order Agent",
            "module": MODULE,
            "public": 1,
            "icon": "sell",
            "content": json.dumps(content),
            "number_cards": [{"number_card_name": name} for name in card_map.values()],
            "shortcuts": [
                {"label": "Review Queue", "link_to": "AI Order Intake", "type": "DocType", "color": "Orange"},
                {"label": "LINE Channel Settings", "link_to": "LINE Channel Settings", "type": "DocType", "color": "Green"},
                {"label": "Automation Settings", "link_to": "NextGen Automation Settings", "type": "DocType", "color": "Grey"},
            ],
            "links": [
                {"label": "Order Intake", "type": "Card Break"},
                {"label": "AI Order Intake", "link_to": "AI Order Intake", "link_type": "DocType", "type": "Link"},
                {"label": "LINE Customer Map", "link_to": "LINE Customer Map", "link_type": "DocType", "type": "Link"},
                {"label": "Integrations", "type": "Card Break"},
                {"label": "LINE Channel Settings", "link_to": "LINE Channel Settings", "link_type": "DocType", "type": "Link"},
                {"label": "NextGen Automation Settings", "link_to": "NextGen Automation Settings", "link_type": "DocType", "type": "Link"},
            ],
        }
    ).insert(ignore_permissions=True)


def _make(defn: dict):
    name = defn["name"]
    if frappe.db.exists("DocType", name):
        return f"exists:{name}"
    doc = frappe.get_doc({"doctype": "DocType", "custom": 0, **defn})
    doc.insert(ignore_permissions=True)
    return f"created:{name}"


def _order_intake_item():
    _make(
        {
            "name": "AI Order Intake Item",
            "module": MODULE,
            "istable": 1,
            "editable_grid": 1,
            "fields": [
                {"fieldname": "raw_text", "label": "Raw Text", "fieldtype": "Data", "in_list_view": 1},
                {"fieldname": "item", "label": "Item", "fieldtype": "Link", "options": "Item", "in_list_view": 1},
                {"fieldname": "item_name", "label": "Item Name", "fieldtype": "Data", "fetch_from": "item.item_name", "read_only": 1},
                {"fieldname": "qty", "label": "Qty", "fieldtype": "Float", "in_list_view": 1, "default": 1},
                {"fieldname": "uom", "label": "UOM", "fieldtype": "Link", "options": "UOM", "in_list_view": 1},
                {"fieldname": "rate", "label": "Rate", "fieldtype": "Currency", "in_list_view": 1},
                {"fieldname": "amount", "label": "Amount", "fieldtype": "Currency", "read_only": 1},
                {"fieldname": "confidence", "label": "Confidence", "fieldtype": "Float"},
                {"fieldname": "exception_reason", "label": "Exception Reason", "fieldtype": "Data"},
            ],
        }
    )


def _order_intake():
    _make(
        {
            "name": "AI Order Intake",
            "module": MODULE,
            "autoname": "AIO-.#####",
            "track_changes": 1,
            "sort_field": "modified",
            "sort_order": "DESC",
            "fields": [
                {"fieldname": "sb_source", "label": "Source", "fieldtype": "Section Break"},
                {"fieldname": "merchant", "label": "Merchant", "fieldtype": "Data", "default": "demo"},
                {"fieldname": "line_ref", "label": "LINE Ref", "fieldtype": "Data"},
                {"fieldname": "source_channel", "label": "Source Channel", "fieldtype": "Select", "options": "simulator\nline\nspreadsheet\ndesk", "default": "simulator"},
                {"fieldname": "cb1", "fieldtype": "Column Break"},
                {"fieldname": "customer", "label": "Customer", "fieldtype": "Link", "options": "Customer", "in_standard_filter": 1},
                {"fieldname": "idempotency_key", "label": "Idempotency Key", "fieldtype": "Data", "unique": 1, "reqd": 1},
                {"fieldname": "source_text", "label": "Source Message", "fieldtype": "Small Text"},
                {"fieldname": "sb_review", "label": "Review", "fieldtype": "Section Break"},
                {"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "Needs Review\nReady\nAwaiting Customer\nReserved\nAwaiting Payment\nPaid\nRejected", "default": "Needs Review", "in_list_view": 1, "in_standard_filter": 1},
                {"fieldname": "automation_mode", "label": "Automation Mode", "fieldtype": "Select", "options": "automatic\nhuman_review", "default": "human_review"},
                {"fieldname": "confidence", "label": "Confidence", "fieldtype": "Float", "in_list_view": 1},
                {"fieldname": "cb2", "fieldtype": "Column Break"},
                {"fieldname": "total", "label": "Total", "fieldtype": "Currency", "in_list_view": 1},
                {"fieldname": "reviewer", "label": "Reviewer", "fieldtype": "Data", "read_only": 1},
                {"fieldname": "decision_note", "label": "Decision Note", "fieldtype": "Small Text"},
                {"fieldname": "exception_reasons", "label": "Exception Reasons", "fieldtype": "Small Text", "description": "JSON list of exception reasons from extraction"},
                {"fieldname": "sb_items", "label": "Items", "fieldtype": "Section Break"},
                {"fieldname": "items", "label": "Items", "fieldtype": "Table", "options": "AI Order Intake Item"},
                {"fieldname": "sb_erp", "label": "ERPNext Documents", "fieldtype": "Section Break"},
                {"fieldname": "sales_order", "label": "Sales Order", "fieldtype": "Link", "options": "Sales Order", "read_only": 1},
                {"fieldname": "pick_list", "label": "Pick List", "fieldtype": "Link", "options": "Pick List", "read_only": 1},
                {"fieldname": "delivery_note", "label": "Delivery Note", "fieldtype": "Link", "options": "Delivery Note", "read_only": 1},
                {"fieldname": "cb3", "fieldtype": "Column Break"},
                {"fieldname": "sales_invoice", "label": "Sales Invoice", "fieldtype": "Link", "options": "Sales Invoice", "read_only": 1},
                {"fieldname": "payment_entry", "label": "Payment Entry", "fieldtype": "Link", "options": "Payment Entry", "read_only": 1},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "share": 1},
                {"role": "Sales User", "read": 1, "write": 1, "create": 1},
                {"role": "NextGen Order Service", "read": 1, "write": 1, "create": 1},
            ],
        }
    )


def _line_customer_map():
    _make(
        {
            "name": "LINE Customer Map",
            "module": MODULE,
            "autoname": "field:line_id",
            "fields": [
                {"fieldname": "line_id", "label": "LINE ID", "fieldtype": "Data", "unique": 1, "reqd": 1, "in_list_view": 1},
                {"fieldname": "customer", "label": "Customer", "fieldtype": "Link", "options": "Customer", "reqd": 1, "in_list_view": 1},
                {"fieldname": "display_name", "label": "Display Name", "fieldtype": "Data", "in_list_view": 1},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
                {"role": "Sales User", "read": 1, "write": 1, "create": 1},
            ],
        }
    )


def _automation_settings():
    _make(
        {
            "name": "NextGen Automation Settings",
            "module": MODULE,
            "issingle": 1,
            "fields": [
                {"fieldname": "confidence_threshold", "label": "Confidence Threshold", "fieldtype": "Float", "default": 0.95},
                {"fieldname": "auto_confirm", "label": "Auto-route High Confidence to Customer Confirmation", "fieldtype": "Check", "default": 1},
                {"fieldname": "invoice_link_days", "label": "Invoice Link Validity (Days)", "fieldtype": "Int", "default": 7},
                {"fieldname": "external_service_url", "label": "External Service URL", "fieldtype": "Data", "description": "order-intake-api base URL for LINE reply webhooks"},
                {"fieldname": "external_service_api_key", "label": "External Service API Key", "fieldtype": "Password"},
            ],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
            ],
        }
    )
