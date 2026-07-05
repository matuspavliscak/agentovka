"""Agentovka MCP server.

A safety-first MCP wrapper over :mod:`isds_client`. Tools are grouped into three
classes, reflected in their names, descriptions and annotations:

  Class A - safe, no legal consequences (searching, own-box info, local archive,
            deadline arithmetic). readOnlyHint = True.
  Class B - DELIVERY-TRIGGERING. Reading the received list or downloading a
            received message counts as a login via the application interface
            (event EV13) and legally delivers every message in the box, starting
            statutory deadlines. These tools require the caller to pass
            acknowledge_delivery_trigger=True.
  Class C - legal act: sending a data message. Guarded by dry_run (default True)
            AND the AGENTOVKA_ALLOW_SEND=true environment variable.

Configuration comes only from environment variables (see agentovka_mcp.config);
credentials are never accepted as tool parameters. The server never polls the
mailbox on its own - every ISDS call happens only in response to a tool call.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from agentovka_mcp.archive import Archive, UnsafeIdentifierError
from agentovka_mcp.config import Settings, load_settings
from agentovka_mcp.deadlines import describe_deadline
from isds_client.client import IsdsClient
from isds_client.errors import IsdsError
from isds_client.zfo import ZfoParseError, parse_zfo

_DELIVERY_WARNING = (
    "POZOR / WARNING: Volání tohoto nástroje se v ISDS počítá jako přihlášení a "
    "způsobí DORUČENÍ všech dodaných zpráv (kód EV13) - začnou běžet zákonné "
    "lhůty (odvolání atd.). Calling this tool counts as a login to ISDS and "
    "legally DELIVERS all messages currently in the box (event EV13); statutory "
    "deadlines start running. There is no way to peek without this effect."
)

mcp = FastMCP(
    "agentovka",
    instructions=(
        "Agentovka provides agentic access to Czech data boxes (datové schránky / "
        "ISDS). Tools are grouped by legal impact. Class A tools are safe. Class B "
        "tools (list_received_messages, download_message) TRIGGER LEGAL DELIVERY of "
        "all messages in the box and require acknowledge_delivery_trigger=True - "
        "never call them to merely 'check' for mail unless the user understands "
        "that deadlines will start. Class C (send_message) performs a legal act "
        "and is disabled unless the operator sets AGENTOVKA_ALLOW_SEND=true. "
        "Prefer reading from the local archive when possible."
    ),
)


# Lazily-initialised, process-wide singletons (env is read at first use).
_settings: Settings | None = None
_client: IsdsClient | None = None
_archive: Archive | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def get_client() -> IsdsClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = IsdsClient(s.username, s.password, environment=s.environment)
    return _client


def get_archive() -> Archive:
    global _archive
    if _archive is None:
        s = get_settings()
        _archive = Archive(s.archive_dir, environment=s.environment.value)
    return _archive


def _envelope_dict(env: Any) -> dict[str, Any]:
    return env.model_dump(mode="json", by_alias=False, exclude_none=True)


# ======================================================================
# Class A - safe (no legal consequences)
# ======================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "SAFE (třída A). Vyhledá datovou schránku adresáta pro účely odesílání. "
        "Nezpůsobuje doručení žádných zpráv. Search for a recipient's data box by "
        "box ID (7 chars), IČ (8 digits) or name. Does NOT trigger delivery."
    ),
)
def search_databox(
    query: Annotated[str, Field(description="Box ID, IČ, or organisation/person name")],
) -> dict[str, Any]:
    try:
        boxes = get_client().find_databox(query)
    except IsdsError as exc:
        return {"error": str(exc)}
    return {"results": [b.model_dump(mode="json", exclude_none=True) for b in boxes]}


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "SAFE (třída A). Vrátí informace o vlastní datové schránce. Does NOT "
        "trigger delivery. Returns info about your own data box."
    ),
)
def get_databox_info() -> dict[str, Any]:
    try:
        return {"owner": get_client().get_owner_info().model_dump(mode="json", exclude_none=True)}
    except IsdsError as exc:
        return {"error": str(exc)}


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    description=(
        "SAFE (třída A). Vypíše zprávy uložené v LOKÁLNÍM archivu (žádné volání "
        "ISDS). Lists messages already stored in the local archive. No ISDS call, "
        "no delivery."
    ),
)
def list_archived_messages(
    limit: Annotated[int, Field(description="Max messages to return", ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    msgs = get_archive().list_messages(limit=limit)
    return {
        "messages": [
            {
                "message_id": m.message_id,
                "subject": m.subject,
                "sender": m.sender,
                "delivery_time": m.delivery_time,
                "acceptance_time": m.acceptance_time,
                "archived_at": m.archived_at,
            }
            for m in msgs
        ]
    }


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    description=(
        "SAFE (třída A). Přečte jednu zprávu z LOKÁLNÍHO archivu včetně metadat a "
        "seznamu příloh. Reads one message from the local archive. No ISDS call."
    ),
)
def read_archived_message(
    message_id: Annotated[str, Field(description="ISDS message ID (dmID)")],
) -> dict[str, Any]:
    data = get_archive().get(message_id)
    if data is None:
        return {"error": f"Message {message_id} is not in the local archive."}
    return data


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    description=(
        "SAFE (třída A). Fulltextové vyhledávání v LOKÁLNÍM archivu (předmět, "
        "odesílatel, text příloh). Full-text search over the local archive."
    ),
)
def search_archive(
    query: Annotated[str, Field(description="Full-text query (SQLite FTS5 syntax)")],
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    msgs = get_archive().search(query, limit=limit)
    return {
        "messages": [
            {"message_id": m.message_id, "subject": m.subject, "sender": m.sender} for m in msgs
        ]
    }


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    description=(
        "SAFE (třída A). Spočítá den fikce doručení (D+10 s posunem na nejbližší "
        "pracovní den) dle § 17 odst. 4 zák. č. 300/2008 Sb. Computes the "
        "fiction-of-delivery date from the date a message was delivered to the box."
    ),
)
def get_delivery_deadline(
    delivery_date: Annotated[
        str, Field(description="Date the message was delivered to the box (dodání), ISO YYYY-MM-DD")
    ],
    today: Annotated[
        str | None, Field(description="Reference date (ISO); defaults to system date")
    ] = None,
) -> dict[str, Any]:
    try:
        delivered = date.fromisoformat(delivery_date)
    except ValueError:
        return {"error": "delivery_date must be ISO format YYYY-MM-DD"}
    ref = date.fromisoformat(today) if today else date.today()
    return describe_deadline(delivered, today=ref)


# ======================================================================
# Class B - DELIVERY-TRIGGERING (event EV13)
# ======================================================================


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    description=(
        "TŘÍDA B - SPOUŠTÍ DORUČENÍ. " + _DELIVERY_WARNING + " Lists messages "
        "delivered to your box from ISDS. Requires acknowledge_delivery_trigger=True."
    ),
)
def list_received_messages(
    acknowledge_delivery_trigger: Annotated[
        bool,
        Field(
            description=(
                "Must be True to proceed. Setting this to True acknowledges that "
                "the call legally delivers all messages in the box (EV13) and "
                "starts statutory deadlines."
            )
        ),
    ] = False,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    if not acknowledge_delivery_trigger:
        return {
            "error": "delivery_trigger_not_acknowledged",
            "explanation": _DELIVERY_WARNING,
            "how_to_proceed": (
                "Call again with acknowledge_delivery_trigger=true if you intend "
                "to trigger delivery."
            ),
        }
    try:
        msgs = get_client().get_list_of_received_messages(limit=limit)
    except IsdsError as exc:
        return {"error": str(exc)}
    return {
        "delivery_triggered": True,
        "messages": [_envelope_dict(m) for m in msgs],
    }


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
    description=(
        "TŘÍDA B - SPOUŠTÍ DORUČENÍ. " + _DELIVERY_WARNING + " Downloads the signed "
        "message (ZFO) from ISDS, stores it in the local archive, extracts "
        "attachments, and returns metadata + text. Requires acknowledge_delivery_trigger=True."
    ),
)
def download_message(
    message_id: Annotated[str, Field(description="ISDS message ID (dmID)")],
    acknowledge_delivery_trigger: Annotated[
        bool, Field(description="Must be True; acknowledges EV13 delivery trigger.")
    ] = False,
) -> dict[str, Any]:
    if not acknowledge_delivery_trigger:
        return {
            "error": "delivery_trigger_not_acknowledged",
            "explanation": _DELIVERY_WARNING,
            "how_to_proceed": (
                "Call again with acknowledge_delivery_trigger=true to download and archive."
            ),
        }
    try:
        zfo_bytes = get_client().signed_message_download(message_id)
    except IsdsError as exc:
        return {"error": str(exc)}

    try:
        parsed = parse_zfo(zfo_bytes)
    except ZfoParseError as exc:
        return {"error": f"could not parse downloaded ZFO: {exc}"}

    archive = get_archive()
    try:
        msg_dir = archive.store(parsed.envelope, zfo_bytes, parsed.files, parsed.events)
    except UnsafeIdentifierError as exc:
        return {"error": f"refusing to archive message with unsafe id: {exc}"}

    text_previews = []
    for f in parsed.files:
        if (
            f.content
            and f.mime_type
            and (f.mime_type.startswith("text/") or f.mime_type.endswith("xml"))
        ):
            text_previews.append(
                {
                    "file_name": f.file_name,
                    "text": f.content.decode("utf-8", errors="replace")[:20000],
                }
            )

    return {
        "delivery_triggered": True,
        "archived_to": str(msg_dir),
        "envelope": _envelope_dict(parsed.envelope),
        "attachments": [
            {
                "file_name": f.file_name,
                "mime_type": f.mime_type,
                "size": f.size,
                "meta_type": f.meta_type,
            }
            for f in parsed.files
        ],
        "text_attachments": text_previews,
    }


# ======================================================================
# Class A/B boundary - sent messages & delivery receipts
# ======================================================================
# Listing SENT messages and reading delivery receipts (doručenky) of one's own
# sent messages do NOT access the received store and therefore do NOT trigger
# delivery of received messages. See docs/delivery-semantics.md. Classified A.


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "SAFE (třída A). Vypíše ODESLANÉ zprávy. Čtení odeslaných zpráv nespouští "
        "doručení dodaných zpráv (netýká se přijaté schránky). Lists your SENT "
        "messages; does not trigger delivery of received messages."
    ),
)
def list_sent_messages(
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    try:
        msgs = get_client().get_list_of_sent_messages(limit=limit)
    except IsdsError as exc:
        return {"error": str(exc)}
    return {"messages": [_envelope_dict(m) for m in msgs]}


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
    description=(
        "SAFE (třída A). Vrátí doručenku (události doručení) zprávy. Reading a "
        "delivery receipt of your own message does not trigger delivery of your "
        "received messages. Returns the delivery receipt (events) for a message."
    ),
)
def get_delivery_receipt(
    message_id: Annotated[str, Field(description="ISDS message ID (dmID)")],
) -> dict[str, Any]:
    try:
        info = get_client().get_delivery_info(message_id)
    except IsdsError as exc:
        return {"error": str(exc)}
    return {
        "envelope": _envelope_dict(info.envelope),
        "events": [
            {
                "time": e.time.isoformat() if isinstance(e.time, datetime) else e.time,
                "description": e.description,
            }
            for e in info.events
        ],
    }


# ======================================================================
# Class C - legal act: sending a message
# ======================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
    ),
    description=(
        "TŘÍDA C - PRÁVNÍ ÚKON. Odeslání datové zprávy je podání/soukromoprávní "
        "jednání. Ve výchozím stavu pouze náhled (dry_run=true). Skutečné odeslání "
        "vyžaduje dry_run=false A proměnnou prostředí AGENTOVKA_ALLOW_SEND=true. "
        "Sends a data message (a legal act). Defaults to a preview; real sending "
        "requires dry_run=false AND AGENTOVKA_ALLOW_SEND=true."
    ),
)
def send_message(
    recipient_id: Annotated[str, Field(description="Recipient data box ID (7 chars)")],
    subject: Annotated[str, Field(description="Message subject (dmAnnotation)")],
    attachments: Annotated[
        list[dict[str, str]],
        Field(
            description=(
                "List of attachments: each {file_name, mime_type, content_base64, "
                "meta_type?}. Exactly one attachment should have meta_type='main'."
            )
        ),
    ],
    dry_run: Annotated[
        bool, Field(description="If True (default) only previews; does not send.")
    ] = True,
    to_hands: Annotated[str | None, Field(description="Optional 'to hands of' (k rukám)")] = None,
) -> dict[str, Any]:
    settings = get_settings()

    # Resolve each attachment's meta_type. ISDS requires EXACTLY ONE 'main'
    # file. A lone attachment with no meta_type is the main document; with
    # several files, extra ones default to 'enclosure' and the caller must mark
    # exactly one 'main' explicitly.
    default_meta = "main" if len(attachments) == 1 else "enclosure"
    resolved_meta = [a.get("meta_type") or default_meta for a in attachments]
    main_count = resolved_meta.count("main")

    preview = {
        "recipient_id": recipient_id,
        "subject": subject,
        "environment": settings.environment.value,
        "attachments": [
            {
                "file_name": a.get("file_name"),
                "mime_type": a.get("mime_type"),
                "meta_type": meta,
            }
            for a, meta in zip(attachments, resolved_meta, strict=True)
        ],
        "to_hands": to_hands,
    }

    if not attachments or main_count != 1:
        return {
            "error": "invalid_attachments",
            "explanation": (
                "ISDS requires exactly one attachment with meta_type='main'. "
                f"Found {main_count} main attachment(s) among {len(attachments)}. "
                "Mark exactly one attachment meta_type='main'; others 'enclosure'."
            ),
            "would_send": preview,
        }

    if dry_run:
        return {
            "dry_run": True,
            "would_send": preview,
            "note": "Set dry_run=false and AGENTOVKA_ALLOW_SEND=true to actually send.",
        }

    if not settings.allow_send:
        return {
            "error": "sending_disabled",
            "explanation": (
                "Sending is disabled. Set AGENTOVKA_ALLOW_SEND=true in the server "
                "environment to enable real sending."
            ),
            "would_send": preview,
        }

    try:
        files = []
        for a, meta in zip(attachments, resolved_meta, strict=True):
            content_b64 = a.get("content_base64")
            if not content_b64:
                return {"error": f"attachment {a.get('file_name')!r} is missing content_base64"}
            files.append(
                {
                    "file_name": a["file_name"],
                    "mime_type": a["mime_type"],
                    "meta_type": meta,
                    "content": base64.b64decode(content_b64),
                }
            )
        new_id = get_client().create_message(recipient_id, subject, files, to_hands=to_hands)
    except IsdsError as exc:
        return {"error": str(exc), "would_send": preview}
    except Exception as exc:
        return {"error": f"failed to send: {exc}", "would_send": preview}

    return {"sent": True, "message_id": new_id, "environment": settings.environment.value}


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
