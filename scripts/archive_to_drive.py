"""Archive datová schránka messages to a Google Drive folder.

Downloads every received message that is not yet in Drive, and uploads per
message a subfolder ``<dmID> - <subject>`` containing the signed original
(``message.zfo``), all extracted attachments, and ``metadata.json``.

POZOR / WARNING - DELIVERY TRIGGER: listing received messages counts as a
login via the ISDS application interface (event EV13) and legally DELIVERS
every message currently in the box; statutory deadlines start running. Running
this script on a schedule is a deliberate choice to have mail delivered (and
safely archived) immediately instead of waiting for a manual login or the
10-day fiction of delivery. This also closes the 90-day deletion gap. Even
``--dry-run`` lists the received messages and therefore triggers delivery.

Configuration (environment variables):
    ISDS_USERNAME / ISDS_PASSWORD   box credentials
    ISDS_ENV                        test | production (default: test)
    GOOGLE_SERVICE_ACCOUNT_JSON     service-account key: raw JSON or a file path
    GOOGLE_DRIVE_FOLDER_ID          target folder ID, shared with the service
                                    account (Editor role)

Run:  uv run --group drive python scripts/archive_to_drive.py [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from typing import Any

import requests

from agentovka_mcp.archive import safe_filename
from isds_client.client import IsdsClient, IsdsEnvironment
from isds_client.zfo import parse_zfo

_DRIVE_API = "https://www.googleapis.com/drive/v3"
_DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
_FOLDER_MIME = "application/vnd.google-apps.folder"


def _die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _die(f"missing required environment variable {name}")
    return value  # type: ignore[return-value]  # _die never returns


def _drive_session() -> requests.Session:
    """Authenticated session for the Drive REST API (service account)."""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    raw = _require_env("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw.lstrip().startswith("{"):
        info = json.loads(raw)
    else:
        with open(raw) as fh:
            info = json.load(fh)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    creds.refresh(Request())
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {creds.token}"
    return session


def _list_archived_ids(drive: requests.Session, folder_id: str) -> set[str]:
    """Message IDs already archived = leading token of subfolder names."""
    ids: set[str] = set()
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {
            "q": f"'{folder_id}' in parents and mimeType = '{_FOLDER_MIME}' and trashed = false",
            "fields": "nextPageToken, files(name)",
            "pageSize": 1000,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token
        resp = drive.get(f"{_DRIVE_API}/files", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        for f in data.get("files", []):
            ids.add(f["name"].split(" ", 1)[0])
        page_token = data.get("nextPageToken")
        if not page_token:
            return ids


def _create_folder(drive: requests.Session, parent_id: str, name: str) -> str:
    resp = drive.post(
        f"{_DRIVE_API}/files",
        params={"supportsAllDrives": "true", "fields": "id"},
        json={"name": name, "mimeType": _FOLDER_MIME, "parents": [parent_id]},
        timeout=60,
    )
    resp.raise_for_status()
    return str(resp.json()["id"])


def _upload_file(
    drive: requests.Session, parent_id: str, name: str, content: bytes, mime_type: str
) -> None:
    metadata = json.dumps({"name": name, "parents": [parent_id]})
    body = (
        b"--boundary\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        + metadata.encode()
        + b"\r\n--boundary\r\nContent-Type: "
        + mime_type.encode()
        + b"\r\nContent-Transfer-Encoding: base64\r\n\r\n"
        + base64.b64encode(content)
        + b"\r\n--boundary--"
    )
    resp = drive.post(
        _DRIVE_UPLOAD + "&supportsAllDrives=true",
        headers={"Content-Type": "multipart/related; boundary=boundary"},
        data=body,
        timeout=300,
    )
    resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what would be uploaded without touching Drive "
        "(still lists received messages and therefore still triggers delivery, EV13)",
    )
    args = parser.parse_args()

    username = _require_env("ISDS_USERNAME")
    password = _require_env("ISDS_PASSWORD")
    env = IsdsEnvironment(os.environ.get("ISDS_ENV", "test").lower())
    folder_id = _require_env("GOOGLE_DRIVE_FOLDER_ID") if not args.dry_run else ""

    client = IsdsClient(username, password, environment=env)

    drive: requests.Session | None = None
    archived: set[str] = set()
    if not args.dry_run:
        drive = _drive_session()
        archived = _list_archived_ids(drive, folder_id)

    # DELIVERY-TRIGGERING (EV13): the whole point of this scheduled archive.
    messages = client.get_list_of_received_messages(limit=1000)
    new = [m for m in messages if m.message_id not in archived]
    print(f"received messages: {len(messages)}, already archived: {len(archived)}, new: {len(new)}")

    failures: list[str] = []
    for msg in new:
        label = f"{msg.message_id} - {msg.subject or '(bez předmětu)'}"
        if args.dry_run:
            print(f"would archive: {label}")
            continue
        assert drive is not None
        try:
            zfo = client.signed_message_download(msg.message_id)
            parsed = parse_zfo(zfo)
            subfolder = _create_folder(
                drive, folder_id, safe_filename(label)[:120] or msg.message_id
            )
            _upload_file(drive, subfolder, "message.zfo", zfo, "application/pkcs7-signature")
            for f in parsed.files:
                if f.content:
                    _upload_file(
                        drive,
                        subfolder,
                        safe_filename(f.file_name or "attachment.bin"),
                        f.content,
                        f.mime_type or "application/octet-stream",
                    )
            envelope = parsed.envelope.model_dump(mode="json", by_alias=True, exclude_none=True)
            _upload_file(
                drive,
                subfolder,
                "metadata.json",
                json.dumps(envelope, ensure_ascii=False, indent=2).encode(),
                "application/json",
            )
            print(f"archived: {label}")
        except Exception as exc:  # one bad message must not stop the rest
            failures.append(f"{label}: {exc}")
            print(f"FAILED: {label}: {exc}", file=sys.stderr)

    if failures:
        _die(f"{len(failures)} message(s) failed to archive")
    print("done")


if __name__ == "__main__":
    main()
