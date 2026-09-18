"""Pure helpers for the structured content-recommendation contract."""

import json
import re
import unicodedata
from datetime import timedelta

CONTENT_TYPES = ("ebook", "meditation", "exercise")
CONTRACT_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
EXPLICIT_RECOMMENDATION_RE = re.compile(
    r"\b(?:je\s+(?:te\s+)?(?:recommande|conseille|sugg(?:è|e)re|propose)"
    r"|je\s+(?:peux|pourrais)\s+te\s+(?:recommander|conseiller|suggérer|suggerer|proposer)"
    r"|(?:tu\s+)?(?:peux|pourrais)\s+(?:essayer|lire|écouter|ecouter|faire))\b",
    re.I,
)
NEGATED_RECOMMENDATION_RE = re.compile(
    r"\b(?:ne\s+(?:te\s+)?(?:recommande|conseille|sugg(?:è|e)re|propose)"
    r"|ne\s+(?:peux|pourrais)\s+pas\s+te\s+(?:recommander|conseiller|suggérer|suggerer|proposer)"
    r"|(?:recommande|conseille|sugg(?:è|e)re|propose)\s+pas)\b",
    re.I,
)


def _title_key(value):
    """Normalize only punctuation/spacing/accents for exact title matching."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    without_marks = "".join(ch for ch in decomposed
                             if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[\w]+", without_marks.casefold()))


def resolve_explicit_catalog_recommendation(reply, catalog_entries):
    """Resolve one explicit actionable mention to a server catalog entry.

    This is deliberately exact after conservative Unicode/punctuation
    normalization. It never invents an ID and refuses ambiguous or historical
    mentions, so a plain catalog mention cannot create a card.
    """
    reply_key = _title_key(reply)
    if not reply_key:
        return None
    padded_reply = f" {reply_key} "
    matches = []
    for entry in catalog_entries or ():
        if isinstance(entry, dict):
            kind = entry.get("content_type")
            content_id = entry.get("content_id")
            title = entry.get("title")
        else:
            kind, content_id, title = entry
        title_key = _title_key(title)
        if not title_key:
            continue
        marker = f" {title_key} "
        start = padded_reply.find(marker)
        if start < 0:
            continue
        prefix = padded_reply[:start]
        if (not EXPLICIT_RECOMMENDATION_RE.search(prefix[-180:]) or
                NEGATED_RECOMMENDATION_RE.search(prefix[-180:])):
            continue
        matches.append((str(kind), str(content_id), str(title)))

    unique_matches = {(kind, content_id, title) for kind, content_id, title in matches}
    if len(unique_matches) != 1:
        return None
    kind, content_id, _title = next(iter(unique_matches))
    if kind not in CONTENT_TYPES or not content_id:
        return None
    return {
        "content_type": kind,
        "content_id": content_id,
        "rationale_code": "explicit_catalog_title",
    }


def _contract_payloads(text):
    """Find unambiguous structured contracts embedded in provider output.

    Some providers prepend/append prose despite the JSON-only instruction.
    Decode complete JSON objects instead of exposing that raw provider output
    as the assistant message. Duplicate sightings of the same object (the
    fenced and raw scans) are collapsed; multiple different contracts remain
    ambiguous and are rejected.
    """
    decoder = json.JSONDecoder()
    found = []

    def add(payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("reply"), str):
            return
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if all(existing[0] != key for existing in found):
            found.append((key, payload))

    try:
        add(json.loads(text))
    except (TypeError, ValueError):
        pass

    for match in CONTRACT_RE.finditer(text):
        try:
            add(json.loads(match.group(1)))
        except (TypeError, ValueError):
            pass

    # raw_decode handles prose before/after a valid contract and nested
    # recommendation objects without a permissive regex.
    for position, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[position:])
        except (TypeError, ValueError):
            continue
        add(payload)
    return [payload for _key, payload in found]


def _without_contract_artifacts(text):
    """Keep natural prose while removing recognizable internal JSON blocks."""
    cleaned = CONTRACT_RE.sub("", text)
    # A malformed/truncated contract must never become visible as technical
    # text. Only trigger on the contract's distinctive leading key.
    marker = re.search(r"\{\s*[\"']reply[\"']\s*:", cleaned, re.I)
    if marker:
        cleaned = cleaned[:marker.start()]
    return cleaned.strip()


def parse_llm_contract(raw):
    """Return (reply, candidate) without trusting candidate content.

    Providers that ignore the JSON instruction degrade to a normal answer with
    no card. Only a parsed object can reach server-side catalogue validation.
    """
    text = str(raw or "").strip()
    if not text:
        return "", None
    payloads = _contract_payloads(text)
    if len(payloads) != 1:
        return _without_contract_artifacts(text), None
    payload = payloads[0]
    reply = payload["reply"].strip()
    if not reply:
        return _without_contract_artifacts(text), None
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
