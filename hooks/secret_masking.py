"""Replaces secrets in trace inputs and outputs with a marker.

mask_secrets(value) returns a copy of value in which every secret of these
eight kinds is replaced by MARKER: a private-key block, the token after
`Authorization: Bearer`, a JSON Web Token, an `sk-` key, an AWS access key
(`AKIA…`), a GitHub token (`ghp_…`), a Slack token (such as `xoxb-…`), and
the value after `password=` (any name ending in `password`, in any case).

  value   a string, or dicts and lists holding strings; dict keys, numbers,
          booleans and None come back as they are
  return  the masked copy; the value passed in is never changed
"""

import re
from typing import Any

MARKER = "[REDACTED]"

# A Bearer token's characters, RFC 6750 section 2.1 (b64token).
_BEARER_TOKEN = r"[A-Za-z0-9._~+/-]+=*"

# Applied in this order; a replacement with \1 keeps the first group as written.
_PATTERNS = [
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        MARKER,
    ),
    # Authorization, optional quote, colon, optional quote, Bearer, then the token.
    (re.compile(r"(?i)(authorization[\"']?[ \t]*:[ \t]*[\"']?bearer[ \t]+)" + _BEARER_TOKEN), r"\1" + MARKER),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), MARKER),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), MARKER),
    (re.compile(r"\bAKIA[A-Z0-9]+"), MARKER),
    (re.compile(r"\bghp_[A-Za-z0-9_]+"), MARKER),
    (re.compile(r"\bxox[A-Za-z]-[A-Za-z0-9-]+"), MARKER),
    # password= then a "quoted" or 'quoted' value on one line, or a run up to a space or &.
    (re.compile(r"(?i)(password=)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s&]+)"), r"\1" + MARKER),
]

# The value of an Authorization dict key: Bearer, a space, then the token.
_BEARER_VALUE = re.compile(r"(?i)^(bearer )" + _BEARER_TOKEN)


def _mask_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _mask_dict_value(key: Any, item: Any) -> Any:
    if isinstance(key, str) and key.lower() == "authorization" and isinstance(item, str):
        item = _BEARER_VALUE.sub(r"\1" + MARKER, item)
    return mask_secrets(item)


def mask_secrets(value: Any) -> Any:
    if isinstance(value, str):
        return _mask_text(value)
    if isinstance(value, dict):
        return {key: _mask_dict_value(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_secrets(item) for item in value]
    return value
