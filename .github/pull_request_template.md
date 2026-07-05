<!--
Thanks for contributing to Agentovka! Please fill in the sections below.
Do not include real credentials, real message contents, or personal data.
-->

## Summary

<!-- What does this PR change and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation
- [ ] Refactor / internal
- [ ] Tests / CI

## Safety model

<!-- The three tool classes (A/B/C), the acknowledge_delivery_trigger guard, and
the dry_run + AGENTOVKA_ALLOW_SEND send guard are the core of the project. -->

- [ ] This change does **not** weaken the safety model.
- [ ] If it changes delivery-triggering (Class B) or sending (Class C) behavior,
      the rationale and a source are described above.

## Checklist

- [ ] Developed and verified against the **test** environment (never production).
- [ ] No credentials or personal data committed or logged.
- [ ] `uv run pytest` passes.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run mypy` passes.
- [ ] Tests added or updated for behavior changes.
- [ ] Docs updated if behavior or configuration changed (README, delivery-semantics).
