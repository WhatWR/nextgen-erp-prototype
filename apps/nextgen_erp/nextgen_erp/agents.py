"""Explicit agent registry for the NextGen AI multi-agent assistant.

Every chat session, message, action and tool execution carries an
``agent_type``. The server — never the LLM — resolves the agent, checks the
user's roles and restricts tool dispatch to that agent's allowlist. Agents
reference their tool modules by dotted path so this registry stays import-safe
(the tool modules import :mod:`nextgen_erp.agents` back).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

import frappe
from frappe import _

DEFAULT_AGENT = "sales"
VALID_AGENT_TYPES = ("sales", "procurement")


@dataclass(frozen=True)
class Agent:
	key: str
	title: str
	subtitle: str
	icon: str
	color: str
	required_roles: frozenset[str]
	tools_path: str
	action_types: frozenset[str]
	route_keywords: tuple[str, ...]
	welcome_message: str
	suggested_questions: tuple[str, ...]
	model_fields: tuple[str, ...] = field(default=("staff_chat_model", "chat_model"))
	action_preview_text: str = ""

	def _module(self):
		return import_module(self.tools_path)

	@property
	def tools(self) -> list[dict[str, Any]]:
		return self._module().TOOLS

	@property
	def tool_names(self) -> set[str]:
		return {tool["function"]["name"] for tool in self.tools}

	@property
	def system_prompt(self) -> str:
		return self._module().SYSTEM_PROMPT

	def dispatch(self, name: str, arguments: dict, *, user: str, session_id: str) -> dict:
		"""Run one tool strictly inside this agent's allowlist."""
		if name not in self.tool_names:
			return {"error": f"Tool not allowed for agent {self.key}: {name}"}
		return self._module().dispatch_tool(name, arguments, user=user, session_id=session_id)

	def has_access(self, user: str) -> bool:
		if user == "Administrator":
			return True
		return bool(self.required_roles.intersection(frappe.get_roles(user)))

	def matches_route(self, route: str) -> bool:
		# Frappe routes mix spaces and hyphens ("Workspaces/AI Sales Copilot"
		# vs "/app/ai-sales-copilot"); normalise before keyword matching.
		route = (route or "").casefold().replace(" ", "-").replace("%20", "-")
		return any(keyword in route for keyword in self.route_keywords)

	def public_config(self) -> dict:
		"""Safe client bootstrap payload. Never includes prompts, models or keys."""
		return {
			"key": self.key,
			"title": self.title,
			"subtitle": self.subtitle,
			"icon": self.icon,
			"color": self.color,
			"route_keywords": list(self.route_keywords),
			"welcome_message": self.welcome_message,
			"suggested_questions": list(self.suggested_questions),
		}


AGENTS: dict[str, Agent] = {
	"sales": Agent(
		key="sales",
		title="AI Sales Copilot",
		subtitle="ขาย · ออเดอร์ · สต๊อก",
		icon="/assets/nextgen_erp/images/ai-sales-copilot.svg",
		color="#5b5cf0",
		required_roles=frozenset({"System Manager", "Sales Manager", "Sales User"}),
		tools_path="nextgen_erp.staff_chat",
		action_types=frozenset({"prepare_sales_order"}),
		route_keywords=("ai-sales-copilot", "selling", "sales-order", "customer", "quotation"),
		welcome_message="สวัสดีค่ะ ฉันคือ AI Sales Copilot ช่วยค้นหาสินค้า ราคา สต๊อก และเตรียม Sales Order ให้ตรวจสอบได้",
		suggested_questions=("สินค้าตัวไหนสต๊อกต่ำ", "ดูออเดอร์ล่าสุด", "สร้าง Sales Order"),
		model_fields=("staff_chat_model", "chat_model"),
		action_preview_text=(
			"สร้าง Action Preview แล้วค่ะ กรุณาตรวจสอบข้อมูลด้านล่าง "
			"ขณะนี้ยังไม่ได้สร้าง Sales Order และจะดำเนินการต่อเมื่อคุณกดยืนยันเท่านั้น"
		),
	),
	"procurement": Agent(
		key="procurement",
		title="AI Procurement Copilot",
		subtitle="จัดซื้อ · พยากรณ์ · สต๊อก",
		icon="/assets/nextgen_erp/images/ai-procurement-copilot.svg",
		color="#0e9f6e",
		required_roles=frozenset({"System Manager", "Purchase Manager", "Purchase User", "Stock Manager"}),
		tools_path="nextgen_erp.procurement",
		action_types=frozenset({"prepare_purchase_order", "prepare_material_request"}),
		route_keywords=(
			"ai-procurement-copilot",
			"buying",
			"purchase-order",
			"purchase-receipt",
			"purchase-invoice",
			"material-request",
			"supplier",
			"nextgen-procurement",
		),
		welcome_message=(
			"สวัสดีค่ะ ฉันคือ AI Procurement Copilot ช่วยวิเคราะห์ demand 30/60/90 วัน "
			"ความเสี่ยงของขาด และเตรียม Purchase Order ให้ตรวจสอบก่อนสร้างจริง"
		),
		suggested_questions=(
			"สินค้าตัวไหนเสี่ยงขาดใน 30 วัน",
			"สรุปสินค้าหมุนเร็วจาก 90 วันที่ผ่านมา",
			"ควรสั่งซื้ออะไรในสัปดาห์นี้",
			"วิเคราะห์ M-150 และสร้าง Purchase Order preview",
			"แสดง Purchase Order ที่ยังรับของไม่ครบ",
			"ราคาซื้อล่าสุดเปลี่ยนจากรอบก่อนเท่าไร",
		),
		model_fields=("staff_chat_model", "chat_model"),
		action_preview_text=(
			"สร้าง Action Preview ฝั่งจัดซื้อแล้วค่ะ กรุณาตรวจสอบข้อมูลด้านล่าง "
			"ขณะนี้ยังไม่ได้สร้างเอกสารจัดซื้อใดๆ และจะดำเนินการต่อเมื่อคุณกดยืนยันเท่านั้น"
		),
	),
}

ACTION_TYPE_AGENTS: dict[str, str] = {
	action_type: agent.key for agent in AGENTS.values() for action_type in agent.action_types
}


def get_agent(key: str | None) -> Agent:
	agent = AGENTS.get((key or "").strip())
	if not agent:
		frappe.throw(_("Unknown NextGen AI agent: {0}").format(key), frappe.ValidationError)
	return agent


def require_agent_access(key: str, user: str | None = None) -> Agent:
	agent = get_agent(key)
	user = user or frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	if not agent.has_access(user):
		frappe.throw(
			_("You are not permitted to use {0}").format(agent.title), frappe.PermissionError
		)
	return agent


def allowed_agents(user: str | None = None) -> list[Agent]:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	return [agent for agent in AGENTS.values() if agent.has_access(user)]


def resolve_route_agent(route: str | None) -> str | None:
	"""Deterministic route → agent mapping. Returns None when no route matches."""
	for agent in AGENTS.values():
		if agent.matches_route(route or ""):
			return agent.key
	return None


def agent_for_action_type(action_type: str) -> Agent:
	key = ACTION_TYPE_AGENTS.get(action_type)
	if not key:
		frappe.throw(_("Unknown chat action type: {0}").format(action_type), frappe.ValidationError)
	return AGENTS[key]
