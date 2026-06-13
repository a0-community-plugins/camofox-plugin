"""Helpers for generating same-origin CamoFox viewer URLs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit


VIEWER_ROUTE_PREFIX = "/plugins/camofox_browser/viewer"
VIEWER_TOKEN_TTL_SECONDS = 3600


def encode_viewer_token(payload: dict, *, secret: str | None = None) -> str:
    signing_secret = (secret or os.getenv("FLASK_SECRET_KEY") or "agent-zero-camofox-viewer").encode()
    envelope = {
        "payload": payload,
        "iat": int(time.time()),
    }
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(signing_secret, body, hashlib.sha256).hexdigest().encode()
    return base64.urlsafe_b64encode(body + b"." + signature).decode().rstrip("=")


def decode_viewer_token(
    token: str,
    *,
    secret: str | None = None,
    max_age: int = VIEWER_TOKEN_TTL_SECONDS,
) -> dict:
    signing_secret = (secret or os.getenv("FLASK_SECRET_KEY") or "agent-zero-camofox-viewer").encode()
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded.encode())
    body, signature = raw.rsplit(b".", 1)
    expected = hmac.new(signing_secret, body, hashlib.sha256).hexdigest().encode()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid viewer token signature")
    envelope = json.loads(body.decode())
    issued_at = int(envelope.get("iat", 0))
    if issued_at + max_age < time.time():
        raise ValueError("Viewer token expired")
    return envelope["payload"]


def build_viewer_src(
    vnc_url: str,
    *,
    request=None,
    server_url: str = "",
    secret: str | None = None,
) -> str:
    """Return a same-origin iframe URL for the main app viewer proxy."""
    if not vnc_url:
        return vnc_url

    parsed = urlsplit(vnc_url)
    upstream_host = parsed.hostname or ""
    if not parsed.scheme or not upstream_host:
        return vnc_url

    upstream_port = parsed.port
    if upstream_port is None:
        upstream_port = 443 if parsed.scheme == "https" else 80

    token = encode_viewer_token(
        {
            "upstream_scheme": parsed.scheme,
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "upstream_ws_path": "/websockify",
        },
        secret=secret,
    )

    viewer_ws_path = f"/plugins/camofox_browser/viewer/t-{token}/websockify"
    query_params = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_params = [(key, value) for key, value in query_params if key not in ("path", "reconnect", "reconnect_delay")]
    filtered_params.append(("path", viewer_ws_path))
    # Enable noVNC auto-reconnect so dropped upstream connections recover
    filtered_params.append(("reconnect", "true"))
    filtered_params.append(("reconnect_delay", "3000"))
    query = urlencode(filtered_params, doseq=True)
    return f"{VIEWER_ROUTE_PREFIX}/t-{token}/vnc.html?{query}" if query else f"{VIEWER_ROUTE_PREFIX}/t-{token}/vnc.html"


def normalize_vnc_url(vnc_url: str, request=None, server_url: str = "") -> str:
    """Backward-compatible wrapper around the same-origin viewer builder."""
    return build_viewer_src(vnc_url, request=request, server_url=server_url)


def viewer_state_for_request(state: dict, request=None, server_url: str = "") -> dict:
    """Attach raw/normalized viewer URL fields for the current request context."""
    result = dict(state or {})
    raw_vnc_url = result.get("vnc_url") or ""
    if not raw_vnc_url:
        return result

    normalized_vnc_url = build_viewer_src(raw_vnc_url, request=request, server_url=server_url)
    result["vnc_url_raw"] = raw_vnc_url
    result["vnc_url"] = normalized_vnc_url
    result["vnc_url_rewritten"] = normalized_vnc_url != raw_vnc_url
    # vnc_session_key identifies the upstream session, not write recency.
    # Hashing the raw URL means the key changes only when the actual session
    # changes (different upstream token), not on every state write.
    result["vnc_session_key"] = hashlib.md5(raw_vnc_url.encode()).hexdigest()[:16]
    return result
