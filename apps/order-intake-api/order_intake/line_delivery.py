from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


def push_text(recipient: str, body: str, access_token: str) -> str:
    if not recipient or not access_token:
        raise ValueError("LINE recipient and Channel Access Token are required")
    payload = json.dumps(
        {"to": recipient, "messages": [{"type": "text", "text": body}]},
        ensure_ascii=False,
    ).encode("utf-8")
    retry_key = str(uuid.uuid4())
    request = urllib.request.Request(
        LINE_PUSH_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Line-Retry-Key": retry_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status not in {200, 202}:
                raise RuntimeError(f"LINE push returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LINE push failed ({exc.code}): {detail}") from exc
    return retry_key
