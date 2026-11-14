from typing import Optional

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import db
import google_send
import importer

PT = ZoneInfo("America/Los_Angeles")


class Sender:
    def __init__(self) -> None:
        self.campaign_id: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._guard = threading.Lock()

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, campaign_id: int) -> None:
        with self._guard:
            if self.alive():
                raise RuntimeError("A campaign is already running. Pause it before starting another.")
            self.campaign_id = campaign_id
            self._stop.clear()
            self._pause.clear()
            self._thread = threading.Thread(
                target=self._run, args=(campaign_id,), name="mailing-sender", daemon=True
            )
            self._thread.start()

    def pause(self) -> None:
        self._pause.set()

    def resume(self) -> None:
        self._pause.clear()

    def _run(self, campaign_id: int) -> None:
        company_id = db.campaign_company(campaign_id)
        if not company_id:
            return
        db.bind_company(company_id)
        try:
            db.update_campaign(campaign_id, status="running", phase="Checking mailboxes", step="checking")
            self._refresh_enabled(campaign_id)
            db.add_event(campaign_id, "Started")
            while not self._stop.is_set():
                if self._pause.is_set():
                    self._park(campaign_id)
                    if self._stop.is_set():
                        break
                    db.update_campaign(campaign_id, status="running", phase="Resumed", step="next")
                campaign = db.get_campaign(campaign_id)
                if campaign is None:
                    return
                db.update_campaign(campaign_id, step="next", phase="Choosing the next address")
                if (campaign.get("rotation") or "drain") == "wave":
                    if not self._send_wave(campaign):
                        return
                    if not self._sleep_delay(campaign_id):
                        break
                    continue
                recipient = db.next_queued(campaign_id)
                if recipient is None:
                    db.update_campaign(campaign_id, status="done", phase="Finished", step="finished")
                    db.add_event(campaign_id, "Queue finished")
                    return
                box = self._pick_box(campaign)
                if box is None:
                    db.update_campaign(
                        campaign_id,
                        status="paused",
                        step="quota",
                        phase="All enabled mailboxes are out of quota or need a refresh. Add a box or press Refresh, then Resume.",
                    )
                    db.add_event(campaign_id, "No mailbox can send right now")
                    return
                outcome = self._deliver(campaign, recipient, box)
                if outcome == "again":
                    continue
                if not self._sleep_delay(campaign_id):
                    break
            current = db.get_campaign(campaign_id)
            if current and current["status"] == "running":
                db.update_campaign(campaign_id, status="paused", phase="Stopped", step="paused")
        except Exception as exc:
            db.update_campaign(campaign_id, status="paused", step="error", phase=f"Sender stopped: {exc}")
            db.add_event(campaign_id, f"Sender stopped: {exc}")
        finally:
            db.bind_company(None)

    def _park(self, campaign_id: int) -> None:
        current = db.get_campaign(campaign_id)
        phase = current["phase"] if current else "Paused"
        if not str(phase).startswith("Paused") and "out of quota" not in str(phase):
            phase = "Paused"
        step = "quota" if "out of quota" in str(phase) else "paused"
        db.update_campaign(campaign_id, status="paused", phase=phase, step=step)
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.2)

    def _refresh_enabled(self, campaign_id: int) -> None:
        for box in db.list_mailboxes():
            if not box["enabled"]:
                continue
            self._refresh(campaign_id, box)

    def _refresh(self, campaign_id: int, box: dict) -> dict:
        try:
            data = google_send.quota(box["web_app_url"], box["token"])
        except Exception as exc:
            data = {"ok": False, "code": "TRANSIENT", "message": str(exc)}
        self._apply_quota(box["id"], data)
        if not data.get("ok"):
            db.add_event(
                campaign_id,
                f"{box['label']}: {data.get('message') or data.get('code') or 'quota check failed'}",
            )
        return data

    def _apply_quota(self, mailbox_id: int, data: dict) -> None:
        if data.get("ok"):
            remaining = int(data.get("quotaRemaining") or 0)
            fields = {
                "quota_remaining": remaining,
                "quota_checked_at": db.now(),
                "skip_until_refresh": 0,
                "last_error": "",
            }
            if data.get("mailbox"):
                fields["account_email"] = str(data["mailbox"])
            if data.get("style") in {"legacy", "app"}:
                fields["script_style"] = data["style"]
            db.update_mailbox(mailbox_id, **fields)
            return
        code = data.get("code") or "TRANSIENT"
        remaining = data.get("quotaRemaining")
        fields = {
            "quota_checked_at": db.now(),
            "skip_until_refresh": 1 if code == "AUTH" else 0,
        }
        if code != "REJECTED":
            fields["last_error"] = str(data.get("message") or code)[:300]
        if remaining is not None:
            fields["quota_remaining"] = int(remaining)
        elif code == "QUOTA":
            fields["quota_remaining"] = 0
        db.update_mailbox(mailbox_id, **fields)

    def _candidate_boxes(self, campaign: dict) -> list[dict]:
        boxes = [row for row in db.list_mailboxes() if row["enabled"]]
        server = str(campaign.get("server_name") or "").strip()
        if server:
            boxes = [row for row in boxes if (row.get("server_name") or "") == server]
        chosen = [part for part in str(campaign.get("mailbox_ids") or "").split(",") if part.strip()]
        if chosen:
            wanted = {int(part) for part in chosen}
            boxes = [row for row in boxes if row["id"] in wanted]
        return boxes

    def _eligible_boxes(self, campaign: dict) -> list[dict]:
        eligible = []
        for box in self._candidate_boxes(campaign):
            if box["skip_until_refresh"]:
                continue
            if self._stale(box):
                fresh = self._refresh(campaign["id"], box)
                box = db.get_mailbox(box["id"]) or box
                if not fresh.get("ok") and fresh.get("code") == "AUTH":
                    continue
            if box["quota_remaining"] is None or int(box["quota_remaining"]) > 0:
                eligible.append(box)
        return eligible

    def _send_wave(self, campaign: dict) -> bool:
        boxes = self._eligible_boxes(campaign)
        if not boxes:
            db.update_campaign(
                campaign["id"],
                status="paused",
                step="quota",
                phase="All enabled mailboxes are out of quota or need a refresh. Add a box or press Refresh, then Resume.",
            )
            db.add_event(campaign["id"], "No mailbox can send right now")
            return False
        people = db.take_queued(campaign["id"], len(boxes))
        if not people:
            db.update_campaign(campaign["id"], status="done", phase="Finished", step="finished")
            db.add_event(campaign["id"], "Queue finished")
            return False
        labels = ", ".join(box["label"] for box in boxes[: len(people)])
        db.update_campaign(
            campaign["id"],
            step="sending",
            phase=f"Sending {len(people)} at once via {labels}",
            cursor_mailbox_id=boxes[0]["id"],
        )
        pairs = list(zip(people, boxes))
        with ThreadPoolExecutor(max_workers=len(pairs)) as pool:
            list(pool.map(lambda pair: self._deliver(campaign, pair[0], pair[1]), pairs))
        return True

    def _pick_box(self, campaign: dict) -> Optional[dict]:
        boxes = self._candidate_boxes(campaign)
        eligible = self._eligible_boxes(campaign)
        if not eligible:
            return None
        cursor = campaign.get("cursor_mailbox_id")
        rotation = campaign.get("rotation") or "drain"
        if rotation == "drain" and cursor:
            for box in eligible:
                if box["id"] == cursor:
                    return box
            return eligible[0]
        if rotation == "round_robin" and cursor:
            ids = [box["id"] for box in boxes]
            start = ids.index(cursor) if cursor in ids else -1
            eligible_ids = {box["id"] for box in eligible}
            for step in range(1, len(boxes) + 1):
                cand_id = ids[(start + step) % len(ids)]
                if cand_id in eligible_ids:
                    return next(box for box in eligible if box["id"] == cand_id)
        return eligible[0]

    def _stale(self, box: dict) -> bool:
        checked = box.get("quota_checked_at")
        if not checked or box.get("quota_remaining") is None:
            return True
        stamp = datetime.fromisoformat(checked)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=PT)
        return stamp.astimezone(PT).date() < datetime.now(PT).date()

    def _offer_fields(self, campaign: dict, fields: dict) -> None:
        offer_id = campaign.get("offer_id")
        if not offer_id:
            return
        for offer in db.list_offers():
            if offer["id"] == offer_id:
                fields["offer_url"] = offer["url"]
                fields["offer url"] = offer["url"]
                return

    def _deliver(self, campaign: dict, recipient: dict, box: dict) -> str:
        db.claim(recipient["id"])
        fields = json.loads(recipient["fields_json"] or "{}")
        self._offer_fields(campaign, fields)
        html = importer.merge_text(campaign["html_body"], fields)
        text = importer.merge_text(campaign["text_body"], fields)
        if not text.strip():
            text = importer.plain_from_html(html)
        subject = importer.merge_text(campaign["subject"], fields)
        payload = {
            "to": recipient["email"],
            "subject": subject,
            "html": html,
            "text": text,
            "fromName": importer.merge_text(campaign["from_name"], fields),
            "replyTo": campaign["reply_to"].strip(),
            "attachments": _attachment_payload(campaign["id"]),
        }
        if not payload["fromName"]:
            payload.pop("fromName")
        if not payload["replyTo"]:
            payload.pop("replyTo")
        if not payload["attachments"]:
            payload.pop("attachments")
        if db.is_suppressed(recipient["email"]):
            db.mark_suppressed(recipient["id"])
            db.add_event(campaign["id"], f"Skipped {recipient['email']}. That address is unsubscribed.")
            return "next"
        token = db.unsub_token_for(recipient["id"])
        html_foot, text_foot = message_footer(campaign, token)
        payload["html"] = html + html_foot
        payload["text"] = text + text_foot
        db.update_campaign(
            campaign["id"],
            step="sending",
            phase=f"Sending {recipient['email']} via {box['label']}",
            cursor_mailbox_id=box["id"],
        )
        try:
            data = google_send.send(
                box["web_app_url"],
                box["token"],
                payload,
                box.get("script_style") or "app",
            )
        except Exception as exc:
            data = {"ok": False, "code": "TRANSIENT", "message": str(exc)}

        if data.get("ok"):
            self._apply_quota(box["id"], data)
            db.mark_sent(recipient["id"], box["id"])
            left = data.get("quotaRemaining")
            db.add_event(
                campaign["id"],
                f"Sent {recipient['email']} via {box['label']}. {left} left on that box.",
            )
            return "next"

        code = data.get("code") or "TRANSIENT"
        message = str(data.get("message") or code)
        self._apply_quota(box["id"], data)
        if code == "QUOTA":
            db.requeue(recipient["id"], recipient["attempts"], message)
            db.update_campaign(campaign["id"], cursor_mailbox_id=box["id"])
            db.add_event(campaign["id"], f"{box['label']} is out of daily quota. Switching mailbox.")
            return "again"
        if code == "AUTH":
            db.requeue(recipient["id"], recipient["attempts"], message)
            db.add_event(campaign["id"], f"{box['label']} rejected the secret. Fix it and press Refresh.")
            return "again"

        attempts = int(recipient["attempts"]) + 1
        auto = int(campaign["auto_retries"])
        if code in {"RATE_LIMIT", "TRANSIENT"} and attempts <= auto:
            db.requeue(recipient["id"], attempts, message)
            wait = min(180, 10 * (2 ** (attempts - 1)))
            db.add_event(
                campaign["id"],
                f"Google asked to wait for {recipient['email']}. Retry {attempts} of {auto} in {wait}s.",
            )
            self._sleep(campaign["id"], wait, f"Waiting {wait}s after Google, {attempts} of {auto}.")
            return "again"

        if code == "RATE_LIMIT":
            self._sleep(campaign["id"], 10, "Waiting 10s so the next mailbox is not hit at once.")
        db.mark_failed(recipient["id"], attempts, message, box["id"])
        db.add_event(
            campaign["id"],
            f"Skipped {recipient['email']}: {message}. It is in Failed and can be retried.",
        )
        return "next"

    def _sleep_delay(self, campaign_id: int) -> bool:
        campaign = db.get_campaign(campaign_id)
        if campaign is None:
            return False
        amount = max(0, int(campaign["delay_amount"]))
        seconds = amount * 60 if campaign["delay_unit"] == "minutes" else amount
        seconds = min(seconds, 24 * 60 * 60)
        if seconds == 0:
            return not self._stop.is_set()
        return self._sleep(campaign_id, seconds, "Next email in")

    def _sleep(self, campaign_id: int, seconds: int, label: str) -> bool:
        end = time.time() + seconds
        last_shown = None
        while time.time() < end:
            if self._stop.is_set():
                return False
            if self._pause.is_set():
                return True
            left = max(0, int(end - time.time()))
            if left != last_shown:
                db.update_campaign(campaign_id, step="waiting", phase=f"{label} {left}s")
                last_shown = left
            time.sleep(min(0.25, max(0.0, end - time.time())))
        return not self._stop.is_set()


def message_footer(campaign: dict, token: str) -> tuple:
    base = db.get_setting("public_base_url", "").strip().rstrip("/")
    if base and token:
        url = f"{base}/u/{token}"
        html = f'<p style="margin-top:24px;font-size:12px"><a href="{url}">Unsubscribe</a></p>'
        return html, f"\n\nUnsubscribe: {url}"
    reply = (campaign.get("reply_to") or "").strip()
    if reply:
        link = f"mailto:{reply}?subject=unsubscribe"
        html = f'<p style="margin-top:24px;font-size:12px"><a href="{link}">Unsubscribe</a></p>'
        return html, f"\n\nUnsubscribe: {link}"
    return "", ""


def _attachment_payload(campaign_id: int) -> list[dict]:
    import base64
    from pathlib import Path

    files = []
    total = 0
    for row in db.list_attachments(campaign_id):
        raw = Path(row["path"]).read_bytes()
        total += len(raw)
        if total > 20 * 1024 * 1024:
            raise RuntimeError("Attachments are larger than 20 MB")
        files.append(
            {
                "filename": row["filename"],
                "mimeType": row["mime_type"],
                "base64": base64.b64encode(raw).decode("ascii"),
            }
        )
    return files


sender = Sender()
