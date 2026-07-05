"""SOAP client for the ISDS (datové schránky) application interface.

Built on :mod:`zeep`. The WSDL/XSD files are bundled under ``wsdl/`` (taken from
CZ.NIC's dslib, which distributes them for exactly this purpose); the SOAP
endpoint address is overridden per environment because the bundled WSDLs point
only at production.

Authentication is HTTP Basic over TLS ("login by username and password"), which
per the ISDS Provozní řád (Aplikační rozhraní) any box user may use without
prior registration.

DELIVERY SEMANTICS — read docs/delivery-semantics.md. Operations that touch the
*received* message store legally deliver everything in the box:

  * ``get_list_of_received_messages`` and any download of a received message
    (``message_envelope_download``, ``message_download``, ``signed_message_download``)
    count as a login via the application interface (event EV13) and mark all
    "delivered to box" (dodaná) messages as legally delivered (doručená) —
    statutory deadlines start running.

Operations on *sent* messages and on delivery receipts of one's own sent
messages (``get_list_of_sent_messages``, ``get_delivery_info``,
``get_signed_delivery_info``) do not access the recipient's received store and
therefore do not trigger delivery of received messages.
"""

from __future__ import annotations

import enum
from datetime import datetime
from importlib import resources
from typing import Any

from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client, Settings
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

_WSDL_FILE: dict[str, str] = {
    "db_access": "db_access.wsdl",
    "db_search": "db_search.wsdl",
    "dm_info": "dm_info.wsdl",
    "dm_operations": "dm_operations.wsdl",
}


def _wsdl_path(name: str) -> str:
    return str(resources.files("isds_client.wsdl").joinpath(_WSDL_FILE[name]))


def _check_status(code: str | None, message: str | None) -> None:
    """Raise on a non-OK ISDS status code (0000 = OK, 0001 = deferred/accepted)."""
    if code in (None, "0000", "0001"):
        return
    raise IsdsResponseError(code or "????", message or "")


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
        transport = Transport(session=session, timeout=timeout, operation_timeout=timeout)
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
        client = self._client(service)
        try:
            return getattr(client.service, operation)(**kwargs)
        except Exception as exc:
            from requests.exceptions import HTTPError

            if (
                isinstance(exc, HTTPError)
                and exc.response is not None
                and exc.response.status_code in (401, 403)
            ):
                raise IsdsAuthError(
                    "ISDS rejected the credentials (HTTP "
                    f"{exc.response.status_code}). Check ISDS_USERNAME/ISDS_PASSWORD "
                    "and ISDS_ENV."
                ) from exc
            raise

    # -- class A: no legal consequences ---------------------------------

    def get_owner_info(self) -> OwnerInfo:
        """GetOwnerInfoFromLogin — info about the authenticated box. Safe."""
        resp = self._call("db_access", "GetOwnerInfoFromLogin")
        _check_status(
            _get(resp, "dbStatus", "dbStatusCode"), _get(resp, "dbStatus", "dbStatusMessage")
        )
        owner = resp["dbOwnerInfo"]
        return OwnerInfo(
            dbID=owner["dbID"],
            dbType=str(owner["dbType"]) if owner["dbType"] is not None else None,
            firmName=owner["firmName"],
            ic=owner["ic"],
            dbState=owner["dbState"],
            dbOpenAddressing=owner["dbOpenAddressing"],
        )

    def find_databox(self, query: str) -> list[DataBox]:
        """FindDataBox — search for a recipient box. Safe (no delivery trigger).

        The query is matched against box ID, name and IČ depending on which
        field is populated; here we search by box ID and by name/IČ heuristically.
        """
        owner_info: dict[str, Any] = {}
        q = query.strip()
        if len(q) == 7 and q.isalnum():
            owner_info["dbID"] = q
        elif q.isdigit() and len(q) in (8,):
            owner_info["ic"] = q
        else:
            owner_info["firmName"] = q
        resp = self._call("db_search", "FindDataBox", dbOwnerInfo=owner_info)
        _check_status(
            _get(resp, "dbStatus", "dbStatusCode"), _get(resp, "dbStatus", "dbStatusMessage")
        )
        results = resp["dbResults"]
        if results is None:
            return []
        boxes = results["dbOwnerInfo"]
        if boxes is None:
            return []
        return [_to_databox(b) for b in boxes]

    def get_list_of_sent_messages(
        self,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        status_filter: int = 0x03FF,
        offset: int = 1,
        limit: int = 100,
    ) -> list[MessageEnvelope]:
        """GetListOfSentMessages — list your OWN sent messages.

        Does not access the received store, so it does not trigger delivery of
        received messages.
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
        """GetDeliveryInfo — delivery receipt (doručenka) of a message. Not signed.

        For sent messages this reports the recipient's delivery events and does
        not trigger delivery of your received messages.
        """
        resp = self._call("dm_info", "GetDeliveryInfo", dmID=message_id)
        _check_status(
            _get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage")
        )
        returned = resp["dmDelivery"]
        return _to_delivery_info(returned)

    def get_signed_delivery_info(self, message_id: str) -> bytes:
        """GetSignedDeliveryInfo — CMS-signed delivery receipt (ZFO). Returns raw bytes."""
        resp = self._call("dm_info", "GetSignedDeliveryInfo", dmID=message_id)
        _check_status(
            _get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage")
        )
        return bytes(resp["dmSignature"])

    # -- class B: triggers delivery (EV13) ------------------------------

    def get_list_of_received_messages(
        self,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        status_filter: int = 0x03FF,
        offset: int = 1,
        limit: int = 100,
    ) -> list[MessageEnvelope]:
        """GetListOfReceivedMessages — DELIVERY-TRIGGERING.

        Reading the received list counts as a login via the application
        interface (EV13) and legally delivers all messages currently in the box.
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
        """SignedMessageDownload — DELIVERY-TRIGGERING. Returns the raw signed ZFO bytes."""
        resp = self._call("dm_operations", "SignedMessageDownload", dmID=message_id)
        _check_status(
            _get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage")
        )
        return bytes(resp["dmSignature"])

    def mark_message_as_downloaded(self, message_id: str) -> None:
        """MarkMessageAsDownloaded — flags a message as downloaded (removes the *new* flag)."""
        resp = self._call("dm_info", "MarkMessageAsDownloaded", dmID=message_id)
        _check_status(_get(resp, "dmStatusCode"), _get(resp, "dmStatusMessage"))

    # -- class C: legal act (write) -------------------------------------

    def create_message(
        self,
        recipient_id: str,
        subject: str,
        files: list[dict[str, Any]],
        *,
        to_hands: str | None = None,
        sender_ref_number: str | None = None,
    ) -> str:
        """CreateMessage — sends a data message (a legal act). Returns the new dmID.

        ``files`` is a list of ``{"file_name", "mime_type", "content"(bytes),
        "meta_type"}`` dicts; exactly one file must have meta_type "main".
        """
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
        envelope = {
            "dbIDRecipient": recipient_id,
            "dmAnnotation": subject,
            "dmToHands": to_hands,
            "dmSenderRefNumber": sender_ref_number,
        }
        resp = self._call(
            "dm_operations",
            "CreateMessage",
            dmEnvelope=envelope,
            dmFiles=dm_files,
        )
        _check_status(
            _get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage")
        )
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
    return MessageEnvelope(
        dmID=str(rec["dmID"]),
        dbIDSender=rec["dbIDSender"],
        dmSender=rec["dmSender"],
        dmSenderAddress=rec["dmSenderAddress"],
        dbIDRecipient=rec["dbIDRecipient"],
        dmRecipient=rec["dmRecipient"],
        dmRecipientAddress=rec["dmRecipientAddress"],
        dmAnnotation=rec["dmAnnotation"],
        dmMessageStatus=_status_from_int(rec["dmMessageStatus"]),
        dmDeliveryTime=rec["dmDeliveryTime"],
        dmAcceptanceTime=rec["dmAcceptanceTime"],
        dmAttachmentSize=rec["dmAttachmentSize"],
        dmSenderRefNumber=_get(rec, "dmSenderRefNumber"),
        dmRecipientRefNumber=_get(rec, "dmRecipientRefNumber"),
        dmToHands=_get(rec, "dmToHands"),
    )


def _records_to_envelopes(resp: Any) -> list[MessageEnvelope]:
    _check_status(_get(resp, "dmStatus", "dmStatusCode"), _get(resp, "dmStatus", "dmStatusMessage"))
    records = resp["dmRecords"]
    if records is None:
        return []
    items = records["dmRecord"]
    if items is None:
        return []
    return [_record_to_envelope(r) for r in items]


def _to_delivery_info(returned: Any) -> DeliveryInfo:
    dm = _get(returned, "dmDm")
    envelope = _record_to_envelope(dm) if dm is not None else MessageEnvelope(dmID="")
    events_container = _get(returned, "dmEvents")
    events: list[DeliveryEvent] = []
    if events_container is not None:
        raw_events = events_container["dmEvent"] or []
        for ev in raw_events:
            events.append(
                DeliveryEvent(dmEventTime=ev["dmEventTime"], dmEventDescr=ev["dmEventDescr"])
            )
    return DeliveryInfo(envelope=envelope, events=events)
