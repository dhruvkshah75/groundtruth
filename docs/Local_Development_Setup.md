# GroundTruth Local Development Setup

This project uses [uv](https://docs.astral.sh/uv/) to manage Python, the virtual environment, dependencies, and the lockfile. Do not manually create or activate `.venv`, and do not commit generated databases or API keys.

## Prerequisites

- Git
- `uv` (this project targets Python 3.11+)
- Node.js 20.19+ and npm (for the TypeScript/React frontend)

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

# Format and lint before opening a pull request
uv run ruff format .
uv run ruff check .
```

## Frontend setup

The browser interface is a TypeScript/React app built with Vite. During development, Vite proxies `/api` requests to the Python server at `http://127.0.0.1:8765`.

Open two terminals from the repository root.

Terminal 1 — Python API:

```bash
uv run python -m src.web.server
```

Terminal 2 — React development server:

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>.

For a single-server local run, build the frontend and then start the Python server:

```bash
cd frontend
npm install
npm run build
cd ..
uv run python -m src.web.server
```

Open <http://127.0.0.1:8765>. Scenario state and SQLite facts live in memory for the server process and are isolated by browser session.

## Adding a Python dependency

Only add packages needed by the issue you own. `uv add` updates both `pyproject.toml` and `uv.lock`; commit both files together.

```bash
# Runtime dependency
uv add pydantic networkx

# Development-only dependency
uv add --dev pytest ruff
```

Never edit `uv.lock` by hand. Before adding a new package, check whether it can be solved using the standard library, Pydantic, SQLite, or NetworkX.

## LLM configuration

GroundTruth supports both a live LLM ReAct loop (backed by Groq) and an explicit offline deterministic mode (using `RuleBasedIntentProvider`).

### Environment variables and `.env`

Create a `.env` file in the repository root (ignored by Git) or set environment variables in your shell:

```bash
# Required for live LLM ReAct mode
GROQ_API_KEY="gsk_..."

# Optional model selection (default: llama-3.3-70b-versatile)
GROQ_MODEL="llama-3.3-70b-versatile"

# Optional explicit mode override: "live" or "offline" (default: "live" if key present, else "offline")
GROUNDTRUTH_PROVIDER_MODE="live"
```

### Provider modes and behaviors

1. **Live LLM ReAct mode (`GROUNDTRUTH_PROVIDER_MODE=live` or `GROQ_API_KEY` present):**
   - The agent invokes Groq using official function calling with strict schemas (`current_route_status`, `current_object_perception`, etc.).
   - Python validates the proposed function call, executes approved Tier 1/Tier 3 operations, and evaluates epistemic ground truth.
   - Tool execution results are returned to the model as verified tool output; the model synthesizes a concise grounded response.
   - `/api/health` reports `status: "ok"`, `provider: "GroqIntentProvider"`, `llm_ready: true`.

2. **Unconfigured live mode (live selected without `GROQ_API_KEY`):**
   - `/api/health` reports `status: "degraded"` and `llm_ready: false` with a clear setup error message.
   - The UI displays an unconfigured warning banner and does **not** silently fall back to rule-based answers or fabricate results.
   - Submitting questions to `/api/ask` returns HTTP 503 (`provider_unavailable`).

3. **Explicit offline mode (`GROUNDTRUTH_PROVIDER_MODE=offline`):**
   - Uses `RuleBasedIntentProvider` for deterministic testing and offline local demonstrations.
   - `/api/health` reports `provider: "RuleBasedIntentProvider"`, `provider_mode: "Offline (deterministic rules)"`.

4. **Testing safety:**
   - Automated tests (`uv run pytest`) run completely offline using injectable client doubles (`FakeGroqClient`).
   - Never commit API keys or credentials to Git.

## Healthy-clone check

Before claiming a setup or integration issue is complete, run:

```bash
uv sync --all-groups
uv run pytest
uv run ruff format --check .
uv run ruff check .
```

## Continuous integration

GitHub Actions runs the same core checks for every push and pull request. The backend job installs the locked Python dependencies with `uv`, runs the full pytest suite, checks Ruff formatting, and runs Ruff lint. The frontend job installs the locked npm dependencies and runs `npm run build`, which includes the TypeScript check and production Vite build. The workflow is defined in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml).

All four commands must pass from a fresh clone. If a command modifies tracked files, include those changes in the same pull request.

## Git workflow

1. Create a branch named `gt-<issue-number>-short-description`.
2. Make one focused change set for one issue.
3. Run the healthy-clone checks relevant to your change.
4. Open a PR that links its issue and lists contracts changed, tests added, and sample behavior.
5. Rebase or merge the latest `main` only after resolving conflicts deliberately; never discard another teammate's changes.
