"""Pure helpers for the structured content-recommendation contract."""

import json
import re
from datetime import timedelta

CONTENT_TYPES = ("ebook", "meditation", "exercise")
CONTRACT_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_llm_contract(raw):
    """Return (reply, candidate) without trusting candidate content.

    Providers that ignore the JSON instruction degrade to a normal answer with
    no card. Only a parsed object can reach server-side catalogue validation.
    """
    text = str(raw or "").strip()
    if not text:
        return "", None
    payload = None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        match = CONTRACT_RE.search(text)
        if match:
            try:
                payload = json.loads(match.group(1))
            except (TypeError, ValueError):
                payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("reply"), str):
        return text, None
    reply = payload["reply"].strip()
    if not reply:
        return text, None
    candidate = payload.get("recommendation")
    if candidate is None:
        return reply, None
    if not isinstance(candidate, dict):
        return reply, None
    kind = str(candidate.get("type") or "").strip().lower()
    content_id = str(candidate.get("id") or "").strip()
    if kind not in CONTENT_TYPES or not content_id or len(content_id) > 128:
        return reply, None
    rationale = str(candidate.get("rationale_code") or "").strip()[:64] or None
    return reply, {"content_type": kind, "content_id": content_id,
                   "rationale_code": rationale}


def ebook_recommendation_allowed(recent, now, window_days=30):
    """Deterministic sliding-window rule for a NEW ebook recommendation."""
    cutoff = now - timedelta(days=window_days)
    return not any(created_at is not None and created_at > cutoff
                   for created_at in recent)


def follow_up_copy(title, opened):
    """Honest wording based only on observable events."""
    safe_title = str(title or "").strip()[:180]
    if opened:
        return (f"Tu avais commencé « {safe_title} » ? On peut en parler si "
                "quelque chose t'a marqué.")
    return (f"Tu as eu le temps de jeter un œil à « {safe_title} » ? Si tu "
            "veux, on peut en parler.")
