from __future__ import annotations

import unittest

from order_intake.ai.client import AIClient, normalize_gateway_url


class AIClientURLTest(unittest.TestCase):
	def test_accepts_gateway_host_or_v1_base(self):
		self.assertEqual(normalize_gateway_url("https://api.opentyphoon.ai"), "https://api.opentyphoon.ai")
		self.assertEqual(normalize_gateway_url("https://api.opentyphoon.ai/v1/"), "https://api.opentyphoon.ai")

	def test_v1_base_does_not_duplicate_version_segment(self):
		calls = []

		def transport(url, method, headers, body):
			calls.append(url)
			return 200, b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

		client = AIClient("https://api.opentyphoon.ai/v1", "secret", transport=transport)
		client.chat(model="model", messages=[])
		self.assertEqual(calls, ["https://api.opentyphoon.ai/v1/chat/completions"])


if __name__ == "__main__":
	unittest.main()
