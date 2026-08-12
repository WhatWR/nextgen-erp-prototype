from __future__ import annotations

import unittest

from nextgen_agent_runtime.api import create_app
from nextgen_agent_runtime.models.contracts import CONTRACT_VERSION, validate
from support import SERVICE_TOKEN, ASGIClient, make_config


class RecordingExecutor:
    def __init__(self):
        self.run_ids: list[str] = []
        self.cancel_check = None

    def execute(self, run_id: str):
        self.run_ids.append(run_id)
        return run_id


def _client(config=None, executor=None):
    executor = executor or RecordingExecutor()
    app = create_app(config or make_config(), executor=executor)
    return ASGIClient(app), executor


def _dispatch_body(run_id="RUN-0001"):
    return {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "correlation_id": f"corr-{run_id}",
        "idempotency_key": f"{run_id}:dispatch",
    }


class ProbeTests(unittest.TestCase):
    def test_health_reports_contract_and_agents_without_a_token(self):
        client, _ = _client()
        status, payload = client.request("GET", "/healthz")
        self.assertEqual(status, 200)
        validate("runtime_health", payload)
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertEqual(payload["enabled_agents"], ["procurement", "sales"])

    def test_readiness_fails_closed_when_credentials_are_missing(self):
        client, _ = _client(make_config(model_api_key="", frappe_api_secret=""))
        status, payload = client.request("GET", "/readyz")
        self.assertEqual(status, 503)
        self.assertIn("model_provider_credentials", payload["missing"])
        self.assertIn("frappe_gateway_credentials", payload["missing"])

    def test_openapi_is_generated_from_the_enforced_schemas(self):
        client, _ = _client()
        status, document = client.request("GET", "/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(document["openapi"], "3.1.0")
        self.assertIn("run_context", document["components"]["schemas"])
        self.assertIn("/v1/runs/dispatch", document["paths"])
        self.assertEqual(
            document["paths"]["/v1/runs/dispatch"]["post"]["security"], [{"ServiceToken": []}]
        )
        self.assertNotIn("security", document["paths"]["/healthz"]["get"])


class AuthenticationTests(unittest.TestCase):
    def test_a_client_without_the_service_token_cannot_dispatch(self):
        client, executor = _client()
        status, payload = client.request("POST", "/v1/runs/dispatch", _dispatch_body())
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "unauthorized")
        self.assertEqual(executor.run_ids, [])

    def test_a_wrong_service_token_is_rejected(self):
        client, executor = _client()
        status, _ = client.request(
            "POST", "/v1/runs/dispatch", _dispatch_body(), {"X-NextGen-Service-Token": "guess"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(executor.run_ids, [])

    def test_an_unconfigured_token_authorises_nobody(self):
        client, executor = _client(make_config(service_token=""))
        status, _ = client.request(
            "POST", "/v1/runs/dispatch", _dispatch_body(), {"X-NextGen-Service-Token": ""}
        )
        self.assertEqual(status, 401)
        self.assertEqual(executor.run_ids, [])


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.headers = {"X-NextGen-Service-Token": SERVICE_TOKEN}

    def test_dispatch_accepts_and_executes_asynchronously(self):
        client, executor = _client()
        status, payload = client.request("POST", "/v1/runs/dispatch", _dispatch_body(), self.headers)
        self.assertEqual(status, 202)
        validate("accepted", payload)
        self.assertEqual(payload["status"], "Dispatched")
        client.drain()
        self.assertEqual(executor.run_ids, ["RUN-0001"])

    def test_resume_uses_the_same_accepted_contract(self):
        client, executor = _client()
        status, payload = client.request("POST", "/v1/runs/resume", _dispatch_body(), self.headers)
        self.assertEqual(status, 202)
        validate("accepted", payload)
        client.drain()
        self.assertEqual(executor.run_ids, ["RUN-0001"])

    def test_dispatch_rejects_a_payload_that_violates_the_contract(self):
        client, executor = _client()
        body = {**_dispatch_body(), "execution_user": "Administrator"}
        status, payload = client.request("POST", "/v1/runs/dispatch", body, self.headers)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "contract_violation")
        self.assertEqual(executor.run_ids, [])

    def test_dispatch_is_refused_while_the_runtime_is_not_ready(self):
        client, executor = _client(make_config(model_api_key=""))
        status, payload = client.request("POST", "/v1/runs/dispatch", _dispatch_body(), self.headers)
        self.assertEqual(status, 503)
        self.assertIn("model_provider_credentials", payload["missing"])
        self.assertEqual(executor.run_ids, [])

    def test_a_malformed_body_is_a_client_error(self):
        client, _ = _client()
        status, payload = client.request("POST", "/v1/runs/dispatch", ["not", "an", "object"], self.headers)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "bad_request")

    def test_cancel_is_accepted_and_recorded(self):
        client, _ = _client()
        status, payload = client.request(
            "POST",
            "/v1/runs/cancel",
            {"contract_version": CONTRACT_VERSION, "run_id": "RUN-0001", "reason": "user cancelled"},
            self.headers,
        )
        self.assertEqual(status, 202)
        self.assertEqual(payload["status"], "Cancelled")
        self.assertTrue(client.app.scheduler.is_cancelled("RUN-0001"))

    def test_unknown_routes_and_methods_are_refused(self):
        client, _ = _client()
        self.assertEqual(client.request("GET", "/v1/runs")[0], 404)
        self.assertEqual(client.request("GET", "/v1/runs/dispatch", None, self.headers)[0], 405)


if __name__ == "__main__":
    unittest.main()
