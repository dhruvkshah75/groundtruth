# GroundTruth Local Development Setup

This project uses [uv](https://docs.astral.sh/uv/) to manage Python, the virtual environment, dependencies, and the lockfile. Do not manually create or activate `.venv`, and do not commit generated databases or API keys.

## Prerequisites

- Git
- `uv` (this project targets Python 3.11+)

Install `uv` using the official instructions for your operating system: <https://docs.astral.sh/uv/getting-started/installation/>. Confirm the installation:

```bash
uv --version
```

## First-time setup

```bash
git clone <YOUR-REPOSITORY-URL>
cd groundtruth
uv python install 3.11
uv sync --all-groups
```

`uv sync` creates `.venv` and installs exactly the package versions recorded in `uv.lock`. You do not need to run `source .venv/bin/activate`; use `uv run` instead.

## Everyday commands

```bash
# Run the automated test suite
uv run pytest

# Run a particular test while developing
uv run pytest tests/integration/test_groundedness.py -q

# Run the composed agent demo
uv run python -m src.main

# Start the dashboard once implemented
uv run streamlit run src/observability/dashboard.py

# Format and lint before opening a pull request
uv run ruff format .
uv run ruff check .
```

## Adding a dependency

Only add packages needed by the issue you own. `uv add` updates both `pyproject.toml` and `uv.lock`; commit both files together.

```bash
# Runtime dependency
uv add pydantic networkx

# Development-only dependency
uv add --dev pytest ruff

# Optional UI integration dependency
uv add streamlit
```

Never edit `uv.lock` by hand. Before adding a new package, check whether it can be solved using the standard library, Pydantic, SQLite, or NetworkX.

## LLM configuration

The deterministic agent and all tests must work with no LLM key. The optional Groq adapter reads `GROQ_API_KEY` only when explicitly selected. Put local secrets in `.env` (which must be gitignored):

```text
GROQ_API_KEY=replace-with-your-personal-key
```

Do not paste keys in source code, issues, pull requests, screenshots, or commits. Use mocked LLM responses in tests.

## Healthy-clone check

Before claiming a setup or integration issue is complete, run:

```bash
uv sync --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
```

All four commands must pass from a fresh clone. If a command modifies tracked files, include those changes in the same pull request.

## Git workflow

1. Create a branch named `gt-<issue-number>-short-description`.
2. Make one focused change set for one issue.
3. Run the healthy-clone checks relevant to your change.
4. Open a PR that links its issue and lists contracts changed, tests added, and sample behavior.
5. Rebase or merge the latest `main` only after resolving conflicts deliberately; never discard another teammate's changes.
