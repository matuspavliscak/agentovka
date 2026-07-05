# Agentovka

**Agentic access to Czech datové schránky (ISDS) via MCP.**

Agentovka is an [MCP](https://modelcontextprotocol.io) server that lets AI agents
(Claude Desktop, Claude Code, any MCP client) work with a Czech **datová
schránka** through the official ISDS application interface - safely and
with the legal semantics of the system treated as a first-class concern.

> **Not a thin SOAP wrapper.** ISDS has legal semantics that a naive client
> silently violates. Reading your inbox *legally delivers your mail* and starts
> statutory deadlines; messages are *permanently deleted* after 90 days; sending
> a message is a *legal act*. Agentovka encodes these facts into the tools
> themselves. See **[docs/delivery-semantics.md](docs/delivery-semantics.md)** -
> the heart of the project.

[![CI](https://github.com/matuspavliscak/agentovka/actions/workflows/ci.yml/badge.svg)](https://github.com/matuspavliscak/agentovka/actions/workflows/ci.yml)
License: MIT · Python ≥ 3.11

---

## Why this exists

The ISDS application interface (SOAP web services) may be used by any box owner
with their login credentials, without prior registration. But the API mirrors
the legal machinery of the system, and an agent that treats a datová schránka
like an IMAP inbox will cause real legal consequences:

1. **Reading triggers delivery.** Listing received messages - whether by logging
   in or via the API (delivery event **EV13**) - marks *all* messages in the box
   as legally delivered (doručeno) and starts statutory deadlines (appeals,
   etc.). There is no consequence-free "peek".
2. **Fiction of delivery.** If nobody logs in, a message is deemed delivered on
   the **10th day** after it was made available (§ 17 (4) of Act No. 300/2008
   Coll.). If that day is a weekend or public holiday, delivery falls on the next
   working day.
3. **90-day deletion.** Messages are permanently deleted **90 days after
   delivery**. The Portál občana auto-archiving notably **fails for messages
   delivered by fiction** - a real gap that Agentovka's local archive closes.
4. **Sending is a legal act** (a submission to a public authority, a
   private-law act).

Agentovka's value is exactly this: a safety model and legal-semantics layer on
top of a clean, reusable client.

## Architecture

Two independent layers in one repository:

| Layer | Package | What it is |
|-------|---------|------------|
| Client library | [`isds_client`](src/isds_client) | Pure Python client over the ISDS SOAP interface (via [`zeep`](https://docs.python-zeep.org), HTTP Basic auth over TLS). Typed [`pydantic`](https://docs.pydantic.dev) models, a ZFO/CMS parser, and a small CLI. Usable **standalone**, without MCP. |
| MCP server | [`agentovka_mcp`](src/agentovka_mcp) | A thin MCP layer over the library, using the official MCP Python SDK (FastMCP), stdio transport. Implements the safety model and the local archive. |

## The safety model

Tools are split into three classes, reflected in their names, descriptions and
MCP annotations:

### Class A - safe, no legal consequences (`readOnlyHint`)
- `search_databox(query)` - find a recipient's box (ID, name, type).
- `get_databox_info()` - info about your own box.
- `list_archived_messages()`, `read_archived_message(id)`, `search_archive(q)` -
  read from the **local archive** only (no ISDS call).
- `list_sent_messages()`, `get_delivery_receipt(id)` - sent messages and their
  delivery receipts. These do **not** touch the received store and so do **not**
  trigger delivery (see [delivery-semantics](docs/delivery-semantics.md#which-operations-trigger-delivery)).
- `get_delivery_deadline(delivery_date)` - computes the fiction-of-delivery date
  (D+10, shifted to the next working day) using Czech public holidays.

### Class B - triggers delivery (event EV13)
- `list_received_messages(...)`, `download_message(id)`.
- Each requires a mandatory `acknowledge_delivery_trigger: bool`. Without `true`
  the tool returns an error explaining the legal consequences instead of acting.
- `download_message` fetches the signed message (ZFO), stores it in the local
  archive, extracts attachments (the ZFO is a PKCS#7/CMS envelope), and returns
  metadata plus text content.

### Class C - legal act: sending (`destructiveHint`)
- `send_message(recipient_id, subject, attachments, dry_run=true)`.
- Defaults to `dry_run=true` (returns a preview). Actually sending requires
  `dry_run=false` **and** `AGENTOVKA_ALLOW_SEND=true` in the environment.

**The server never polls the mailbox on its own** - no background jobs, no
scheduler. Every ISDS call happens only in response to an explicit tool call.

## Installation & configuration

Run with [`uv`](https://docs.astral.sh/uv):

```bash
uvx agentovka        # once published to PyPI
# or from a clone:
uv run agentovka
```

All configuration is via environment variables - **credentials are never
accepted as tool parameters** (they would leak into the LLM context):

```bash
ISDS_USERNAME=...
ISDS_PASSWORD=...
ISDS_ENV=test                 # test | production  (default: test)
AGENTOVKA_ARCHIVE_DIR=~/.agentovka/archive
AGENTOVKA_ALLOW_SEND=false    # default: sending disabled
```

`ISDS_ENV=test` is the default on purpose - nobody should accidentally deliver
their production mail while trying the server out. The test environment is free:
create a test box at **[datovka-test.gov.cz](https://www.datovka-test.gov.cz)**
(SOAP host `ws1.czebox.cz`).

### Claude Desktop / Claude Code

Add to your MCP client config (e.g. `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "agentovka": {
      "command": "uvx",
      "args": ["agentovka"],
      "env": {
        "ISDS_USERNAME": "your-test-username",
        "ISDS_PASSWORD": "your-test-password",
        "ISDS_ENV": "test",
        "AGENTOVKA_ALLOW_SEND": "false"
      }
    }
  }
}
```

See [`examples/`](examples) for ready-to-copy configs.

## Using the library standalone

```python
from isds_client import IsdsClient, IsdsEnvironment

client = IsdsClient(username="...", password="...", environment=IsdsEnvironment.TEST)
print(client.get_owner_info())

# Parse a ZFO file with no network access:
from isds_client.zfo import parse_zfo
parsed = parse_zfo(open("message.zfo", "rb").read())
print(parsed.envelope, parsed.files)
```

CLI:

```bash
isds-client owner-info
isds-client find "Ministerstvo vnitra"
isds-client parse message.zfo         # offline
```

## Development

```bash
uv sync --group dev
uv run pytest            # unit tests (mocked SOAP)
uv run ruff check .
uv run mypy

# integration tests against the TEST environment (opt-in):
AGENTOVKA_RUN_INTEGRATION=1 ISDS_USERNAME=... ISDS_PASSWORD=... uv run pytest -m integration
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Prior art & credits

- WSDL/XSD schema files are the ISDS interface descriptions distributed by
  CZ.NIC's [dslib](https://github.com/yarda/dslib) (LGPL).
- ZFO/CMS handling follows the approach of the maintained reference
  implementations [libdatovka](https://gitlab.nic.cz/datovka/libdatovka) and the
  Datovka desktop app (CZ.NIC), and clean Python references such as
  [isds-unpack](https://github.com/adh/isds-unpack).

---

## ⚠️ Disclaimer

**Agentovka is an independent open-source project.** The name *Agentovka* is a
play on *datovka* (datová schránka): an **agent's** datovka.

**This project is not a legal service.** The information about delivery, fiction
of delivery, deadlines and archiving is provided for engineering context and may
be simplified or become outdated. It is not legal advice. For binding
information consult the ISDS operating rules (Provozní řád ISDS), the relevant
statutes, or a qualified lawyer.

Use against the **test environment** until you understand the legal consequences
of each tool. You are responsible for what you send and for the deadlines that
your reads set in motion.

---

## Česky

**Agentovka** je MCP server, který umožní AI agentům (Claude Desktop, Claude
Code, libovolný MCP klient) pracovat s českou **datovou schránkou** přes oficiální
aplikační rozhraní ISDS - bezpečně a s ohledem na právní sémantiku systému.

### Proč to není jen tenký SOAP wrapper

ISDS má právní sémantiku, kterou naivní klient tiše porušuje:

1. **Čtení spouští doručení.** Načtení seznamu dodaných zpráv (přihlášením i přes
   API - doručenkový kód **EV13**) způsobí, že se všechny dodané zprávy považují
   za **doručené** a začnou běžet zákonné lhůty. „Peek" bez následků neexistuje.
2. **Fikce doručení.** Nepřihlásí-li se nikdo, je zpráva doručena **10. dnem** od
   dodání (§ 17 odst. 4 zák. č. 300/2008 Sb.); padne-li 10. den na víkend/svátek,
   nastává fikce nejbližší pracovní den.
3. **Mazání po 90 dnech.** Zprávy se **90 dní po doručení** trvale mažou.
   Automatická archivace Portálu občana navíc **nezvládá zprávy doručené fikcí** -
   tuto díru řeší lokální archiv Agentovky.
4. **Odeslání je právní úkon.**

Podrobně a se zdroji: **[docs/delivery-semantics.md](docs/delivery-semantics.md)**.

### Bezpečnostní model

- **Třída A (bezpečné):** `search_databox`, `get_databox_info`, čtení lokálního
  archivu, `list_sent_messages`, `get_delivery_receipt`, `get_delivery_deadline`.
- **Třída B (spouští doručení, EV13):** `list_received_messages`,
  `download_message` - vyžadují parametr `acknowledge_delivery_trigger=true`, bez
  něj vrátí chybu s vysvětlením právních následků.
- **Třída C (právní úkon):** `send_message` - výchozí `dry_run=true`; skutečné
  odeslání vyžaduje `dry_run=false` **a** `AGENTOVKA_ALLOW_SEND=true`.

Server nikdy sám nepolluje schránku. Vše jen na explicitní volání nástroje.

### Konfigurace

Výhradně přes proměnné prostředí (přihlašovací údaje nikdy neprotékají do LLM
kontextu jako parametry nástrojů). Výchozí `ISDS_ENV=test` je záměrný bezpečnostní
prvek. Testovací schránku zdarma získáte na
[datovka-test.gov.cz](https://www.datovka-test.gov.cz).

### Upozornění

Agentovka je nezávislý open-source projekt. Název je odvozenina slova *datovka*.
**Projekt není právní služba** - uvedené informace nejsou právní radou.
