# Delivery semantics of ISDS / Právní sémantika doručování v ISDS

This document explains the legal semantics that Agentovka encodes into its
tools. It is engineering context, **not legal advice** (see the disclaimer at
the end). Every claim is sourced.

Tento dokument vysvětluje právní sémantiku, kterou Agentovka promítá do svých
nástrojů. Jde o technický kontext, **nikoli o právní radu** (viz upozornění na
konci). Každé tvrzení je opatřeno zdrojem.

---

## 1. Dodání vs. doručení / Delivery to the box vs. legal delivery

ISDS distinguishes two distinct moments in a message's life:

- **Dodání (delivery to the box).** The message physically arrives in the
  recipient's data box. Timestamp: `dmDeliveryTime`. No deadlines run yet.
- **Doručení (legal delivery).** The message is legally delivered to the
  recipient. Timestamp: `dmAcceptanceTime`. **Statutory deadlines start running
  from this moment** (appeals, responses, etc.).

Legal delivery happens in one of two ways:

1. **By login (doručení přihlášením).** An authorized person logs in - via the
   web portal **or via the application interface (API)**. The moment they do,
   *every* message currently sitting in the box (in the *dodaná* state) becomes
   legally delivered.
2. **By fiction (doručení fikcí).** If nobody logs in, see §2.

Sources:
- Provozní řád ISDS (operating rules), published at mojedatovaschranka.cz →
  *Aplikační rozhraní*.
- Act No. 300/2008 Coll., § 17 (on delivery via data boxes):
  <https://www.zakonyprolidi.cz/cs/2008-300#p17>

## 2. Fikce doručení / Fiction of delivery

> § 17 odst. 4 zák. č. 300/2008 Sb.: Nepřihlásí-li se do datové schránky osoba
> oprávněná k přístupu k dodanému dokumentu ve lhůtě **10 dnů** ode dne, kdy byl
> dokument dodán do datové schránky, považuje se tento dokument za **doručený
> posledním dnem této lhůty**.

If no authorized person logs in within **10 days** of the message being made
available in the box, the message is deemed delivered on the last day of that
period (the 10th day). This is the **fiction of delivery**.

**Working-day shift.** If the 10th day falls on a Saturday, Sunday or a public
holiday, delivery occurs on the **nearest following working day**. Agentovka
computes this with the `holidays` library (Czech holiday calendar) in
[`get_delivery_deadline`](../src/agentovka_mcp/deadlines.py).

Sources:
- § 17 (4) Act No. 300/2008 Coll.:
  <https://www.zakonyprolidi.cz/cs/2008-300#p17>
- General counting-of-time rule for the working-day shift: § 40 správního řádu
  (Act No. 500/2004 Coll.): <https://www.zakonyprolidi.cz/cs/2004-500#p40>

> **Why fiction matters for archiving.** A message delivered by fiction was, by
> definition, never opened. It is still subject to the 90-day deletion (§3), and
> the Portál občana auto-archiving is reported not to capture fiction-delivered
> messages. Downloading such a message *does* deliver it (if not already
> delivered by fiction) - so Agentovka's archive is the durable copy.

## 3. Devadesátidenní mazání / 90-day deletion

Messages are **permanently deleted 90 days after delivery** (doručení). After
deletion the content is gone from ISDS; only envelope metadata and the delivery
receipt (doručenka) remain available for longer. A paid *Datový trezor* (data
vault) extends retention, but the free default is 90 days.

This is the core reason for Agentovka's **local archive**: anything passing
through `download_message` is stored verbatim (original signed ZFO + extracted
attachments + metadata), so it survives the 90-day deletion **and** covers the
fiction-delivery gap in Portál občana.

Sources:
- Provozní řád ISDS, section on message retention (mojedatovaschranka.cz).
- ISDS help, "Jak dlouho jsou zprávy v datové schránce":
  <https://info.mojedatovaschranka.cz/info/cs/>

## 4. Which operations trigger delivery / Které operace spouštějí doručení

This is the crux of the safety model. **Delivery is triggered by accessing the
*received* message store**, not by every API call.

| Operation | ISDS WS | Triggers delivery? | Agentovka class |
|-----------|---------|--------------------|-----------------|
| `GetListOfReceivedMessages` | dm_info (dx) | **YES** - counts as a login (EV13); delivers all *dodaná* messages | **B** |
| `MessageEnvelopeDownload` / `SignedMessageDownload` / `MessageDownload` | dm_info / dm_operations | **YES** - downloading a received message delivers it | **B** |
| `GetListOfSentMessages` | dm_info (dx) | No - operates on your *sent* messages | A |
| `GetDeliveryInfo` / `GetSignedDeliveryInfo` | dm_info (dx) | No - reads a delivery receipt; does not access the received store | A |
| `FindDataBox` | db_search (df) | No - directory lookup | A |
| `GetOwnerInfoFromLogin` | db_access (DsManage) | No - box metadata | A |
| `CreateMessage` | dm_operations (dz) | N/A - sends (legal act) | C |

The reasoning: legal delivery is tied to an **authorized person accessing the
delivered document**. Listing your own sent messages or reading a delivery
receipt of a message *you sent* does not access any recipient's undelivered
document, so it does not start anyone's deadlines. Listing or downloading
*received* messages does.

> **Conservative stance.** Agentovka treats `list_received_messages` and any
> download of a received message as delivery-triggering (class B) and blocks them
> behind `acknowledge_delivery_trigger=true`. If you have an authoritative source
> that narrows or widens this set, please open an issue - the classification is
> documented here precisely so it can be reviewed.

Sources:
- Provozní řád ISDS - *Aplikační rozhraní*, description of
  `GetListOfReceivedMessages` and message-download operations
  (mojedatovaschranka.cz).
- poradnaisds.cz - knowledge base on delivery events and API access.

## 5. Event codes / Kódy událostí (doručenka)

Delivery receipts (`GetDeliveryInfo`) list events with `EV*` codes describing
what happened to a message. Common codes:

| Code | Meaning |
|------|---------|
| EV0 | Doručení fikcí - delivery by fiction (10-day period elapsed) |
| EV5 | Datová zpráva dodána do schránky - delivered to the box (dodání) |
| EV11 | Přihlášení oprávněné osoby - login of an authorized person |
| EV12 | Doručení přihlášením osoby oprávněné číst tuto zprávu |
| EV13 | Přihlášení/přístup elektronické aplikace (aplikační rozhraní, systémový certifikát) - delivery caused by API access |

`EV13` is the one that matters most here: it is the event recorded when a message
is delivered because an application (API) accessed the box. This is why reading
your received list via Agentovka is a delivery-triggering, deadline-starting act.

Sources:
- poradnaisds.cz - list of doručenka events.
- Provozní řád ISDS - doručenka / delivery receipt structure.

## 6. Application interface access / Přístup přes aplikační rozhraní

The ISDS SOAP web services may be used by **any box owner** with their login
credentials, over HTTPS with HTTP Basic authentication, **without prior
registration**. Endpoints:

- **Production:** `https://ws1.mojedatovaschranka.cz/DS/{service}`
- **Test:** `https://ws1.czebox.cz/DS/{service}` (portal:
  [datovka-test.gov.cz](https://www.datovka-test.gov.cz))

Service path suffixes: `dz` = dm_operations, `dx` = dm_info, `df` = db_search,
`DsManage` = db_access / db_manipulations.

Sources:
- Provozní řád ISDS - *Aplikační rozhraní* (mojedatovaschranka.cz).
- ISDS help, "Vyzkoušejte si Datovou schránku":
  <https://info.mojedatovaschranka.cz/info/cs/95.html>

---

## Disclaimer / Upozornění

This document is engineering context, may be simplified, and can become outdated
as statutes and the ISDS operating rules change. **It is not legal advice and
Agentovka is not a legal service.** For binding information consult the Provozní
řád ISDS, the cited statutes, or a qualified lawyer.

Tento dokument je technický kontext, může být zjednodušený a časem zastarat.
**Nejde o právní radu a Agentovka není právní služba.** Pro závazné informace
konzultujte Provozní řád ISDS, citované zákony nebo advokáta.
