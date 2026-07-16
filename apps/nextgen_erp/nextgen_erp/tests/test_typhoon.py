import json
import unittest

from nextgen_erp.typhoon import TyphoonClient, TyphoonError, iter_sse_content, normalize_base_url


class TyphoonClientTest(unittest.TestCase):
	def test_normalizes_host_and_documented_v1_base(self):
		self.assertEqual(normalize_base_url("https://api.opentyphoon.ai"), "https://api.opentyphoon.ai/v1")
		self.assertEqual(normalize_base_url("https://api.opentyphoon.ai/v1/"), "https://api.opentyphoon.ai/v1")

	def test_chat_uses_bearer_auth_and_openai_payload(self):
		calls = []

		def transport(url, headers, body, timeout):
			calls.append((url, headers, json.loads(body), timeout))
			return 200, json.dumps({
				"choices": [{"message": {"role": "assistant", "content": "สวัสดี"}}],
				"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
			}).encode()

		client = TyphoonClient("https://api.opentyphoon.ai/v1", "secret", transport=transport)
		message = client.chat(model="typhoon-test", messages=[{"role": "user", "content": "hello"}])
		self.assertEqual(message["content"], "สวัสดี")
		self.assertEqual(calls[0][0], "https://api.opentyphoon.ai/v1/chat/completions")
		self.assertEqual(calls[0][1]["Authorization"], "Bearer secret")
		self.assertEqual(calls[0][2]["model"], "typhoon-test")
		self.assertEqual(client.usage["total_tokens"], 12)

	def test_surfaces_http_and_malformed_response_errors(self):
		client = TyphoonClient("https://api.opentyphoon.ai", "secret", transport=lambda *_: (429, b"rate limit"))
		with self.assertRaisesRegex(TyphoonError, "HTTP 429"):
			client.chat(model="model", messages=[])
		client = TyphoonClient("https://api.opentyphoon.ai", "secret", transport=lambda *_: (200, b"not-json"))
		with self.assertRaisesRegex(TyphoonError, "invalid JSON"):
			client.chat(model="model", messages=[])

	def test_stream_parser_ignores_noise_and_stops_at_done(self):
		lines = [
			b": keepalive\n",
			'data: {"choices":[{"delta":{"content":"สวัสดี"}}]}\n'.encode(),
			b"data: malformed\n",
			'data: {"choices":[{"delta":{"content":"ค่ะ"}}]}\n'.encode(),
			b"data: [DONE]\n",
			b'data: {"choices":[{"delta":{"content":"ignored"}}]}\n',
		]
		self.assertEqual(list(iter_sse_content(lines)), ["สวัสดี", "ค่ะ"])


if __name__ == "__main__":
	unittest.main()
