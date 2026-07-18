# Contributing to BabyMonitorVL

BabyMonitorVL is an experimental single-frame VLM demo. Before contributing, read the repository rules in [AGENTS.md](AGENTS.md), even if you are a human developer; they capture product and safety constraints that code alone does not express.

## Start here

- [Architecture and data flow](docs/ARCHITECTURE.md)
- [Development environment and commands](docs/DEVELOPMENT.md)
- [Prompt, schema, coordinates, and provider contract](docs/ANALYSIS_CONTRACT.md)
- [Release checklist](docs/RELEASE.md)
- [Change history](CHANGELOG.md)

## Pull-request expectations

- Keep one behavioral concern per change.
- Explain user-visible behavior, contract changes, privacy effects, and tests run.
- Add tests for fixes and new behavior.
- Update backend and frontend contracts together.
- Update `PROMPT_VERSION` for semantic prompt changes and `schema_version` for schema contract changes.
- Do not include secrets, camera frames, local models, generated frontend output, caches, or virtual environments.
- Do not introduce conventional CV inference or tracking into this MVP.

Before requesting review, complete the checks in [docs/RELEASE.md](docs/RELEASE.md#required-quality-gates) that apply to the change.
