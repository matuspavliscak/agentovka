# Contributing to Agentovka

Thanks for your interest! Agentovka aims to be a correct, safety-first bridge
between AI agents and the Czech ISDS system. Contributions of code, tests,
documentation and — especially — corrections to the legal-semantics
documentation are all welcome.

## Ground rules

- **Never develop against production.** Use the test environment
  ([datovka-test.gov.cz](https://www.datovka-test.gov.cz), SOAP host
  `ws1.czebox.cz`) until your change is verified. Default `ISDS_ENV=test`.
- **Never weaken the safety model without discussion.** The three tool classes
  (A/B/C), the `acknowledge_delivery_trigger` guard, and the
  `dry_run` + `AGENTOVKA_ALLOW_SEND` send guard are the reason this project
  exists. Changes to them need a clear rationale and a source.
- **No automatic mailbox polling.** The server must only call ISDS in response
  to an explicit tool call. No background jobs, no schedulers.
- **Credentials come only from the environment.** They must never be tool
  parameters and must never be logged or persisted.
- **Cite sources for legal claims.** Anything in
  [docs/delivery-semantics.md](docs/delivery-semantics.md) or in a tool's
  delivery classification must be backed by the Provozní řád ISDS, a statute, or
  poradnaisds.cz.

## Development setup

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

### Integration tests

Integration tests hit the ISDS **test** environment and are opt-in:

```bash
AGENTOVKA_RUN_INTEGRATION=1 ISDS_USERNAME=... ISDS_PASSWORD=... uv run pytest -m integration
```

They refuse to run if `ISDS_ENV=production`.

## Commits & PRs

- Use [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- Keep the CI green: ruff (lint + format), mypy, pytest across Python 3.11–3.13.
- Add or update tests for behavior changes. For the client, prefer mocked SOAP
  unit tests; reserve the test environment for genuine integration coverage.

## Reporting security issues

If you find a way that Agentovka could cause an unintended legal delivery, an
unintended send, or a credential leak, please open an issue (or contact the
maintainer privately for sensitive reports) rather than a public PR with a
proof-of-concept against a real box.
