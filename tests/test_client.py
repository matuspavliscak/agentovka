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
