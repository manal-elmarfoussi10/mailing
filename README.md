# Sendora

Sendora runs on your computer. It sends campaigns from mailboxes you connect with Google Apps Script. It does not store a Google password.

## Install

```bash
git clone https://github.com/manal-elmarfoussi10/mailing.git
cd mailing
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

You need Python 3.9 or newer. You do not need Node.js or a database server.

## Start

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8765
```

Leave that terminal open. Open [http://127.0.0.1:8765](http://127.0.0.1:8765). Stop the app with `Ctrl+C`.

## First sign-in

1. The first visit asks you to create the super admin. Enter a name, email, and a password of at least 8 characters.
2. In Administration, create a company and its first user.
3. Sign out, then sign in as that company user.

The super admin manages companies and users. The company user is the account that opens mailboxes, campaigns, and contacts. There is no shared password in this repository.

## Use the app

1. Add a mailbox. A practice mailbox stays on this computer and does not send real mail. A real mailbox needs an Apps Script web app deployed as **Execute as Me** and **Anyone**. Paste the `/exec` URL and the secret, then run **Test connection**.
2. Create a campaign. Write the subject and message, add people, and pick the mailboxes.
3. Start the campaign. You can pause, resume, retry failed recipients, or restart the queue.
4. Import contacts from a CSV, Excel file, paste, or public Google Sheet. Groups, tags, and suppression live under Audience.
5. Check Analytics and Activity for accepted sends and failures. An accepted send means Google took the message. It does not prove the message reached the inbox.

Only one campaign sends at a time. One Apps Script call sends one email.
