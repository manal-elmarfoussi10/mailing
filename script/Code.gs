/**
 * One deployment per Gmail mailbox.
 *
 * 1. script.google.com → New project → paste this file.
 * 2. Project settings → Script properties → add SECRET = a long random string.
 * 3. Deploy → New deployment → Web app.
 *    Execute as: Me
 *    Who has access: Anyone
 * 4. Put that web app URL and the same SECRET into the mailing app.
 *
 * The app sends one email per request and reads the daily quota.
 * Deploy this once per account, free Gmail or Google Workspace.
 * Free Gmail is about 100 recipients per day. Workspace is about 1,500.
 * The quota resets around midnight Pacific time.
 */
var PROP_KEY = "SECRET";

function doGet(e) {
  try {
    var token = e && e.parameter && e.parameter.token ? String(e.parameter.token) : "";
    if (!allowed_(token)) return out_({ ok: false, code: "AUTH", message: "Bad token" });
    return out_(snapshot_());
  } catch (err) {
    return out_({ ok: false, code: "TRANSIENT", message: String(err) });
  }
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return out_({ ok: false, code: "REJECTED", message: "Missing JSON body" });
    }
    var body = JSON.parse(e.postData.contents);
    if (!allowed_(body.token)) return out_({ ok: false, code: "AUTH", message: "Bad token" });
    if (body.action === "quota") return out_(snapshot_());
    return out_(sendOne_(body));
  } catch (err) {
    return out_({
      ok: false,
      code: codeFor_(err),
      message: String(err),
      quotaRemaining: quotaOrNull_(),
      mailbox: me_()
    });
  }
}

function sendOne_(body) {
  var left = MailApp.getRemainingDailyQuota();
  var mailbox = me_();
  if (left <= 0) {
    return {
      ok: false,
      code: "QUOTA",
      message: "Daily quota is used up",
      quotaRemaining: 0,
      mailbox: mailbox
    };
  }

  var to = String(body.to || "").trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
    return {
      ok: false,
      code: "REJECTED",
      message: "Invalid recipient",
      quotaRemaining: left,
      mailbox: mailbox
    };
  }

  var html = body.html ? String(body.html) : "";
  var text = body.text ? String(body.text) : "";
  if (!html && !text) {
    return {
      ok: false,
      code: "REJECTED",
      message: "Message is empty",
      quotaRemaining: left,
      mailbox: mailbox
    };
  }

  var options = {
    to: to,
    subject: String(body.subject || ""),
    body: text || " "
  };
  if (html) options.htmlBody = html;
  if (body.fromName) options.name = String(body.fromName);
  if (body.replyTo) options.replyTo = String(body.replyTo);
  var files = attachments_(body);
  if (files.length) options.attachments = files;

  MailApp.sendEmail(options);
  return {
    ok: true,
    code: "SENT",
    quotaRemaining: MailApp.getRemainingDailyQuota(),
    mailbox: mailbox
  };
}

function attachments_(body) {
  var list = body.attachments || [];
  var files = [];
  for (var i = 0; i < list.length; i++) {
    var item = list[i] || {};
    if (!item.base64) continue;
    var bytes = Utilities.base64Decode(String(item.base64));
    files.push(
      Utilities.newBlob(
        bytes,
        item.mimeType || "application/octet-stream",
        item.filename || "attachment"
      )
    );
  }
  return files;
}

function snapshot_() {
  return {
    ok: true,
    code: "OK",
    quotaRemaining: MailApp.getRemainingDailyQuota(),
    mailbox: me_()
  };
}

function allowed_(token) {
  var secret = PropertiesService.getScriptProperties().getProperty(PROP_KEY);
  if (!secret || !token) return false;
  return String(token) === String(secret);
}

function me_() {
  try {
    return Session.getEffectiveUser().getEmail();
  } catch (err) {
    return "";
  }
}

function quotaOrNull_() {
  try {
    return MailApp.getRemainingDailyQuota();
  } catch (err) {
    return null;
  }
}

function codeFor_(err) {
  var text = String(err);
  if (/for one day|sending quota|Daily quota|limit exceeded/i.test(text)) return "QUOTA";
  if (/too many times|rate limit|try again later|Service invoked/i.test(text)) return "RATE_LIMIT";
  if (/Invalid (email|addresses)|Address unavailable/i.test(text)) return "REJECTED";
  return "TRANSIENT";
}

function out_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(
    ContentService.MimeType.JSON
  );
}
