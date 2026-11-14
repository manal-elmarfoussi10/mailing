"""Call a mailbox web app.

Google answers the first POST with a redirect and drops the body if the client
turns that POST into a GET. This client sends the JSON again on each hop.
"""

import base64
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

SEND_HOSTS = {"script.google.com"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
REDIRECT_HOSTS = SEND_HOSTS | LOCAL_HOSTS | {"script.googleusercontent.com"}


def validate_url(url: str) -> None:
    parts = urlparse(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and host in SEND_HOSTS and parts.path.startswith("/macros/"):
        return
    if (
        parts.scheme == "http"
        and host in LOCAL_HOSTS
        and parts.path.startswith("/mock/box/")
    ):
        return
    raise ValueError(
        "Use an Apps Script web app URL (https://script.google.com/macros/...) "
        "or a practice mailbox on this computer."
    )


def quota(url: str, token: str) -> dict:
    return _call("GET", url, token, None)


def send(url: str, token: str, payload: dict, style: str = "app") -> dict:
    body = _legacy_payload(payload) if style == "legacy" else payload
    return _call("POST", url, token, body)


def _legacy_payload(payload: dict) -> dict:
    def packed(text: str) -> str:
        return base64.b64encode((text or "").encode("utf-8")).decode("ascii")

    html = str(payload.get("html") or "")
    if not html.strip():
        html = f"<p>{payload.get('text') or ''}</p>"
    return {
        "recipient": str(payload.get("to") or ""),
        "subject": packed(str(payload.get("subject") or "(no subject)")),
        "htmlBody": packed(html),
        "fromName": packed(str(payload.get("fromName") or " ")),
    }


def _normalize(data: dict) -> dict:
    if data.get("ok") is not None or data.get("code"):
        return data
    if "quota" in data and "result" not in data:
        return {
            "ok": True,
            "code": "OK",
            "quotaRemaining": int(data["quota"]),
            "style": "legacy",
        }
    if data.get("result") == "success":
        out = {
            "ok": True,
            "code": "SENT",
            "message": str(data.get("message") or "Email sent successfully"),
            "style": "legacy",
        }
        if data.get("quota") is not None:
            out["quotaRemaining"] = int(data["quota"])
        return out
    if data.get("result") == "error":
        message = str(data.get("error") or data.get("message") or "Failed to send email")
        code = "QUOTA" if re.search(r"quota|limit exceeded|for one day", message, re.I) else "TRANSIENT"
        out = {"ok": False, "code": code, "message": message, "style": "legacy"}
        if data.get("quota") is not None:
            out["quotaRemaining"] = int(data["quota"])
        return out
    return data


def _call(method: str, url: str, token: str, payload: Optional[dict]) -> dict:
    validate_url(url)
    current = url.strip()
    body = None
    if method == "GET":
        parts = urlparse(current)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["token"] = token
        current = urlunparse(parts._replace(query=urlencode(query)))
    else:
        body = dict(payload or {})
        body["token"] = token

    headers = {"Accept": "application/json"}
    with httpx.Client(timeout=90.0) as client:
        response = None
        for _ in range(6):
            if method == "GET":
                response = client.get(current, headers=headers, follow_redirects=False)
            else:
                response = client.post(current, json=body, headers=headers, follow_redirects=False)
            if response.status_code in (301, 302, 307, 308) and response.headers.get("location"):
                nxt = urljoin(current, response.headers["location"])
                host = (urlparse(nxt).hostname or "").lower()
                if host not in REDIRECT_HOSTS:
                    return {
                        "ok": False,
                        "code": "TRANSIENT",
                        "message": f"Unexpected redirect host: {host}",
                    }
                # Google runs the POST, then 302s to the JSON result. A second POST is rejected.
                if method == "POST" and response.status_code in (301, 302, 303):
                    method = "GET"
                    body = None
                current = nxt
                continue
            break
        else:
            return {"ok": False, "code": "TRANSIENT", "message": "Too many redirects from Google"}

    text = response.text if response is not None else ""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return {
            "ok": False,
            "code": "AUTH",
            "message": "Google returned a login page. Deploy the web app as Anyone, execute as you.",
        }
    try:
        data = response.json()
    except Exception:
        status = response.status_code if response is not None else 0
        return {
            "ok": False,
            "code": "TRANSIENT",
            "message": f"Script did not return JSON (HTTP {status})",
        }
    if not isinstance(data, dict):
        return {"ok": False, "code": "TRANSIENT", "message": "Script returned an unexpected payload"}
    return _normalize(data)
