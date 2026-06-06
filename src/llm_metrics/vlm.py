"""VLM client (P4): the independent verifier (section 2.3).

It is handed ONLY one crop and asked what number is in the highlighted region.
It never sees the structural extractor's answer, so its agreement is an
independent signal. We use the Anthropic API directly over stdlib ``urllib``
(the SDK's httpx transport fails in this proxied environment; urllib works).
"""

import base64
import json
import os
import pathlib
import urllib.error
import urllib.request


class VlmUnavailable(RuntimeError):
    """The verifier could not be reached (auth/billing/network). Distinct from a
    successful read that simply disagrees -- callers fall back to the structural
    + OCR-presence signal instead of treating this as a disagreement."""


_disabled = False  # circuit breaker: once auth/billing fails, stop hammering the API

MODEL = "claude-haiku-4-5-20251001"
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_PROMPT = ("This image is a tight crop from a model card, with one value outlined "
           "in a red box. Reply with ONLY the number/value inside the red box, "
           "exactly as printed (keep signs, %, decimals). If none, reply NONE.")


def _api_key() -> str:
    key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise VlmUnavailable("no CLAUDE_API_KEY / ANTHROPIC_API_KEY in environment")
    return key


def read_number(crop_path: pathlib.Path, model: str = MODEL, timeout: int = 30) -> str:
    global _disabled
    if os.environ.get("LLM_METRICS_NO_VLM"):
        raise VlmUnavailable("VLM disabled via LLM_METRICS_NO_VLM")
    if _disabled:
        raise VlmUnavailable("VLM disabled after a prior auth/billing failure")
    if not crop_path.exists():
        raise FileNotFoundError(f"crop missing for VLM read: {crop_path}")
    b64 = base64.standard_b64encode(crop_path.read_bytes()).decode()
    body = json.dumps({
        "model": model, "max_tokens": 30,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": _PROMPT}]}],
    }).encode()
    req = urllib.request.Request(_ENDPOINT, data=body, headers={
        "x-api-key": _api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):  # auth/billing: persistent, trip the breaker
            _disabled = True
            raise VlmUnavailable(f"HTTP {e.code}: {e.read().decode()[:160]}") from e
        raise
    return data["content"][0]["text"].strip()
