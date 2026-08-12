"""One correlation ID per operation, generated at channel entry.

The ID is created when a channel first calls an authenticated Frappe method and
then travels with the run: Frappe logs, dispatch, runtime logs, tool calls,
proposals and the resulting ERP documents all carry it, so one operation can be
followed across both services.
"""

from __future__ import annotations

import uuid

import frappe

HEADER = "X-NextGen-Correlation-Id"
_LOCAL_KEY = "nextgen_correlation_id"


def new_correlation_id() -> str:
	return f"ngc-{uuid.uuid4()}"


def from_request() -> str | None:
	"""Read an inbound correlation ID, preferring the caller's own."""
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) if request else None
	if not headers:
		return None
	value = headers.get(HEADER) or headers.get(HEADER.lower())
	value = str(value or "").strip()
	# Untrusted input: bound it so a client cannot smuggle a payload into logs.
	return value[:140] or None


def current(create: bool = True) -> str | None:
	"""Correlation ID for this request, generating one on first use."""
	value = getattr(frappe.local, _LOCAL_KEY, None)
	if value:
		return value
	value = from_request()
	if not value and create:
		value = new_correlation_id()
	if value:
		setattr(frappe.local, _LOCAL_KEY, value)
	return value


def bind(value: str) -> str:
	setattr(frappe.local, _LOCAL_KEY, value)
	return value
