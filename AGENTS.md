# Repository Guidelines

## Project Structure & Module Organization

Core application code lives in `app/`. Use `app/agent/` for conversational logic, prompts, shared contracts, and deterministic helpers; `app/api/routes/` for FastAPI endpoints; `app/scheduler/` for APScheduler jobs; `app/models/` and `app/db/` for persistence; and `app/services/` for Telegram integration. Database migrations live in `alembic/`. Regression and harness utilities live in `tests/` and `tests/harness/`. Technical notes in `.techdebt/` are local-only and ignored by Git.

## Build, Test, and Development Commands

- `python -m venv venv && source venv/bin/activate` creates the local environment.
- `pip install -r requirements.txt` installs runtime and dev dependencies.
- `docker compose --env-file .env.local up -d` starts local PostgreSQL on port `5433`.
- `ENV_FILE=.env.local alembic upgrade head` applies migrations against the local DB.
- `ENV_FILE=.env.local uvicorn app.main:app --reload` runs the API locally.
- `ENV_FILE=.env.local python cli.py` runs the safe CLI workflow.
- `ENV_FILE=.env.local venv/bin/python tests/run_harness_smoke.py phase-1-smoke` runs the harness smoke check.
- `ENV_FILE=.env.local venv/bin/python test_deploy.py` runs the full deploy regression suite.

## Coding Style & Naming Conventions

Target Python `3.12`. Follow existing style: 4-space indentation, snake_case for functions/modules, PascalCase for classes, and short docstrings only where they add context. Keep business rules deterministic and centralized; prefer adding helpers in `app/agent/contracts.py` or `app/agent/dates.py` instead of duplicating strings or date logic in `sara_agent.py` or `scheduler/jobs.py`.

## Testing Guidelines

Use `unittest`-style tests. Add focused tests near the affected domain, for example `tests/test_dates.py` for date semantics and `tests/test_operation_contracts.py` for structured write results. Name tests `test_<behavior>`. Validate narrow changes first, then run `test_deploy.py` before shipping behavior that touches agent flows, state, or scheduler logic.

## Commit & Pull Request Guidelines

Recent history uses concise, imperative commits such as `techdebt: centralize listing period ranges`, `hotfix: verify single-task completion before confirming`, and `chore: ignore local techdebt notes`. Keep the prefix meaningful (`techdebt`, `hotfix`, `chore`). PRs should describe user-visible behavior, affected flows, validation commands run, and any env or migration impact. Include screenshots only for UI or Telegram message-format changes.

## Security & Configuration Tips

Do not commit real secrets. Use `ENV_FILE=.env.local` for local runs and keep production webhook settings untouched during harness testing. Prefer the fake Telegram transport in `tests/harness/telegram.py` for local verification.
