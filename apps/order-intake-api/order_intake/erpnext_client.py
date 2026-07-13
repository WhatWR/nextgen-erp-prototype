from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import quote, urlencode


class ERPNextError(RuntimeError):
    """Raised when an ERPNext request fails or returns an unexpected response."""


# A transport takes (url, method, headers, body) and returns (status, body_bytes).
# The default uses urllib; tests inject a fake so no live ERPNext site is needed.
Transport = Callable[[str, str, dict[str, str], "bytes | None"], "tuple[int, bytes]"]


def _urllib_transport(
    url: str, method: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        # Surface ERPNext's error body to the caller instead of masking it.
        return exc.code, exc.read()


class ERPNextClient:
    """Thin ERPNext / Frappe REST client.

    Reads are plain resource/`frappe.client` calls. Writes should go through the
    ``nextgen_erp`` app's whitelisted methods (see :class:`ERPNextAdapter`), not
    generic CRUD, so submission and idempotency stay server-side.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("ERPNEXT_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("ERPNEXT_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("ERPNEXT_API_SECRET", "")
        self._transport = transport or _urllib_transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.api_secret)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key and self.api_secret:
            # ERPNext token auth: never a user password, only an API key/secret pair.
            headers["Authorization"] = f"token {self.api_key}:{self.api_secret}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.base_url:
            raise ERPNextError("ERPNEXT_URL is not configured")
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = self._headers()
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(url, method, headers, body)
        text = raw.decode("utf-8", errors="replace") if raw else ""
        if status >= 400:
            raise ERPNextError(
                f"ERPNext {method} {path} failed (HTTP {status}): {text[:500].strip()}"
            )
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ERPNextError(f"ERPNext {method} {path} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ERPNextError(f"ERPNext {method} {path} returned an unexpected response")
        return payload

    def get_list(
        self,
        doctype: str,
        *,
        fields: list[str] | None = None,
        filters: list[list[Any]] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit_page_length": limit}
        if fields:
            params["fields"] = json.dumps(fields)
        if filters:
            params["filters"] = json.dumps(filters)
        data = self._request("GET", f"/api/resource/{quote(doctype)}", params=params).get("data")
        return data if isinstance(data, list) else []

    def get_doc(self, doctype: str, name: str) -> dict[str, Any]:
        data = self._request(
            "GET", f"/api/resource/{quote(doctype)}/{quote(name)}"
        ).get("data")
        return data if isinstance(data, dict) else {}

    def call_method(self, method: str, **kwargs: Any) -> Any:
        """Invoke a whitelisted server method; returns the ``message`` payload."""
        return self._request("POST", f"/api/method/{method}", json_body=kwargs).get("message")
