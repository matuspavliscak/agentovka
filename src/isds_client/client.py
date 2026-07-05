"""SOAP client for the ISDS (datové schránky) application interface.

Built on :mod:`zeep`. The WSDL/XSD files are bundled under ``wsdl/`` (taken from
CZ.NIC's dslib, which distributes them for exactly this purpose); the SOAP
endpoint address is overridden per environment because the bundled WSDLs point
only at production.

Authentication is HTTP Basic over TLS ("login by username and password"), which
per the ISDS Provozní řád (Aplikační rozhraní) any box user may use without
prior registration.

DELIVERY SEMANTICS - read docs/delivery-semantics.md. Operations that touch the
*received* message store legally deliver everything in the box:

  * ``get_list_of_received_messages`` and any download of a received message
    (``message_envelope_download``, ``message_download``, ``signed_message_download``)
    count as a login via the application interface (event EV13) and mark all
    "delivered to box" (dodaná) messages as legally delivered (doručená) -
    statutory deadlines start running.

Operations on *sent* messages and on delivery receipts of one's own sent
messages (``get_list_of_sent_messages``, ``get_delivery_info``,
``get_signed_delivery_info``) do not access the recipient's received store and
therefore do not trigger delivery of received messages.
"""

from __future__ import annotations

import enum
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import resources
from typing import Any

from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
from zeep import xsd as zeep_xsd
from zeep.transports import Transport

from isds_client.errors import IsdsAuthError, IsdsResponseError
from isds_client.models import (
    DataBox,
    DeliveryEvent,
    DeliveryInfo,
    MessageEnvelope,
    MessageStatus,
    OwnerInfo,
)


class IsdsEnvironment(enum.Enum):
    """ISDS environment. TEST is the default everywhere in Agentovka."""

    TEST = "test"
    PRODUCTION = "production"


# Basic-auth (username + password) SOAP hosts.
# Production is documented in the Provozní řád ISDS; the public test environment
# is datovka-test.gov.cz (formerly czebox.cz), reachable with the same paths.
_HOSTS: dict[IsdsEnvironment, str] = {
    IsdsEnvironment.PRODUCTION: "https://ws1.mojedatovaschranka.cz",
    IsdsEnvironment.TEST: "https://ws1.czebox.cz",
}

# Per-service URL path suffix under /DS/ (matches the WSDL <soap:address> paths
# and libisds/libdatovka service routing).
_SERVICE_PATH: dict[str, str] = {
    "db_access": "DsManage",
    "db_search": "df",
    "dm_info": "dx",
    "dm_operations": "dz",
}

# Box types queried by a name search (dbType is mandatory for name searches).
_SEARCH_DB_TYPES: tuple[str, ...] = ("OVM", "PO", "PFO", "FO")

# Element sequence of tDbOwnerInfo in dbTypes.xsd. Every element is required
# by the schema, so FindDataBox requests must name each one - filled with a
# value or explicitly skipped (zeep xsd.SkipValue).
_DB_OWNER_INFO_ELEMENTS: tuple[str, ...] = (
    "dbID",
    "dbType",
    "ic",
    "pnFirstName",
    "pnMiddleName",
    "pnLastName",
    "pnLastNameAtBirth",
    "firmName",
    "biDate",
    "biCity",
    "biCounty",
    "biState",
    "adCity",
    "adStreet",
    "adNumberInStreet",
    "adNumberInMunicipality",
    "adZipCode",
    "adState",
    "adUnstruct",
    "nationality",
    "email",
    "telNumber",
    "identifier",
    "registryCode",
    "dbState",
    "dbEffectiveOVM",
    "dbOpenAddressing",
)

_WSDL_FILE: dict[str, str] = {
    "db_access": "db_access.wsdl",
    "db_search": "db_search.wsdl",
    "dm_info": "dm_info.wsdl",
    "dm_operations": "dm_operations.wsdl",
}


def _wsdl_path(name: str) -> str:
    return str(resources.files("isds_client.wsdl").joinpath(_WSDL_FILE[name]))


class _IsdsTransport(Transport):
    """Transport that turns HTTP 401/403 into IsdsAuthError.

    The ISDS servers answer bad credentials with 401 and an XHTML error page,
    not a SOAP fault - zeep would otherwise surface it as an unhelpful
    ``Fault('Unknown fault occured')``.
    """

    def post_xml(self, address: str, envelope: Any, headers: dict[str, str]) -> Any:
        response = super().post_xml(address, envelope, headers)
        if response.status_code in (401, 403):
            raise IsdsAuthError(
                f"ISDS rejected the credentials (HTTP {response.status_code}). "
                "Check ISDS_USERNAME/ISDS_PASSWORD and ISDS_ENV."
            )
        return response


def _check_status(code: str | None, message: str | None) -> None:
    """Raise on a non-OK ISDS status code (0000 = OK, 0001 = deferred/accepted)."""
    if code in (None, "0000", "0001"):
        return
    raise IsdsResponseError(code or "????", message or "")


def _check_dm(resp: Any) -> None:
    """Check the dmStatus envelope of a dm_* service response."""
    _check_status(_get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage"))


def _check_db(resp: Any) -> None:
    """Check the dbStatus envelope of a db_* service response."""
    _check_status(_get(resp, "dbStatus", "dbStatusCode"), _get(resp, "dbStatus", "dbStatusMessage"))


class IsdsClient:
    """Thin, typed wrapper over the ISDS SOAP services.

    Usable standalone::

        client = IsdsClient(username="...", password="...",
                            environment=IsdsEnvironment.TEST)
        info = client.get_owner_info()
    """

    def __init__(
        self,
        username: str,
        password: str,
        environment: IsdsEnvironment = IsdsEnvironment.TEST,
        *,
        timeout: int = 30,
        session: Session | None = None,
    ) -> None:
        self.environment = environment
        self._host = _HOSTS[environment]
        session = session or Session()
        session.auth = HTTPBasicAuth(username, password)
        transport = _IsdsTransport(session=session, timeout=timeout, operation_timeout=timeout)
        self._settings = Settings(strict=False, xml_huge_tree=True, raw_response=False)
        self._transport = transport
        self._clients: dict[str, Client] = {}

    # -- plumbing --------------------------------------------------------

    def _client(self, service: str) -> Client:
        if service not in self._clients:
            client = Client(_wsdl_path(service), transport=self._transport, settings=self._settings)
            client.service._binding_options["address"] = f"{self._host}/DS/{_SERVICE_PATH[service]}"
            self._clients[service] = client
        return self._clients[service]

    def _call(self, service: str, operation: str, **kwargs: Any) -> Any:
        # Auth failures (401/403) are raised as IsdsAuthError by _IsdsTransport
        # before zeep ever parses the response; other transport errors propagate.
        return getattr(self._client(service).service, operation)(**kwargs)

    # -- class A: no legal consequences ---------------------------------

    def get_owner_info(self) -> OwnerInfo:
        """GetOwnerInfoFromLogin - info about the authenticated box. Safe."""
        # The request schema (tDummyInput) requires a dbDummy string element;
        # ISDS ignores its value but the serializer must include it.
        resp = self._call("db_access", "GetOwnerInfoFromLogin", dbDummy="")
        _check_db(resp)
        owner = resp["dbOwnerInfo"]
        # FO/PFO boxes carry the owner's name in pnFirstName/pnLastName;
        # firmName is only set for PO/OVM boxes.
        person_name = " ".join(
            p
            for p in (
                _get(owner, "pnFirstName"),
                _get(owner, "pnMiddleName"),
                _get(owner, "pnLastName"),
            )
            if p and p.strip()
        )
        return OwnerInfo(
            dbID=owner["dbID"],
            dbType=str(owner["dbType"]) if owner["dbType"] is not None else None,
            name=person_name or None,
            firmName=owner["firmName"],
            ic=owner["ic"],
            dbState=owner["dbState"],
            dbOpenAddressing=owner["dbOpenAddressing"],
        )

    def find_databox(self, query: str) -> list[DataBox]:
        """FindDataBox - search for a recipient box. Safe (no delivery trigger).

        Heuristics: an 8-digit query searches by IČ; a 7-char lowercase
        alphanumeric query is first tried as a box ID and, if that finds
        nothing, retried as a name (7-letter names are legal too); anything
        else searches by name.
        """
        q = query.strip()
        if re.fullmatch(r"\d{8}", q):
            return self._find_databox_by({"ic": q})
        if re.fullmatch(r"[a-z0-9]{7}", q):
            try:
                boxes = self._find_databox_by({"dbID": q})
            except IsdsResponseError:
                boxes = []
            if boxes:
                return boxes
        # A name search requires dbType (ISDS error 1101 without it), so query
        # every box type and merge, deduplicating by box ID. The typed queries
        # are independent, so they run concurrently (requests' connection pool
        # is thread-safe; the zeep client is pre-built before the threads start).
        self._client("db_search")

        def _search_type(db_type: str) -> list[DataBox] | IsdsResponseError:
            try:
                return self._find_databox_by({"firmName": q, "dbType": db_type})
            except IsdsResponseError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=len(_SEARCH_DB_TYPES)) as pool:
            results = list(pool.map(_search_type, _SEARCH_DB_TYPES))

        found: dict[str, DataBox] = {}
        for result in results:
            if isinstance(result, IsdsResponseError):
                continue
            for box in result:
                if box.box_id and box.box_id not in found:
                    found[box.box_id] = box
        if not found and all(isinstance(r, IsdsResponseError) for r in results):
            # Every typed query failed with a real ISDS error ("no match" is
            # status 0002 and maps to an empty list, not an exception), so
            # surface the failure instead of a false "no box found".
            raise next(r for r in results if isinstance(r, IsdsResponseError))
        return list(found.values())

    def _find_databox_by(self, owner_info: dict[str, Any]) -> list[DataBox]:
        # The live ISDS parser rejects xsi:nil placeholders for unused search
        # fields (error 2004), so send only the filled elements and skip the
        # rest of the tDbOwnerInfo sequence entirely (as libisds does).
        payload = {
            name: owner_info.get(name, zeep_xsd.SkipValue) for name in _DB_OWNER_INFO_ELEMENTS
        }
        resp = self._call("db_search", "FindDataBox", dbOwnerInfo=payload)
        # FindDataBox-specific statuses: 0002 = no box matches (an empty
        # result, not an error); 0000/0001/0003 carry data (0003 means the
        # server truncated the result list).
        code = _get(resp, "dbStatus", "dbStatusCode")
        if code == "0002":
            return []
        if code not in ("0000", "0001", "0003"):
            raise IsdsResponseError(code or "????", _get(resp, "dbStatus", "dbStatusMessage") or "")
        boxes = _unwrap_repeated(resp["dbResults"], "dbOwnerInfo")
        return [_to_databox(b) for b in boxes]

    def get_list_of_sent_messages(
        self,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        status_filter: int = -1,
        offset: int = 1,
        limit: int = 100,
    ) -> list[MessageEnvelope]:
        """GetListOfSentMessages - list your OWN sent messages.

        Does not access the received store, so it does not trigger delivery of
        received messages. status_filter -1 means all message states.
        """
        resp = self._call(
            "dm_info",
            "GetListOfSentMessages",
            dmFromTime=from_time,
            dmToTime=to_time,
            dmSenderOrgUnitNum=None,
            dmStatusFilter=str(status_filter),
            dmOffset=offset,
            dmLimit=limit,
        )
        return _records_to_envelopes(resp)

    def get_delivery_info(self, message_id: str) -> DeliveryInfo:
        """GetDeliveryInfo - delivery receipt (doručenka) of a message. Not signed.

        For sent messages this reports the recipient's delivery events and does
        not trigger delivery of your received messages.
        """
        resp = self._call("dm_info", "GetDeliveryInfo", dmID=message_id)
        _check_dm(resp)
        returned = resp["dmDelivery"]
        return _to_delivery_info(returned)

    def get_signed_delivery_info(self, message_id: str) -> bytes:
        """GetSignedDeliveryInfo - CMS-signed delivery receipt (ZFO). Returns raw bytes."""
        resp = self._call("dm_info", "GetSignedDeliveryInfo", dmID=message_id)
        _check_dm(resp)
        return bytes(resp["dmSignature"])

    # -- class B: triggers delivery (EV13) ------------------------------

    def get_list_of_received_messages(
        self,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        status_filter: int = -1,
        offset: int = 1,
        limit: int = 100,
    ) -> list[MessageEnvelope]:
        """GetListOfReceivedMessages - DELIVERY-TRIGGERING.

        Reading the received list counts as a login via the application
        interface (EV13) and legally delivers all messages currently in the box.
        status_filter -1 means all message states.
        """
        resp = self._call(
            "dm_info",
            "GetListOfReceivedMessages",
            dmFromTime=from_time,
            dmToTime=to_time,
            dmRecipientOrgUnitNum=None,
            dmStatusFilter=str(status_filter),
            dmOffset=offset,
            dmLimit=limit,
        )
        return _records_to_envelopes(resp)

    def signed_message_download(self, message_id: str) -> bytes:
        """SignedMessageDownload - DELIVERY-TRIGGERING. Returns the raw signed ZFO bytes."""
        resp = self._call("dm_operations", "SignedMessageDownload", dmID=message_id)
        _check_dm(resp)
        return bytes(resp["dmSignature"])

    def mark_message_as_downloaded(self, message_id: str) -> None:
        """MarkMessageAsDownloaded - flags a message as downloaded (removes the *new* flag)."""
        resp = self._call("dm_info", "MarkMessageAsDownloaded", dmID=message_id)
        _check_dm(resp)

    # -- class C: legal act (write) -------------------------------------

    def create_message(
        self,
        recipient_id: str,
        subject: str,
        files: list[dict[str, Any]],
        *,
        to_hands: str | None = None,
        sender_ref_number: str | None = None,
        message_type: str | None = None,
    ) -> str:
        """CreateMessage - sends a data message (a legal act). Returns the new dmID.

        ``files`` is a list of ``{"file_name", "mime_type", "content"(bytes),
        "meta_type"}`` dicts; exactly one file must have meta_type "main".

        ``message_type`` is the dmType envelope attribute: "V" (or None) for a
        veřejná DZ - only valid when the recipient is an OVM - and "K" for a
        poštovní datová zpráva to a private-law recipient (free of charge for
        the sender since 1 Jan 2022, Act No. 261/2021 Coll.). Sending to a
        non-OVM box without "K" fails with ISDS error 1205.
        """
        if message_type not in (None, "V", "K"):
            raise ValueError('message_type must be None, "V" or "K"')
        dm_files = {
            "dmFile": [
                {
                    "dmMimeType": f["mime_type"],
                    "dmFileMetaType": f.get("meta_type", "main"),
                    "dmFileDescr": f["file_name"],
                    "dmEncodedContent": f["content"],
                }
                for f in files
            ]
        }
        envelope: dict[str, Any] = {
            "dbIDRecipient": recipient_id,
            "dmAnnotation": subject,
            "dmToHands": to_hands,
            "dmSenderRefNumber": sender_ref_number,
        }
        if message_type is not None:
            envelope["dmType"] = message_type
        resp = self._call(
            "dm_operations",
            "CreateMessage",
            dmEnvelope=envelope,
            dmFiles=dm_files,
        )
        _check_dm(resp)
        return str(resp["dmID"])


# -- serialization helpers ----------------------------------------------


def _get(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if cur is None:
            return None
        try:
            cur = cur[key]
        except (KeyError, TypeError):
            cur = getattr(cur, key, None)
    return cur


def _unwrap_repeated(container: Any, name: str) -> list[Any]:
    """Unwrap an ISDS array type whose whole <sequence> repeats.

    zeep exposes such arrays (tDbOwnersArray, tRecordsArray, tEventsArray) as
    ``_value_1 = [{name: {...}}, ...]`` rather than a flat list under ``name``.
    """
    if container is None:
        return []
    rows = getattr(container, "_value_1", None)
    if rows is not None:
        return [row[name] for row in rows]
    return _get(container, name) or []


def _to_databox(b: Any) -> DataBox:
    return DataBox(
        dbID=b["dbID"],
        dbType=str(b["dbType"]) if b["dbType"] is not None else None,
        dbName=b["firmName"],
        dbIC=b["ic"],
        dbEffectiveOVM=b["dbEffectiveOVM"],
    )


def _status_from_int(value: Any) -> MessageStatus | None:
    if value is None:
        return None
    try:
        return MessageStatus(int(value))
    except (ValueError, TypeError):
        return None


def _record_to_envelope(rec: Any) -> MessageEnvelope:
    # Tolerant lookups throughout: depending on the operation, the envelope
    # comes as tRecord (status/times inline) or as tDelivery's dmDm (only the
    # gMessageEnvelope group - status/times live as SIBLINGS of dmDm).
    # dmID alone is mandatory in every envelope shape - fail loudly rather
    # than let a message with the bogus id "None" flow downstream.
    dm_id = _get(rec, "dmID")
    if dm_id is None:
        raise IsdsResponseError("????", "response envelope is missing the mandatory dmID")
    return MessageEnvelope(
        dmID=str(dm_id),
        dbIDSender=_get(rec, "dbIDSender"),
        dmSender=_get(rec, "dmSender"),
        dmSenderAddress=_get(rec, "dmSenderAddress"),
        dbIDRecipient=_get(rec, "dbIDRecipient"),
        dmRecipient=_get(rec, "dmRecipient"),
        dmRecipientAddress=_get(rec, "dmRecipientAddress"),
        dmAnnotation=_get(rec, "dmAnnotation"),
        dmMessageStatus=_status_from_int(_get(rec, "dmMessageStatus")),
        dmDeliveryTime=_get(rec, "dmDeliveryTime"),
        dmAcceptanceTime=_get(rec, "dmAcceptanceTime"),
        dmAttachmentSize=_get(rec, "dmAttachmentSize"),
        dmSenderRefNumber=_get(rec, "dmSenderRefNumber"),
        dmRecipientRefNumber=_get(rec, "dmRecipientRefNumber"),
        dmToHands=_get(rec, "dmToHands"),
    )


def _records_to_envelopes(resp: Any) -> list[MessageEnvelope]:
    _check_dm(resp)
    items = _unwrap_repeated(resp["dmRecords"], "dmRecord")
    return [_record_to_envelope(r) for r in items]


def _to_delivery_info(returned: Any) -> DeliveryInfo:
    dm = _get(returned, "dmDm")
    envelope = _record_to_envelope(dm) if dm is not None else MessageEnvelope(dmID="")
    # In tDelivery the delivery time/acceptance time/status are siblings of
    # dmDm, not members of it - overlay them onto the envelope.
    envelope.delivery_time = envelope.delivery_time or _get(returned, "dmDeliveryTime")
    envelope.acceptance_time = envelope.acceptance_time or _get(returned, "dmAcceptanceTime")
    envelope.status = envelope.status or _status_from_int(_get(returned, "dmMessageStatus"))
    events = [
        DeliveryEvent(dmEventTime=_get(ev, "dmEventTime"), dmEventDescr=_get(ev, "dmEventDescr"))
        for ev in _unwrap_repeated(_get(returned, "dmEvents"), "dmEvent")
    ]
    return DeliveryInfo(envelope=envelope, events=events)
