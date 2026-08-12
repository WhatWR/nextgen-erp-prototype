from __future__ import annotations

import unittest

from nextgen_agent_runtime.models.provider import ModelResponse, ScriptedModelProvider, ToolCall
from nextgen_agent_runtime.orchestration import RunExecutor
from nextgen_agent_runtime.orchestration.frappe_client import GatewayRejected
from nextgen_agent_runtime.orchestration.workflow import (
    GraphError,
    WorkflowExecutor,
    topological_order,
)
from support import FakeGateway, make_config, run_context


def node(node_id, agent="sales", x=0, prompt=""):
    return {
        "id": node_id,
        "type": "agent",
        "label": node_id,
        "position": {"x": x, "y": 0},
        "data": {"agent": agent, "prompt": prompt},
    }


def chain(*ids, agent="sales"):
    nodes = [node(n, agent=agent, x=index) for index, n in enumerate(ids)]
    edges = [{"source": a, "target": b} for a, b in zip(ids, ids[1:])]
    return {"nodes": nodes, "edges": edges}


class WorkflowGateway(FakeGateway):
    """Adds node-run creation on top of the single-agent fake."""

    def __init__(self, graph, node_status="Running", **kwargs):
        super().__init__(**kwargs)
        self.parent = run_context(run_id="RUN-PARENT", graph=graph, workflow="WF-00001")
        self.node_runs: list[dict] = []
        self.node_status = node_status
        self.node_error: Exception | None = None

    def create_node_run(self, **kwargs):
        if self.node_error:
            raise self.node_error
        self.node_runs.append(kwargs)
        index = len(self.node_runs)
        return run_context(
            run_id=f"RUN-NODE-{index}",
            status=self.node_status,
            agent_type=kwargs["agent_type"],
            workflow="WF-00001",
            workflow_node=kwargs["node_id"],
            parent_run="RUN-PARENT",
        )

    def claim_run(self, run_id):
        self.claims += 1
        if run_id == "RUN-PARENT":
            return dict(self.parent)
        return run_context(run_id=run_id, status="Running")


def _executor(gateway, answers, **overrides):
    provider = ScriptedModelProvider([ModelResponse(content=text) for text in answers])
    config = make_config(**overrides)
    return WorkflowExecutor(config, gateway, provider), provider


class TopologicalOrderTests(unittest.TestCase):
    def test_a_chain_runs_in_order(self):
        self.assertEqual([n["id"] for n in topological_order(chain("a", "b", "c"))], ["a", "b", "c"])

    def test_independent_nodes_are_ordered_stably_by_canvas_position(self):
        graph = {"nodes": [node("z", x=9), node("a", x=1), node("m", x=5)], "edges": []}
        self.assertEqual([n["id"] for n in topological_order(graph)], ["a", "m", "z"])
        self.assertEqual([n["id"] for n in topological_order(graph)], ["a", "m", "z"])

    def test_a_cycle_is_refused_rather_than_looped(self):
        graph = chain("a", "b")
        graph["edges"].append({"source": "b", "target": "a"})
        with self.assertRaises(GraphError):
            topological_order(graph)

    def test_an_empty_graph_is_refused(self):
        with self.assertRaises(GraphError):
            topological_order({"nodes": [], "edges": []})


class WorkflowExecutionTests(unittest.TestCase):
    def test_each_node_becomes_its_own_child_run(self):
        gateway = WorkflowGateway(chain("a", "b", "c"))
        executor, _ = _executor(gateway, ["one", "two", "three"])
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(outcome.nodes_run, ["a", "b", "c"])
        self.assertEqual([call["node_id"] for call in gateway.node_runs], ["a", "b", "c"])
        self.assertEqual(gateway.completed[-1]["status"], "Completed")

    def test_node_runs_share_the_parents_correlation_id(self):
        gateway = WorkflowGateway(chain("a", "b"))
        executor, _ = _executor(gateway, ["one", "two"])
        executor.execute(gateway.parent)
        for call in gateway.node_runs:
            self.assertEqual(call["correlation_id"], gateway.parent["correlation_id"])

    def test_each_node_receives_the_previous_answer(self):
        gateway = WorkflowGateway(chain("a", "b"))
        executor, _ = _executor(gateway, ["ยอดขายเดือนนี้ 120,000", "สรุปแล้ว"])
        executor.execute(gateway.parent)
        second = gateway.node_runs[1]["input_payload"]["messages"]
        carried = " ".join(message["content"] for message in second)
        self.assertIn("120,000", carried)

    def test_the_node_prompt_travels_with_the_node(self):
        graph = chain("a")
        graph["nodes"][0]["data"]["prompt"] = "คุณคือผู้ตรวจสอบ"
        gateway = WorkflowGateway(graph)
        executor, _ = _executor(gateway, ["ok"])
        executor.execute(gateway.parent)
        self.assertEqual(gateway.node_runs[0]["node_prompt"], "คุณคือผู้ตรวจสอบ")

    def test_node_keys_are_deterministic_so_a_restart_replays(self):
        gateway = WorkflowGateway(chain("a", "b"))
        executor, _ = _executor(gateway, ["one", "two"])
        executor.execute(gateway.parent)
        first = [call["idempotency_key"] for call in gateway.node_runs]

        again = WorkflowGateway(chain("a", "b"))
        executor, _ = _executor(again, ["one", "two"])
        executor.execute(again.parent)
        self.assertEqual(first, [call["idempotency_key"] for call in again.node_runs])
        self.assertEqual(first, ["RUN-PARENT:node:a", "RUN-PARENT:node:b"])

    def test_an_already_finished_node_is_not_run_again(self):
        gateway = WorkflowGateway(chain("a", "b"), node_status="Completed")
        executor, provider = _executor(gateway, [])
        outcome = executor.execute(gateway.parent)
        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(outcome.nodes_run, ["a", "b"])
        self.assertEqual(provider.calls, [])


class WorkflowApprovalTests(unittest.TestCase):
    def test_a_proposal_parks_the_workflow_and_stops_later_nodes(self):
        gateway = WorkflowGateway(
            chain("a", "b", "c"),
            tool_results={
                "prepare_sales_order": {
                    "status": "ok",
                    "tool_name": "prepare_sales_order",
                    "data": {"proposal_id": "PROP-0001"},
                    "warnings": [],
                    "replayed": False,
                }
            },
        )
        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    content="",
                    tool_calls=(ToolCall(id="1", name="prepare_sales_order", arguments={}),),
                ),
                ModelResponse(content="เตรียมข้อเสนอแล้ว"),
            ]
        )
        executor = WorkflowExecutor(make_config(), gateway, provider)
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Waiting Approval")
        self.assertEqual(outcome.proposals, ["PROP-0001"])
        # A workflow never approves its own work: nodes b and c did not run.
        self.assertEqual(outcome.nodes_run, ["a"])
        self.assertEqual(gateway.completed[-1]["status"], "Waiting Approval")

    def test_the_executor_cannot_approve_anything(self):
        for forbidden in ("approve_proposal", "execute_proposal"):
            self.assertFalse(hasattr(WorkflowExecutor, forbidden))


class WorkflowFailClosedTests(unittest.TestCase):
    def test_a_cyclic_graph_fails_the_run(self):
        graph = chain("a", "b")
        graph["edges"].append({"source": "b", "target": "a"})
        gateway = WorkflowGateway(graph)
        executor, _ = _executor(gateway, ["one"])
        outcome = executor.execute(gateway.parent)
        self.assertEqual(outcome.status, "Failed")
        self.assertIn("cycle", gateway.completed[-1]["error"])

    def test_a_refused_node_run_fails_the_workflow(self):
        gateway = WorkflowGateway(chain("a", "b"))
        gateway.node_error = GatewayRejected("node agent does not match the graph", 403)
        executor, _ = _executor(gateway, ["one"])
        outcome = executor.execute(gateway.parent)
        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(gateway.completed[-1]["status"], "Failed")

    def test_a_failing_node_stops_the_workflow(self):
        gateway = WorkflowGateway(chain("a", "b", "c"))
        executor, _ = _executor(gateway, [])  # provider exhausted: node a fails
        outcome = executor.execute(gateway.parent)
        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(outcome.nodes_run, ["a"])


class WorkflowDispatchTests(unittest.TestCase):
    def test_a_parent_run_is_routed_to_the_graph_executor(self):
        gateway = WorkflowGateway(chain("a"))
        provider = ScriptedModelProvider([ModelResponse(content="done")])
        outcome = RunExecutor(make_config(), gateway, provider).execute("RUN-PARENT")
        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(len(gateway.node_runs), 1)

    def test_a_plain_run_still_takes_the_single_agent_path(self):
        gateway = FakeGateway()
        provider = ScriptedModelProvider([ModelResponse(content="done")])
        outcome = RunExecutor(make_config(), gateway, provider).execute("RUN-0001")
        self.assertEqual(outcome.status, "Completed")
        self.assertFalse(hasattr(gateway, "node_runs"))


if __name__ == "__main__":
    unittest.main()
