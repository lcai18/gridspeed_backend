from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")


class VoiceAgentError(RuntimeError):
    """Raised when a voice-agent REST call fails."""


def _post_json(path: str, payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{APP_BASE_URL}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise VoiceAgentError(f"Voice agent request failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise VoiceAgentError(f"Voice agent request failed: {exc.reason}") from exc


def post_chatgpt_response(text: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Push a GPT response to the voice pipeline so it can be spoken on active calls."""
    payload = {"text": text, "metadata": metadata or {}}
    return _post_json("/chatgpt-response", payload)


def trigger_dispatch_call(
    *,
    work_order_id: str,
    dispatch_context: dict[str, Any],
    to: str | None = None,
    from_number: str | None = None,
) -> dict[str, Any]:
    """Ask the voice agent to initiate an outbound call for a dispatchable work order."""
    payload: dict[str, Any] = {
        "work_order_id": work_order_id,
        "context": dispatch_context,
    }
    if to:
        payload["to"] = to
    if from_number:
        payload["from"] = from_number

    return _post_json("/call-me", payload)
