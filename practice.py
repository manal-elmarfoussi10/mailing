from typing import Optional

import secrets
import threading

_lock = threading.Lock()
_boxes: dict[str, dict] = {}


def create(label: str, quota: int, fail_once: bool) -> tuple[str, str]:
    box_id = secrets.token_hex(4)
    token = secrets.token_urlsafe(18)
    with _lock:
        _boxes[box_id] = {
            "token": token,
            "quota": quota,
            "label": label,
            "fail_once": fail_once,
            "last": {},
        }
    return box_id, token


def restore(rows: list[dict]) -> None:
    with _lock:
        for row in rows:
            if not row.get("practice"):
                continue
            box_id = str(row["web_app_url"]).rstrip("/").split("/")[-1]
            if box_id in _boxes:
                continue
            remaining = row.get("quota_remaining")
            _boxes[box_id] = {
                "token": row["token"],
                "quota": 100 if remaining is None else int(remaining),
                "label": row.get("account_email") or row["label"],
                "fail_once": False,
                "last": {},
            }


def last_send(box_id: str, token: str) -> dict:
    with _lock:
        box = _boxes.get(box_id)
        if box is None or token != box["token"]:
            return {"ok": False}
        return {"ok": True, **box.get("last", {})}


def handle(box_id: str, token: str, body: Optional[dict]) -> dict:
    with _lock:
        box = _boxes.get(box_id)
        if box is None:
            return {
                "ok": False,
                "code": "TRANSIENT",
                "message": "Practice mailbox expired. Add it again.",
            }
        if token != box["token"]:
            return {"ok": False, "code": "AUTH", "message": "Bad token"}
        if body is None or body.get("action") == "quota":
            return {
                "ok": True,
                "code": "OK",
                "quotaRemaining": box["quota"],
                "mailbox": box["label"],
            }
        to = str(body.get("to") or "").strip().lower()
        if to.startswith("bounce@"):
            return {
                "ok": False,
                "code": "REJECTED",
                "message": "Invalid recipient",
                "quotaRemaining": box["quota"],
                "mailbox": box["label"],
            }
        if box["fail_once"]:
            box["fail_once"] = False
            return {
                "ok": False,
                "code": "RATE_LIMIT",
                "message": "Practice mailbox asked the app to wait",
                "quotaRemaining": box["quota"],
                "mailbox": box["label"],
            }
        box["last"] = {
            "to": to,
            "subject": body.get("subject") or "",
            "attachments": [item.get("filename") for item in (body.get("attachments") or [])],
            "html": body.get("html") or "",
            "hasHtml": bool(body.get("html")),
            "hasText": bool(body.get("text")),
            "replyTo": body.get("replyTo") or "",
            "fromName": body.get("fromName") or "",
        }
        if box["quota"] <= 0:
            return {
                "ok": False,
                "code": "QUOTA",
                "message": "Daily quota is used up",
                "quotaRemaining": 0,
                "mailbox": box["label"],
            }
        if not body.get("subject") or not (body.get("html") or body.get("text")):
            return {
                "ok": False,
                "code": "REJECTED",
                "message": "Message is empty",
                "quotaRemaining": box["quota"],
                "mailbox": box["label"],
            }
        box["quota"] -= 1
        return {
            "ok": True,
            "code": "SENT",
            "quotaRemaining": box["quota"],
            "mailbox": box["label"],
        }
