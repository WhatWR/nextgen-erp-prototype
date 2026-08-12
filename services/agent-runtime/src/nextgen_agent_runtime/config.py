"""Deployment configuration.

Every credential is read from the process environment so nothing lands in
source control. The runtime intentionally has no database, cache or site-file
configuration: recoverable execution state lives in Frappe Agent Run and Agent
Step records.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import RUNTIME_VERSION

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_CONCURRENT_RUNS = 8


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _float_env(name: str, default: float) -> float:
    try:
        return float(_env(name) or default)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _set_env(name: str) -> frozenset[str]:
    return frozenset(part.strip() for part in _env(name).split(",") if part.strip())


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable process configuration."""

    runtime_version: str = RUNTIME_VERSION
    # Frappe gateway (runtime -> Frappe). The credentials belong to the
    # dedicated restricted runtime service user, never to Administrator.
    frappe_base_url: str = ""
    frappe_api_key: str = ""
    frappe_api_secret: str = ""
    # Frappe -> runtime dispatch credential. A separate secret from the above so
    # the two directions can be rotated independently.
    service_token: str = ""
    # Model provider (OpenAI-compatible).
    model_base_url: str = ""
    model_api_key: str = ""
    default_model: str = ""
    # Agents are disabled by default: rollout step 2 deploys the service with no
    # enabled agents, then widens the set.
    enabled_agents: frozenset[str] = field(default_factory=frozenset)
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        return cls(
            runtime_version=_env("NEXTGEN_RUNTIME_VERSION", RUNTIME_VERSION),
            frappe_base_url=_env("NEXTGEN_FRAPPE_BASE_URL").rstrip("/"),
            frappe_api_key=_env("NEXTGEN_FRAPPE_API_KEY"),
            frappe_api_secret=_env("NEXTGEN_FRAPPE_API_SECRET"),
            service_token=_env("NEXTGEN_RUNTIME_SERVICE_TOKEN"),
            model_base_url=_env("NEXTGEN_MODEL_BASE_URL").rstrip("/"),
            model_api_key=_env("NEXTGEN_MODEL_API_KEY"),
            default_model=_env("NEXTGEN_MODEL_DEFAULT"),
            enabled_agents=_set_env("NEXTGEN_ENABLED_AGENTS"),
            request_timeout_seconds=_float_env(
                "NEXTGEN_REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
            ),
            max_concurrent_runs=_int_env("NEXTGEN_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS),
            log_level=_env("NEXTGEN_LOG_LEVEL", "INFO").upper(),
        )

    @property
    def gateway_configured(self) -> bool:
        return bool(self.frappe_base_url and self.frappe_api_key and self.frappe_api_secret)

    @property
    def dispatch_auth_configured(self) -> bool:
        return bool(self.service_token)

    @property
    def model_configured(self) -> bool:
        return bool(self.model_base_url and self.model_api_key)

    def readiness(self) -> tuple[bool, list[str]]:
        """Readiness is fail-closed: an unconfigured runtime never claims runs."""
        missing = []
        if not self.gateway_configured:
            missing.append("frappe_gateway_credentials")
        if not self.dispatch_auth_configured:
            missing.append("dispatch_service_token")
        if not self.model_configured:
            missing.append("model_provider_credentials")
        return (not missing), missing
