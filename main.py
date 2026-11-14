from typing import Optional

import csv
import io
import json
import sqlite3
import mimetypes
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import db
import google_send
import importer
import practice
import sender
import vault

ROOT = Path(__file__).resolve().parent
MAX_ATTACHMENT = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    db.reset_interrupted()
    practice.restore(db.list_all_mailboxes())
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(PermissionError)
def deny_company(_request: Request, _exc: PermissionError):
    return JSONResponse({"detail": "That workspace is not available"}, status_code=403)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/static")
        or path.startswith("/u/")
        or path.startswith("/mock/")
        or path in {"/login", "/api/login"}
    ):
        return await call_next(request)
    user_id = vault.session_user(request.cookies.get("mailing_session", ""))
    user = db.get_user(user_id) if user_id else None
    company = db.get_company(user["company_id"]) if user and user.get("company_id") else None
    allowed = bool(user and user["status"] == "active" and (user["role"] == "super_admin" or (company and company["status"] == "active")))
    if not allowed:
        db.bind_company(None)
        if path.startswith("/api"):
            return JSONResponse({"detail": "Sign in required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    if user["role"] == "company_user":
        db.bind_company(user["company_id"])
    else:
        db.bind_company(None)
        if path.startswith("/api") and not path.startswith("/api/admin") and path not in {"/api/me", "/api/logout"}:
            db.bind_company(None)
            return JSONResponse({"detail": "Open the admin area for this account"}, status_code=403)
    request.state.user = user
    try:
        return await call_next(request)
    finally:
        db.bind_company(None)


class ConnectionTestIn(BaseModel):
    web_app_url: str
    token: str


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
    description: str = ""
    status: str = "active"


class ConversionIn(BaseModel):
    offer_id: Optional[int] = None
    amount: float
    note: str = ""
    campaign_id: Optional[int] = None
    email: str = ""
    occurred_at: str = ""


class SettingsIn(BaseModel):
    delay_amount: int = 1
    delay_unit: str = "seconds"
    rotation: str = "drain"
    auto_retries: int = 3
    owner_name: str = "Admin"
    public_base_url: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    from_name: str = ""
    reply_to: str = ""
    import_mode: str = "skip"


class PasswordIn(BaseModel):
    password: str
    confirm: str = ""
    current: str = ""
    email: str = ""
    name: str = ""


class TemplateIn(BaseModel):
    name: str
    subject: str = ""
    html_body: str = ""
    text_body: str = ""
    from_name: str = ""
    reply_to: str = ""


class PreviewIn(BaseModel):
    recipient_id: int


class DraftIn(BaseModel):
    brief: str


class SuppressIn(BaseModel):
    email: str


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


@app.get("/login")
def login_page(request: Request):
    if vault.session_user(request.cookies.get("mailing_session", "")):
        return RedirectResponse("/", status_code=303)
    setup = db.user_count() == 0
    title = "Create the super admin" if setup else "Sign in"
    extra = """
<label>Name<input name="name" required></label>
<label>Again<input name="confirm" type="password" required minlength="8"></label>
""" if setup else ""
    hint = "This creates the only super admin. It is not stored in the code." if setup else "Use the email and password for your company, or the super admin account."
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Sendora</title>
<link rel="icon" href="/static/favicon.png" type="image/png">
<link rel="stylesheet" href="/static/app.css?v=24"></head>
<body><main class="login-wrap"><form id="login-form" class="panel stack">
<img class="login-logo" src="/static/sendora-logo.png" alt="Sendora">
<h1>{title}</h1>
<p class="hint">{hint}</p>
<label>Email<input name="email" type="email" required></label>
{extra}
<label>Password<input name="password" type="password" required minlength="8"></label>
<button type="submit">{title}</button>
<p id="login-error" class="field-error" hidden></p>
</form>
<script>
document.getElementById("login-form").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const form = new FormData(event.target);
  const response = await fetch("/api/login", {{
    method: "POST",
    headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{email: form.get("email"), name: form.get("name") || "", password: form.get("password"), confirm: form.get("confirm") || ""}})
  }});
  const data = await response.json().catch(() => ({{}}));
  if (!response.ok) {{
    const note = document.getElementById("login-error");
    note.hidden = false;
    note.textContent = data.detail || "Sign in failed";
    return;
  }}
  location.href = "/";
}});
</script></main></body></html>"""
    return HTMLResponse(page)


def _session(response: Response, user_id: int) -> None:
    response.set_cookie("mailing_session", vault.make_session(user_id), httponly=True, samesite="lax", max_age=14 * 86400)


@app.post("/api/login")
def login(body: PasswordIn, response: Response):
    email = body.email.strip().lower()
    if not importer.valid_email(email):
        raise HTTPException(400, "Enter a valid email address")
    if db.user_count() == 0:
        if len(body.password) < 8 or body.password != body.confirm:
            raise HTTPException(400, "Use at least 8 characters, and enter the password twice")
        if not body.name.strip():
            raise HTTPException(400, "Enter the super admin name")
        user = db.create_user(email, body.name, vault.hash_password(body.password), "super_admin", None)
    else:
        user = db.user_by_email(email)
        if user is None or not vault.password_ok(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong email or password")
        if user["status"] != "active":
            raise HTTPException(403, "This user is suspended")
        if user["role"] == "company_user":
            company = db.get_company(user["company_id"])
            if company is None or company["status"] != "active":
                raise HTTPException(403, "This company is suspended")
        user = db.public_user(user)
    db.touch_login(user["id"])
    _session(response, user["id"])
    return {"ok": True, "role": user["role"]}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("mailing_session")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = request.state.user
    company = db.get_company(user["company_id"]) if user.get("company_id") else None
    return {"user": user, "company": company}


@app.post("/api/password")
def change_password(body: PasswordIn, request: Request, response: Response):
    stored = db.user_by_email(request.state.user["email"])
    if stored is None or not vault.password_ok(body.current, stored["password_hash"]):
        raise HTTPException(401, "Current password is wrong")
    if len(body.password) < 8:
        raise HTTPException(400, "Use at least 8 characters")
    db.set_user_password(stored["id"], vault.hash_password(body.password))
    _session(response, stored["id"])
    return {"ok": True}


def _super(request: Request) -> dict:
    user = request.state.user
    if user["role"] != "super_admin":
        raise HTTPException(403, "Super admin only")
    return user


class CompanyIn(BaseModel):
    name: str
    email: str = ""
    contact_name: str = ""
    phone: str = ""
    status: str = "active"
    user_name: str = ""
    user_email: str = ""
    user_password: str = ""


class UserIn(BaseModel):
    name: str
    email: str
    company_id: int
    password: str = ""
    status: str = "active"


@app.get("/api/admin/summary")
def admin_summary(request: Request):
    _super(request)
    return db.admin_summary()


@app.get("/api/admin/companies")
def admin_companies(request: Request):
    _super(request)
    return {"rows": db.list_companies()}


@app.post("/api/admin/companies")
def admin_company_create(body: CompanyIn, request: Request):
    _super(request)
    if not body.name.strip():
        raise HTTPException(400, "Name the company")
    company = db.create_company(body.name, body.email, body.contact_name, body.phone)
    if body.user_email.strip():
        if len(body.user_password) < 8 or not importer.valid_email(body.user_email):
            raise HTTPException(400, "The first user needs a valid email and a password of at least 8 characters")
        try:
            db.create_user(body.user_email, body.user_name or body.contact_name or "User", vault.hash_password(body.user_password), "company_user", company["id"])
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "That user email is already in use") from exc
    return company


@app.patch("/api/admin/companies/{company_id}")
def admin_company_edit(company_id: int, body: CompanyIn, request: Request):
    _super(request)
    if db.get_company(company_id) is None:
        raise HTTPException(404, "Company not found")
    db.update_company(company_id, body.name, body.email, body.contact_name, body.phone, body.status)
    return {"ok": True}


@app.delete("/api/admin/companies/{company_id}")
def admin_company_delete(company_id: int, request: Request):
    _super(request)
    try:
        db.delete_company(company_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/admin/users")
def admin_users(request: Request):
    _super(request)
    return {"rows": db.list_users()}


@app.post("/api/admin/users")
def admin_user_create(body: UserIn, request: Request):
    _super(request)
    if db.get_company(body.company_id) is None:
        raise HTTPException(400, "Choose a company")
    if len(body.password) < 8 or not importer.valid_email(body.email):
        raise HTTPException(400, "Enter a valid email and a password of at least 8 characters")
    try:
        return db.create_user(body.email, body.name, vault.hash_password(body.password), "company_user", body.company_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That email is already in use") from exc


@app.patch("/api/admin/users/{user_id}")
def admin_user_edit(user_id: int, body: UserIn, request: Request):
    _super(request)
    current = db.get_user(user_id)
    if current is None or current["role"] != "company_user":
        raise HTTPException(404, "User not found")
    try:
        db.update_user(user_id, body.name, body.email, body.company_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That email is already in use") from exc
    if body.status in {"active", "suspended"}:
        db.set_user_status(user_id, body.status)
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(400, "Use at least 8 characters")
        db.set_user_password(user_id, vault.hash_password(body.password))
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_user_delete(user_id: int, request: Request):
    _super(request)
    db.delete_user(user_id)
    return {"ok": True}


@app.get("/u/{token}")
def unsubscribe(token: str):
    row = db.recipient_by_token(token)
    if row is None:
        return HTMLResponse("<p>This unsubscribe link is not valid.</p>", status_code=404)
    db.suppress_email(row["email"], "unsubscribe")
    return HTMLResponse("<p>You are unsubscribed. This address will not be emailed again.</p>")


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
        "today": db.sent_today(),
        "series": db.activity_series(30),
        "lastSync": db.latest_quota_check(),
        "recentEvents": db.recent_events(8),
        "templates": db.list_templates(),
        "groups": db.list_groups(),
        "segments": db.list_segments(),
        "mailboxGroups": db.list_mailbox_groups(),
        "audience": db.audience_summary(),
    }
    if campaign:
        payload["campaign"] = _campaign_view(campaign, status, offset)
    return payload


@app.post("/api/mailboxes/test")
def test_mailbox_connection(body: ConnectionTestIn):
    url = body.web_app_url.strip()
    token = body.token.strip()
    if not url or not token:
        raise HTTPException(400, "Paste the web app URL and the secret first")
    try:
        google_send.validate_url(url)
        data = google_send.quota(url, token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data.get("ok"):
        raise HTTPException(400, str(data.get("message") or "The script did not accept this connection"))
    return {
        "ok": True,
        "email": data.get("mailbox") or "",
        "quotaRemaining": data.get("quotaRemaining"),
    }


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
        from_name=prefs.get("from_name") or "",
        reply_to=prefs.get("reply_to") or "",
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
    report = _preflight(campaign)
    if report["blockers"]:
        raise HTTPException(400, report["blockers"][0])
    _require_ready(campaign)
    if sender.sender.alive():
        if sender.sender.campaign_id == campaign_id:
            sender.sender.resume()
            db.update_campaign(campaign_id, status="running", phase="Resumed")
            db.add_event(campaign_id, "Resumed")
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
    db.update_campaign(campaign_id, status="paused", phase="Paused", step="paused")
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
        _open_queue(campaign_id, f"{count} failed rows are back in the queue. Press Start.")
        db.add_event(campaign_id, f"Returned {count} failed rows to the queue")
    return {"ok": True, "count": count}


@app.post("/api/campaigns/{campaign_id}/resend")
def resend_sent(campaign_id: int):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    count = db.requeue_sent(campaign_id)
    if count:
        _open_queue(campaign_id, f"{count} sent rows are back in the queue. Press Start.")
        db.add_event(campaign_id, f"Queued {count} sent rows again")
    return {"ok": True, "count": count}


@app.post("/api/campaigns/{campaign_id}/restart")
def restart_campaign(campaign_id: int):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    if sender.sender.alive() and sender.sender.campaign_id == campaign_id:
        raise HTTPException(409, "Pause this campaign before restarting it")
    count = db.requeue_finished(campaign_id)
    db.update_campaign(
        campaign_id,
        status="paused",
        step="paused",
        phase="Back in the queue. Press Start.",
    )
    db.add_event(campaign_id, f"Restart queued {count} sent and failed rows")
    return {"ok": True, "count": count}


@app.post("/api/campaigns/{campaign_id}/duplicate")
def duplicate_campaign(campaign_id: int):
    created = db.duplicate_campaign(campaign_id)
    if created is None:
        raise HTTPException(404, "Campaign not found")
    db.add_event(created["id"], f"Copied from campaign {campaign_id}")
    return {"id": created["id"], "name": created["name"]}


@app.post("/api/campaigns/{campaign_id}/preflight")
def preflight(campaign_id: int):
    campaign = db.get_campaign(campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaign not found")
    return _preflight(campaign)


@app.post("/api/campaigns/{campaign_id}/preview")
def preview_recipient(campaign_id: int, body: PreviewIn):
    campaign = db.get_campaign(campaign_id)
    person = db.get_recipient(body.recipient_id)
    if campaign is None or person is None or person["campaign_id"] != campaign_id:
        raise HTTPException(404, "Recipient not found")
    return _merged_message(campaign, person)


@app.get("/api/templates")
def templates():
    return {"rows": db.list_templates()}


@app.post("/api/templates")
def create_template(body: TemplateIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name the template")
    return db.add_template(name, body.subject, body.html_body, body.text_body, body.from_name, body.reply_to)


@app.get("/api/templates/{template_id}")
def read_template(template_id: int):
    row = db.get_template(template_id)
    if row is None:
        raise HTTPException(404, "Template not found")
    return row


@app.delete("/api/templates/{template_id}")
def remove_template(template_id: int):
    db.delete_template(template_id)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/draft")
def draft_campaign(campaign_id: int, body: DraftIn):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(400, "Describe the email first")
    key = db.secret_setting("ai_api_key")
    if not key:
        raise HTTPException(400, "Add an API key in Settings")
    base = (db.get_setting("ai_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = db.get_setting("ai_model") or "gpt-4o-mini"
    names = ", ".join(db.placeholder_names(campaign_id))
    try:
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Write one email. Reply with JSON keys subject, html, and text. Use only these merge fields: "
                        + names
                        + ". Do not invent extra placeholders.",
                    },
                    {"role": "user", "content": brief},
                ],
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        raise HTTPException(400, f"The draft service did not answer. {exc}") from exc
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise HTTPException(400, "The draft was not readable")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "The draft was not readable") from exc
    return {
        "subject": str(data.get("subject") or ""),
        "html": str(data.get("html") or ""),
        "text": str(data.get("text") or ""),
    }


@app.get("/api/suppression")
def suppression_list():
    return {"rows": db.list_suppression()}


@app.post("/api/suppression")
def suppress(body: SuppressIn):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Type an email address")
    db.suppress_email(email, "manual")
    return {"ok": True}


@app.delete("/api/suppression")
def unsuppress(email: str):
    db.remove_suppression(email)
    return {"ok": True}


def _open_queue(campaign_id: int, phase: str) -> None:
    current = db.get_campaign(campaign_id)
    if current and current["status"] in {"done", "paused", "draft"}:
        db.update_campaign(campaign_id, status="paused", phase=phase, step="paused")


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
    if result["requeued"]:
        _open_queue(
            campaign_id,
            f"{result['requeued']} people are queued again. Press Start.",
        )
    db.add_event(
        campaign_id,
        f"Imported {result['added']} people. {result['requeued']} queued again, {result.get('suppressed', 0)} suppressed, {result['duplicate']} duplicates, {skipped} skipped.",
    )
    return result


def _merged_message(campaign: dict, person: dict) -> dict:
    try:
        fields = json.loads(person.get("fields_json") or "{}")
    except json.JSONDecodeError:
        fields = {}
    if not isinstance(fields, dict):
        fields = {}
    fields.setdefault("email", person.get("email") or "")
    sender.sender._offer_fields(campaign, fields)
    html = importer.merge_text(campaign["html_body"], fields)
    text = importer.merge_text(campaign["text_body"], fields)
    if not text.strip():
        text = importer.plain_from_html(html)
    token = db.unsub_token_for(person["id"]) if person.get("id") else ""
    html_foot, text_foot = sender.message_footer(campaign, token)
    return {
        "subject": importer.merge_text(campaign["subject"], fields),
        "html": html + html_foot,
        "text": text + text_foot,
    }


def _preflight(campaign: dict) -> dict:
    blockers = []
    warnings = []
    if not campaign["subject"].strip():
        blockers.append("Add a subject before starting")
    if not campaign["html_body"].strip() and not campaign["text_body"].strip():
        blockers.append("Add HTML or plain text before starting")
    if db.counts(campaign["id"])["queued"] < 1:
        blockers.append("The queue is empty")
    if not sender.sender._candidate_boxes(campaign):
        blockers.append("Choose at least one mailbox that can send")
    for row in db.list_attachments(campaign["id"]):
        if not Path(row["path"]).exists():
            warnings.append(f"Attachment {row['filename']} is missing on disk")
    if not db.get_setting("public_base_url") and not (campaign.get("reply_to") or "").strip():
        warnings.append("Add a reply-to or a public unsubscribe URL so people can opt out")
    body = f"{campaign['subject']}\n{campaign['html_body']}\n{campaign['text_body']}"
    names = sorted(set(importer.normalize_key(item) for item in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", body)))
    offer_ready = bool(campaign.get("offer_id"))
    missing = {}
    for person in db._rows(
        "SELECT fields_json FROM recipients WHERE campaign_id = ? AND status = 'queued' LIMIT 500",
        (campaign["id"],),
    ):
        try:
            fields = json.loads(person["fields_json"] or "{}")
        except json.JSONDecodeError:
            fields = {}
        lookup = {importer.normalize_key(str(key)): str(val or "").strip() for key, val in fields.items()}
        for name in names:
            if name in {"offer url", "offerurl"} and offer_ready:
                continue
            if lookup.get(name):
                continue
            missing[name] = missing.get(name, 0) + 1
    for name, count in missing.items():
        label = "person is" if count == 1 else "people are"
        warnings.append(f"{count} {label} missing {{{{{name}}}}}")
    seen = set()
    for url in re.findall(r"https?://[^\s\"'<>]+", campaign["html_body"] or "")[:15]:
        if url in seen:
            continue
        seen.add(url)
        try:
            response = httpx.head(url, timeout=4, follow_redirects=True)
            if response.status_code >= 400:
                warnings.append(f"Link did not answer: {url}")
        except Exception:
            warnings.append(f"Link did not answer: {url}")
    return {"blockers": blockers, "warnings": warnings}


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
    if "rotation" in fields and fields["rotation"] not in {"drain", "round_robin", "wave"}:
        raise HTTPException(400, "Rotation must be one mailbox, in turn, or all at once")
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
    if status not in {"all", "queued", "sent", "failed", "sending", "suppressed"}:
        status = "all"
    offset = max(0, offset)
    view = dict(campaign)
    view["counts"] = db.counts(campaign_id)
    activity = db.campaign_activity(campaign_id)
    amount = int(campaign["delay_amount"] or 0)
    delay = amount * 60 if campaign["delay_unit"] == "minutes" else amount
    waiting = view["counts"]["queued"] + view["counts"]["sending"]
    if activity["perMinute"]:
        eta = int(round(waiting / activity["perMinute"] * 60))
    elif delay:
        eta = waiting * delay
    else:
        eta = 0
    activity["etaSeconds"] = eta
    view["live"] = activity
    view["recipients"] = db.list_recipients(campaign_id, status, 100, offset)
    view["attachments"] = [
        {"id": row["id"], "filename": row["filename"]}
        for row in db.list_attachments(campaign_id)
    ]
    view["events"] = db.list_events(campaign_id)
    view["mailboxStats"] = db.mailbox_results(campaign_id)
    view["placeholders"] = db.placeholder_names(campaign_id)
    labels = {row["id"]: row["label"] for row in db.list_mailboxes()}
    for person in view["recipients"]:
        person["mailbox"] = labels.get(person["mailbox_id"], "")
    for person in view["live"]["recent"]:
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


@app.post("/api/script")
def generate_script():
    secret = secrets.token_urlsafe(24)
    source = (ROOT / "script" / "Code.gs").read_text()
    escaped = secret.replace("\\", "\\\\").replace('"', '\\"')
    source = source.replace(
        "var secret = PropertiesService.getScriptProperties().getProperty(PROP_KEY);",
        f'var secret = "{escaped}";',
    )
    note = (
        "Paste this whole file into a new project at script.google.com.\n"
        "Deploy → New deployment → Web app. Execute as Me. Who has access: Anyone.\n"
        "Put the web app URL and the secret below into Add mailbox.\n\n"
    )
    return {"secret": secret, "code": note + source}


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
    if db.is_suppressed(target):
        raise HTTPException(400, "That address is unsubscribed")
    fields = db.first_fields(campaign_id)
    fields["email"] = target
    fields.setdefault("first name", "Friend")
    fields.setdefault("first_name", fields.get("first name", "Friend"))
    sender.sender._offer_fields(campaign, fields)
    html = importer.merge_text(campaign["html_body"], fields)
    text = importer.merge_text(campaign["text_body"], fields)
    if not text.strip():
        text = importer.plain_from_html(html)
    html_foot, text_foot = sender.message_footer(campaign, "")
    html += html_foot
    text += text_foot
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
        data = google_send.send(
            box["web_app_url"],
            box["token"],
            payload,
            box.get("script_style") or "app",
        )
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


class AudienceIn(BaseModel):
    email: str
    first_name: str = ""
    last_name: str = ""
    company: str = ""


class AudienceIds(BaseModel):
    ids: list[int] = Field(default_factory=list)


class AssignIn(BaseModel):
    ids: list[int] = Field(default_factory=list)
    group_id: Optional[int] = None
    tag_id: Optional[int] = None
    remove: bool = False


class ImportIn(BaseModel):
    text: str = ""
    url: str = ""
    mode: str = "skip"
    group_id: Optional[int] = None
    group_name: str = ""
    source: str = "paste"


class NamedIn(BaseModel):
    name: str


class SegmentIn(BaseModel):
    name: str
    provider: str = ""
    status: str = "any"
    group_id: str = ""
    tag_id: str = ""


class GroupUse(BaseModel):
    group_id: Optional[int] = None
    group_ids: list[int] = Field(default_factory=list)
    segment_id: Optional[int] = None


class MailboxGroupIn(BaseModel):
    name: str
    mailbox_ids: list[int] = Field(default_factory=list)


def _import_text(body: ImportIn) -> str:
    if body.url.strip():
        try:
            return importer.fetch_sheet(body.url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return body.text


def _parsed_import(text: str) -> dict:
    try:
        return importer.contact_table(text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _preview_payload(parsed: dict) -> dict:
    counts = db.classify_import(parsed["rows"])
    return {
        "total": parsed["total"],
        "invalid": parsed["invalid"],
        "duplicateInFile": parsed["duplicateInFile"],
        "new": counts["new"],
        "existing": counts["existing"],
        "suppressed": counts["suppressed"],
        "sample": parsed["rows"][:8],
    }


@app.get("/api/audience")
def audience(q: str = "", status: str = "", provider: str = "", group: str = "", tag: str = "", sort: str = "newest", page: int = 0):
    return {"summary": db.audience_summary(), "groups": db.list_groups(), "tags": db.list_tags(), "segments": db.list_segments(), **db.list_contacts(q, status, provider, group, tag, sort, page)}


@app.post("/api/audience")
def audience_add(body: AudienceIn):
    email = body.email.strip().lower()
    if not importer.valid_email(email):
        raise HTTPException(400, "That email address is not valid.")
    if db.is_suppressed(email):
        raise HTTPException(400, "That address is unsubscribed. Remove it from suppression before adding it.")
    try:
        row = db.save_contact(email, body.first_name, body.last_name, body.company, "manual")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return row


@app.patch("/api/audience/{contact_id}")
def audience_edit(contact_id: int, body: AudienceIn):
    email = body.email.strip().lower()
    if not importer.valid_email(email):
        raise HTTPException(400, "That email address is not valid.")
    try:
        return db.save_contact(email, body.first_name, body.last_name, body.company, "manual", contact_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/audience/delete")
def audience_delete(body: AudienceIds):
    return {"deleted": db.delete_contacts(body.ids)}


@app.post("/api/audience/assign")
def audience_assign(body: AssignIn):
    if body.group_id:
        db.assign_groups(body.ids, body.group_id, body.remove)
    if body.tag_id:
        db.assign_tags(body.ids, body.tag_id, body.remove)
    return {"ok": True}


@app.post("/api/audience/preview")
def audience_preview(body: ImportIn):
    return _preview_payload(_parsed_import(_import_text(body)))


@app.post("/api/audience/preview-file")
async def audience_preview_file(file: UploadFile = File(...)):
    raw = await file.read()
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        try:
            text = importer.xlsx_to_csv(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    else:
        text = raw.decode("utf-8", errors="replace")
    return {**_preview_payload(_parsed_import(text)), "text": text}


@app.post("/api/audience/import")
def audience_import(body: ImportIn):
    parsed = _parsed_import(_import_text(body))
    group_id = body.group_id
    if body.group_name.strip():
        try:
            group_id = db.add_group(body.group_name.strip())["id"]
        except sqlite3.IntegrityError as exc:
            raise HTTPException(400, "That group already exists. Choose it from the list.") from exc
    result = db.commit_import(parsed["rows"], body.mode, body.source or "paste", group_id, parsed["invalid"], parsed["duplicateInFile"], parsed["total"])
    db.add_event(None, f"Imported {result['added']} contacts. {result['updated']} updated, {result['skipped']} duplicates skipped, {result['suppressed']} unsubscribed.")
    return result


@app.post("/api/groups/merge")
def group_merge(body: AssignIn):
    if not body.group_id or not body.ids:
        raise HTTPException(400, "Choose two groups.")
    return {"contacts": db.merge_groups(body.ids[0], body.group_id)}


@app.post("/api/mailbox-groups")
def mailbox_group_add(body: MailboxGroupIn):
    if not body.name.strip():
        raise HTTPException(400, "Name the mailbox group.")
    try:
        return db.save_mailbox_group(body.name.strip(), body.mailbox_ids)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That mailbox group already exists.") from exc


@app.delete("/api/mailbox-groups/{group_id}")
def mailbox_group_delete(group_id: int):
    db.delete_mailbox_group(group_id)
    return {"ok": True}


@app.post("/api/groups")
def group_add(body: NamedIn):
    if not body.name.strip():
        raise HTTPException(400, "Name the group.")
    try:
        return db.add_group(body.name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That group already exists.") from exc


@app.patch("/api/groups/{group_id}")
def group_rename(group_id: int, body: NamedIn):
    try:
        db.rename_group(group_id, body.name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That group already exists.") from exc
    return {"ok": True}


@app.delete("/api/groups/{group_id}")
def group_delete(group_id: int):
    db.delete_group(group_id)
    return {"ok": True}


@app.post("/api/tags")
def tag_add(body: NamedIn):
    if not body.name.strip():
        raise HTTPException(400, "Name the tag.")
    try:
        return db.add_tag(body.name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, "That tag already exists.") from exc
    return {"ok": True}


@app.delete("/api/tags/{tag_id}")
def tag_delete(tag_id: int):
    db.delete_tag(tag_id)
    return {"ok": True}


@app.post("/api/segments")
def segment_add(body: SegmentIn):
    if not body.name.strip():
        raise HTTPException(400, "Name the segment.")
    return db.add_segment(body.name, body.provider, body.status, body.group_id, body.tag_id)


@app.delete("/api/segments/{segment_id}")
def segment_delete(segment_id: int):
    db.delete_segment(segment_id)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/audience-group")
def use_group(campaign_id: int, body: GroupUse):
    if db.get_campaign(campaign_id) is None:
        raise HTTPException(404, "Campaign not found")
    chosen = list(body.group_ids)
    if body.group_id:
        chosen.append(body.group_id)
    emails = []
    for group_id in chosen:
        emails.extend(db.group_emails(group_id))
    if body.segment_id:
        emails.extend(db.segment_emails(body.segment_id))
    unique = list(dict.fromkeys(emails))
    if not unique:
        raise HTTPException(400, "Those groups have no contacts who can be emailed.")
    people = [(email, json.dumps({"email": email})) for email in unique]
    result = db.insert_recipients(campaign_id, people, False)
    db.add_event(campaign_id, f"Imported {result['added']} people from a group. {result['duplicate']} already in the campaign, {result.get('suppressed', 0)} unsubscribed.")
    return result


@app.get("/api/activity")
def activity(q: str = "", campaign: str = "", kind: str = "", outcome: str = "", page: int = 0):
    return db.search_activity(q, campaign, kind, outcome, page)


@app.get("/api/subscribers")
def subscribers(q: str = ""):
    return {"rows": db.search_subscribers(q)}


@app.get("/api/reports")
def reports(start: str = "", end: str = ""):
    data = db.deliverability()
    stored_boxes = {row["id"]: row for row in db.list_mailboxes()}
    stored_campaigns = {row["id"]: row for row in db.list_campaigns()}
    labels = {key: row["label"] for key, row in stored_boxes.items()}
    names = {key: row["name"] for key, row in stored_campaigns.items()}
    quota_notes = db.quota_messages()
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
            {
                "id": key,
                "label": labels.get(key, ""),
                "server": (stored_boxes.get(key) or {}).get("server_name") or "",
                "accountKind": (stored_boxes.get(key) or {}).get("account_kind") or "",
                "sent": value["sent"],
                "failed": value["failed"],
                "quotaStops": sum(1 for note in quota_notes if labels.get(key) and labels.get(key) in note),
            }
            for key, value in box_counts.items()
        ],
        "campaigns": [
            {
                "id": key,
                "name": names.get(key, ""),
                "sent": value["sent"],
                "failed": value["failed"],
                "status": (stored_campaigns.get(key) or {}).get("status") or "",
                "created_at": (stored_campaigns.get(key) or {}).get("created_at") or "",
                "recipients": int((stored_campaigns.get(key) or {}).get("queued") or 0) + int((stored_campaigns.get(key) or {}).get("sent") or 0) + int((stored_campaigns.get(key) or {}).get("failed") or 0),
            }
            for key, value in camp_counts.items()
        ],
        "quotaStops": data["quotaStops"],
        "googleWaits": data["googleWaits"],
        "sentInRange": db.sent_between(start, end),
        "series": db.activity_series(30),
        "domains": db.domain_stats(),
        "campaignCount": len(stored_campaigns),
    }


@app.get("/api/backup")
def download_backup():
    return FileResponse(db.DB_PATH, filename="mailing-backup.sqlite", media_type="application/octet-stream")


@app.get("/api/audience/export")
def export_audience():
    stored = db._rows("SELECT email, first_name, last_name, company, source, created_at FROM contacts ORDER BY email")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["email", "first name", "last name", "company", "source", "added"])
    for row in stored:
        writer.writerow([row["email"], row["first_name"], row["last_name"], row["company"], row["source"], row["created_at"]])
    return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=audience.csv"})


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
    kinds = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf"}
    mime = kinds.get(Path(filename).suffix.lower())
    if not mime:
        raise HTTPException(400, "Upload a PNG, JPG, WebP, GIF, or PDF.")
    stored = folder / f"{secrets.token_hex(4)}_{filename}"
    stored.write_bytes(raw)
    row = db.add_media(filename, mime, str(stored), len(raw))
    row["url"] = f"/api/media/{row['id']}/file"
    return row


@app.patch("/api/media/{media_id}")
def media_rename(media_id: int, body: NamedIn):
    filename = Path(body.name).name.strip()
    if not filename or db.get_media(media_id) is None:
        raise HTTPException(400, "That file was not found.")
    db.rename_media(media_id, filename)
    return {"ok": True}


@app.delete("/api/media/{media_id}")
def media_delete(media_id: int):
    path = db.delete_media(media_id)
    if path is None:
        raise HTTPException(404, "File not found")
    stored = Path(path)
    try:
        if stored.is_file() and db.DATA.resolve() in stored.resolve().parents:
            stored.unlink()
    except OSError:
        pass
    return {"ok": True}


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
    if not name or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Add a name and an http or https URL.")
    return db.add_offer(name, url, body.note.strip(), body.description.strip(), body.status)


@app.get("/api/offers/{offer_id}")
def read_offer(offer_id: int):
    row = db.offer_detail(offer_id)
    if row is None:
        raise HTTPException(404, "Offer not found")
    return row


@app.patch("/api/offers/{offer_id}")
def edit_offer(offer_id: int, body: OfferIn):
    if not body.name.strip() or not body.url.strip().startswith(("http://", "https://")):
        raise HTTPException(400, "Add a name and an http or https URL.")
    row = db.update_offer(offer_id, body.name.strip(), body.url.strip(), body.note.strip(), body.description.strip(), body.status)
    if row is None:
        raise HTTPException(404, "Offer not found")
    return row


@app.post("/api/offers/{offer_id}/duplicate")
def copy_offer(offer_id: int):
    row = db.offer_detail(offer_id)
    if row is None:
        raise HTTPException(404, "Offer not found")
    return db.add_offer(f"{row['name']} copy", row["url"], row["note"], row.get("description") or "", row.get("status") or "active")


@app.delete("/api/offers/{offer_id}")
def remove_offer(offer_id: int):
    db.delete_offer(offer_id)
    return {"ok": True}


@app.post("/api/conversions")
def create_conversion(body: ConversionIn):
    if body.campaign_id and db.get_campaign(body.campaign_id) is None:
        raise HTTPException(400, "That campaign was not found.")
    if body.email.strip() and not importer.valid_email(body.email):
        raise HTTPException(400, "That email address is not valid.")
    return db.add_conversion(body.offer_id, float(body.amount), body.note.strip(), body.campaign_id, body.email, body.occurred_at.strip())


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
    db.set_setting("public_base_url", fields["public_base_url"].strip().rstrip("/"))
    if fields["ai_base_url"].strip():
        db.set_setting("ai_base_url", fields["ai_base_url"].strip().rstrip("/"))
    if fields["ai_model"].strip():
        db.set_setting("ai_model", fields["ai_model"].strip())
    if fields["ai_api_key"].strip():
        db.set_setting("ai_api_key", vault.seal(fields["ai_api_key"].strip()))
    db.set_setting("from_name", fields["from_name"].strip())
    db.set_setting("reply_to", fields["reply_to"].strip())
    db.set_setting("import_mode", "update" if fields["import_mode"] == "update" else "skip")
    return db.all_settings()
