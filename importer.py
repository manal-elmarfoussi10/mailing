from typing import Optional

import csv
import io
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx

EMAIL_HEADERS = {"email", "e mail", "email address", "mail", "recipient", "to"}


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def merge_text(template: str, fields: dict) -> str:
    lookup = {normalize_key(str(key)): "" if val is None else str(val) for key, val in fields.items()}

    def replace(match: re.Match) -> str:
        key = normalize_key(match.group(1))
        if key in lookup:
            return lookup[key]
        return match.group(0)

    return re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", replace, template or "")


def plain_from_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or " "


def parse_csv(text: str) -> tuple[list[tuple[str, str]], int]:
    sample = text.lstrip("\ufeff").strip()
    if not sample:
        return [], 0
    raw_rows = list(csv.reader(io.StringIO(sample)))
    raw_rows = [row for row in raw_rows if any(cell.strip() for cell in row)]
    if not raw_rows:
        return [], 0

    headers = [cell.strip() for cell in raw_rows[0]]
    body = raw_rows[1:]
    if _email_column(headers) is None and "@" in (headers[0] if headers else ""):
        width = max(len(row) for row in raw_rows)
        headers = ["email"] + [f"column {index}" for index in range(2, width + 1)]
        body = raw_rows

    email_index = _email_column(headers)
    if email_index is None:
        raise ValueError("Add a column named email.")

    people: list[tuple[str, str]] = []
    skipped = 0
    seen: set[str] = set()
    for row in body:
        if email_index >= len(row) or not row[email_index].strip():
            skipped += 1
            continue
        email = row[email_index].strip().lower()
        if not _looks_like_email(email) or email in seen:
            skipped += 1
            continue
        seen.add(email)
        fields = {}
        for index, header in enumerate(headers):
            if index == email_index or not header:
                continue
            fields[header] = row[index].strip() if index < len(row) else ""
        fields["email"] = email
        people.append((email, json.dumps(fields)))
    return people, skipped


def fetch_sheet(url: str) -> str:
    export = sheet_csv_url(url)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        response = client.get(export, headers={"Accept": "text/csv"})
    text = response.text
    if response.status_code >= 400 or text.lstrip().startswith("<"):
        raise ValueError(
            "Could not read that sheet. Share it as Anyone with the link, Viewer, then try again."
        )
    if len(text.encode("utf-8")) > 5_000_000:
        raise ValueError("That sheet is larger than 5 MB. Export a smaller CSV and upload that.")
    return text


def sheet_csv_url(url: str) -> str:
    parts = urlparse(url.strip())
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parts.path)
    if not match:
        raise ValueError("Paste a Google Sheets link, not a CSV link.")
    query = parse_qs(parts.query)
    fragment = parse_qs(parts.fragment.replace("gid=", "gid=") if parts.fragment else "")
    gid = "0"
    if "gid" in query and query["gid"]:
        gid = query["gid"][0]
    elif parts.fragment:
        found = re.search(r"gid=(\d+)", parts.fragment)
        if found:
            gid = found.group(1)
        elif fragment.get("gid"):
            gid = fragment["gid"][0]
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv&gid={gid}"


def _email_column(headers: list[str]) -> Optional[int]:
    for index, header in enumerate(headers):
        if normalize_key(header) in EMAIL_HEADERS:
            return index
    return None


def _looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def valid_email(value: str) -> bool:
    return _looks_like_email(value.strip().lower())


def contact_table(text: str) -> dict:
    sample = text.lstrip("\ufeff").strip()
    if not sample:
        return {"rows": [], "invalid": 0, "duplicateInFile": 0, "total": 0}
    raw_rows = [row for row in csv.reader(io.StringIO(sample)) if any(cell.strip() for cell in row)]
    if not raw_rows:
        return {"rows": [], "invalid": 0, "duplicateInFile": 0, "total": 0}
    headers = [cell.strip() for cell in raw_rows[0]]
    body = raw_rows[1:]
    if _email_column(headers) is None and "@" in (headers[0] if headers else ""):
        width = max(len(row) for row in raw_rows)
        headers = ["email"] + [f"column {index}" for index in range(2, width + 1)]
        body = raw_rows
    email_index = _email_column(headers)
    if email_index is None:
        raise ValueError("Add a column named email, or paste one address per line.")
    rows = []
    invalid = 0
    duplicate = 0
    seen: set[str] = set()
    for row in body:
        if email_index >= len(row) or not row[email_index].strip():
            invalid += 1
            continue
        email = row[email_index].strip().lower()
        if not _looks_like_email(email):
            invalid += 1
            continue
        if email in seen:
            duplicate += 1
            continue
        seen.add(email)
        fields = {}
        for index, header in enumerate(headers):
            if index == email_index or not header:
                continue
            fields[header] = row[index].strip() if index < len(row) else ""
        fields["email"] = email
        rows.append({
            "email": email,
            "first_name": _named(fields, "first name", "firstname", "given name"),
            "last_name": _named(fields, "last name", "lastname", "surname"),
            "company": _named(fields, "company", "organization", "organisation"),
            "fields": fields,
        })
    return {"rows": rows, "invalid": invalid, "duplicateInFile": duplicate, "total": len(body)}


def _named(fields: dict, *names: str) -> str:
    wanted = {normalize_key(name) for name in names}
    for key, value in fields.items():
        if normalize_key(str(key)) in wanted:
            return str(value).strip()
    return ""


def xlsx_to_csv(raw: bytes) -> str:
    import zipfile
    from xml.etree import ElementTree as ET

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        book = zipfile.ZipFile(io.BytesIO(raw))
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{ns['m']}}}t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in book.namelist():
            sheets = [name for name in book.namelist() if name.startswith("xl/worksheets/sheet")]
            if not sheets:
                raise ValueError("no sheet")
            sheet_name = sorted(sheets)[0]
        root = ET.fromstring(book.read(sheet_name))
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        raise ValueError("That Excel file could not be read. Save it as CSV and upload that.") from exc
    table = []
    for row in root.findall("m:sheetData/m:row", ns):
        values: dict[int, str] = {}
        for cell in row.findall("m:c", ns):
            letters = "".join(ch for ch in (cell.get("r") or "A") if ch.isalpha())
            index = 0
            for ch in letters:
                index = index * 26 + (ord(ch.upper()) - 64)
            node = cell.find("m:v", ns)
            text = ""
            if node is not None and node.text:
                text = shared[int(node.text)] if cell.get("t") == "s" else node.text
            values[max(0, index - 1)] = text
        if not values:
            continue
        width = max(values) + 1
        table.append([values.get(index, "") for index in range(width)])
    if not table:
        raise ValueError("That workbook has no rows on the first sheet.")
    buffer = io.StringIO()
    csv.writer(buffer).writerows(table)
    return buffer.getvalue()
