"""Deterministic purchase-email validation rules.

Mirrors the purchase team's Excel checks: vendor lists, amount thresholds,
required fields, and SKU allowlists. Pure Python — no Frappe import — so the
suite runs without a site.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RuleSpec:
	name: str
	rule_type: str
	action: str = "Flag"
	priority: int = 100
	field_name: str = ""
	compare_op: str = "<="
	value: str = ""
	message: str = ""

	@classmethod
	def from_mapping(cls, row: dict[str, Any]) -> "RuleSpec":
		return cls(
			name=str(row.get("rule_name") or row.get("name") or "Rule"),
			rule_type=str(row.get("rule_type") or ""),
			action=str(row.get("action") or "Flag"),
			priority=int(row.get("priority") or 100),
			field_name=str(row.get("field_name") or ""),
			compare_op=str(row.get("compare_op") or "<="),
			value=str(row.get("value") or ""),
			message=str(row.get("message") or ""),
		)


@dataclass
class RuleResult:
	rule_name: str
	action: str
	passed: bool
	message: str
	rule: str = ""

	def as_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass
class Evaluation:
	decision: str
	summary: str
	results: list[RuleResult] = field(default_factory=list)

	def as_dict(self) -> dict[str, Any]:
		return {
			"decision": self.decision,
			"summary": self.summary,
			"results": [row.as_dict() for row in self.results],
		}


def _split_list(raw: str) -> set[str]:
	"""Split allow/block lists on newlines or semicolons only.

	Commas are kept so vendor names like ``Siam Hardware Co., Ltd.`` stay intact.
	"""
	parts: list[str] = []
	for chunk in (raw or "").replace(";", "\n").splitlines():
		token = chunk.strip().casefold()
		if token:
			parts.append(token)
	return set(parts)


def _get_field(draft: dict[str, Any], field_name: str) -> Any:
	name = (field_name or "").strip()
	if not name:
		return None
	if name.startswith("items."):
		attr = name.split(".", 1)[1]
		return [item.get(attr) for item in (draft.get("items") or [])]
	return draft.get(name)


def _is_present(value: Any) -> bool:
	if value is None:
		return False
	if isinstance(value, (list, tuple)):
		return bool(value) and all(_is_present(v) for v in value)
	if isinstance(value, str):
		return bool(value.strip())
	return True


def _compare(left: float, op: str, right: float) -> bool:
	if op == "<=":
		return left <= right
	if op == ">=":
		return left >= right
	if op == "=":
		return left == right
	if op == "!=":
		return left != right
	return left <= right


def evaluate_rule(rule: RuleSpec, draft: dict[str, Any]) -> RuleResult:
	"""Return whether the draft satisfies one rule.

	``passed`` means the check is OK (no fail action should fire).
	"""
	rule_type = rule.rule_type
	default_message = rule.message or f"{rule.name} failed"
	vendor = str(draft.get("vendor_name") or "").strip()
	total = float(draft.get("total_amount") or 0)
	skus = [
		str(item.get("sku") or item.get("item_code") or "").strip()
		for item in (draft.get("items") or [])
	]

	passed = True
	message = f"{rule.name} passed"

	if rule_type == "Vendor Allowlist":
		allowed = _split_list(rule.value)
		passed = vendor.casefold() in allowed if allowed else bool(vendor)
		if not passed:
			message = rule.message or f"Vendor '{vendor or '-'}' is not allowlisted"

	elif rule_type == "Vendor Blocklist":
		blocked = _split_list(rule.value)
		passed = vendor.casefold() not in blocked
		if not passed:
			message = rule.message or f"Vendor '{vendor}' is blocklisted"

	elif rule_type == "Amount Max":
		threshold = float(rule.value or 0)
		passed = _compare(total, rule.compare_op or "<=", threshold)
		if not passed:
			message = rule.message or f"Amount {total} exceeds max {threshold}"

	elif rule_type == "Amount Min":
		threshold = float(rule.value or 0)
		op = rule.compare_op if rule.compare_op in (">=", ">") else ">="
		passed = _compare(total, op, threshold)
		if not passed:
			message = rule.message or f"Amount {total} is below min {threshold}"

	elif rule_type == "Required Field":
		value = _get_field(draft, rule.field_name)
		passed = _is_present(value)
		if not passed:
			message = rule.message or f"Required field '{rule.field_name}' is missing"

	elif rule_type == "SKU Exists":
		# Pure-engine stand-in: treat non-empty SKUs as existing. The Frappe
		# pipeline re-checks against Item master when a site is available.
		passed = bool(skus) and all(skus)
		if not passed:
			message = rule.message or "One or more line items are missing a SKU"

	elif rule_type == "SKU Allowlist":
		allowed = _split_list(rule.value)
		unknown = [sku for sku in skus if sku.casefold() not in allowed]
		passed = not unknown
		if not passed:
			message = rule.message or f"Unknown SKUs: {', '.join(unknown)}"

	elif rule_type == "Custom Expression":
		# Reserved hook — fail closed for the MVP so operators must use typed rules.
		passed = False
		message = rule.message or "Custom Expression is not enabled in the MVP"

	else:
		passed = False
		message = default_message

	return RuleResult(
		rule_name=rule.name,
		action=rule.action,
		passed=passed,
		message=message,
	)


def evaluate_rules(rules: list[RuleSpec], draft: dict[str, Any]) -> Evaluation:
	ordered = sorted(rules, key=lambda row: (row.priority, row.name))
	results: list[RuleResult] = []
	decision = "Approve"
	summaries: list[str] = []

	for rule in ordered:
		result = evaluate_rule(rule, draft)
		results.append(result)
		if result.passed:
			continue
		summaries.append(result.message)
		if rule.action == "Reject":
			decision = "Reject"
			break
		if rule.action == "Flag" and decision != "Reject":
			decision = "Flag"
		elif rule.action == "Approve" and decision == "Approve":
			# Approve-on-fail is unusual; treat as Flag so a human still sees it.
			decision = "Flag"

	if decision == "Approve" and not results:
		decision = "Needs Review"
		summaries.append("No validation rules configured")

	summary = "; ".join(summaries) if summaries else "All rules passed"
	return Evaluation(decision=decision, summary=summary, results=results)
