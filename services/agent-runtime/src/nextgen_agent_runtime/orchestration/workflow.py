"""Multi-agent workflow execution.

A workflow run walks a validated graph in topological order. An **agent node**
executes as an ordinary agent turn through :class:`RunExecutor`, so the tool
allowlist, company scoping, proposals, approval and audit are identical to a
single-agent run. Every other node kind — a query, a spreadsheet, an API call, a
report — is executed *by Frappe* on request, because this service has no
database, file or outbound-network access of its own and must not acquire any.

What travels between nodes is an **envelope**: text, structured rows, ERP
documents and file artifacts. A node reads the envelopes of the nodes actually
connected to it, so a graph that fans out and merges behaves as it is drawn
rather than collapsing into whatever ran last.

Three properties matter most:

* **A workflow cannot approve its own work.** If a node produces a proposal, the
  parent run ends at ``Waiting Approval`` and the remaining nodes do not run. A
  human decides before anything else happens.
* **An approval resumes it.** The resumed workflow is a *new* execution of the
  same parent run, so what earlier nodes produced is read back from Frappe via
  ``get_workflow_state`` rather than remembered.
* **A restart replays rather than duplicates.** Node keys are derived from the
  parent run and node id, so re-dispatching reuses the child runs and node
  results already recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .. import telemetry
from ..models.contracts import TERMINAL_RUN_STATUSES
from ..redaction import redact_error
from .frappe_client import GatewayError, GatewayRejected
from .runner import RunExecutor, RunRejected

MAX_NODES = 40
CARRIED_ANSWER_CHARS = 4000
#: How many rows of an incoming envelope a node's prompt actually shows. The
#: whole result stays on the node record; a model gets a readable sample.
PROMPT_ROWS = 25


class GraphError(ValueError):
    """The graph is unusable: cyclic, empty, or structurally broken."""


@dataclass
class WorkflowOutcome:
    run_id: str
    status: str
    nodes_run: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    error: str = ""


def node_data(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def node_agent(node: dict[str, Any]) -> str:
    return str(node_data(node).get("agent") or "").strip()


def node_prompt(node: dict[str, Any]) -> str:
    return str(node_data(node).get("prompt") or "").strip()


def node_kind(node: dict[str, Any]) -> str:
    """A graph saved before typed nodes existed is a graph of agent nodes."""
    return str(node_data(node).get("kind") or "").strip() or "agent"


def is_agent(node: dict[str, Any]) -> bool:
    return node_kind(node) == "agent"


def node_label(node: dict[str, Any]) -> str:
    return str(node.get("label") or node_data(node).get("label") or node.get("id") or "").strip()


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Kahn's algorithm, tie-broken by canvas position then id.

    Frappe validates the graph on save, so this should never fail in practice.
    It is re-checked because executing a cycle would loop forever.
    """
    nodes = {str(n["id"]): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    if not nodes:
        raise GraphError("workflow graph has no nodes")
    if len(nodes) > MAX_NODES:
        raise GraphError(f"workflow graph exceeds {MAX_NODES} nodes")

    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source in nodes and target in nodes:
            outgoing[source].append(target)
            incoming[target] += 1

    def sort_key(node_id: str) -> tuple:
        position = nodes[node_id].get("position") or {}
        return (float(position.get("x") or 0), float(position.get("y") or 0), node_id)

    ready = sorted([n for n, count in incoming.items() if count == 0], key=sort_key)
    ordered: list[dict[str, Any]] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes[node_id])
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort(key=sort_key)

    if len(ordered) != len(nodes):
        raise GraphError("workflow graph contains a cycle")
    return ordered


def parents(graph: dict[str, Any], node_id: str, order: list[dict[str, Any]]) -> list[str]:
    """Ids feeding this node, in execution order.

    This is what makes an edge mean something: a node reads the output of the
    nodes connected to it, not of whichever node happened to run last.
    """
    position = {str(node["id"]): index for index, node in enumerate(order)}
    sources = [
        str(edge.get("source"))
        for edge in graph.get("edges") or []
        if isinstance(edge, dict)
        and str(edge.get("target")) == str(node_id)
        and str(edge.get("source")) in position
    ]
    return sorted(dict.fromkeys(sources), key=lambda source: position[source])


class WorkflowExecutor:
    """Runs one workflow: a parent run, its node runs and its node results."""

    def __init__(self, config, gateway, provider, node_executor: RunExecutor | None = None):
        self.config = config
        self.gateway = gateway
        self.provider = provider
        # Agent nodes reuse the ordinary single-agent executor unchanged.
        self.node_executor = node_executor or RunExecutor(config, gateway, provider)

    def execute(self, parent_context: dict[str, Any]) -> WorkflowOutcome:
        run_id = parent_context["run_id"]
        outcome = WorkflowOutcome(run_id=run_id, status="Failed")
        graph = parent_context.get("graph") or {}
        try:
            order = topological_order(graph)
        except GraphError as exc:
            return self._fail(parent_context, outcome, exc)

        telemetry.info("workflow.started", nodes=len(order), workflow=parent_context.get("workflow"))
        seed = _seed_message(parent_context)
        # What earlier nodes produced. On a resumed run this starts populated:
        # the previous execution ended in a different process.
        outputs = self._restore(parent_context, outcome)
        if outcome.status == "Cancelled":
            return self._complete(parent_context, outcome, "Cancelled")

        for index, node in enumerate(order):
            node_id = str(node["id"])
            if node_id in outputs:
                telemetry.info("workflow.node_replayed", node=node_id)
                outcome.nodes_run.append(node_id)
                continue

            inputs = [outputs[source] for source in parents(graph, node_id, order) if source in outputs]
            # Recorded before execution so a node that fails still appears in
            # the run's usage as the point the workflow reached.
            outcome.nodes_run.append(node_id)
            try:
                if is_agent(node):
                    envelope, parked = self._run_agent_node(parent_context, outcome, node, seed, inputs)
                else:
                    envelope, parked = self._run_deterministic_node(parent_context, node)
            except (GatewayRejected, GatewayError, RunRejected) as exc:
                return self._fail(parent_context, outcome, exc)

            if parked:
                # A human decides before the rest of the graph runs.
                telemetry.info(
                    "workflow.waiting_approval", node=node_id, remaining=len(order) - index - 1
                )
                return self._complete(parent_context, outcome, "Waiting Approval")
            outputs[node_id] = envelope

        return self._complete(parent_context, outcome, "Completed")

    # -- nodes -------------------------------------------------------------

    def _run_agent_node(self, context, outcome, node, seed, inputs) -> tuple[dict[str, Any], bool]:
        """Execute an agent node as its own child run."""
        node_id = str(node["id"])
        child = self.gateway.create_node_run(
            parent_run_id=outcome.run_id,
            node_id=node_id,
            agent_type=node_agent(node),
            node_prompt=node_prompt(node),
            input_payload=_agent_input(node, seed, inputs),
            # Deterministic: re-dispatching the parent reuses this run.
            idempotency_key=f"{outcome.run_id}:node:{node_id}",
            correlation_id=context["correlation_id"],
        )
        if child["status"] in TERMINAL_RUN_STATUSES:
            # Already executed on a previous attempt and not restored from
            # state; carry an empty envelope rather than running it twice.
            telemetry.info("workflow.node_replayed", node=node_id, status=child["status"])
            return _envelope(node), False

        node_outcome = self.node_executor.execute(child["run_id"])
        if node_outcome.status not in ("Completed", "Waiting Approval"):
            raise RunRejected(f"node {node_id!r} ended as {node_outcome.status}")
        if node_outcome.proposals:
            outcome.proposals.extend(node_outcome.proposals)
            return _envelope(node), True
        return _envelope(node, text=getattr(node_outcome, "final_text", "") or ""), False

    def _run_deterministic_node(self, context, node) -> tuple[dict[str, Any], bool]:
        """Ask Frappe to execute a source or sink node.

        Frappe re-reads the node from the stored graph and rebuilds its inputs
        from the outputs it already persisted, so nothing about *what* the node
        does comes from this service.
        """
        node_id = str(node["id"])
        result = self.gateway.execute_node(
            parent_run_id=context["run_id"],
            node_id=node_id,
            idempotency_key=f"{context['run_id']}:node:{node_id}",
            correlation_id=context["correlation_id"],
        )
        if result["status"] != "Completed":
            raise RunRejected(
                f"node {node_id!r} ended as {result['status']}: {result.get('error') or 'no detail'}"
            )
        telemetry.info("workflow.node_executed", node=node_id, kind=node_kind(node))
        return result.get("output") or _envelope(node), False

    def _restore(self, context, outcome: WorkflowOutcome) -> dict[str, dict[str, Any]]:
        """Read back what earlier executions of this run already achieved.

        A workflow that parked for an approval resumes in a different process.
        Without this, a resumed run would repeat every node it had already done.
        """
        try:
            state = self.gateway.get_workflow_state(
                parent_run_id=context["run_id"], correlation_id=context.get("correlation_id")
            )
        except (GatewayRejected, GatewayError) as exc:
            # State is an optimisation for resumption, not a precondition for a
            # first run: a fresh workflow has nothing to restore.
            telemetry.warning("workflow.state_unavailable", error=redact_error(exc))
            return {}

        restored: dict[str, dict[str, Any]] = {}
        for entry in state.get("nodes") or []:
            if entry.get("status") == "Cancelled":
                outcome.status = "Cancelled"
                outcome.error = str(entry.get("error") or "a proposal was not approved")
                telemetry.info("workflow.cancelled", node=entry.get("node_id"))
                return {}
            if entry.get("status") == "Completed" and entry.get("output"):
                restored[str(entry["node_id"])] = entry["output"]
        if restored:
            telemetry.info("workflow.resumed", restored=len(restored))
        return restored

    # -- internals ---------------------------------------------------------

    def _complete(self, context, outcome: WorkflowOutcome, status: str) -> WorkflowOutcome:
        outcome.status = status
        try:
            self.gateway.complete_run(
                run_id=outcome.run_id,
                status=status,
                usage={
                    "nodes_run": outcome.nodes_run,
                    "proposals": outcome.proposals,
                    "workflow": context.get("workflow"),
                },
                error=outcome.error or None,
                correlation_id=context.get("correlation_id"),
            )
        except GatewayError as exc:
            telemetry.error("workflow.complete_failed", error=redact_error(exc))
        telemetry.info("workflow.finished", status=status, nodes=len(outcome.nodes_run))
        return outcome

    def _fail(self, context, outcome: WorkflowOutcome, exc: BaseException) -> WorkflowOutcome:
        message = redact_error(exc)
        outcome.status = "Failed"
        outcome.error = message
        telemetry.error("workflow.failed", error=message)
        try:
            self.gateway.complete_run(
                run_id=outcome.run_id,
                status="Failed",
                error=message,
                correlation_id=context.get("correlation_id"),
            )
        except GatewayError as complete_error:
            telemetry.error("workflow.complete_failed", error=redact_error(complete_error))
        return outcome


def _envelope(node: dict[str, Any], *, text: str = "") -> dict[str, Any]:
    """An agent node's output in the same shape a Frappe-executed node returns."""
    return {
        "node_id": str(node.get("id") or ""),
        "node_kind": node_kind(node),
        "label": node_label(node) or None,
        "text": (text or "")[:CARRIED_ANSWER_CHARS] or None,
        "data": [],
        "row_count": 0,
        "truncated": False,
        "documents": [],
        "artifacts": [],
    }


def _seed_message(context: dict[str, Any]) -> str:
    for turn in (context.get("input") or {}).get("messages") or []:
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("content") or "")
    return ""


def _summarise(item: dict[str, Any]) -> str:
    """One incoming envelope, rendered for a model to read."""
    if not isinstance(item, dict):
        return ""
    lines = [f"[{item.get('label') or item.get('node_id') or 'step'}]"]
    if item.get("text"):
        lines.append(str(item["text"]))
    rows = item.get("data") or []
    if rows:
        total = int(item.get("row_count") or len(rows))
        sample = rows[:PROMPT_ROWS]
        header = f"ข้อมูล {total} แถว"
        if total > len(sample):
            header += f" (แสดง {len(sample)} แถวแรก)"
        lines.append(f"{header}:\n" + json.dumps(sample, ensure_ascii=False, default=str))
    for document in item.get("documents") or []:
        if isinstance(document, dict) and document.get("doctype"):
            lines.append(f"เอกสาร: {document['doctype']} {document.get('name') or ''}".strip())
    for artifact in item.get("artifacts") or []:
        lines.append(f"ไฟล์: {artifact}")
    return "\n".join(line for line in lines if line)


def _agent_input(node: dict[str, Any], seed: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """What an agent node sees: the original request plus its actual inputs."""
    messages = []
    if seed:
        messages.append({"role": "user", "content": seed})
    carried = "\n\n".join(summary for summary in (_summarise(item) for item in inputs) if summary)
    if carried:
        messages.append(
            {
                "role": "user",
                "content": f"ผลลัพธ์จากขั้นตอนก่อนหน้า:\n{carried[:CARRIED_ANSWER_CHARS]}",
            }
        )
    return {"messages": messages, "context": {"node": str(node.get("id") or "")}}
