from typing import Optional

import csv
import io
import mimetypes
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import google_send
import importer
import practice
import sender

ROOT = Path(__file__).resolve().parent
MAX_ATTACHMENT = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    db.reset_interrupted()
    practice.restore(db.list_mailboxes())
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class MailboxIn(BaseModel):
    label: str = ""
    web_app_url: str
    token: str
    account_kind: str = "gmail"
    account_email: str = ""
    server_name: str = ""
    owner_name: str = ""
    enabled: bool = True


class MailboxPatch(BaseModel):
    label: Optional[str] = None
    web_app_url: Optional[str] = None
    token: Optional[str] = None
    enabled: Optional[bool] = None
    account_kind: Optional[str] = None
    account_email: Optional[str] = None
    server_name: Optional[str] = None
    owner_name: Optional[str] = None


class PracticeIn(BaseModel):
    label: str = "Practice"
    quota: int = 100
    fail_once: bool = False
    account_kind: str = "gmail"
    account_email: str = ""
    server_name: str = ""
    owner_name: str = ""


class BulkIn(BaseModel):
    ids: list[int]
    action: str
    server_name: str = ""
    enabled: Optional[bool] = None


class AccountImportIn(BaseModel):
    csv: str


class CampaignIn(BaseModel):
    name: str = "Campaign"


class CampaignPatch(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    html_body: Optional[str] = None
    text_body: Optional[str] = None
    delay_amount: Optional[int] = None
    delay_unit: Optional[str] = None
    rotation: Optional[str] = None
    auto_retries: Optional[int] = None
    offer_id: Optional[int] = None
    server_name: Optional[str] = None
    mailbox_ids: Optional[str] = None


class TestSendIn(BaseModel):
    to: str
    mailbox_id: int


class ServerIn(BaseModel):
    name: str


class OfferIn(BaseModel):
    name: str
    url: str
    note: str = ""


class ConversionIn(BaseModel):
    offer_id: Optional[int] = None
    amount: float
    note: str = ""


class SettingsIn(BaseModel):
    delay_amount: int = 1
    delay_unit: str = "seconds"
    rotation: str = "drain"
    auto_retries: int = 3
    owner_name: str = "Admin"


class PasteIn(BaseModel):
    csv: str
    replace: bool = False


class SheetIn(BaseModel):
    url: str
    replace: bool = False


class StartIn(BaseModel):
    delay_amount: int = 1
    delay_unit: str = "seconds"
    rotation: str = "drain"
    auto_retries: int = Field(default=3, ge=0, le=20)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/state")
def state(campaign: Optional[int] = None, status: str = "all", offset: int = 0):
    mailboxes = [_public_mailbox(row) for row in db.list_mailboxes()]
    checked = [row for row in mailboxes if row["enabled"] and row["quotaRemaining"] is not None]
    payload = {
        "mailboxes": mailboxes,
        "capacity": {
            "remaining": sum(row["quotaRemaining"] for row in checked),
            "checked": len(checked),
            "enabled": sum(1 for row in mailboxes if row["enabled"]),
        },
        "campaigns": db.list_campaigns(),
        "runningCampaignId": db.running_campaign_id() if sender.sender.alive() else None,
        "pausedCampaignId": _paused_id(),
        "campaign": None,
        "servers": db.list_servers(),
        "offers": db.list_offers(),
        "settings": db.all_settings(),
        "todayRevenue": db.today_revenue(),
    }
    if campaign:
        payload["campaign"] = _campaign_view(campaign, status, offset)
    return payload


@app.post("/api/mailboxes")
def create_mailbox(body: MailboxIn):
    label = body.label.strip()
    url = body.web_app_url.strip()
    token = body.token.strip()
    if not label or not url or not token:
        raise HTTPException(400, "Label, web app URL, and secret are required")
    kind = _account_kind(body.account_kind)
    try:
        google_send.validate_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    email = body.account_email.strip()
    label = label or email or "Mailbox"
    owner = body.owner_name.strip() or db.get_setting("owner_name", "Admin") or "Admin"
    row = db.add_mailbox(
        label,
        url,
        token,
        practice=False,
        account_kind=kind,
        account_email=email,
        server_name=body.server_name.strip(),
        owner_name=owner,
    )
    if not body.enabled:
        db.update_mailbox(row["id"], enabled=0)
    _refresh_row(db.get_mailbox(row["id"]))
    return _public_mailbox(db.get_mailbox(row["id"]))


@app.patch("/api/mailboxes/{mailbox_id}")
def patch_mailbox(mailbox_id: int, body: MailboxPatch):
    if db.get_mailbox(mailbox_id) is None:
        raise HTTPException(404, "Mailbox not found")
    fields = body.model_dump(exclude_none=True)
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if "web_app_url" in fields:
        try:
            google_send.validate_url(fields["web_app_url"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    if "account_kind" in fields:
        fields["account_kind"] = _account_kind(fields["account_kind"])
    updated = db.update_mailbox(mailbox_id, **fields)
    if "web_app_url" in fields or "token" in fields:
        _refresh_row(updated)
        updated = db.get_mailbox(mailbox_id)
    return _public_mailbox(updated)


@app.delete("/api/mailboxes/{mailbox_id}")
def remove_mailbox(mailbox_id: int):
    db.delete_mailbox(mailbox_id)
    return {"ok": True}


@app.post("/api/mailboxes/{mailbox_id}/refresh")
def refresh_mailbox(mailbox_id: int):
    row = db.get_mailbox(mailbox_id)
    if row is None:
        raise HTTPException(404, "Mailbox not found")
    try:
        data = google_send.quota(row["web_app_url"], row["token"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        data = {"ok": False, "code": "TRANSIENT", "message": str(exc)}
    sender.sender._apply_quota(mailbox_id, data)
    updated = db.get_mailbox(mailbox_id)
    return {"mailbox": _public_mailbox(updated), "result": data}


@app.post("/api/practice-mailbox")
def create_practice(body: PracticeIn, request: Request):
    quota = max(0, min(int(body.quota), 2000))
    kind = _account_kind(body.account_kind)
    label = body.label.strip() or "Practice"
    email = body.account_email.strip() or f"{label.lower().replace(' ', '.')}@practice.test"
    owner = body.owner_name.strip() or db.get_setting("owner_name", "Admin") or "Admin"
    box_id, token = practice.create(email, quota, body.fail_once)
    url = f"{str(request.base_url).rstrip('/')}/mock/box/{box_id}"
    row = db.add_mailbox(
        label,
        url,
        token,
        practice=True,
        account_kind=kind,
        account_email=email,
        server_name=body.server_name.strip(),
        owner_name=owner,
    )
    _refresh_row(db.get_mailbox(row["id"]))
    return _public_mailbox(db.get_mailbox(row["id"]))


@app.get("/mock/box/{box_id}")
def mock_get(box_id: str, token: str = ""):
    return practice.handle(box_id, token, None)


@app.get("/mock/box/{box_id}/last")
def mock_last(box_id: str, token: str = ""):
    return practice.last_send(box_id, token)


@app.post("/mock/box/{box_id}")
async def mock_post(box_id: str, request: Request):
    body = await request.json()
    return practice.handle(box_id, str(body.get("token") or ""), body)


@app.post("/api/campaigns")
def create_campaign(body: CampaignIn):
    name = body.name.strip() or "Campaign"
    row = db.add_campaign(name)
    prefs = db.all_settings()
    return db.update_campaign(
        row["id"],
        delay_amount=prefs["delay_amount"],
        delay_unit=prefs["delay_unit"],
        rotation=prefs["rotation"],
        auto_retries=prefs["auto_retries"],
    )


@app.patch("/api/campaigns/{campaign_id}")
def patch_campaign(campaign_id: int, body: CampaignPatch):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    fields = body.model_dump(exclude_unset=True)
    if "offer_id" in fields and not fields["offer_id"]:
        fields["offer_id"] = None
    _check_timing(fields)
    return db.update_campaign(campaign_id, **fields)


@app.delete("/api/campaigns/{campaign_id}")
def remove_campaign(campaign_id: int):
    if sender.sender.alive() and sender.sender.campaign_id == campaign_id:
        raise HTTPException(409, "Pause this campaign before deleting it")
    db.delete_campaign(campaign_id)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/paste")
def import_paste(campaign_id: int, body: PasteIn):
    return _import(campaign_id, body.csv, body.replace)


@app.post("/api/campaigns/{campaign_id}/sheet")
def import_sheet(campaign_id: int, body: SheetIn):
    try:
        text = importer.fetch_sheet(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _import(campaign_id, text, body.replace)


@app.post("/api/campaigns/{campaign_id}/csv")
async def import_csv(campaign_id: int, replace: bool = False, file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > 5_000_000:
        raise HTTPException(400, "CSV is larger than 5 MB")
    text = raw.decode("utf-8-sig", errors="replace")
    return _import(campaign_id, text, replace)


@app.post("/api/campaigns/{campaign_id}/attachments")
async def upload_attachment(campaign_id: int, file: UploadFile = File(...)):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    filename = Path(file.filename or "attachment").name
    if not filename or filename in {".", ".."}:
        raise HTTPException(400, "Missing filename")
    raw = await file.read()
    if len(raw) > MAX_ATTACHMENT:
        raise HTTPException(400, "Each attachment must be 10 MB or smaller")
    if len(raw) == 0:
        raise HTTPException(400, "That file is empty")
    folder = db.ATTACH_DIR / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    stored = folder / f"{secrets.token_hex(4)}_{filename}"
    stored.write_bytes(raw)
    row = db.add_attachment(campaign_id, filename, mime, str(stored))
    return row


@app.delete("/api/attachments/{attachment_id}")
def remove_attachment(attachment_id: int):
    db.delete_attachment(attachment_id)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/start")
def start_campaign(campaign_id: int, body: StartIn):
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    fields = body.model_dump()
    _check_timing(fields)
    db.update_campaign(campaign_id, **fields)
    campaign = db.get_campaign(campaign_id)
    _require_ready(campaign)
    if sender.sender.alive():
        if sender.sender.campaign_id == campaign_id:
            sender.sender.resume()
            db.update_campaign(campaign_id, status="running", phase="Resumed")
            return {"ok": True}
        raise HTTPException(409, "Another campaign is running")
    try:
        sender.sender.start(campaign_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/pause")
def pause_campaign(campaign_id: int):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    if sender.sender.alive() and sender.sender.campaign_id == campaign_id:
        sender.sender.pause()
    db.update_campaign(campaign_id, status="paused", phase="Paused")
    db.add_event(campaign_id, "Paused")
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/resume")
def resume_campaign(campaign_id: int):
    return start_campaign(
        campaign_id,
        StartIn(
            delay_amount=_int_field(campaign_id, "delay_amount", 1),
            delay_unit=_str_field(campaign_id, "delay_unit", "seconds"),
            rotation=_str_field(campaign_id, "rotation", "drain"),
            auto_retries=_int_field(campaign_id, "auto_retries", 3),
        ),
    )


@app.post("/api/campaigns/{campaign_id}/retry-failed")
def retry_failed(campaign_id: int):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    count = db.retry_failed(campaign_id)
    if count:
        current = db.get_campaign(campaign_id)
        if current and current["status"] == "done":
            db.update_campaign(
                campaign_id,
                status="paused",
                phase=f"{count} failed rows are back in the queue. Press Start.",
            )
        db.add_event(campaign_id, f"Returned {count} failed rows to the queue")
    return {"ok": True, "count": count}


def _import(campaign_id: int, text: str, replace: bool) -> dict:
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    try:
        people, skipped = importer.parse_csv(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not people and skipped:
        raise HTTPException(400, "No valid email addresses in that list")
    result = db.insert_recipients(campaign_id, people, replace)
    result["skipped"] = skipped
    db.add_event(
        campaign_id,
        f"Imported {result['added']} people. {result['duplicate']} duplicates, {skipped} skipped.",
    )
    return result


def _require_ready(campaign: dict) -> None:
    if not campaign["subject"].strip():
        raise HTTPException(400, "Add a subject before starting")
    if not campaign["html_body"].strip() and not campaign["text_body"].strip():
        raise HTTPException(400, "Add HTML or plain text before starting")
    if campaign["reply_to"].strip() and not re.match(
        r"^[^@\s]+@[^@\s]+\.[^@\s]+$", campaign["reply_to"].strip()
    ):
        raise HTTPException(400, "Reply-to must be an email address")
    counts = db.counts(campaign["id"])
    if counts["queued"] < 1:
        raise HTTPException(400, "The queue is empty")
    enabled = [row for row in db.list_mailboxes() if row["enabled"]]
    if not enabled:
        raise HTTPException(400, "Enable at least one mailbox")


def _check_timing(fields: dict) -> None:
    if "delay_unit" in fields and fields["delay_unit"] not in {"seconds", "minutes"}:
        raise HTTPException(400, "Delay unit must be seconds or minutes")
    if "rotation" in fields and fields["rotation"] not in {"drain", "round_robin"}:
        raise HTTPException(400, "Rotation must be drain or round robin")
    if "delay_amount" in fields:
        amount = int(fields["delay_amount"])
        if amount < 0 or amount > 24 * 60:
            raise HTTPException(400, "Delay must be between 0 and 1440")
        fields["delay_amount"] = amount
    if "auto_retries" in fields:
        retries = int(fields["auto_retries"])
        if retries < 0 or retries > 20:
            raise HTTPException(400, "Automatic retries must be between 0 and 20")
        fields["auto_retries"] = retries


def _campaign_view(campaign_id: int, status: str, offset: int) -> Optional[dict]:
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        return None
    if status not in {"all", "queued", "sent", "failed", "sending"}:
        status = "all"
    offset = max(0, offset)
    view = dict(campaign)
    view["counts"] = db.counts(campaign_id)
    view["recipients"] = db.list_recipients(campaign_id, status, 100, offset)
    view["attachments"] = [
        {"id": row["id"], "filename": row["filename"]}
        for row in db.list_attachments(campaign_id)
    ]
    view["events"] = db.list_events(campaign_id)
    view["placeholders"] = db.placeholder_names(campaign_id)
    labels = {row["id"]: row["label"] for row in db.list_mailboxes()}
    for person in view["recipients"]:
        person["mailbox"] = labels.get(person["mailbox_id"], "")
    return view


def _public_mailbox(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    return {
        "id": row["id"],
        "label": row["label"],
        "webAppUrl": row["web_app_url"],
        "enabled": bool(row["enabled"]),
        "practice": bool(row["practice"]),
        "accountKind": row["account_kind"] or "gmail",
        "quotaRemaining": row["quota_remaining"],
        "quotaCheckedAt": row["quota_checked_at"],
        "lastError": row["last_error"],
        "email": row["account_email"] or "",
        "server": row["server_name"] or "",
        "user": row["owner_name"] or "Admin",
        "status": _mailbox_status(row),
    }


def _account_kind(value: str) -> str:
    kind = (value or "gmail").strip().lower()
    if kind not in {"gmail", "workspace"}:
        raise HTTPException(400, "Choose Gmail or Google Workspace")
    return kind


def _paused_id() -> Optional[int]:
    rows = db.list_campaigns()
    for row in rows:
        if row["status"] == "paused":
            return row["id"]
    return None


def _int_field(campaign_id: int, key: str, default: int) -> int:
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        return default
    return int(campaign[key])


def _str_field(campaign_id: int, key: str, default: str) -> str:
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        return default
    return str(campaign[key])


def _mailbox_status(row: dict) -> str:
    if row["last_error"] and (row["skip_until_refresh"] or row["quota_remaining"] is None):
        return "Error"
    if not row["enabled"]:
        return "Disabled"
    if row["quota_remaining"] == 0:
        return "Quota used"
    return "Active"


def _refresh_row(row: dict) -> dict:
    try:
        data = google_send.quota(row["web_app_url"], row["token"])
    except Exception as exc:
        data = {"ok": False, "code": "TRANSIENT", "message": str(exc)}
    sender.sender._apply_quota(row["id"], data)
    return data


def _owner(value: str) -> str:
    return value.strip() or db.get_setting("owner_name", "Admin") or "Admin"


@app.post("/api/account-bulk")
def account_bulk(body: BulkIn):
    action = body.action.strip().lower()
    if action not in {"link", "status", "delete", "refresh"}:
        raise HTTPException(400, "Choose link, status, delete, or refresh")
    if action == "link":
        name = body.server_name.strip()
        known = {row["name"] for row in db.list_servers()}
        if name not in known:
            raise HTTPException(400, "Create that server first")
    done = 0
    for mailbox_id in body.ids:
        row = db.get_mailbox(mailbox_id)
        if row is None:
            continue
        if action == "delete":
            db.delete_mailbox(mailbox_id)
        elif action == "status":
            db.update_mailbox(mailbox_id, enabled=1 if body.enabled else 0)
        elif action == "link":
            db.update_mailbox(mailbox_id, server_name=body.server_name.strip())
        elif action == "refresh":
            _refresh_row(row)
        done += 1
    return {"ok": True, "done": done, "message": "Bulk action completed successfully."}


@app.post("/api/account-import")
def account_import(body: AccountImportIn):
    sample = body.csv.lstrip("\ufeff").strip()
    if not sample:
        raise HTTPException(400, "Paste a CSV with a header row")
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV needs a header row")
    added = 0
    errors = []
    for raw in reader:
        mapped = {importer.normalize_key(key): (value or "").strip() for key, value in raw.items() if key}
        email = mapped.get("email") or mapped.get("account email") or ""
        url = mapped.get("web app url") or mapped.get("url") or mapped.get("webapp url") or ""
        token = mapped.get("secret") or mapped.get("token") or ""
        kind = mapped.get("account type") or mapped.get("account kind") or mapped.get("type") or "gmail"
        server_name = mapped.get("server") or mapped.get("server name") or ""
        user = mapped.get("user") or mapped.get("owner") or mapped.get("owner name") or ""
        if not url or not token:
            errors.append(email or "row missing url or secret")
            continue
        try:
            google_send.validate_url(url)
            kind = _account_kind(kind)
        except (ValueError, HTTPException) as exc:
            errors.append(str(exc.detail if isinstance(exc, HTTPException) else exc))
            continue
        if server_name and server_name not in {row["name"] for row in db.list_servers()}:
            try:
                db.add_server(server_name)
            except Exception:
                pass
        row = db.add_mailbox(
            email or "Mailbox",
            url,
            token,
            practice=False,
            account_kind=kind,
            account_email=email,
            server_name=server_name,
            owner_name=_owner(user),
        )
        _refresh_row(row)
        added += 1
    return {"added": added, "errors": errors}


@app.post("/api/servers")
def create_server(body: ServerIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Server name is required")
    try:
        return db.add_server(name)
    except Exception as exc:
        raise HTTPException(400, "That server name already exists") from exc


@app.delete("/api/servers/{server_id}")
def remove_server(server_id: int):
    db.delete_server(server_id)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/test-send")
def test_send(campaign_id: int, body: TestSendIn):
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    box = db.get_mailbox(body.mailbox_id)
    if box is None:
        raise HTTPException(404, "Mailbox not found")
    target = body.to.strip()
    if "@" not in target:
        raise HTTPException(400, "Type an email address")
    fields = db.first_fields(campaign_id)
    fields["email"] = target
    fields.setdefault("first name", "Friend")
    fields.setdefault("first_name", fields.get("first name", "Friend"))
    sender.sender._offer_fields(campaign, fields)
    html = importer.merge_text(campaign["html_body"], fields)
    text = importer.merge_text(campaign["text_body"], fields)
    if not text.strip():
        text = importer.plain_from_html(html)
    payload = {
        "to": target,
        "subject": importer.merge_text(campaign["subject"], fields) or "(no subject)",
        "html": html,
        "text": text,
        "fromName": importer.merge_text(campaign["from_name"], fields),
        "replyTo": (campaign["reply_to"] or "").strip(),
    }
    if not payload["fromName"]:
        payload.pop("fromName")
    if not payload["replyTo"]:
        payload.pop("replyTo")
    try:
        data = google_send.send(box["web_app_url"], box["token"], payload)
    except Exception as exc:
        data = {"ok": False, "code": "TRANSIENT", "message": str(exc)}
    sender.sender._apply_quota(box["id"], data)
    if data.get("ok"):
        outcome = "sent"
    elif data.get("code") == "REJECTED":
        outcome = "skipped"
    else:
        outcome = "failed"
    line = f"{outcome}: {box['label']} → {target}"
    if not data.get("ok"):
        line = f"{line}. {data.get('message') or data.get('code')}"
    db.add_event(campaign_id, f"Test {line}")
    db.record_test(campaign_id, box["id"], target, outcome)
    updated = db.get_mailbox(box["id"])
    return {
        "outcome": outcome,
        "line": line,
        "mailbox": _public_mailbox(updated),
        "to": target,
    }


@app.get("/api/subscribers")
def subscribers(q: str = ""):
    return {"rows": db.search_subscribers(q)}


@app.get("/api/reports")
def reports(start: str = "", end: str = ""):
    data = db.deliverability()
    labels = {row["id"]: row["label"] for row in db.list_mailboxes()}
    names = {row["id"]: row["name"] for row in db.list_campaigns()}
    box_counts = {}
    camp_counts = {}
    for row in data["mailboxes"]:
        box_counts[row["mailbox_id"]] = {"sent": row["sent"], "failed": row["failed"]}
    for row in data["campaigns"]:
        camp_counts[row["campaign_id"]] = {"sent": row["sent"], "failed": row["failed"]}
    for row in data["tests"]:
        box = box_counts.setdefault(row["mailbox_id"], {"sent": 0, "failed": 0})
        camp = camp_counts.setdefault(row["campaign_id"], {"sent": 0, "failed": 0})
        box["sent"] += row["sent"]
        box["failed"] += row["failed"]
        camp["sent"] += row["sent"]
        camp["failed"] += row["failed"]
    return {
        "mailboxes": [
            {"id": key, "label": labels.get(key, ""), "sent": value["sent"], "failed": value["failed"]}
            for key, value in box_counts.items()
        ],
        "campaigns": [
            {"id": key, "name": names.get(key, ""), "sent": value["sent"], "failed": value["failed"]}
            for key, value in camp_counts.items()
        ],
        "quotaStops": data["quotaStops"],
        "googleWaits": data["googleWaits"],
        "sentInRange": db.sent_between(start, end),
    }


@app.get("/api/media")
def media_list():
    rows = db.list_media()
    for row in rows:
        row["url"] = f"/api/media/{row['id']}/file"
    return {"rows": rows}


@app.post("/api/media")
async def media_upload(file: UploadFile = File(...)):
    filename = Path(file.filename or "image").name
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "That file is empty")
    if len(raw) > MAX_ATTACHMENT:
        raise HTTPException(400, "Each file must be 10 MB or smaller")
    folder = db.DATA / "media"
    folder.mkdir(parents=True, exist_ok=True)
    mime = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    stored = folder / f"{secrets.token_hex(4)}_{filename}"
    stored.write_bytes(raw)
    row = db.add_media(filename, mime, str(stored))
    row["url"] = f"/api/media/{row['id']}/file"
    return row


@app.get("/api/media/{media_id}/file")
def media_file(media_id: int):
    row = db.get_media(media_id)
    if row is None:
        raise HTTPException(404, "File not found")
    return FileResponse(row["path"], media_type=row["mime_type"], filename=row["filename"])


@app.post("/api/campaigns/{campaign_id}/use-media/{media_id}")
def use_media(campaign_id: int, media_id: int):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    row = db.get_media(media_id)
    if row is None:
        raise HTTPException(404, "File not found")
    source = Path(row["path"])
    folder = db.ATTACH_DIR / str(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{secrets.token_hex(4)}_{row['filename']}"
    stored.write_bytes(source.read_bytes())
    return db.add_attachment(campaign_id, row["filename"], row["mime_type"], str(stored))


@app.post("/api/offers")
def create_offer(body: OfferIn):
    name = body.name.strip()
    url = body.url.strip()
    if not name or not url:
        raise HTTPException(400, "Name and URL are required")
    return db.add_offer(name, url, body.note.strip())


@app.delete("/api/offers/{offer_id}")
def remove_offer(offer_id: int):
    db.delete_offer(offer_id)
    return {"ok": True}


@app.post("/api/conversions")
def create_conversion(body: ConversionIn):
    return db.add_conversion(body.offer_id, float(body.amount), body.note.strip())


@app.post("/api/settings")
def save_settings(body: SettingsIn):
    fields = body.model_dump()
    _check_timing(fields)
    owner = fields["owner_name"].strip() or "Admin"
    db.set_setting("delay_amount", str(fields["delay_amount"]))
    db.set_setting("delay_unit", fields["delay_unit"])
    db.set_setting("rotation", fields["rotation"])
    db.set_setting("auto_retries", str(fields["auto_retries"]))
    db.set_setting("owner_name", owner)
    return db.all_settings()
