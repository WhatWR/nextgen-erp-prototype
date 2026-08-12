from __future__ import annotations

import json
import unittest

from nextgen_agent_runtime.orchestration.frappe_client import (
    METHOD_PREFIX,
    FrappeGatewayClient,
    GatewayError,
    GatewayRejected,
)
from support import make_config, run_context


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(
            {"url": url, "headers": headers, "body": json.loads(body.decode()), "timeout": timeout}
        )
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


def _client(responses):
    transport = FakeTransport(responses)
    client = FrappeGatewayClient(make_config(), transport=transport, sleep=lambda _seconds: None)
    return client, transport


class TransportTests(unittest.TestCase):
    def test_calls_go_to_whitelisted_gateway_methods_only(self):
        client, transport = _client([(200, {"message": run_context()})])
        client.claim_run("RUN-0001")
        url = transport.calls[0]["url"]
        self.assertTrue(url.endswith(f"/api/method/{METHOD_PREFIX}.claim_run"))
        self.assertIn("/api/method/", url)
        self.assertNotIn("/api/resource/", url)

    def test_requests_authenticate_as_the_restricted_service_user(self):
        client, transport = _client([(200, {"message": run_context()})])
        client.claim_run("RUN-0001")
        headers = transport.calls[0]["headers"]
        self.assertEqual(headers["Authorization"], "token key:secret")
        self.assertEqual(headers["X-NextGen-Runtime-Version"], "test-runtime")

    def test_correlation_ids_are_propagated_to_frappe(self):
        client, transport = _client(
            [(200, {"message": {"step_id": "S1", "sequence": 1, "replayed": False}})]
        )
        client.record_step(
            run_id="RUN-0001",
            sequence=1,
            step_type="System",
            operation="run.claimed",
            status="Success",
            idempotency_key="RUN-0001:1:run.claimed",
            correlation_id="corr-1",
        )
        self.assertEqual(transport.calls[0]["headers"]["X-NextGen-Correlation-Id"], "corr-1")

    def test_a_server_error_is_retried_with_the_same_idempotency_key(self):
        client, transport = _client(
            [
                (503, {"message": "restarting"}),
                (200, {"message": {"step_id": "S1", "sequence": 1, "replayed": True}}),
            ]
        )
        client.record_step(
            run_id="RUN-0001",
            sequence=1,
            step_type="System",
            operation="run.claimed",
            status="Success",
            idempotency_key="RUN-0001:1:run.claimed",
        )
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(
            transport.calls[0]["body"]["idempotency_key"],
            transport.calls[1]["body"]["idempotency_key"],
        )

    def test_a_permission_error_is_a_decision_and_is_never_retried(self):
        client, transport = _client([(403, {"exception": "frappe.PermissionError: nope"})])
        with self.assertRaises(GatewayRejected) as raised:
            client.execute_tool(
                run_id="RUN-0001",
                sequence=2,
                tool_name="search_items",
                arguments={},
                idempotency_key="RUN-0001:2:search_items",
            )
        self.assertEqual(raised.exception.status, 403)
        self.assertEqual(len(transport.calls), 1)

    def test_exhausted_retries_raise_a_retryable_gateway_error(self):
        client, transport = _client([(500, {"message": "boom"})] * 3)
        with self.assertRaises(GatewayError):
            client.claim_run("RUN-0001")
        self.assertEqual(len(transport.calls), 3)

    def test_a_response_that_breaks_the_contract_is_rejected(self):
        client, _ = _client([(200, {"message": {"run_id": "RUN-0001"}})])
        with self.assertRaises(Exception):
            client.claim_run("RUN-0001")


class RedactionAtTheBoundaryTests(unittest.TestCase):
    def test_tool_arguments_are_sanitised_before_transport(self):
        client, transport = _client(
            [
                (
                    200,
                    {
                        "message": {
                            "status": "ok",
                            "tool_name": "search_items",
                            "data": {},
                            "warnings": [],
                            "replayed": False,
                        }
                    },
                )
            ]
        )
        client.execute_tool(
            run_id="RUN-0001",
            sequence=2,
            tool_name="search_items",
            arguments={"query": "A", "api_key": "sk-live-abcdef1234567890"},
            idempotency_key="RUN-0001:2:search_items",
        )
        sent = transport.calls[0]["body"]["arguments"]
        self.assertEqual(sent["query"], "A")
        self.assertEqual(sent["api_key"], "[redacted]")

    def test_step_payloads_are_sanitised_before_persistence(self):
        client, transport = _client(
            [(200, {"message": {"step_id": "S1", "sequence": 1, "replayed": False}})]
        )
        client.record_step(
            run_id="RUN-0001",
            sequence=1,
            step_type="Model",
            operation="model.completion",
            status="Success",
            idempotency_key="k",
            sanitized_input={"token": "abc123"},
            result={"reasoning": "hidden"},
        )
        body = transport.calls[0]["body"]
        self.assertEqual(body["sanitized_input"]["token"], "[redacted]")
        self.assertEqual(body["result"]["reasoning"], "[redacted]")


if __name__ == "__main__":
    unittest.main()
