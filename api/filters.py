"""
Input/output security filters for PropSense API.

  sanitize_query()   — detect and strip prompt injection attempts from user query
  mask_pii()         — mask NRIC, SG phone, email in output strings
  mask_pii_in_dict() — recursively mask PII across response dict
"""
import re

# Singapore NRIC: S/T/F/G + 7 digits + letter
_NRIC = re.compile(r"\b[STFG]\d{7}[A-Z]\b", re.IGNORECASE)
# Singapore mobile/landline: 8-digit numbers (6xxx/8xxx/9xxx)
_PHONE = re.compile(r"\b[689]\d{7}\b")
# Email addresses
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Prompt injection markers — patterns that signal an attempt to override the system prompt
_INJECTION_PATTERNS = re.compile(
    r"ignore\s+(previous|all|your|above)\s+(instructions?|rules?|prompt)|"
    r"forget\s+(your|all|previous)\s+(instructions?|rules?|context)|"
    r"you\s+are\s+now\s+|"
    r"new\s+instructions?:|"
    r"system\s*prompt:|"
    r"pretend\s+(you\s+are|to\s+be)|"
    r"\bDAN\b|"
    r"jailbreak|"
    r"override\s+(your\s+)?(instructions?|rules?)|"
    r"disregard\s+(your\s+)?(instructions?|rules?)",
    re.IGNORECASE,
)


def sanitize_query(query: str) -> tuple[str, bool]:
    """
    Scan query for prompt injection markers.
    Returns (cleaned_query, was_flagged).
    Strips the injected fragment but keeps the rest of the query intact.
    """
    flagged = bool(_INJECTION_PATTERNS.search(query))
    if flagged:
        cleaned = _INJECTION_PATTERNS.sub("", query).strip()
        return cleaned, True
    return query, False


def mask_pii(text: str) -> str:
    """Replace detected PII with masked placeholders."""
    text = _NRIC.sub("[NRIC REDACTED]", text)
    text = _EMAIL.sub("[EMAIL REDACTED]", text)
    text = _PHONE.sub("[PHONE REDACTED]", text)
    return text


def mask_pii_in_dict(obj):
    """Recursively mask PII in all string values of a dict/list."""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_pii_in_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_pii_in_dict(item) for item in obj]
    return obj
