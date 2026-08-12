"""The generic document capability — the app's highest-risk surface.

Most of these tests assert a refusal. That is deliberate: the value of this
module is what it will not do, and the controls are layered, so each layer is
tested on its own rather than only through the happy path.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp.action_proposals import service as proposals
from nextgen_erp.agent_gateway import policy
from nextgen_erp.agent_records import runs
from nextgen_erp.domain_tools import documents
from nextgen_erp.domain_tools.registry import acting_as

TEST_ITEM_NAME = "NextGen Assistant Test Item"


def _ensure_user(email: str, roles: list[str]):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role} for role in roles if frappe.db.exists("Role", role)],
			}
		).insert(ignore_permissions=True)
		return
	user = frappe.get_doc("User", email)
	user.roles = []
	for role in roles:
		if frappe.db.exists("Role", role):
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)


class DocumentToolTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
		if cls.company:
			cls._reset_policy()

	@classmethod
	def _reset_policy(cls):
		"""Re-assert policy state before every test.

		Execution and supersede commit by design, which can also commit a
		policy change a test made moments earlier. Rebuilding the expected
		state per test is more robust than unwinding it.
		"""
		doc = policy.ensure_policy(cls.company)
		doc.enabled = 1
		doc.default_mode = policy.APPROVAL_REQUIRED
		doc.writable_doctypes = "Item"
		doc.save(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def _cleanup(cls):
		"""Execution commits by design, so its rows outlive a test rollback."""
		for name in frappe.get_all(
			"Item", filters={"item_code": ["like", "NextGen%Test%"]}, pluck="name"
		):
			frappe.delete_doc("Item", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		if getattr(cls, "company", None):
			cls._cleanup()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		if not self.company:
			self.skipTest("no Company exists on this site")
		self._cleanup()
		self._reset_policy()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def _prepare(self, doctype: str, values: dict):
		return documents.dispatch_tool(
			"prepare_document",
			{"doctype": doctype, "values": values},
			user="Administrator",
			run=None,
		)

	def _item_values(self, name: str = TEST_ITEM_NAME):
		return {"item_code": name, "item_name": name, "item_group": "All Item Groups"}


class IntegrationTestDenylist(DocumentToolTestCase):
	def test_code_executing_doctypes_are_refused_even_for_administrator(self):
		# Administrator can create a Server Script in Desk. Through an agent it
		# would be remote code execution, so the denylist sits above the role.
		self.assertIn("System Manager", frappe.get_roles("Administrator"))
		for doctype in ("Server Script", "Client Script", "Webhook"):
			with self.subTest(doctype=doctype):
				result = self._prepare(doctype, {"script": "print(1)"})
				self.assertIn("error", result)
				self.assertIn("cannot be accessed", result["error"])

	def test_permission_doctypes_are_refused(self):
		for doctype in ("User", "Role", "Custom DocPerm", "Custom Field"):
			with self.subTest(doctype=doctype):
				self.assertTrue(documents.is_denied(doctype))
				self.assertIn("error", self._prepare(doctype, {}))

	def test_the_gateways_own_records_are_refused(self):
		for doctype in ("NextGen Agent Run", "NextGen Automation Policy", "LINE Channel Settings"):
			with self.subTest(doctype=doctype):
				self.assertTrue(documents.is_denied(doctype))

	def test_denied_doctypes_cannot_be_read_either(self):
		self.assertIn("error", documents.dispatch_tool(
			"describe_doctype", {"doctype": "Server Script"}, user="Administrator"
		))

	def test_the_denylist_cannot_be_widened_by_policy(self):
		doc = policy.get_policy_doc(self.company)
		doc.writable_doctypes = "Server Script"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_policy_cannot_add_a_doctype_outside_the_code_maximum(self):
		doc = policy.get_policy_doc(self.company)
		doc.writable_doctypes = "Sales Invoice"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)


class IntegrationTestPolicyNarrowing(DocumentToolTestCase):
	def test_an_empty_policy_allows_nothing(self):
		doc = policy.get_policy_doc(self.company)
		original = doc.writable_doctypes
		doc.writable_doctypes = ""
		doc.save(ignore_permissions=True)
		try:
			self.assertEqual(documents.writable_doctypes(self.company), [])
			result = self._prepare("Item", self._item_values())
			self.assertIn("error", result)
			self.assertIn("policy", result["error"].casefold())
		finally:
			doc.writable_doctypes = original
			doc.save(ignore_permissions=True)

	def test_the_policy_only_narrows_the_code_maximum(self):
		allowed = set(documents.writable_doctypes(self.company))
		self.assertTrue(allowed.issubset(documents.WRITABLE_DOCTYPES))
		self.assertEqual(allowed, {"Item"})

	def test_the_tool_reports_what_this_company_may_create(self):
		result = documents.dispatch_tool("list_writable_doctypes", {}, user="Administrator")
		self.assertEqual(result["doctypes"], ["Item"])
		self.assertEqual(result["company"], self.company)


class IntegrationTestPreviewWritesNothing(DocumentToolTestCase):
	def test_a_preview_leaves_no_row_behind(self):
		before = frappe.db.count("Item")
		result = self._prepare("Item", self._item_values())
		self.assertNotIn("error", result, result.get("error"))
		self.assertEqual(frappe.db.count("Item"), before)
		self.assertFalse(frappe.db.exists("Item", TEST_ITEM_NAME))

	def test_the_preview_carries_what_the_controller_resolved(self):
		result = self._prepare("Item", self._item_values())
		preview = result["preview"]
		self.assertEqual(preview["doctype"], "Item")
		self.assertEqual(preview["proposed_name"], TEST_ITEM_NAME)
		# Defaults the controller filled in are visible to the reviewer.
		self.assertIn("stock_uom", preview["resolved"])

	def test_a_controller_rejection_becomes_a_readable_error(self):
		# No item_group: the Item controller refuses, and the model gets told why.
		result = self._prepare("Item", {"item_code": "NextGen Broken Item"})
		self.assertIn("error", result)
		self.assertFalse(frappe.db.exists("Item", "NextGen Broken Item"))

	def test_unknown_and_protected_fields_are_refused(self):
		for values, label in (
			({"item_code": "X", "not_a_real_field": 1}, "unknown field"),
			({"item_code": "X", "owner": "Administrator"}, "owner"),
			({"item_code": "X", "docstatus": 1}, "docstatus"),
		):
			with self.subTest(label=label):
				result = self._prepare("Item", values)
				self.assertIn("error", result)


class IntegrationTestApprovalCreatesADraft(DocumentToolTestCase):
	def _proposal(self):
		run = runs.create_run(
			agent_type="assistant",
			company=self.company,
			requested_by="Administrator",
			trigger_type="chat",
			input_payload={"messages": []},
		)
		result = self._prepare("Item", self._item_values())
		self.assertNotIn("error", result, result.get("error"))
		payload = proposals.from_tool_result(run, "prepare_document", {}, result)
		return frappe.get_doc("NextGen Action Proposal", payload["proposal_id"])

	def test_the_proposal_targets_the_proposed_doctype(self):
		proposal = self._proposal()
		self.assertEqual(proposal.action_type, "prepare_document")
		self.assertEqual(proposal.target_doctype, "Item")
		self.assertEqual(proposal.company, self.company)
		self.assertEqual(proposal.status, "Pending Approval")

	def test_approval_then_execution_creates_a_draft(self):
		proposal = self._proposal()
		proposals.approve(proposal.name)
		result = proposals.execute(proposal.name)
		self.assertEqual(result["document_type"], "Item")
		self.assertTrue(frappe.db.exists("Item", result["document_name"]))
		# Never submitted: the human still owns that step. Assert against the
		# stored row rather than the return value.
		self.assertEqual(
			frappe.db.get_value("Item", result["document_name"], "docstatus"), 0
		)
		self.assertEqual(
			frappe.db.get_value("NextGen Action Proposal", proposal.name, "status"), "Completed"
		)

	def test_an_unapproved_proposal_still_cannot_execute(self):
		proposal = self._proposal()
		with self.assertRaises(frappe.ValidationError):
			proposals.execute(proposal.name)

	def test_policy_drift_after_approval_supersedes_instead_of_writing(self):
		proposal = self._proposal()
		proposals.approve(proposal.name)
		# The company withdraws permission to create Items between approval and
		# execution. The write must not happen.
		doc = policy.get_policy_doc(self.company)
		original = doc.writable_doctypes
		doc.writable_doctypes = ""
		doc.save(ignore_permissions=True)
		try:
			with self.assertRaises(frappe.ValidationError):
				proposals.execute(proposal.name)
			self.assertEqual(
				frappe.db.get_value("NextGen Action Proposal", proposal.name, "status"),
				"Superseded",
			)
			self.assertFalse(frappe.db.exists("Item", TEST_ITEM_NAME))
		finally:
			doc.writable_doctypes = original
			doc.save(ignore_permissions=True)


class IntegrationTestUserPermissions(DocumentToolTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# No roles at all: Frappe grants only "All", which cannot create an Item.
		_ensure_user("assistant-reader@nextgen.test", [])
		frappe.db.commit()

	def test_a_user_without_create_permission_is_refused(self):
		# Frappe's own permission model is the real boundary, not a list here.
		with acting_as("assistant-reader@nextgen.test"):
			result = documents.dispatch_tool(
				"prepare_document",
				{"doctype": "Item", "values": self._item_values()},
				user="assistant-reader@nextgen.test",
			)
		self.assertIn("error", result)
		self.assertFalse(frappe.db.exists("Item", TEST_ITEM_NAME))
