# Provenance of the WSDL/XSD files in this directory

These files describe the **ISDS application interface** (SOAP web services,
XML namespace `http://isds.czechpoint.cz/v20`). They are **interface
description files authored by the ISDS operator** (Czech state / Czech POINT),
published as attachments to the *Provozní řád ISDS* (ISDS Operating Rules) at
<https://www.mojedatovaschranka.cz> → *Aplikační rozhraní*.

## Files

| File | Purpose |
|------|---------|
| `dm_operations.wsdl` | Message operations (send, download): CreateMessage, SignedMessageDownload, MessageDownload |
| `dm_info.wsdl` | Message info: GetListOfReceivedMessages, GetListOfSentMessages, delivery receipts |
| `db_search.wsdl` | Data box directory search: FindDataBox |
| `db_access.wsdl` | Login-related services: GetOwnerInfoFromLogin, GetUserInfoFromLogin |
| `db_manipulations.wsdl` | Data box management services |
| `dbTypes.xsd` | Shared XML schema types for the db_* services |
| `dmBaseTypes.xsd` | Shared XML schema types for the dm_* services |

## How they got here

This specific copy was obtained from the **dslib** project's mirror
(<https://github.com/yarda/dslib>, LGPL), which distributes the ISDS WSDL/XSD
files for use with the interface. dslib is credited in the project README.

## Notes on licensing

WSDL/XSD files are **interface descriptions** - the published, machine-readable
contract of a public e-government service - rather than an authored software
program. They are used here only to let the SOAP client speak the ISDS
protocol. If you need an unambiguous upstream copy, download the current
versions directly from the *Provozní řád ISDS* attachments on
mojedatovaschranka.cz and replace the files in this directory; the client loads
them by name (see `isds_client/client.py`).

If you are the rights holder and believe these files should not be redistributed
here, please open an issue and we will fetch them from the official source at
runtime instead of vendoring them.
