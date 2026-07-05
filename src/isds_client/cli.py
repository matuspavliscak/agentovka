"""Minimal CLI for isds_client - useful for manual testing against the test env.

Reads credentials from the environment (ISDS_USERNAME, ISDS_PASSWORD, ISDS_ENV)
exactly like the MCP server, so it never takes a password on the command line.

    isds-client owner-info
    isds-client find "Ministerstvo vnitra"
    isds-client parse message.zfo
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from isds_client.client import IsdsClient, IsdsEnvironment
from isds_client.zfo import parse_zfo


def _client() -> IsdsClient:
    username = os.environ.get("ISDS_USERNAME", "")
    password = os.environ.get("ISDS_PASSWORD", "")
    if not username or not password:
        sys.exit("ISDS_USERNAME and ISDS_PASSWORD must be set in the environment.")
    env = (
        IsdsEnvironment.PRODUCTION
        if os.environ.get("ISDS_ENV", "test").lower() == "production"
        else IsdsEnvironment.TEST
    )
    return IsdsClient(username, password, environment=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isds-client", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("owner-info", help="show info about your own datová schránka")
    p_find = sub.add_parser("find", help="search for a recipient datová schránka")
    p_find.add_argument("query")
    p_parse = sub.add_parser("parse", help="parse a local .zfo file (no network)")
    p_parse.add_argument("path")

    args = parser.parse_args(argv)

    if args.command == "parse":
        with open(args.path, "rb") as fh:
            parsed = parse_zfo(fh.read())
        print(
            json.dumps(
                {
                    "envelope": parsed.envelope.model_dump(mode="json", by_alias=True),
                    "attachments": [
                        {"file_name": f.file_name, "mime_type": f.mime_type, "size": f.size}
                        for f in parsed.files
                    ],
                    "is_delivery_receipt": parsed.is_delivery_receipt,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    client = _client()
    if args.command == "owner-info":
        print(
            json.dumps(
                client.get_owner_info().model_dump(mode="json"), ensure_ascii=False, indent=2
            )
        )
    elif args.command == "find":
        boxes = client.find_databox(args.query)
        print(json.dumps([b.model_dump(mode="json") for b in boxes], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
