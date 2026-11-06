from __future__ import annotations

import unittest

from nextgen_agent_runtime.models.provider import ModelResponse, ScriptedModelProvider, ToolCall
from nextgen_agent_runtime.orchestration import RunExecutor
from nextgen_agent_runtime.orchestration.frappe_client import GatewayRejected
from nextgen_agent_runtime.orchestration.workflow import (
    GraphError,
    WorkflowExecutor,
    parents,
    topological_order,
)
from support import FakeGateway, make_config, run_context


def node(node_id, agent="sales", x=0, prompt="", kind="agent", **data):
    payload = {"kind": kind, "prompt": prompt, **data}
    if kind == "agent":
        payload["agent"] = agent
    return {
        "id": node_id,
        "type": "agent",
        "label": node_id,
        "position": {"x": x, "y": 0},
        "data": payload,
    }


def chain(*ids, agent="sales"):
    nodes = [node(n, agent=agent, x=index) for index, n in enumerate(ids)]
    edges = [{"source": a, "target": b} for a, b in zip(ids, ids[1:])]
    return {"nodes": nodes, "edges": edges}


def envelope(node_id, text="", rows=None):
    return {
        "node_id": node_id,
        "node_kind": "source.query",
        "label": node_id,
        "text": text or None,
        "data": rows or [],
        "row_count": len(rows or []),
        "truncated": False,
        "documents": [],
        "artifacts": [],
    }


class WorkflowGateway(FakeGateway):
    """Adds node-run creation, node execution and state on top of the fake."""

    def __init__(self, graph, node_status="Running", state_nodes=None, **kwargs):
        super().__init__(**kwargs)
        self.parent = run_context(run_id="RUN-PARENT", graph=graph, workflow="WF-00001")
        self.node_runs: list[dict] = []
        self.executed_nodes: list[dict] = []
        self.node_status = node_status
        self.node_error: Exception | None = None
        self.state_nodes = state_nodes
        self.node_outputs: dict[str, dict] = {}
        self.node_failures: dict[str, str] = {}

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

    def execute_node(self, **kwargs):
        self.executed_nodes.append(kwargs)
        node_id = kwargs["node_id"]
        if node_id in self.node_failures:
            return {
                "node_id": node_id,
                "status": "Failed",
                "output": None,
                "error": self.node_failures[node_id],
                "replayed": False,
            }
        return {
            "node_id": node_id,
            "status": "Completed",
            "output": self.node_outputs.get(node_id, envelope(node_id, text=f"ran {node_id}")),
            "replayed": False,
        }

    def get_workflow_state(self, **kwargs):
        return {
            "contract_version": "v2",
            "run_id": "RUN-PARENT",
            "status": "Running",
            "nodes": self.state_nodes or [],
        }

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

    def test_parents_are_the_nodes_actually_connected(self):
        graph = {
            "nodes": [node("a", x=0), node("b", x=1), node("c", x=2)],
            "edges": [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}],
        }
        order = topological_order(graph)
        self.assertEqual(parents(graph, "c", order), ["a", "b"])
        self.assertEqual(parents(graph, "a", order), [])


class WorkflowExecutionTests(unittest.TestCase):
    def test_each_agent_node_becomes_its_own_child_run(self):
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


class DeterministicNodeTests(unittest.TestCase):
    """excel -> agent -> report, and the other shapes that start outside a model."""

    def _graph(self):
        return {
            "nodes": [
                node("src", kind="source.excel", x=0, file="FILE-1"),
                node("think", kind="agent", agent="sales", x=1),
                node("out", kind="sink.report", x=2, format="html"),
            ],
            "edges": [
                {"source": "src", "target": "think"},
                {"source": "think", "target": "out"},
            ],
        }

    def test_a_source_node_is_executed_by_frappe_not_by_the_runtime(self):
        gateway = WorkflowGateway(self._graph())
        executor, _ = _executor(gateway, ["สรุปแล้ว"])
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(outcome.nodes_run, ["src", "think", "out"])
        # Only the agent node became a child run; the source and sink were
        # executed inside Frappe.
        self.assertEqual([call["node_id"] for call in gateway.node_runs], ["think"])
        self.assertEqual([call["node_id"] for call in gateway.executed_nodes], ["src", "out"])

    def test_the_runtime_sends_only_the_node_identity(self):
        gateway = WorkflowGateway(self._graph())
        executor, _ = _executor(gateway, ["ok"])
        executor.execute(gateway.parent)
        for call in gateway.executed_nodes:
            self.assertEqual(
                set(call),
                {"parent_run_id", "node_id", "idempotency_key", "correlation_id"},
            )

    def test_source_rows_reach_the_agent_prompt(self):
        gateway = WorkflowGateway(self._graph())
        gateway.node_outputs["src"] = envelope(
            "src", text="อ่านไฟล์ได้ 2 แถว", rows=[{"item": "M-150"}, {"item": "OISHI"}]
        )
        executor, _ = _executor(gateway, ["ok"])
        executor.execute(gateway.parent)

        prompt = " ".join(m["content"] for m in gateway.node_runs[0]["input_payload"]["messages"])
        self.assertIn("M-150", prompt)
        self.assertIn("อ่านไฟล์ได้ 2 แถว", prompt)

    def test_a_failing_deterministic_node_fails_the_workflow(self):
        gateway = WorkflowGateway(self._graph())
        gateway.node_failures["src"] = "Unknown file: FILE-1"
        executor, _ = _executor(gateway, ["ok"])
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Failed")
        self.assertEqual(gateway.completed[-1]["status"], "Failed")
        self.assertEqual(gateway.node_runs, [])


class FanInTests(unittest.TestCase):
    def test_a_merging_node_reads_every_parent(self):
        graph = {
            "nodes": [
                node("db", kind="source.query", x=0, doctype="Sales Order"),
                node("api", kind="source.api", x=1, connection="CRM", path="/v1/leads"),
                node("merge", kind="agent", agent="sales", x=2),
            ],
            "edges": [
                {"source": "db", "target": "merge"},
                {"source": "api", "target": "merge"},
            ],
        }
        gateway = WorkflowGateway(graph)
        gateway.node_outputs["db"] = envelope("db", rows=[{"order": "SO-1"}])
        gateway.node_outputs["api"] = envelope("api", rows=[{"lead": "L-9"}])
        executor, _ = _executor(gateway, ["รวมแล้ว"])
        executor.execute(gateway.parent)

        prompt = " ".join(m["content"] for m in gateway.node_runs[0]["input_payload"]["messages"])
        self.assertIn("SO-1", prompt)
        self.assertIn("L-9", prompt)


class WorkflowApprovalTests(unittest.TestCase):
    def _proposal_gateway(self, graph):
        return WorkflowGateway(
            graph,
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

    def _proposal_provider(self):
        return ScriptedModelProvider(
            [
                ModelResponse(
                    content="",
                    tool_calls=(ToolCall(id="1", name="prepare_sales_order", arguments={}),),
                ),
                ModelResponse(content="เตรียมข้อเสนอแล้ว"),
            ]
        )

    def test_a_proposal_parks_the_workflow_and_stops_later_nodes(self):
        gateway = self._proposal_gateway(chain("a", "b", "c"))
        executor = WorkflowExecutor(make_config(), gateway, self._proposal_provider())
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Waiting Approval")
        self.assertEqual(outcome.proposals, ["PROP-0001"])
        # A workflow never approves its own work: nodes b and c did not run.
        self.assertEqual(outcome.nodes_run, ["a"])
        self.assertEqual(gateway.completed[-1]["status"], "Waiting Approval")

    def test_an_approved_workflow_resumes_where_it_parked(self):
        gateway = WorkflowGateway(
            chain("a", "b", "c"),
            state_nodes=[
                {"node_id": "a", "status": "Completed", "output": envelope("a", text="ทำแล้ว")},
                {"node_id": "b", "status": "Pending"},
                {"node_id": "c", "status": "Pending"},
            ],
        )
        executor, provider = _executor(gateway, ["two", "three"])
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Completed")
        self.assertEqual(outcome.nodes_run, ["a", "b", "c"])
        # Node a is not executed a second time; its stored output is reused.
        self.assertEqual([call["node_id"] for call in gateway.node_runs], ["b", "c"])
        prompt = " ".join(m["content"] for m in gateway.node_runs[0]["input_payload"]["messages"])
        self.assertIn("ทำแล้ว", prompt)

    def test_a_rejected_proposal_cancels_the_workflow(self):
        gateway = WorkflowGateway(
            chain("a", "b"),
            state_nodes=[
                {"node_id": "a", "status": "Cancelled", "error": "A proposal was not approved"},
                {"node_id": "b", "status": "Pending"},
            ],
        )
        executor, provider = _executor(gateway, ["two"])
        outcome = executor.execute(gateway.parent)

        self.assertEqual(outcome.status, "Cancelled")
        self.assertEqual(gateway.completed[-1]["status"], "Cancelled")
        self.assertEqual(gateway.node_runs, [])
        self.assertEqual(provider.calls, [])

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

    def test_unreadable_state_still_lets_a_fresh_workflow_run(self):
        """State is an optimisation for resumption, not a precondition."""
        gateway = WorkflowGateway(chain("a"))
        gateway.get_workflow_state = lambda **kwargs: (_ for _ in ()).throw(
            GatewayRejected("not available", 404)
        )
        executor, _ = _executor(gateway, ["one"])
        outcome = executor.execute(gateway.parent)
        self.assertEqual(outcome.status, "Completed")


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
