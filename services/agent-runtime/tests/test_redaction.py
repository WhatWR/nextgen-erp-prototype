from __future__ import annotations

import unittest

from nextgen_agent_runtime import redaction


class RedactionTests(unittest.TestCase):
    def test_secret_fields_never_survive(self):
        result = redaction.redact(
            {
                "api_key": "sk-live-1234567890abcdef",
                "channel_secret": "abc",
                "line_access_token": "xyz",
                "nested": {"password": "hunter2", "customer": "CUST-0001"},
            }
        )
        self.assertEqual(result["api_key"], redaction.REDACTED)
        self.assertEqual(result["channel_secret"], redaction.REDACTED)
        self.assertEqual(result["line_access_token"], redaction.REDACTED)
        self.assertEqual(result["nested"]["password"], redaction.REDACTED)
        self.assertEqual(result["nested"]["customer"], "CUST-0001")

    def test_hidden_model_reasoning_is_never_persisted(self):
        result = redaction.redact({"content": "ok", "reasoning_content": "step 1 ..."})
        self.assertEqual(result["reasoning_content"], redaction.REDACTED)
        self.assertEqual(result["content"], "ok")

    def test_credential_shaped_text_is_masked(self):
        text = redaction.redact_text("call with Bearer abcdef1234567890 and token abcd1234:efgh5678")
        self.assertNotIn("abcdef1234567890", text)
        self.assertNotIn("efgh5678", text)

    def test_line_ids_and_phone_numbers_are_masked(self):
        line_id = "U" + "a1b2c3d4" * 4
        result = redaction.redact_text(f"sender {line_id} phone 0812345678")
        self.assertNotIn(line_id, result)
        self.assertIn("Ua1***", result)
        self.assertNotIn("0812345678", result)

    def test_payloads_are_bounded(self):
        result = redaction.redact({"note": "x" * (redaction.MAX_STRING_LENGTH + 50)})
        self.assertTrue(result["note"].endswith("…[truncated]"))
        deep: dict = {}
        cursor = deep
        for _ in range(redaction.MAX_DEPTH + 3):
            cursor["next"] = {}
            cursor = cursor["next"]
        self.assertIn("truncated", str(redaction.redact(deep)))

    def test_long_lists_are_capped(self):
        result = redaction.redact(list(range(redaction.MAX_ITEMS + 25)))
        self.assertEqual(len(result), redaction.MAX_ITEMS + 1)
        self.assertIn("truncated", result[-1])

    def test_error_summaries_are_single_line_and_bounded(self):
        summary = redaction.redact_error(ValueError("boom\nwith Bearer abcdef1234567890"))
        self.assertNotIn("\n", summary)
        self.assertTrue(summary.startswith("ValueError:"))
        self.assertNotIn("abcdef1234567890", summary)


if __name__ == "__main__":
    unittest.main()
