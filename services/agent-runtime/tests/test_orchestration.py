from __future__ import annotations

import unittest

from nextgen_agent_runtime.agents import DEFINITIONS, get_definition
from nextgen_agent_runtime.models.provider import (
    ModelError,
    ModelResponse,
    ScriptedModelProvider,
    ToolCall,
    parse_tool_calls,
)
from nextgen_agent_runtime.orchestration import RunExecutor
from nextgen_agent_runtime.orchestration.frappe_client import GatewayError, GatewayRejected
from support import FakeGateway, make_config, run_context


def _executor(gateway, responses, **config_overrides):
    provider = ScriptedModelProvider(responses)
    return RunExecutor(make_config(**config_overrides), gateway, provider), provider


def _answer(text="ราคาปัจจุบันคือ 120 บาท"):
    return ModelResponse(content=text)


def _call(name, arguments=None, call_id="call-1"):
    return ToolCall(id=call_id, name=name, arguments=arguments or {})


class AgentDefinitionTests(unittest.TestCase):
    def test_configuration_narrows_but_never_widens_the_allowlist(self):
        sales = get_definition("sales")
        narrowed = sales.effective_tools(["search_items", "prepare_purchase_order", "rm -rf"])
        self.assertEqual(narrowed, ("search_items",))

    def test_the_inventory_agent_waits_for_its_phase_2_signals(self):
        self.assertEqual(sorted(DEFINITIONS), ["assistant", "procurement", "sales"])
        self.assertNotIn("inventory", DEFINITIONS)

    def test_the_assistant_can_only_write_through_a_proposal(self):
        assistant = get_definition("assistant")
        self.assertEqual(assistant.write_tools, ("prepare_document",))
        # Every other tool it has is a read.
        reads = set(assistant.tools) - set(assistant.write_tools)
        self.assertEqual(
            sorted(reads),
            ["describe_doctype", "get_document", "list_writable_doctypes", "search_documents"],
        )

    def test_write_tools_are_declared_for_every_agent(self):
        for definition in DEFINITIONS.values():
            self.assertTrue(set(definition.write_tools).issubset(set(definition.tools)))


class ToolCallParsingTests(unittest.TestCase):
    def test_string_arguments_are_decoded(self):
        calls = parse_tool_calls(
            {
                "tool_calls": [
                    {"id": "a", "function": {"name": "search_items", "arguments": '{"query": "M-150"}'}}
                ]
            }
        )
        self.assertEqual(calls[0].arguments, {"query": "M-150"})

    def test_malformed_arguments_become_empty_and_are_still_authorised_downstream(self):
        calls = parse_tool_calls(
            {"tool_calls": [{"id": "a", "function": {"name": "search_items", "arguments": "{oops"}}]}
        )
        self.assertEqual(calls[0].arguments, {})


class RunExecutionTests(unittest.TestCase):
    def test_a_read_only_turn_records_ordered_steps_and_completes(self):
        gateway = FakeGateway()
        executor, provider = _executor(
            gateway,
            [ModelResponse(content="", tool_calls=(_call("search_items", {"query": "A"}),)), _answer()],
        )
        outcome = executor.execute("RUN-0001")

        self.assertEqual(outcome.status, "Completed")
        self.assertEqual([step["sequence"] for step in gateway.steps], [1, 2, 4])
        self.assertEqual(
            [step["operation"] for step in gateway.steps],
            ["run.claimed", "model.completion", "model.completion"],
        )
        # The Tool step is recorded by Frappe inside execute_tool, never twice.
        self.assertEqual([tool["tool_name"] for tool in gateway.tools], ["search_items"])
        self.assertEqual(gateway.tools[0]["sequence"], 3)
        self.assertEqual(gateway.completed[0]["status"], "Completed")

    def test_the_system_prompt_and_page_context_come_from_this_release(self):
        gateway = FakeGateway(run_context(input={"messages": [], "context": {"route": "Form/Item"}}))
        executor, provider = _executor(gateway, [_answer()])
        executor.execute("RUN-0001")
        system = provider.calls[0]["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("prepare_sales_order", system["content"])
        self.assertIn("Form/Item", system["content"])

    def test_only_run_allowed_tool_schemas_reach_the_model(self):
        gateway = FakeGateway()
        executor, provider = _executor(gateway, [_answer()])
        executor.execute("RUN-0001")
        offered = [tool["function"]["name"] for tool in provider.calls[0]["tools"]]
        self.assertEqual(
            sorted(offered), ["get_item_price_and_stock", "prepare_sales_order", "search_items"]
        )
        self.assertNotIn("summarize_sales_pipeline", offered)

    def test_a_tool_outside_the_allowlist_is_rejected_and_recorded(self):
        gateway = FakeGateway()
        executor, _ = _executor(
            gateway,
            [
                ModelResponse(content="", tool_calls=(_call("summarize_procurement_risk"),)),
                _answer(),
            ],
        )
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(gateway.tools, [])
        rejected = [step for step in gateway.steps if step["status"] == "Rejected"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["step_type"], "Tool")
        self.assertIn("not allowed", rejected[0]["error"])

    def test_a_write_tool_that_returns_a_proposal_leaves_the_run_waiting_for_a_human(self):
        gateway = FakeGateway(
            tool_results={
                "prepare_sales_order": {
                    "status": "ok",
                    "tool_name": "prepare_sales_order",
                    "data": {"proposal_id": "PROP-0001", "status": "Pending Approval"},
                    "warnings": [],
                    "replayed": False,
                }
            }
        )
        executor, _ = _executor(
            gateway,
            [
                ModelResponse(content="", tool_calls=(_call("prepare_sales_order", {"customer": "C"}),)),
                _answer("สร้าง preview แล้วค่ะ"),
            ],
        )
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Waiting Approval")
        self.assertEqual(outcome.proposals, ["PROP-0001"])
        self.assertEqual(gateway.completed[0]["status"], "Waiting Approval")

    def test_the_runtime_never_approves_a_proposal(self):
        gateway = FakeGateway()
        self.assertFalse(hasattr(gateway, "approve_proposal"))
        from nextgen_agent_runtime.orchestration.frappe_client import FrappeGatewayClient

        for forbidden in ("approve_proposal", "execute_proposal", "reject_proposal"):
            self.assertFalse(hasattr(FrappeGatewayClient, forbidden))

    def test_idempotency_keys_are_deterministic_so_a_restart_replays_safely(self):
        responses = [
            ModelResponse(content="", tool_calls=(_call("search_items", {"query": "A"}),)),
            _answer(),
        ]
        first = FakeGateway()
        executor, _ = _executor(first, list(responses))
        executor.execute("RUN-0001")
        second = FakeGateway()
        executor, _ = _executor(second, list(responses))
        executor.execute("RUN-0001")
        self.assertEqual(
            [step["idempotency_key"] for step in first.steps],
            [step["idempotency_key"] for step in second.steps],
        )
        self.assertEqual(first.tools[0]["idempotency_key"], second.tools[0]["idempotency_key"])

    def test_a_resumed_run_continues_from_its_persisted_sequence(self):
        gateway = FakeGateway(run_context(next_sequence=7))
        executor, _ = _executor(gateway, [_answer()])
        executor.execute("RUN-0001")
        self.assertEqual([step["sequence"] for step in gateway.steps], [7, 8])

    def test_hidden_model_reasoning_is_never_sent_to_frappe(self):
        gateway = FakeGateway()
        executor, _ = _executor(gateway, [_answer("คำตอบ")])
        executor.execute("RUN-0001")
        model_step = [step for step in gateway.steps if step["step_type"] == "Model"][0]
        self.assertEqual(
            sorted(model_step["result"]), ["content_preview", "finish_reason", "tool_calls", "usage"]
        )


class FailClosedTests(unittest.TestCase):
    def test_a_model_outage_fails_the_run_with_a_sanitised_error(self):
        gateway = FakeGateway()
        executor, _ = _executor(gateway, [])  # scripted provider is exhausted
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(gateway.completed[0]["status"], "Failed")
        self.assertIn("ModelError", gateway.completed[0]["error"])

    def test_a_frappe_rejection_fails_the_run_rather_than_retrying(self):
        gateway = FakeGateway(tool_results={"search_items": GatewayRejected("no permission", 403)})
        executor, _ = _executor(
            gateway,
            [ModelResponse(content="", tool_calls=(_call("search_items"),)), _answer()],
        )
        outcome = executor.execute("RUN-0001")
        # The rejection is reported back to the model as a tool error and the
        # turn still finishes with a complete, auditable trace.
        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(len(gateway.tools), 1)

    def test_an_unclaimable_run_is_left_for_the_dispatcher(self):
        gateway = FakeGateway()
        gateway.claim_error = GatewayError("gateway unreachable")
        executor, _ = _executor(gateway, [_answer()])
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Unclaimed")
        self.assertEqual(gateway.completed, [])

    def test_an_already_final_run_is_not_executed_again(self):
        gateway = FakeGateway(run_context(status="Completed"))
        executor, provider = _executor(gateway, [_answer()])
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(provider.calls, [])
        self.assertEqual(gateway.steps, [])

    def test_a_disabled_agent_cannot_run_in_this_deployment(self):
        gateway = FakeGateway()
        executor, provider = _executor(gateway, [_answer()], enabled_agents=frozenset({"procurement"}))
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(provider.calls, [])
        self.assertIn("not enabled", gateway.completed[0]["error"])

    def test_an_unknown_agent_fails_closed(self):
        gateway = FakeGateway(run_context(agent_type="warehouse"))
        executor, _ = _executor(gateway, [_answer()], enabled_agents=frozenset({"warehouse"}))
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Failed")
        self.assertIn("unknown agent", gateway.completed[0]["error"])

    def test_a_lost_completion_callback_leaves_frappe_owning_the_run(self):
        gateway = FakeGateway()
        gateway.complete_error = GatewayError("callback lost")
        executor, _ = _executor(gateway, [_answer()])
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(gateway.completed, [])

    def test_cancellation_stops_the_loop_before_the_next_model_turn(self):
        gateway = FakeGateway()
        executor, provider = _executor(gateway, [_answer()])
        executor.cancel_check = lambda run_id: True
        outcome = executor.execute("RUN-0001")
        self.assertEqual(outcome.status, "Cancelled")
        self.assertEqual(provider.calls, [])
        self.assertEqual(gateway.completed[0]["status"], "Cancelled")

    def test_the_tool_budget_is_bounded_by_the_run_and_the_release(self):
        gateway = FakeGateway(run_context(max_tool_calls=2))
        executor, provider = _executor(
            gateway,
            [ModelResponse(content="", tool_calls=(_call("search_items"),)) for _ in range(4)],
        )
        outcome = executor.execute("RUN-0001")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(outcome.status, "Completed")


class ModelErrorTests(unittest.TestCase):
    def test_model_error_is_the_declared_failure_type(self):
        with self.assertRaises(ModelError):
            ScriptedModelProvider([]).complete(model="m", messages=[])


if __name__ == "__main__":
    unittest.main()
