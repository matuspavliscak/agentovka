"""Unit tests for IsdsClient response parsing, using a fake zeep service.

These do not hit the network: we monkeypatch the per-service zeep client with a
stub whose ``service.<operation>`` returns canned zeep-like dict structures.
Integration tests against the real test environment live in
test_integration.py behind an env flag.
"""

from __future__ import annotations

from typing import Any

import pytest

from isds_client.client import IsdsClient, IsdsEnvironment
from isds_client.errors import IsdsResponseError
from isds_client.models import MessageStatus


class _FakeOperation:
    def __init__(self, result: Any) -> None:
        self._result = result

    def __call__(self, **kwargs: Any) -> Any:
        return self._result


class _FakeService:
    def __init__(self, ops: dict[str, Any]) -> None:
        for name, result in ops.items():
            setattr(self, name, _FakeOperation(result))


class _FakeClient:
    def __init__(self, ops: dict[str, Any]) -> None:
        self.service = _FakeService(ops)


@pytest.fixture
def client() -> IsdsClient:
    return IsdsClient("u", "p", IsdsEnvironment.TEST)


def _install(client: IsdsClient, service: str, ops: dict[str, Any]) -> None:
    client._clients[service] = _FakeClient(ops)  # type: ignore[assignment]


def test_get_owner_info(client: IsdsClient) -> None:
    _install(
        client,
        "db_access",
        {
            "GetOwnerInfoFromLogin": {
                "dbOwnerInfo": {
                    "dbID": "abcdefg",
                    "dbType": "FO",
                    "firmName": "Jan Novák",
                    "ic": None,
                    "dbState": 1,
                    "dbOpenAddressing": False,
                },
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }
        },
    )
    info = client.get_owner_info()
    assert info.box_id == "abcdefg"
    assert info.firm_name == "Jan Novák"


def test_error_status_raises(client: IsdsClient) -> None:
    _install(
        client,
        "db_access",
        {
            "GetOwnerInfoFromLogin": {
                "dbOwnerInfo": {
                    "dbID": "",
                    "dbType": None,
                    "firmName": None,
                    "ic": None,
                    "dbState": None,
                    "dbOpenAddressing": None,
                },
                "dbStatus": {"dbStatusCode": "1234", "dbStatusMessage": "Chyba"},
            }
        },
    )
    with pytest.raises(IsdsResponseError) as exc:
        client.get_owner_info()
    assert exc.value.status_code == "1234"


def test_find_databox(client: IsdsClient) -> None:
    _install(
        client,
        "db_search",
        {
            "FindDataBox": {
                "dbResults": {
                    "dbOwnerInfo": [
                        {
                            "dbID": "aaaaaaa",
                            "dbType": "OVM",
                            "firmName": "Ministerstvo vnitra",
                            "ic": "00007064",
                            "dbEffectiveOVM": True,
                        }
                    ]
                },
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }
        },
    )
    boxes = client.find_databox("Ministerstvo vnitra")
    assert len(boxes) == 1
    assert boxes[0].box_id == "aaaaaaa"
    assert boxes[0].name == "Ministerstvo vnitra"


def test_find_databox_empty(client: IsdsClient) -> None:
    _install(
        client,
        "db_search",
        {
            "FindDataBox": {
                "dbResults": None,
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }
        },
    )
    assert client.find_databox("nic") == []


def test_list_received_messages(client: IsdsClient) -> None:
    _install(
        client,
        "dm_info",
        {
            "GetListOfReceivedMessages": {
                "dmRecords": {
                    "dmRecord": [
                        {
                            "dmID": "10123456",
                            "dbIDSender": "aaaaaaa",
                            "dmSender": "Úřad",
                            "dmSenderAddress": None,
                            "dbIDRecipient": "bbbbbbb",
                            "dmRecipient": "Jan Novák",
                            "dmRecipientAddress": None,
                            "dmAnnotation": "Věc",
                            "dmMessageStatus": 4,
                            "dmDeliveryTime": None,
                            "dmAcceptanceTime": None,
                            "dmAttachmentSize": 12,
                        }
                    ]
                },
                "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"},
            }
        },
    )
    msgs = client.get_list_of_received_messages()
    assert len(msgs) == 1
    assert msgs[0].message_id == "10123456"
    assert msgs[0].status == MessageStatus.DELIVERED_TO_BOX


def test_signed_message_download_returns_bytes(client: IsdsClient) -> None:
    _install(
        client,
        "dm_operations",
        {
            "SignedMessageDownload": {
                "dmSignature": b"\x30\x82fake-cms",
                "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"},
            }
        },
    )
    raw = client.signed_message_download("10123456")
    assert raw == b"\x30\x82fake-cms"


def test_create_message_returns_id(client: IsdsClient) -> None:
    _install(
        client,
        "dm_operations",
        {
            "CreateMessage": {
                "dmID": "99999",
                "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"},
            }
        },
    )
    new_id = client.create_message(
        "aaaaaaa",
        "Test",
        [{"file_name": "a.pdf", "mime_type": "application/pdf", "content": b"x"}],
    )
    assert new_id == "99999"


def test_create_message_dm_type(client: IsdsClient) -> None:
    """message_type is passed as the dmType envelope attribute (K = PDZ)."""
    calls: list[dict[str, Any]] = []

    class _Op:
        def __call__(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return {"dmID": "1", "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"}}

    class _Svc:
        CreateMessage = _Op()

    class _Cl:
        service = _Svc()

    client._clients["dm_operations"] = _Cl()  # type: ignore[assignment]
    files = [{"file_name": "a.txt", "mime_type": "text/plain", "content": b"x"}]

    client.create_message("aaaaaaa", "Verejna", files)
    assert "dmType" not in calls[0]["dmEnvelope"]

    client.create_message("aaaaaaa", "PDZ", files, message_type="K")
    assert calls[1]["dmEnvelope"]["dmType"] == "K"

    with pytest.raises(ValueError, match="message_type"):
        client.create_message("aaaaaaa", "Spatny typ", files, message_type="X")


def test_auth_error_from_http_401_with_html_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live ISDS answers bad credentials with 401 + an XHTML page (no SOAP
    fault), so the transport itself must raise IsdsAuthError before zeep
    tries to parse the body."""
    from zeep.transports import Transport

    from isds_client.client import _IsdsTransport
    from isds_client.errors import IsdsAuthError

    class _Resp:
        status_code = 401

    monkeypatch.setattr(Transport, "post_xml", lambda self, address, envelope, headers: _Resp())
    transport = _IsdsTransport()
    with pytest.raises(IsdsAuthError):
        transport.post_xml("https://example.invalid/DS/DsManage", None, {})


def test_find_databox_falls_back_to_name_for_7char_query(client: IsdsClient) -> None:
    """A 7-char lowercase query that matches no box ID retries as a name.

    Unused search fields are sent as zeep SkipValue (omitted from the XML),
    and a name search fans out over the four box types (dbType is mandatory
    for name searches, ISDS error 1101).
    """
    calls: list[dict[str, Any]] = []

    def _filled(payload: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in payload.items() if isinstance(v, str)}

    class _Op:
        def __call__(self, *, dbOwnerInfo: dict[str, Any]) -> Any:
            calls.append(_filled(dbOwnerInfo))
            if "dbID" in _filled(dbOwnerInfo):
                return {
                    "dbResults": None,
                    "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
                }
            return {
                "dbResults": {
                    "dbOwnerInfo": [
                        {
                            "dbID": "zzzzzzz",
                            "dbType": "PO",
                            "firmName": "Tescoma",
                            "ic": "11111111",
                            "dbEffectiveOVM": False,
                        }
                    ]
                },
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }

    class _Svc:
        FindDataBox = _Op()

    class _Cl:
        service = _Svc()

    client._clients["db_search"] = _Cl()  # type: ignore[assignment]
    boxes = client.find_databox("tescoma")
    assert calls[0] == {"dbID": "tescoma"}
    # The four typed name queries run concurrently, so compare as a set.
    assert {c["firmName"] for c in calls[1:]} == {"tescoma"}
    assert {c["dbType"] for c in calls[1:]} == {"OVM", "PO", "PFO", "FO"}
    # The same box returned for every type is deduplicated by box ID.
    assert len(boxes) == 1
    assert boxes[0].name == "Tescoma"


def test_find_databox_by_name_raises_when_all_types_fail(client: IsdsClient) -> None:
    """If every typed query fails with a real ISDS error, the error surfaces
    instead of a false 'no box found' (0002 'no match' is not an error)."""

    class _Op:
        def __call__(self, *, dbOwnerInfo: dict[str, Any]) -> Any:
            return {
                "dbResults": None,
                "dbStatus": {"dbStatusCode": "1301", "dbStatusMessage": "rate limited"},
            }

    class _Svc:
        FindDataBox = _Op()

    class _Cl:
        service = _Svc()

    client._clients["db_search"] = _Cl()  # type: ignore[assignment]
    with pytest.raises(IsdsResponseError) as exc:
        client.find_databox("Ministerstvo vnitra")
    assert exc.value.status_code == "1301"


def test_find_databox_by_name_tolerates_partial_type_errors(client: IsdsClient) -> None:
    """An error for one box type does not discard results from the others
    (e.g. FO name searches can be restricted while OVM succeeds)."""

    class _Op:
        def __call__(self, *, dbOwnerInfo: dict[str, Any]) -> Any:
            if dbOwnerInfo.get("dbType") == "FO":
                return {
                    "dbResults": None,
                    "dbStatus": {"dbStatusCode": "1234", "dbStatusMessage": "restricted"},
                }
            return {
                "dbResults": {
                    "dbOwnerInfo": [
                        {
                            "dbID": "aaaaaaa",
                            "dbType": dbOwnerInfo.get("dbType"),
                            "firmName": "Ministerstvo vnitra",
                            "ic": "00007064",
                            "dbEffectiveOVM": True,
                        }
                    ]
                },
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }

    class _Svc:
        FindDataBox = _Op()

    class _Cl:
        service = _Svc()

    client._clients["db_search"] = _Cl()  # type: ignore[assignment]
    boxes = client.find_databox("Ministerstvo vnitra")
    assert [b.box_id for b in boxes] == ["aaaaaaa"]


def test_db_owner_info_elements_match_bundled_schema() -> None:
    """_DB_OWNER_INFO_ELEMENTS must mirror the tDbOwnerInfo sequence in the
    bundled dbTypes.xsd - if the schema is ever updated, this fails instead of
    FindDataBox silently breaking with ISDS error 2004 again."""
    from isds_client.client import _DB_OWNER_INFO_ELEMENTS

    client = IsdsClient("u", "p", IsdsEnvironment.TEST)
    owner_type = client._client("db_search").get_type("{http://isds.czechpoint.cz/v20}tDbOwnerInfo")
    assert tuple(name for name, _ in owner_type.elements) == _DB_OWNER_INFO_ELEMENTS


class _Value1Rows:
    """Mimics zeep's representation of ISDS array types whose whole <sequence>
    repeats (tDbOwnersArray, tRecordsArray): rows live under the private
    _value_1 attribute as [{element_name: {...}}, ...], not as a flat list."""

    def __init__(self, name: str, items: list[Any]) -> None:
        self._value_1 = [{name: item} for item in items]


def test_find_databox_unwraps_value1_rows(client: IsdsClient) -> None:
    """The production zeep shape (_value_1 rows) is unwrapped correctly."""
    _install(
        client,
        "db_search",
        {
            "FindDataBox": {
                "dbResults": _Value1Rows(
                    "dbOwnerInfo",
                    [
                        {
                            "dbID": "xind94x",
                            "dbType": "OVM",
                            "firmName": "Ministerstvo vnitra",
                            "ic": "00007064",
                            "dbEffectiveOVM": True,
                        }
                    ],
                ),
                "dbStatus": {"dbStatusCode": "0000", "dbStatusMessage": "OK"},
            }
        },
    )
    boxes = client.find_databox("Ministerstvo vnitra")
    assert [b.box_id for b in boxes] == ["xind94x"]


def test_sent_list_unwraps_value1_rows(client: IsdsClient) -> None:
    _install(
        client,
        "dm_info",
        {
            "GetListOfSentMessages": {
                "dmRecords": _Value1Rows(
                    "dmRecord",
                    [{"dmID": "10123456", "dmAnnotation": "Věc", "dmMessageStatus": 1}],
                ),
                "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"},
            }
        },
    )
    msgs = client.get_list_of_sent_messages()
    assert [m.message_id for m in msgs] == ["10123456"]


def test_record_missing_dmid_raises(client: IsdsClient) -> None:
    """A record without the mandatory dmID fails loudly instead of producing
    a message with the bogus id 'None'."""
    _install(
        client,
        "dm_info",
        {
            "GetListOfSentMessages": {
                "dmRecords": {"dmRecord": [{"dmAnnotation": "bez ID"}]},
                "dmStatus": {"dmStatusCode": "0000", "dmStatusMessage": "OK"},
            }
        },
    )
    with pytest.raises(IsdsResponseError, match="dmID"):
        client.get_list_of_sent_messages()
