import base64
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from nextgen_erp import line, line_simulator

# Smallest valid PNG, so the upload path exercises the real signature check.
TINY_PNG = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQAB"
	"h6FO1AAAAABJRU5ErkJggg=="
)


class IntegrationTestLineSimulator(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.conf["nextgen_line_simulator"] = 1
		self.line_id = line_simulator.sim_line_id("Administrator")
		frappe.db.delete(line_simulator.DOCTYPE, {"recipient": self.line_id})

	def tearDown(self):
		frappe.conf.pop("nextgen_line_simulator", None)
		frappe.db.delete(line_simulator.DOCTYPE, {"recipient": self.line_id})

	# --- gating ----------------------------------------------------------- #
	def test_disabled_site_refuses_every_entry_point(self):
		frappe.conf.pop("nextgen_line_simulator", None)
		with self.assertRaises(frappe.PermissionError):
			line_simulator.guard()

	def test_guest_is_refused_even_when_enabled(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				line_simulator.guard()
		finally:
			frappe.set_user("Administrator")

	def test_identity_is_stable_and_per_user(self):
		self.assertEqual(line_simulator.sim_line_id("a@example.com"), line_simulator.sim_line_id("a@example.com"))
		self.assertNotEqual(
			line_simulator.sim_line_id("a@example.com"), line_simulator.sim_line_id("b@example.com")
		)
		self.assertTrue(line_simulator.sim_line_id("a@example.com").startswith(line_simulator.SIM_PREFIX))

	# --- outbound --------------------------------------------------------- #
	def test_push_to_a_simulated_recipient_is_captured_not_sent(self):
		with patch("nextgen_erp.line.make_post_request") as post:
			result = line.push_text(self.line_id, "ยืนยันออเดอร์", image_url="/qr.png")
		post.assert_not_called()
		self.assertTrue(result["simulated"])
		messages = line_simulator.poll(self.line_id)["messages"]
		self.assertEqual(len(messages), 1)
		self.assertEqual(messages[0]["direction"], "Outbound")
		self.assertEqual(messages[0]["text"], "ยืนยันออเดอร์")
		self.assertEqual(messages[0]["image_url"], "/qr.png")

	def test_a_real_recipient_is_never_diverted(self):
		"""The prefix gate must not swallow production traffic."""
		disabled = frappe._dict(enabled=0)
		with patch("nextgen_erp.line.frappe.get_single", return_value=disabled):
			result = line.push_text("U0123456789abcdef", "hello")
		# Falls through to the normal channel path, not the simulator.
		self.assertFalse(result.get("simulated"))
		self.assertFalse(result["sent"])
		self.assertEqual(
			frappe.db.count(line_simulator.DOCTYPE, {"recipient": "U0123456789abcdef"}), 0
		)

	# --- inbound ---------------------------------------------------------- #
	def test_send_records_the_message_and_forwards_a_signed_webhook(self):
		with patch("nextgen_erp.line_simulator._forward_webhook", return_value={"delivered": True}) as fwd:
			line_simulator.send("น้ำแดง 2 ลัง")
		payload = fwd.call_args.args[0]
		event = payload["events"][0]
		self.assertEqual(event["source"]["userId"], self.line_id)
		self.assertEqual(event["message"]["text"], "น้ำแดง 2 ลัง")
		messages = line_simulator.poll(self.line_id)["messages"]
		self.assertEqual(messages[-1]["direction"], "Inbound")

	def test_signature_matches_what_line_would_produce(self):
		import hashlib
		import hmac

		body = b'{"events":[]}'
		expected = base64.b64encode(
			hmac.new(b"secret", body, hashlib.sha256).digest()
		).decode()
		self.assertEqual(line_simulator.sign_line_body("secret", body), expected)

	def test_slip_upload_round_trips_through_fetch_message_content(self):
		with patch("nextgen_erp.line_simulator._forward_webhook", return_value={"delivered": True}):
			result = line_simulator.send_image("slip.png", base64.b64encode(TINY_PNG).decode())
		content = line.fetch_message_content(result["sim_message_id"], self.line_id)
		self.assertEqual(content, TINY_PNG)

	def test_non_image_uploads_are_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			line_simulator.send_image("slip.pdf", base64.b64encode(b"%PDF-1.4 not an image").decode())

	# --- isolation -------------------------------------------------------- #
	def test_poll_refuses_another_users_conversation(self):
		with self.assertRaises(frappe.ValidationError):
			line_simulator.poll(line_simulator.sim_line_id("someone-else@example.com"))

	def test_poll_allows_the_shared_delivery_pane(self):
		self.assertEqual(
			line_simulator.poll(line_simulator.DELIVERY_LINE_ID)["recipient"],
			line_simulator.DELIVERY_LINE_ID,
		)

	def test_reset_clears_only_this_identity(self):
		line.push_text(self.line_id, "mine")
		line.push_text(line_simulator.DELIVERY_LINE_ID, "delivery team")
		try:
			line_simulator.reset()
			self.assertEqual(len(line_simulator.poll(self.line_id)["messages"]), 0)
			self.assertGreaterEqual(
				len(line_simulator.poll(line_simulator.DELIVERY_LINE_ID)["messages"]), 1
			)
		finally:
			frappe.db.delete(line_simulator.DOCTYPE, {"recipient": line_simulator.DELIVERY_LINE_ID})
