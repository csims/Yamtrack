# Repository Guidelines

- Ask if any task is unclear or seems unfeasible.

## Project Structure & Module Organization
- `src/` holds the Django project and apps. Key apps include `app/`, `events/`, `lists/`, `integrations/`, and `users/`.
- `src/config/` contains settings, Celery, and WSGI (`settings.py`, `test_settings.py`, `celery.py`).
- `src/templates/` and `src/static/` hold HTML templates and front-end assets; icons, CSS, JS, and images live under `src/static/`.
- Tests live alongside apps (for example `src/app/tests/`, `src/events/tests/`) with pytest discovery enabled.
- `agent-docs/` contains agent-facing documentation (for example `agent-docs/history-tracking.md`).

## Build, Test, and Development Commands
- `python -m pip install -U -r requirements-dev.txt` installs dev dependencies.
- `npm install` installs tailwindcss.
- `python manage.py migrate` applies database migrations (run from `src/`).
- `honcho start` starts all development tasks (Django dev server, Celery worker and beat, Tailwind in watch mode).
- `docker-compose up -d` starts the Docker deployment stack (SQLite by default).

## Coding Style & Naming Conventions
- Python targets 3.14.x; follow standard 4-space indentation and Django conventions.
- Linting uses Ruff (`ruff check .`) with a broad rule set and local ignores in `pyproject.toml`.
- Templates are formatted with DjLint (2-space indent per `pyproject.toml`).
- Use snake_case for modules and tests; Django apps and templates follow existing naming patterns.

## Testing Guidelines
- Tests use `pytest` with `pytest-django`; settings module is `config.test_settings`.
- Naming convention: `test_*.py` or `*_tests.py` (see `pytest.ini`).
- Run all tests with `pytest`; add focused tests next to the relevant app.

## Commit & Pull Request Guidelines
- Recent commits mix concise verbs and optional prefixes like `feat:` or `refactor:` and often include issue IDs (e.g., `#802`).
- Keep commits scoped and descriptive; include issue links when relevant.
- PRs should include a clear summary, linked issues, migration notes, and screenshots for UI changes.

## Configuration & Security Notes
- Local dev expects a `.env` file with API keys and `SECRET`; see `README.md` for the base list.
- Redis is required for Celery; Docker users can start a standalone Redis container as documented.

## Changes Since Forking
- Changes that have been made to this repo after forking it from the original start at commit hash 6cb3c458
