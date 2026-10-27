"""A small Langfuse REST client that needs only the Python standard library.

LangfuseClient(config, timeout=30) sends requests to config.host, signed with
config.public_key and config.secret_key (HTTP basic auth).

  config    any object with host, public_key and secret_key
  timeout   seconds to wait for each answer
  returns   an HttpResponse for every request: the HTTP status and the answer
            parsed as JSON (None when it is empty or not JSON); status 0 when
            no answer came at all, such as no network, a refused connection or
            a timeout. A request never raises.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import error as urlerror
from urllib import request as urlrequest


@dataclass
class HttpResponse:
    status: int
    body: Any


def _parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class LangfuseClient:
    def __init__(self, config: Any, *, timeout: float = 30) -> None:
        self._config = config
        self._timeout = timeout

    def _request(self, method: str, path: str, data: Optional[bytes], headers: Dict[str, str]) -> HttpResponse:
        url = self._config.host.rstrip("/") + path
        req = urlrequest.Request(url, data=data, method=method)
        for name, value in headers.items():
            req.add_header(name, value)
        credentials = f"{self._config.public_key}:{self._config.secret_key}".encode("utf-8")
        req.add_header("Authorization", "Basic " + base64.b64encode(credentials).decode("ascii"))
        try:
            with urlrequest.urlopen(req, timeout=self._timeout) as resp:
                return HttpResponse(status=resp.status, body=_parse_json(resp.read()))
        except urlerror.HTTPError as exc:
            return HttpResponse(status=exc.code, body=_parse_json(exc.read()))
        except Exception:
            return HttpResponse(status=0, body=None)

    def get(self, path: str) -> HttpResponse:
        return self._request("GET", path, None, {"Content-Type": "application/json"})

    def post(self, path: str, body: dict) -> HttpResponse:
        return self._request("POST", path, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"})

    def post_bytes(self, path: str, data: bytes, headers: Dict[str, str]) -> HttpResponse:
        """POSTs `data` as it is, with `headers` added to the auth header."""
        return self._request("POST", path, data, headers)
