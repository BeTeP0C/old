# AGENTS.md

## Project
WorkTime Sync — backend-система для актуализации рабочего времени сотрудников.

## Tech stack
- Python 3.12
- FastAPI
- PostgreSQL 16
- SQLAlchemy 2.0 async
- Alembic
- Pydantic v2
- JWT auth
- VK OAuth через httpx
- pytest + pytest-asyncio
- ruff + mypy
- Docker Compose

## Architecture rules
- Use API-first backend style.
- Keep SQLAlchemy models separate from Pydantic schemas.
- Keep business logic in services.
- Keep calculations in a pure `analytics` module where possible.
- Do not connect Google Calendar, Jira, HR systems in MVP.
- Use CSV/JSON/manual/mock import instead of real external integrations.
- Recommendations are generated on the fly and are not stored in DB in MVP.
- `employee_metrics` stores the latest snapshot only, not history.

## Expected project layout

app/
  main.py
  core/
    config.py
    security.py
  db/
    base.py
    session.py
    models/
  api/
    deps.py
    v1/
      router.py
      endpoints/
  schemas/
  services/
  repositories/
  analytics/
  importers/
  auth/
tests/
alembic/
docker-compose.yml
pyproject.toml
.env.example

## Commands
- Run app: `uvicorn app.main:app --reload`
- Run tests: `pytest`
- Run lint: `ruff check .`
- Run type check: `mypy app`
- Run migrations: `alembic upgrade head`
- Create migration: `alembic revision --autogenerate -m "message"`

## Done means
- Code is typed.
- Public endpoints have Pydantic request/response schemas.
- DB changes include Alembic migration.
- Tests are added for changed business logic.
- `pytest`, `ruff check .`, and `mypy app` pass or failures are clearly reported.

"Не борись с ошибками! Каждый раз, когда ты сталкиваешься с одной и той же ошибкой дважды, изучи веб и найди 3–5 возможных способов её исправления. Затем выбери самое эффективное решение и реализуй его."
