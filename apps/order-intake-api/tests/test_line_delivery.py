from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from order_intake.line_delivery import push_text


class LineDeliveryTest(unittest.TestCase):
    @patch("order_intake.line_delivery.urllib.request.urlopen")
    def test_push_text_uses_bearer_token_and_recipient(self, urlopen: MagicMock) -> None:
        response = MagicMock()
        response.status = 200
        urlopen.return_value.__enter__.return_value = response

        retry_key = push_text("U123", "ยืนยันออเดอร์", "token-secret")

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["to"], "U123")
        self.assertEqual(payload["messages"][0]["text"], "ยืนยันออเดอร์")
        self.assertEqual(request.get_header("Authorization"), "Bearer token-secret")
        self.assertTrue(retry_key)


if __name__ == "__main__":
    unittest.main()
