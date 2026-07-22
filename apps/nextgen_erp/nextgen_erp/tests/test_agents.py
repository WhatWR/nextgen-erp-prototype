import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from nextgen_erp import agents, staff_chat

SALES_ONLY_USER = "sales-only@nextgen.test"
PURCHASE_ONLY_USER = "purchase-only@nextgen.test"


def _ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles],
			}
		).insert(ignore_permissions=True)
	else:
		user = frappe.get_doc("User", email)
		user.roles = []
		for role in roles:
			user.append("roles", {"role": role})
		user.save(ignore_permissions=True)


class IntegrationTestAgentRouting(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_user(SALES_ONLY_USER, ["Sales User"])
		_ensure_user(PURCHASE_ONLY_USER, ["Purchase User"])
		frappe.db.commit()

	def setUp(self):
		frappe.set_user("Administrator")

	def test_registry_is_complete(self):
		self.assertEqual(set(agents.AGENTS), {"sales", "procurement"})
		for agent in agents.AGENTS.values():
			self.assertTrue(agent.title and agent.icon and agent.color)
			self.assertTrue(agent.required_roles)
			self.assertTrue(agent.welcome_message)
			self.assertTrue(agent.suggested_questions)
			self.assertTrue(agent.route_keywords)
			self.assertTrue(agent.tool_names)

	def test_procurement_suggestions_are_resolved_from_live_erp_data(self):
		procurement_agent = agents.AGENTS["procurement"]
		self.assertNotIn("M-150", "\n".join(procurement_agent.suggested_questions))
		questions = procurement_agent.public_config()["suggested_questions"]
		self.assertTrue(questions)
		self.assertNotIn("{item}", "\n".join(questions))

	def test_sales_route_selects_sales_agent(self):
		self.assertEqual(agents.resolve_route_agent("Workspaces/AI Sales Copilot"), "sales")
		self.assertEqual(agents.resolve_route_agent("Workspaces/Selling"), "sales")
		self.assertEqual(agents.resolve_route_agent("Form/Sales Order/SO-0001"), "sales")

	def test_buying_route_selects_procurement_agent(self):
		self.assertEqual(agents.resolve_route_agent("Workspaces/AI Procurement Copilot"), "procurement")
		self.assertEqual(agents.resolve_route_agent("Workspaces/Buying"), "procurement")
		self.assertEqual(agents.resolve_route_agent("Form/Purchase Order/PO-0001"), "procurement")
		self.assertEqual(agents.resolve_route_agent("List/Material Request"), "procurement")

	def test_other_routes_resolve_to_none(self):
		self.assertIsNone(agents.resolve_route_agent("Workspaces/Home"))
		self.assertIsNone(agents.resolve_route_agent(""))
		self.assertIsNone(agents.resolve_route_agent(None))

	def test_action_types_map_to_their_agent(self):
		self.assertEqual(agents.agent_for_action_type("prepare_sales_order").key, "sales")
		self.assertEqual(agents.agent_for_action_type("prepare_purchase_order").key, "procurement")
		self.assertEqual(agents.agent_for_action_type("prepare_material_request").key, "procurement")

	def test_sales_agent_cannot_invoke_procurement_tools(self):
		result = agents.AGENTS["sales"].dispatch(
			"prepare_purchase_order", {}, user="Administrator", session_id="x"
		)
		self.assertIn("error", result)
		result = agents.AGENTS["sales"].dispatch(
			"forecast_item_demand", {"item_code": "DRK-M150"}, user="Administrator", session_id="x"
		)
		self.assertIn("error", result)

	def test_procurement_agent_cannot_invoke_sales_write_tools(self):
		result = agents.AGENTS["procurement"].dispatch(
			"prepare_sales_order", {"customer": "x", "items": []}, user="Administrator", session_id="x"
		)
		self.assertIn("error", result)

	def test_sales_only_user_cannot_use_procurement_agent(self):
		self.assertFalse(agents.AGENTS["procurement"].has_access(SALES_ONLY_USER))
		self.assertTrue(agents.AGENTS["sales"].has_access(SALES_ONLY_USER))
		frappe.set_user(SALES_ONLY_USER)
		with self.assertRaises(frappe.PermissionError):
			agents.require_agent_access("procurement")
		frappe.set_user("Administrator")

	def test_purchase_only_user_cannot_use_sales_agent(self):
		self.assertFalse(agents.AGENTS["sales"].has_access(PURCHASE_ONLY_USER))
		self.assertTrue(agents.AGENTS["procurement"].has_access(PURCHASE_ONLY_USER))

	def test_session_agent_cannot_be_silently_changed(self):
		frappe.db.set_single_value("NextGen AI Settings", "enable_staff_chat", 1)
		frappe.db.set_single_value("NextGen Procurement Settings", "enable_procurement_copilot", 1)
		session = frappe.get_doc(
			{
				"doctype": "NextGen Chat Session",
				"user": "Administrator",
				"agent_type": "sales",
				"title": "pinned",
				"status": "Open",
				"last_activity_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			staff_chat.start_turn(
				session_id=session.name, message="switch please", agent_type="procurement"
			)

	def test_cross_user_session_access_is_rejected(self):
		session = frappe.get_doc(
			{
				"doctype": "NextGen Chat Session",
				"user": SALES_ONLY_USER,
				"agent_type": "sales",
				"title": "private",
				"status": "Open",
				"last_activity_at": now_datetime(),
			}
		).insert(ignore_permissions=True)
		frappe.set_user(PURCHASE_ONLY_USER)
		try:
			with self.assertRaises(frappe.DoesNotExistError):
				staff_chat.get_session(session.name)
		finally:
			frappe.set_user("Administrator")

	def test_unknown_agent_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			agents.get_agent("finance")

	def test_procurement_prompt_and_tools_are_isolated(self):
		procurement_agent = agents.AGENTS["procurement"]
		self.assertIn("forecast_item_demand", procurement_agent.tool_names)
		self.assertNotIn("prepare_sales_order", procurement_agent.tool_names)
		sales_agent = agents.AGENTS["sales"]
		self.assertIn("prepare_sales_order", sales_agent.tool_names)
		self.assertNotIn("prepare_purchase_order", sales_agent.tool_names)
		self.assertIn("ห้ามแต่งเลขเอกสาร", procurement_agent.system_prompt)
