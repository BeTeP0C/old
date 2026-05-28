# WorkTime Sync Backend

FastAPI MVP backend for storing employees, teams, schedules, exceptions, imported activity events, calculated metrics, recommendations, and dashboard summaries.

Full production-oriented technical documentation: [docs/technical-documentation.md](docs/technical-documentation.md).

## Stack

- Python 3.12
- FastAPI
- PostgreSQL 16
- SQLAlchemy 2.0 async
- Alembic
- Pydantic v2
- VK OAuth + JWT
- pytest, ruff, mypy
- Docker Compose

## Setup

```powershell
python -m venv venv
venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
```

Update `.env` before real VK OAuth usage:

- `JWT_SECRET_KEY`
- `VK_CLIENT_ID`
- `VK_CLIENT_SECRET`
- `VK_REDIRECT_URI`

## Database

PostgreSQL is exposed on host port `55432` to avoid conflicts with local PostgreSQL instances on `5432`.

```powershell
docker compose up -d
venv\Scripts\python.exe -m alembic upgrade head
```

Useful migration commands:

```powershell
venv\Scripts\python.exe -m alembic current
venv\Scripts\python.exe -m alembic check
venv\Scripts\python.exe -m alembic revision --autogenerate -m "message"
```

## Run API

```powershell
venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Health checks:

- `GET /health`
- `GET /api/v1/health`

Swagger:

- `http://127.0.0.1:8000/docs`

## Tests And Quality

```powershell
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m mypy app
```

Some integration tests use PostgreSQL. If the database is unavailable, those tests skip where appropriate.

## MVP Endpoints

Auth:

- `GET /api/v1/auth/vk/login`
- `GET /api/v1/auth/vk/callback`
- `GET /api/v1/auth/me`

Employees and schedules:

- `POST /api/v1/employees`
- `GET /api/v1/employees`
- `GET /api/v1/employees/{employee_id}`
- `PATCH /api/v1/employees/{employee_id}`
- `POST /api/v1/employees/{employee_id}/schedules`
- `GET /api/v1/employees/{employee_id}/schedules/active`
- `POST /api/v1/employees/{employee_id}/exceptions`
- `GET /api/v1/employees/{employee_id}/exceptions`

Teams:

- `POST /api/v1/teams`
- `GET /api/v1/teams`
- `GET /api/v1/teams/{team_id}`
- `PATCH /api/v1/teams/{team_id}`
- `POST /api/v1/teams/{team_id}/members`
- `DELETE /api/v1/teams/{team_id}/members/{employee_id}`
- `GET /api/v1/teams/{team_id}/availability`
- `POST /api/v1/teams/{team_id}/meeting-recommendations`

Activity events:

- `POST /api/v1/import/events/csv`
- `POST /api/v1/import/events/json`
- `POST /api/v1/events/manual`
- `GET /api/v1/employees/{employee_id}/events`

Recommendations and dashboard:

- `GET /api/v1/recommendations`
- `GET /api/v1/employees/{employee_id}/recommendations`
- `GET /api/v1/teams/{team_id}/recommendations`
- `GET /api/v1/dashboard/summary`

## Auth Notes

Write endpoints are protected by JWT bearer authentication. The token contains:

- `employee_id`
- `role`
- `exp`

VK access tokens are not stored in the MVP.

## Known Limitations

- No Google Calendar, Jira, or HR integrations in MVP.
- Activity events are imported manually through CSV/JSON/manual/mock flows.
- Recommendations are generated on demand and are not stored in DB.
- Meeting recommendations are rule-based and use a 30-minute slot granularity.
- Dashboard summary uses stored `employee_metrics`; it does not recalculate heavy metrics.
- Role-based authorization is minimal: authenticated write access is enforced, but fine-grained RBAC is not implemented yet.
- The default `JWT_SECRET_KEY` in `.env.example` is not production-safe.
