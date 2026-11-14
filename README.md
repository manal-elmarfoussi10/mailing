# Sendora

Sendora is a local mailing application. A company signs in, connects its own Gmail or Google Workspace mailboxes through Google Apps Script, and sends campaigns from those real mailboxes. One Apps Script call sends one email. The message comes from that mailbox’s real address. Sendora does not store a Google password.

The app is served on this computer at [http://127.0.0.1:8765](http://127.0.0.1:8765). Another computer cannot open it until Sendora is hosted somewhere other than localhost.

## What you can do

- Create a super admin, then create companies and company users.
- Keep each company’s mailboxes, campaigns, contacts, and files separate.
- Connect a mailbox with a generated Apps Script web app, or add a practice mailbox that does not send real mail.
- Build a campaign, import people, preview it, and send with pause, resume, retry, and restart.
- Choose one of three sending patterns: one mailbox at a time, a rotation, or a parallel wave.
- Keep a contact list with groups, tags, segments, imports, and an unsubscribe list.
- Review sends, failures, quota stops, and the activity log.
- Store private images and PDFs, and record offers with manual conversions.

## What is not included

- Opens, clicks, and inbox placement are not tracked. A “success” means Google accepted the send.
- There is no second daily sending cap. Google’s remaining quota is what stops a mailbox.
- There is no email that resets a password. The super admin sets a new password.
- There is no shared test login in this repository. Each person creates accounts on their own computer.
- The administration screen can create, suspend, activate, and delete companies and users. It does not yet have a form to rename a company or move a user to another company. Those updates exist on the API only.
- Deleting a company is refused while it still has users or campaigns. Deleting a user does not delete that company’s mailboxes or campaigns.

## Technologies

Inspected from this repository:

- Python 3.9 or newer
- FastAPI and Uvicorn
- SQLite, file `data/mailing.sqlite`, created on first start
- HTML, CSS, and JavaScript in `static/`. There is no separate frontend build.
- `httpx` for Apps Script and Google Sheet requests
- `cryptography` to encrypt mailbox URLs and secrets at rest

Schema changes run inside the application when it starts. There is no separate migration command and no Alembic.

## Architecture

One process serves the pages, the API, and the sender.

- `main.py` is the FastAPI application and the page routes.
- `db.py` opens SQLite and applies schema updates.
- `sender.py` runs one campaign at a time in a background thread. Rotations are `drain` (sequential), `round_robin` (rotation), and `wave` (parallel waves, then one wait).
- `google_send.py` calls the Apps Script web app. One call sends one email.
- `vault.py` hashes passwords and signs the session cookie.
- `static/` is the interface. The browser loads it from the same process.

A company user only sees rows for their company. The super admin uses the administration screen and does not open a company’s workspace. To work inside a company, sign in as that company’s user.

## Prerequisites

Install these before you start.

| Tool | Why | Check |
| --- | --- | --- |
| Git | Clone the repository | `git --version` |
| Python 3.9 or newer | Run the app | `python3 --version` |
| pip | Install Python packages. It comes with Python. | `python3 -m pip --version` |

You do not need Node.js, Docker, or a database server.

A Google account is needed only when you connect a real mailbox. Practice mailboxes do not need one.

## Clone the repository

```bash
git clone https://github.com/manal-elmarfoussi10/mailing.git
cd mailing
```

## Install

From the project directory:

```bash
python3 -m venv .venv
```

This creates a local Python environment in `.venv` so packages are not installed system-wide.

macOS and Linux:

```bash
source .venv/bin/activate
```

Windows Command Prompt:

```bat
.venv\Scripts\activate
```

Then install the libraries listed in `requirements.txt`:

```bash
python -m pip install -r requirements.txt
```

No environment variables are required. Copy the example file only if you want the note in your folder:

```bash
cp .env.example .env
```

On Windows you can copy the file in Explorer. `.env` is ignored by Git. Do not put real secrets in it. Sendora does not read it.

The database, session key, and mailbox encryption key are created automatically the first time the app starts. You do not run a migration command.

There is no frontend install or build step.

## Create the super admin

1. Start the app using the command in the next section.
2. Open [http://127.0.0.1:8765](http://127.0.0.1:8765).
3. The first screen is **Create the super admin** when no users exist.
4. Enter a name, an email address, and a password of at least 8 characters twice.

That account exists only in your local `data/mailing.sqlite`. It is not stored in the code and it is not shared with the team. Choose your own email and password. Do not reuse a production password.

The first start also creates a company named **Primary company**. Existing mailboxes and campaigns, if this database already had them, belong to that company. The super admin cannot open them. Create a company user, choose **Primary company**, sign out, and sign in as that user.

## Run Sendora

From the project directory, with the virtual environment active:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8765
```

Leave that terminal open.

- Application: [http://127.0.0.1:8765](http://127.0.0.1:8765)
- Sign-in: [http://127.0.0.1:8765/login](http://127.0.0.1:8765/login)
- API reference: [http://127.0.0.1:8765/docs](http://127.0.0.1:8765/docs) after you are signed in

The pages and the API are the same process. There is no second server to start.

Stop the app with `Ctrl+C` in that terminal.

If you change Python files, stop the app and run the same command again. HTML, CSS, and JavaScript updates are picked up on a browser reload. If the screen looks old, reload and bypass the cache.

## Accounts and roles

There are exactly two roles.

### Super admin

Signs in from the first setup screen, or later with that email and password.

- Opens the administration dashboard.
- Sees company and user counts.
- Creates a company and, optionally, its first user.
- Creates more users and assigns each one to one company.
- Suspends, activates, and deletes users.
- Sets a new password for a company user.
- Suspends and activates a company. A suspended company’s users cannot sign in.
- Does not open mailboxes, campaigns, or contacts. Those pages refuse a super admin.

### Company user

Created by the super admin. Each user belongs to one company.

- Signs in with the email and password the super admin set.
- Manages that company’s mailboxes, campaigns, audience, queues, analytics, media, offers, activity, and settings.
- Cannot open another company’s records, even by changing an id in the address bar.
- Cannot open the administration pages.

### How testers get accounts

There is no shared test account in the repository. On a new database:

1. Create your own super admin on the first visit.
2. In Administration, create a company and fill in the first user name, email, and password.
3. Sign out. Sign in with that company user’s email and password.
4. To see **Primary company** data, create a user and select Primary company in the company list.

To test two companies, create a second company and a second user, then use a private browser window so both sessions stay separate.

Password reset is **Reset password** on the user row. The signed-in user can also change their own password under Settings, Security, after entering the current password.

## Testing guide

Use practice mailboxes for sending tests. For a real mailbox, send only a few messages to addresses you are allowed to use. Do not run a bulk send to a real list.

Mark each item pass or fail. If a feature is listed under “Not in the product,” do not file it as a bug.

### Authentication

- [ ] A new database shows **Create the super admin**. After saving, you land in Administration.
- [ ] Signing in again with that email and password works.
- [ ] A wrong password is rejected.
- [ ] Sign out returns you to the sign-in page.
- [ ] A company user can sign in and sees the company menu, not Administration.
- [ ] A company user who opens `#/admin` or `/api/admin/summary` is refused.
- [ ] A super admin who opens `#/campaigns` or `/api/campaigns` is refused.
- [ ] Suspend a user, then confirm that user cannot sign in. Activate the user and confirm sign-in works again.
- [ ] Suspend a company, then confirm its user cannot sign in. Activate the company and confirm sign-in works again.
- [ ] Delete a company user and confirm the company’s campaigns are still listed for another user of that company.

### Company management

- [ ] Create a company with a name, email, contact, and optional phone.
- [ ] Create the first company user on that form. Sign in as that user.
- [ ] Create a second user from the Users form and assign an existing company.
- [ ] User A cannot see user B’s campaign by opening user B’s campaign id.
- [ ] Suspend and activate the company from the company row.
- [ ] Delete is refused while the company still has a user or a campaign.
- [ ] Renaming a company from the screen is not available yet.

### Mailboxes

- [ ] Add a practice mailbox from the mailbox page. It uses the local mock and does not send through Google.
- [ ] Refresh the practice mailbox. The quota display updates from the mock.
- [ ] Disable the mailbox and confirm a campaign will not use it. Enable it again.
- [ ] Delete a practice mailbox you created for the test.
- [ ] For a real mailbox: generate the script while signed in, deploy it at [script.google.com](https://script.google.com) as a web app, **Execute as Me**, **Who has access: Anyone**, and paste the `/exec` URL and the secret. Run **Test connection**. That check asks for quota and does not send mail.
- [ ] A bad URL or a wrong secret shows an error and does not save a connected mailbox.
- [ ] The Gmail meter shows 100 and the Workspace meter shows 1,500. Those numbers are the meter size. The remaining count comes from Google after a refresh. If Google has not been checked, remaining is empty rather than invented.

### Campaigns

- [ ] Create a campaign. Set the subject, from name, reply-to, HTML, and plain text.
- [ ] Preview the message. `{{first name}}` uses the recipient’s first name. `{{offer url}}` uses the offer selected on the campaign.
- [ ] Add people by pasting rows, uploading a CSV, or pasting a public Google Sheet link.
- [ ] An invalid email is reported and not queued.
- [ ] Importing the same address again can skip it or update it, depending on the import choice. Sent rows are not silently wiped.
- [ ] Add people from a contact group.
- [ ] Attach a file, or attach a file from the media library.
- [ ] Save, leave, and reopen the campaign. The content is still there.
- [ ] Duplicate creates a new draft. Restart puts people back on the queue and does not start sending by itself.
- [ ] The screen asks you to confirm before a real start.

### Sending

Do this with a practice mailbox first.

- [ ] Start a small campaign. The queue moves and the activity log records the sends.
- [ ] Pause, then resume.
- [ ] Sequential keeps using one mailbox before the next. Rotation takes one person per mailbox in turn. Parallel waves sends one different person from each selected mailbox, then waits once.
- [ ] Change the wait between seconds and minutes and confirm the gap matches the setting.
- [ ] A failed practice recipient can be retried with **Retry failed**.
- [ ] Stop the app with `Ctrl+C` while a campaign is running, start it again, and confirm the campaign is paused rather than still marked running. Start it again only if you mean to send the remaining practice messages.
- [ ] Only one campaign sends at a time. A second start is refused while one is running.
- [ ] A real mailbox test, if you do one, uses one or two authorized recipients only.

### Audience

- [ ] Add a contact by hand.
- [ ] Import a CSV, an Excel file, a paste, or a public Google Sheet. The preview shows new, existing, invalid, and unsubscribed rows before you save.
- [ ] Create a group and put contacts in it.
- [ ] Edit and delete a contact you created.
- [ ] The same email cannot be stored twice. Two companies also cannot store the same email, because the contact email is unique in the whole database.
- [ ] Add an address to Suppression. It is not added to a new queue. A public unsubscribe link works only when Settings has a public base URL that people on the internet can open. On localhost, use the suppression page instead of a public link.

### Analytics and activity

- [ ] Analytics counts match sends Google, or the practice mock, accepted. Failed sends are separate. The rate is accepted divided by accepted plus failed.
- [ ] The chart shows days that have sends. It does not invent opens or clicks.
- [ ] Activity lists stored events for this company. **All campaigns** shows every event. A single campaign filter hides the others.
- [ ] The notification bell lists real problems only, such as a mailbox error, a mailbox with no quota left, a paused campaign, or failed recipients. An empty company shows “No notifications.”

### Media and offers

- [ ] Upload a real PNG, JPG, WebP, GIF, or PDF under 10 MB. The preview opens inside the app.
- [ ] A file that is not a real image shows “Not a readable image” instead of a broken name.
- [ ] Copy link warns that the link only works while signed in.
- [ ] Create an offer, select it on a campaign, and confirm `{{offer url}}` uses it.
- [ ] Record a conversion with an amount, a date, and a note. Revenue is the sum of those records.
- [ ] Click tracking is not implemented. The offers page says clicks are not tracked.

### Settings

- [ ] Change the default wait, rotation, retries, from name, or reply-to. Create a new campaign and confirm it copies those defaults.
- [ ] Settings apply to the signed-in company. Another company does not inherit them.
- [ ] The primary company can still fall back to older global settings that were saved before companies existed.
- [ ] Download a backup from Settings. The file is the SQLite database. Do not send that file to other people. It contains contacts and campaign history. Restoring it is not a button in the app.

## Troubleshooting

**The app does not start, or Python says a module is missing.**  
Activate `.venv`, then run `python -m pip install -r requirements.txt` again. Use `python3 --version` and confirm it is 3.9 or newer.

**`Address already in use` on port 8765.**  
The app is already running in another terminal. Use that window, or stop it. On macOS:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Stop only the process that is listening on 8765, then start Uvicorn again.

**The sign-in page says the email or password is wrong.**  
There is no default password. If this is a new database, the first screen must be **Create the super admin**. If you already created it, use that same email and password. A suspended user or a user of a suspended company is rejected with a suspension message, not a wrong-password message.

**I am the super admin and I cannot see mailboxes.**  
That is expected. Create a company user for the company you want, sign out, and sign in as that user.

**The page looks like an old version.**  
Reload the browser. Python changes need a restart. HTML changes do not.

**A mailbox test fails.**  
Confirm the web app URL ends in `/exec`, the secret matches the script, and the deployment is **Execute as Me** and **Anyone**. A Google login page in the result means the script is not deployed for anyone to call. The test does not send an email.

**Images or the logo do not load.**  
Confirm the app was started in the project directory and that `static/sendora-logo.png` and `static/favicon.png` exist. Open [http://127.0.0.1:8765/static/sendora-logo.png](http://127.0.0.1:8765/static/sendora-logo.png).

**Another computer cannot open the site.**  
The server listens on `127.0.0.1` only. That is local to one computer. Hosting it for the team requires a reachable address and HTTPS. Do not treat localhost as a shared server.

## Reporting a bug

Use one report per problem. Include:

- Module, such as Campaigns, Mailboxes, or Sign-in.
- Steps to reproduce.
- Expected result.
- Actual result.
- A screenshot or the exact error text.
- Severity: Critical, High, Medium, or Low.

Critical means you cannot sign in, companies can see each other’s data, or mail is sent to the wrong people. High means a main task is blocked. Medium means a task has a workaround. Low means a visual or wording issue.

## Short checklist

- [ ] Fresh install, app opens at http://127.0.0.1:8765
- [ ] Super admin created locally
- [ ] Company and company user created
- [ ] Company user sees only that company
- [ ] Practice mailbox sends, pauses, and resumes
- [ ] Real mailbox tested only with a few authorized recipients, if at all
- [ ] Audience import and suppression checked
- [ ] Analytics and activity match the sends you made
- [ ] Media upload and a manual offer conversion checked
- [ ] Settings saved and visible on a new campaign
