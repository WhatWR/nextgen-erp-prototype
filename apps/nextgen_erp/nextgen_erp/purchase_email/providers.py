"""Mailbox provider adapters.

Real OAuth flows need client secrets that this prototype usually does not have.
Each live provider therefore:

1. Exposes a clear ``auth_status`` / connect URL shape for when secrets exist.
2. Raises a structured ``ProviderNotConfigured`` when credentials are missing.
3. Leaves Sync Inbox able to fall back to fixtures via ``use_mock_fixtures``.

No network calls are made unless credentials are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

from nextgen_erp.purchase_email.fixtures import list_fixture_emails


class ProviderError(Exception):
	"""Base provider failure."""


class ProviderNotConfigured(ProviderError):
	"""Secrets or tokens are missing — caller should stay on mock fixtures."""


@dataclass
class MailMessage:
	message_id: str
	from_email: str
	subject: str
	received_at: str
	body: str
	folder: str = ""

	def as_dict(self) -> dict[str, Any]:
		return {
			"message_id": self.message_id,
			"from_email": self.from_email,
			"subject": self.subject,
			"received_at": self.received_at,
			"body": self.body,
			"folder": self.folder,
		}


class MailProvider(Protocol):
	name: str

	def connection_status(self) -> str: ...

	def connect_hint(self) -> dict[str, Any]: ...

	def list_messages(self, folder: str, limit: int = 20) -> list[MailMessage]: ...


class MockProvider:
	name = "Mock"

	def connection_status(self) -> str:
		return "Connected"

	def connect_hint(self) -> dict[str, Any]:
		return {
			"provider": self.name,
			"status": "Connected",
			"mode": "fixtures",
			"message": "Using built-in purchase-order email fixtures. No OAuth secrets required.",
			"auth_url": None,
		}

	def list_messages(self, folder: str, limit: int = 20) -> list[MailMessage]:
		rows = []
		for raw in list_fixture_emails()[:limit]:
			rows.append(
				MailMessage(
					message_id=raw["message_id"],
					from_email=raw["from_email"],
					subject=raw["subject"],
					received_at=raw["received_at"],
					body=raw["body"],
					folder=folder or "INBOX/Purchase Orders",
				)
			)
		return rows


class GmailProvider:
	"""Gmail API hook. Requires OAuth client id/secret + refresh token."""

	name = "Gmail"
	AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
	SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

	def __init__(
		self,
		mailbox_email: str = "",
		client_id: str = "",
		client_secret: str = "",
		refresh_token: str = "",
		redirect_uri: str = "http://127.0.0.1:8000/api/method/nextgen_erp.purchase_email.api.oauth_callback",
	):
		self.mailbox_email = mailbox_email or ""
		self.client_id = client_id or ""
		self.client_secret = client_secret or ""
		self.refresh_token = refresh_token or ""
		self.redirect_uri = redirect_uri

	def connection_status(self) -> str:
		if self.client_id and self.client_secret and self.refresh_token:
			return "Connected"
		if self.client_id and self.client_secret:
			return "Needs Auth"
		return "Not Connected"

	def connect_hint(self) -> dict[str, Any]:
		status = self.connection_status()
		auth_url = None
		if self.client_id:
			query = urlencode(
				{
					"client_id": self.client_id,
					"redirect_uri": self.redirect_uri,
					"response_type": "code",
					"scope": self.SCOPES,
					"access_type": "offline",
					"prompt": "consent",
					"state": "gmail",
				}
			)
			auth_url = f"{self.AUTH_BASE}?{query}"
		return {
			"provider": self.name,
			"status": status,
			"mode": "oauth",
			"mailbox": self.mailbox_email,
			"auth_url": auth_url,
			"message": (
				"Paste the Google OAuth client id/secret, complete consent, then store the refresh token."
				if status != "Connected"
				else "Gmail OAuth credentials are present. Live list is not enabled in the MVP — enable fixtures or wire gmail.users.messages.list."
			),
		}

	def list_messages(self, folder: str, limit: int = 20) -> list[MailMessage]:
		if self.connection_status() != "Connected":
			raise ProviderNotConfigured(
				"Gmail OAuth is not fully configured (need client id, secret, and refresh token)."
			)
		raise ProviderNotConfigured(
			"Live Gmail fetch is stubbed in the MVP. Turn on Use Mock Fixtures, or implement gmail.users.messages.list with the stored refresh token."
		)


class MicrosoftProvider:
	"""Microsoft Graph mail hook."""

	name = "Microsoft"
	SCOPES = "offline_access Mail.Read"

	def __init__(
		self,
		mailbox_email: str = "",
		client_id: str = "",
		client_secret: str = "",
		refresh_token: str = "",
		tenant_id: str = "common",
		redirect_uri: str = "http://127.0.0.1:8000/api/method/nextgen_erp.purchase_email.api.oauth_callback",
	):
		self.mailbox_email = mailbox_email or ""
		self.client_id = client_id or ""
		self.client_secret = client_secret or ""
		self.refresh_token = refresh_token or ""
		self.tenant_id = tenant_id or "common"
		self.redirect_uri = redirect_uri

	def connection_status(self) -> str:
		if self.client_id and self.client_secret and self.refresh_token:
			return "Connected"
		if self.client_id and self.client_secret:
			return "Needs Auth"
		return "Not Connected"

	def connect_hint(self) -> dict[str, Any]:
		status = self.connection_status()
		auth_url = None
		if self.client_id:
			query = urlencode(
				{
					"client_id": self.client_id,
					"response_type": "code",
					"redirect_uri": self.redirect_uri,
					"response_mode": "query",
					"scope": self.SCOPES,
					"state": "microsoft",
				}
			)
			auth_url = (
				f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?{query}"
			)
		return {
			"provider": self.name,
			"status": status,
			"mode": "oauth",
			"mailbox": self.mailbox_email,
			"auth_url": auth_url,
			"message": (
				"Register an Azure app with Mail.Read, paste client id/secret, then complete consent."
				if status != "Connected"
				else "Microsoft OAuth credentials are present. Live Graph fetch is stubbed in the MVP — use fixtures for demos."
			),
		}

	def list_messages(self, folder: str, limit: int = 20) -> list[MailMessage]:
		if self.connection_status() != "Connected":
			raise ProviderNotConfigured(
				"Microsoft OAuth is not fully configured (need client id, secret, and refresh token)."
			)
		raise ProviderNotConfigured(
			"Live Microsoft Graph fetch is stubbed in the MVP. Turn on Use Mock Fixtures, or implement /me/mailFolders/{folder}/messages."
		)


class IMAPProvider:
	name = "IMAP"

	def __init__(
		self,
		host: str = "",
		port: int = 993,
		username: str = "",
		password: str = "",
		use_ssl: bool = True,
	):
		self.host = host or ""
		self.port = int(port or 993)
		self.username = username or ""
		self.password = password or ""
		self.use_ssl = bool(use_ssl)

	def connection_status(self) -> str:
		if self.host and self.username and self.password:
			return "Connected"
		return "Not Connected"

	def connect_hint(self) -> dict[str, Any]:
		status = self.connection_status()
		return {
			"provider": self.name,
			"status": status,
			"mode": "imap",
			"mailbox": self.username,
			"auth_url": None,
			"message": (
				"Fill IMAP host, username, and password to enable a live mailbox poll."
				if status != "Connected"
				else "IMAP credentials are present. Live IMAP poll is stubbed in the MVP — use fixtures for demos."
			),
		}

	def list_messages(self, folder: str, limit: int = 20) -> list[MailMessage]:
		if self.connection_status() != "Connected":
			raise ProviderNotConfigured("IMAP host/username/password are required.")
		raise ProviderNotConfigured(
			"Live IMAP fetch is stubbed in the MVP. Turn on Use Mock Fixtures for the review-queue demo."
		)


def build_provider(settings: dict[str, Any]) -> MailProvider:
	provider = str(settings.get("provider") or "Mock")
	if settings.get("use_mock_fixtures") or provider == "Mock":
		return MockProvider()
	if provider == "Gmail":
		return GmailProvider(
			mailbox_email=str(settings.get("mailbox_email") or ""),
			client_id=str(settings.get("oauth_client_id") or ""),
			client_secret=str(settings.get("oauth_client_secret") or ""),
			refresh_token=str(settings.get("oauth_refresh_token") or ""),
		)
	if provider == "Microsoft":
		return MicrosoftProvider(
			mailbox_email=str(settings.get("mailbox_email") or ""),
			client_id=str(settings.get("oauth_client_id") or ""),
			client_secret=str(settings.get("oauth_client_secret") or ""),
			refresh_token=str(settings.get("oauth_refresh_token") or ""),
			tenant_id=str(settings.get("oauth_tenant_id") or "common"),
		)
	if provider == "IMAP":
		return IMAPProvider(
			host=str(settings.get("imap_host") or ""),
			port=int(settings.get("imap_port") or 993),
			username=str(settings.get("imap_username") or ""),
			password=str(settings.get("imap_password") or ""),
			use_ssl=bool(settings.get("imap_use_ssl", True)),
		)
	return MockProvider()
