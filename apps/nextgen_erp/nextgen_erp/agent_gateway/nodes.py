"""Workflow node kinds: what a node is, how it validates, how it executes.

A workflow used to be a chain of agents. It is now a small graph of *typed*
nodes, because the shapes people actually ask for start and end outside the
model: a spreadsheet becomes a report, an API response becomes a decision, a
query becomes a sales order.

Two rules shape everything here.

**Deterministic nodes execute in Frappe, never in the runtime.** The runtime has
no file access, no database connection and no outbound network of its own, and
this module does not hand it any. It asks Frappe to execute a node; Frappe
re-reads that node from the *stored* graph, so the runtime cannot describe a
node the workflow does not contain, and cannot supply a URL, a path, a query or
a file of its own choosing.

**A node never widens authority.** Reads run as the human who requested the run,
under their own Frappe permissions. Outbound HTTP is confined to a company's
configured Connection and its path allowlist. Saving a graph grants nothing that
the policy, the permission or the connection did not already allow.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from nextgen_erp.agent_gateway.redaction import redact

#: A node's structured payload is bounded before it is stored or handed on. A
#: workflow moves business data between steps, not entire tables.
MAX_ROWS = 500
MAX_TEXT_CHARS = 4000
#: What a downstream agent actually sees of its input rows. The full envelope
#: stays on the node result; the prompt gets a readable sample.
PROMPT_ROWS = 25

CONNECTION_DOCTYPE = "NextGen Agent Connection"


class NodeError(Exception):
	"""The node is misconfigured or refused. Recorded, never retried."""


Transport = Callable[[str, str, dict[str, str], bytes, float], tuple[int, bytes]]


# ---------------------------------------------------------------------------
# envelopes
# ---------------------------------------------------------------------------


def envelope(
	node: dict[str, Any],
	*,
	text: str = "",
	data: list[dict[str, Any]] | None = None,
	documents: list[dict[str, Any]] | None = None,
	artifacts: list[str] | None = None,
	row_count: int | None = None,
) -> dict[str, Any]:
	"""One node's output, in the single shape every downstream node reads."""
	rows = [row for row in (data or []) if isinstance(row, dict)]
	total = len(rows) if row_count is None else int(row_count)
	return {
		"node_id": str(node.get("id") or ""),
		"node_kind": node_kind(node),
		"label": node_label(node)[:MAX_TEXT_CHARS] or None,
		"text": (text or "")[:MAX_TEXT_CHARS] or None,
		"data": redact(rows[:MAX_ROWS]),
		"row_count": total,
		"truncated": total > MAX_ROWS,
		"documents": documents or [],
		"artifacts": artifacts or [],
	}


def envelope_summary(item: dict[str, Any]) -> str:
	"""A compact, model-readable rendering of one incoming envelope.

	Rows are sampled rather than dumped: an agent needs enough to reason with,
	and a run's model budget is not the place to spend a whole result set.
	"""
	if not isinstance(item, dict):
		return ""
	label = item.get("label") or item.get("node_id") or "step"
	lines = [f"[{label}]"]
	if item.get("text"):
		lines.append(str(item["text"]))
	rows = item.get("data") or []
	if rows:
		total = cint(item.get("row_count")) or len(rows)
		sample = rows[:PROMPT_ROWS]
		lines.append(
			f"ข้อมูล {total} แถว"
			+ (f" (แสดง {len(sample)} แถวแรก)" if total > len(sample) else "")
			+ ":\n"
			+ json.dumps(sample, ensure_ascii=False, default=str)
		)
	for document in item.get("documents") or []:
		if isinstance(document, dict) and document.get("doctype"):
			lines.append(f"เอกสาร: {document['doctype']} {document.get('name') or ''}".strip())
	for artifact in item.get("artifacts") or []:
		lines.append(f"ไฟล์: {artifact}")
	return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# node shape
# ---------------------------------------------------------------------------


def node_data(node: dict[str, Any]) -> dict[str, Any]:
	data = node.get("data")
	return data if isinstance(data, dict) else {}


def node_kind(node: dict[str, Any]) -> str:
	"""The node's kind, defaulting to ``agent``.

	Graphs saved before typed nodes existed carry only ``data.agent``; they stay
	valid and keep running as agent nodes.
	"""
	kind = str(node_data(node).get("kind") or "").strip()
	return kind or "agent"


def node_label(node: dict[str, Any]) -> str:
	data = node_data(node)
	return str(node.get("label") or data.get("label") or node.get("id") or "").strip()


def is_agent(node: dict[str, Any]) -> bool:
	return node_kind(node) == "agent"


@dataclass(frozen=True)
class NodeKind:
	key: str
	title: str
	#: Raises NodeError when the node's own configuration is unusable.
	validate: Callable[[dict[str, Any], str | None], None]
	#: Executes the node and returns its envelope. An agent node has no executor
	#: here: it runs as its own child agent run.
	execute: Callable[..., dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# source.query — read ERP data as the requester
# ---------------------------------------------------------------------------


def _validate_query(node: dict[str, Any], company: str | None) -> None:
	from nextgen_erp.domain_tools import documents

	data = node_data(node)
	doctype = str(data.get("doctype") or "").strip()
	if not doctype:
		raise NodeError(_("A query node needs a DocType"))
	if documents.is_denied(doctype):
		# The records the assistant may never touch are equally off limits to a
		# workflow, whoever drew the graph.
		raise NodeError(_("{0} cannot be read by a workflow").format(doctype))
	_json_object(data.get("filters"), _("Query filters"))
	_field_list(data.get("fields"))


def _execute_query(node: dict[str, Any], run, inputs: list[dict[str, Any]]) -> dict[str, Any]:
	"""Read rows with ``frappe.get_list``, so permissions are the real boundary."""
	from nextgen_erp.domain_tools import documents

	data = node_data(node)
	doctype = str(data.get("doctype") or "").strip()
	documents.assert_readable(doctype)

	meta = frappe.get_meta(doctype)
	# Only real fields of this DocType survive, so a stale or mistyped column
	# narrows the result instead of erroring at the database.
	fields = [
		fieldname
		for fieldname in _field_list(data.get("fields")) or ["name"]
		if fieldname in ("name", "owner", "creation", "modified") or meta.get_field(fieldname)
	] or ["name"]
	filters = _json_object(data.get("filters"), _("Query filters"))
	if meta.get_field("company") and run.company:
		# A workflow is company-scoped; a query inside it cannot reach across.
		filters.setdefault("company", run.company)

	rows = [
		dict(row)
		for row in frappe.get_list(
			doctype,
			filters=filters,
			fields=fields,
			order_by=_safe_order_by(meta, data.get("order_by")),
			limit_page_length=max(1, min(cint(data.get("limit")) or 100, MAX_ROWS)),
		)
	]
	return envelope(
		node,
		text=_("อ่าน {0} จำนวน {1} แถว").format(doctype, len(rows)),
		data=rows,
		documents=[
			{"doctype": doctype, "name": row["name"]} for row in rows[:PROMPT_ROWS] if row.get("name")
		],
	)


def _safe_order_by(meta, order_by: Any) -> str:
	"""Only a real field of this DocType, and only asc/desc.

	``order_by`` reaches SQL, so it is rebuilt from validated parts rather than
	passed through.
	"""
	parts = str(order_by or "modified desc").replace(",", " ").split()
	fieldname = parts[0] if parts else "modified"
	direction = parts[1].lower() if len(parts) > 1 else "desc"
	if fieldname not in ("name", "modified", "creation") and not meta.get_field(fieldname):
		fieldname = "modified"
	if direction not in ("asc", "desc"):
		direction = "desc"
	return f"`tab{meta.name}`.`{fieldname}` {direction}"


# ---------------------------------------------------------------------------
# source.excel — read an uploaded spreadsheet
# ---------------------------------------------------------------------------


def _validate_excel(node: dict[str, Any], company: str | None) -> None:
	if not str(node_data(node).get("file") or "").strip():
		raise NodeError(_("A spreadsheet node needs an attached file"))


def _execute_excel(node: dict[str, Any], run, inputs: list[dict[str, Any]]) -> dict[str, Any]:
	"""Parse a Frappe File the requester is allowed to read into rows."""
	data = node_data(node)
	file_doc = _resolve_file(str(data.get("file") or "").strip())
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8", errors="replace")

	name = (file_doc.file_name or "").lower()
	if name.endswith((".xlsx", ".xlsm")):
		table = _read_xlsx(content)
	elif name.endswith((".csv", ".txt", ".tsv")):
		table = _read_csv(content, delimiter="\t" if name.endswith(".tsv") else ",")
	else:
		raise NodeError(_("Only .xlsx, .csv and .tsv files can be read by a workflow"))

	header_row = max(0, cint(data.get("header_row")) - 1)
	if len(table) <= header_row:
		raise NodeError(_("The file has no rows below its header"))
	headers = [
		str(cell or "").strip() or f"column_{index + 1}"
		for index, cell in enumerate(table[header_row])
	]
	limit = max(1, min(cint(data.get("limit")) or MAX_ROWS, MAX_ROWS))

	rows: list[dict[str, Any]] = []
	for raw in table[header_row + 1 :]:
		if not any(str(cell or "").strip() for cell in raw):
			continue
		rows.append(
			{headers[index]: raw[index] if index < len(raw) else None for index in range(len(headers))}
		)
		if len(rows) >= limit:
			break

	return envelope(
		node,
		text=_("อ่านไฟล์ {0} ได้ {1} แถว ({2} คอลัมน์)").format(
			file_doc.file_name, len(rows), len(headers)
		),
		data=rows,
		artifacts=[file_doc.file_url] if file_doc.file_url else [],
	)


def _resolve_file(reference: str):
	"""Find the File by name or URL and refuse one the requester cannot read.

	``acting_as`` is already in force, so ``has_permission`` gives the
	requester's own answer, including any private-file ownership rule.
	"""
	name = reference
	if reference.startswith(("/", "http")):
		name = frappe.db.get_value("File", {"file_url": reference}, "name")
	if not name or not frappe.db.exists("File", name):
		raise NodeError(_("Unknown file: {0}").format(reference))
	if not frappe.has_permission("File", "read", doc=name):
		raise NodeError(_("You do not have permission to read this file"))
	return frappe.get_doc("File", name)


def _read_csv(content: bytes, delimiter: str = ",") -> list[list[Any]]:
	for encoding in ("utf-8-sig", "utf-8", "cp874", "latin-1"):
		try:
			text = content.decode(encoding)
			break
		except UnicodeDecodeError:
			continue
	else:  # pragma: no cover - latin-1 decodes any byte string
		raise NodeError(_("The file could not be decoded as text"))
	return [list(row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _read_xlsx(content: bytes) -> list[list[Any]]:
	try:
		from openpyxl import load_workbook
	except ImportError as exc:  # pragma: no cover - openpyxl ships with Frappe
		raise NodeError(_("Reading .xlsx files requires openpyxl on the server")) from exc
	# read_only keeps a large upload from being materialised in memory, and
	# data_only returns cached values instead of formula strings.
	workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	try:
		sheet = workbook.active
		return [list(row) for row in sheet.iter_rows(max_row=MAX_ROWS + 20, values_only=True)]
	finally:
		workbook.close()


# ---------------------------------------------------------------------------
# source.api — call a configured connection
# ---------------------------------------------------------------------------


def _default_transport(method: str, url: str, headers: dict[str, str], body: bytes, timeout: float):
	request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			return response.status, response.read()
	except urllib.error.HTTPError as exc:
		return exc.code, exc.read()
	except urllib.error.URLError as exc:
		raise NodeError(_("The connection could not be reached: {0}").format(exc.reason)) from exc


_transport: Transport = _default_transport


def set_transport(transport: Transport | None) -> None:
	"""Test seam. Production always uses the urllib transport."""
	global _transport
	_transport = transport or _default_transport


def _validate_api(node: dict[str, Any], company: str | None) -> None:
	data = node_data(node)
	connection = str(data.get("connection") or "").strip()
	if not connection:
		raise NodeError(_("An API node needs a connection"))
	if str(data.get("method") or "GET").upper() not in ("GET", "POST"):
		raise NodeError(_("An API node may only use GET or POST"))
	_json_object(data.get("query"), _("Query parameters"))
	if data.get("body") not in (None, ""):
		_json_object(data.get("body"), _("Request body"))
	if not company:
		return
	doc = _connection_doc(connection, company, validating=True)
	if not doc.allows(str(data.get("path") or "/")):
		raise NodeError(
			_("Connection {0} does not allow the path {1}").format(connection, data.get("path"))
		)


def _connection_doc(name: str, company: str, *, validating: bool = False):
	if not frappe.db.exists(CONNECTION_DOCTYPE, name):
		raise NodeError(_("Unknown connection: {0}").format(name))
	doc = frappe.get_doc(CONNECTION_DOCTYPE, name)
	if doc.company != company:
		raise NodeError(_("Connection {0} belongs to another company").format(name))
	if not cint(doc.enabled) and not validating:
		raise NodeError(_("Connection {0} is disabled").format(name))
	return doc


def _execute_api(node: dict[str, Any], run, inputs: list[dict[str, Any]]) -> dict[str, Any]:
	data = node_data(node)
	connection = _connection_doc(str(data.get("connection") or "").strip(), run.company)
	path = "/" + str(data.get("path") or "").lstrip("/")
	if not connection.allows(path):
		# Checked again at execution: the allowlist may have been narrowed since
		# the graph was saved, and the narrower answer is the one that counts.
		raise NodeError(_("Connection {0} does not allow the path {1}").format(connection.name, path))

	method = str(data.get("method") or "GET").upper()
	query = _json_object(data.get("query"), _("Query parameters"))
	url = connection.base_url + quote(path, safe="/") + (f"?{urlencode(query)}" if query else "")

	body = b""
	headers = {"Accept": "application/json", "User-Agent": "NextGen-ERP-Workflow"}
	if method == "POST":
		payload = (
			_json_object(data.get("body"), _("Request body")) if data.get("body") else _forward(inputs)
		)
		body = json.dumps(payload, ensure_ascii=False, default=str).encode()
		headers["Content-Type"] = "application/json"
	headers.update(_auth_headers(connection))

	status, raw = _transport(method, url, headers, body, float(cint(connection.timeout_seconds) or 10))
	limit = max(1, cint(connection.max_response_kb)) * 1024
	if len(raw) > limit:
		raise NodeError(
			_("The response is larger than the {0} KB this connection allows").format(
				cint(connection.max_response_kb)
			)
		)
	if status >= 400:
		raise NodeError(_("{0} returned HTTP {1}").format(connection.name, status))

	rows, text = _rows_from_response(raw)
	return envelope(
		node,
		text=text or _("{0} {1} ตอบกลับ HTTP {2}").format(method, path, status),
		data=rows,
	)


def _auth_headers(connection) -> dict[str, str]:
	"""Credentials are read here and never travel through a graph or a payload."""
	secret = connection.get_password("secret", raise_exception=False) or ""
	if connection.auth_type == "Bearer Token" and secret:
		return {"Authorization": f"Bearer {secret}"}
	if connection.auth_type == "Header" and secret:
		return {(connection.header_name or "X-API-Key").strip(): secret}
	if connection.auth_type == "Basic" and secret:
		encoded = base64.b64encode(f"{connection.username or ''}:{secret}".encode()).decode()
		return {"Authorization": f"Basic {encoded}"}
	return {}


def _rows_from_response(raw: bytes) -> tuple[list[dict[str, Any]], str]:
	text = raw.decode(errors="replace")
	try:
		payload = json.loads(text or "null")
	except json.JSONDecodeError:
		return [], text[:MAX_TEXT_CHARS]
	if isinstance(payload, list):
		return [row for row in payload if isinstance(row, dict)][:MAX_ROWS], ""
	if isinstance(payload, dict):
		for key in ("data", "results", "items", "records", "message"):
			value = payload.get(key)
			if isinstance(value, list):
				return [row for row in value if isinstance(row, dict)][:MAX_ROWS], ""
		return [payload], ""
	return [], text[:MAX_TEXT_CHARS]


def _forward(inputs: list[dict[str, Any]]) -> dict[str, Any]:
	"""What a POST sends when the node declares no body of its own."""
	return {
		"rows": [row for item in inputs for row in (item.get("data") or [])][:MAX_ROWS],
		"text": "\n\n".join(str(item.get("text") or "") for item in inputs if item.get("text"))[
			:MAX_TEXT_CHARS
		],
	}


# ---------------------------------------------------------------------------
# sink.report — write the result out as a file
# ---------------------------------------------------------------------------


def _validate_report(node: dict[str, Any], company: str | None) -> None:
	if str(node_data(node).get("format") or "html").lower() not in ("html", "csv", "json"):
		raise NodeError(_("A report may be html, csv or json"))


def _execute_report(node: dict[str, Any], run, inputs: list[dict[str, Any]]) -> dict[str, Any]:
	"""Render the incoming envelopes into a File attached to the run."""
	data = node_data(node)
	fmt = str(data.get("format") or "html").lower()
	title = str(data.get("title") or node_label(node) or "Workflow report").strip()
	rows = [row for item in inputs for row in (item.get("data") or [])]
	narrative = "\n\n".join(str(item.get("text") or "") for item in inputs if item.get("text"))

	if fmt == "csv":
		content, extension = _render_csv(rows), "csv"
	elif fmt == "json":
		content = json.dumps(
			{"title": title, "text": narrative, "rows": rows[:MAX_ROWS]},
			ensure_ascii=False,
			indent=2,
			default=str,
		)
		extension = "json"
	else:
		content, extension = _render_html(title, narrative, rows), "html"

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{_slug(title)}-{run.name}.{extension}",
			"content": content,
			"is_private": 1,
			"attached_to_doctype": "NextGen Agent Run",
			"attached_to_name": run.name,
		}
	).insert(ignore_permissions=True)

	return envelope(
		node,
		text=_("สร้างรายงาน {0} ({1} แถว)").format(file_doc.file_name, len(rows)),
		data=rows,
		artifacts=[file_doc.file_url],
	)


def _columns(rows: list[dict[str, Any]]) -> list[str]:
	"""Union of the rows' keys, in first-seen order."""
	headers: list[str] = []
	for row in rows:
		for key in row:
			if str(key) not in headers:
				headers.append(str(key))
	return headers


def _render_csv(rows: list[dict[str, Any]]) -> str:
	if not rows:
		return ""
	headers = _columns(rows)
	buffer = io.StringIO()
	writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore")
	writer.writeheader()
	for row in rows[:MAX_ROWS]:
		writer.writerow({key: row.get(key) for key in headers})
	return buffer.getvalue()


def _render_html(title: str, narrative: str, rows: list[dict[str, Any]]) -> str:
	from frappe.utils import escape_html

	headers = _columns(rows)
	head = "".join(f"<th>{escape_html(name)}</th>" for name in headers)
	body = "".join(
		"<tr>" + "".join(f"<td>{escape_html(str(row.get(key, '')))}</td>" for key in headers) + "</tr>"
		for row in rows[:MAX_ROWS]
	)
	table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>" if headers else ""
	paragraphs = "".join(
		f"<p>{escape_html(line)}</p>" for line in (narrative or "").splitlines() if line.strip()
	)
	return (
		"<!doctype html><html><head><meta charset='utf-8'>"
		f"<title>{escape_html(title)}</title>"
		"<style>body{font-family:system-ui,sans-serif;margin:32px;color:#111}"
		"table{border-collapse:collapse;width:100%;margin-top:16px;font-size:14px}"
		"th,td{border:1px solid #ddd;padding:6px 10px;text-align:left}"
		"th{background:#f5f5f5}</style></head><body>"
		f"<h1>{escape_html(title)}</h1>"
		f"<p><small>{escape_html(str(now_datetime()))}</small></p>"
		f"{paragraphs}{table}</body></html>"
	)


def _slug(value: str) -> str:
	cleaned = "".join(character if character.isalnum() else "-" for character in value.strip().lower())
	return "-".join(part for part in cleaned.split("-") if part)[:60] or "report"


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def _validate_agent(node: dict[str, Any], company: str | None) -> None:
	"""An agent node names an agent this company's policy leaves able to work."""
	from nextgen_erp import agents
	from nextgen_erp.domain_tools import allowed_tool_names

	agent_key = str(node_data(node).get("agent") or "").strip()
	if not agent_key:
		raise NodeError(_("Node {0} does not name an agent").format(node_label(node)))
	if agent_key not in agents.AGENTS:
		raise NodeError(
			_("Node {0} references an unknown agent: {1}").format(node_label(node), agent_key)
		)
	if company and not allowed_tool_names(agent_key, company):
		# The agent exists but this company's policy leaves it with nothing it
		# may call, so the node could never do useful work.
		raise NodeError(
			_("Agent {0} has no tools allowed by the policy for {1}").format(agent_key, company)
		)


NODE_KINDS: dict[str, NodeKind] = {
	"agent": NodeKind("agent", "AI Agent", _validate_agent),
	"source.query": NodeKind("source.query", "ERP Query", _validate_query, _execute_query),
	"source.excel": NodeKind("source.excel", "Spreadsheet", _validate_excel, _execute_excel),
	"source.api": NodeKind("source.api", "API Call", _validate_api, _execute_api),
	"sink.report": NodeKind("sink.report", "Report", _validate_report, _execute_report),
}


def get_kind(node: dict[str, Any]) -> NodeKind:
	kind = node_kind(node)
	definition = NODE_KINDS.get(kind)
	if not definition:
		raise NodeError(_("Unknown node kind: {0}").format(kind))
	return definition


def validate_node(node: dict[str, Any], company: str | None = None) -> None:
	get_kind(node).validate(node, company)


def execute_node(node: dict[str, Any], run, inputs: list[dict[str, Any]]) -> dict[str, Any]:
	"""Run one deterministic node as the requester and return its envelope."""
	from nextgen_erp.domain_tools.registry import acting_as

	definition = get_kind(node)
	if not definition.execute:
		raise NodeError(_("Node kind {0} is not executed by Frappe").format(definition.key))
	# The gateway authenticates as the restricted service identity, but the work
	# is authorised by the human on the run: reads, files and documents are all
	# subject to that person's own permissions.
	with acting_as(run.requested_by):
		return definition.execute(node, run, inputs)


def describe_kinds() -> list[dict[str, Any]]:
	"""What the designer may offer in its node palette."""
	return [
		{"key": kind.key, "title": _(kind.title), "deterministic": kind.execute is not None}
		for kind in NODE_KINDS.values()
	]


def _field_list(value: Any) -> list[str]:
	"""Field names as a list, whether the designer sent a list or "a, b, c".

	Each name is restricted to the characters a fieldname can contain, so
	nothing that reaches the query builder can be anything but an identifier.
	"""
	if value in (None, ""):
		return []
	names = value if isinstance(value, list) else str(value).split(",")
	fields = []
	for name in names:
		name = str(name).strip()
		if not name:
			continue
		if not name.replace("_", "").isalnum():
			raise NodeError(_("{0} is not a valid field name").format(name))
		fields.append(name)
	return fields


def _json_object(value: Any, label: str) -> dict[str, Any]:
	if value in (None, ""):
		return {}
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except (TypeError, ValueError) as exc:
			raise NodeError(_("{0} is not valid JSON").format(label)) from exc
	if not isinstance(value, dict):
		raise NodeError(_("{0} must be a JSON object").format(label))
	return value
