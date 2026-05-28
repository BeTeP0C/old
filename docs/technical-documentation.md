# WorkTime Sync: production-oriented technical documentation

Документ описывает фактическое состояние backend-проекта WorkTime Sync и инженерные решения, заложенные в текущем MVP. Формат рассчитан на onboarding, техническую защиту, передачу проекта другой команде и дальнейшее развитие до production-системы.

## 1. Общая информация о проекте

### Назначение

WorkTime Sync — backend-система для хранения и анализа рабочих графиков сотрудников, исключений из расписания, команд, импортированных событий активности, метрик нагрузки и рекомендаций по актуализации рабочего времени.

Система помогает понять:

- у кого график устарел;
- какие события выходят за рабочее расписание;
- какие сотрудники перегружены встречами или активностями;
- когда команда может провести встречу;
- какие действия стоит предпринять менеджеру или HR.

### Какую проблему решает

В распределенных командах рабочее время часто хранится в разных местах: HR-системе, календарях, Jira, чатах, личных договоренностях. Из-за этого возникают типовые проблемы:

| Проблема | Последствие | Как решает WorkTime Sync |
|---|---|---|
| Неактуальные графики | Встречи ставятся вне рабочего времени | Хранит `work_schedules.last_updated_at`, считает устаревание и рекомендации |
| Разные часовые пояса | Ошибки в планировании встреч | Хранит timezone сотрудника, расписания и событий |
| Перегруз встречами | Выгорание, низкая фокусировка | Хранит метрики `load_level`, `risk_score`, `risk_level` |
| Ручной импорт событий | Дубли и грязные данные | Импорт CSV/JSON с валидацией и дедупликацией по `(source, external_id)` |
| Непрозрачная доступность команды | Долгий подбор слотов | Считает окна доступности и рекомендует meeting windows |

### Основные сценарии использования

1. Администратор или менеджер создает сотрудников и команды.
2. Для сотрудника задается активный рабочий график.
3. Добавляются исключения: отпуск, больничный, недоступность.
4. События активности импортируются из CSV/JSON или создаются вручную.
5. Сервис строит dashboard summary на базе сохраненных метрик.
6. Менеджер получает рекомендации по сотруднику, команде или всей организации.
7. Команда получает список оптимальных окон для встречи.

### Целевая аудитория

| Роль | Потребность |
|---|---|
| Backend-разработчики | Поддерживать API, бизнес-логику, миграции, тесты |
| Team leads | Видеть риски расписаний и загрузки |
| HR/People Operations | Отслеживать устаревшие графики и перегрузку |
| Интеграционная команда | Подключить календари, HRIS, Jira, BI |
| DevOps/SRE | Развернуть API, БД, мониторинг и CI/CD |

### Пользовательские роли MVP

В MVP роли нужно отражать как конкретные бизнес-роли, а не как абстрактные permissions. Поле `employees.role` может хранить одно из следующих значений:

| Роль | Основная задача в MVP | Приоритетный экран/сценарий |
|---|---|---|
| `analyst` | Смотреть метрики, выявлять паттерны перегрузки и конфликтов расписания | Dashboard summary, рекомендации по сотрудникам и командам |
| `pm` | Планировать встречи и контролировать загрузку проектной команды | Team availability, meeting recommendations |
| `admin` | Заводить сотрудников, команды, графики и поддерживать справочники | CRUD сотрудников, команд, расписаний и исключений |
| `hr` | Следить за актуальностью графиков, отпусками, больничными и рисками выгорания | Устаревшие графики, исключения, high/critical risk |
| `leader` | Принимать управленческие решения по команде и эскалировать риски | Team recommendations, dashboard по рискам |

Для MVP достаточно хранить роль как строку и использовать ее в документации, тестовых данных и будущей RBAC-матрице. Полноценные permissions остаются production-улучшением.

### Приоритизация сотрудников по рискам планирования встреч

В первую очередь под рассмотрение должны попадать сотрудники, у которых встречные события и рабочий график дают высокий риск ошибки планирования. Приоритет строится не по должности, а по комбинации метрик расписания, доступности и нагрузки.

| Приоритет | Какие сотрудники попадают | Почему важно для планирования встреч | MVP-сигналы |
|---|---|---|---|
| P0 | `risk_level = critical` или `risk_score` близок к максимуму | Встречи с высокой вероятностью ставятся вне доступности или усугубляют перегрузку | высокий `risk_score`, высокий `conflict_rate`, много событий вне графика |
| P1 | Много событий вне рабочего расписания | Есть системная проблема с актуальностью графика или календаря | `outside_events_count > 0`, высокий `conflict_rate` |
| P2 | Перегруз встречами | Новые встречи могут снижать фокусировку и повышать риск выгорания | высокий `load_level`, много activity events в рабочем окне |
| P3 | Устаревший график | Доступность может быть рассчитана по неактуальным данным | большой `days_since_update`, низкий `actuality_score` |
| P4 | Несовпадение timezone сотрудника и активного графика | Слоты встреч могут быть рассчитаны с ошибкой часового пояса | `employee.timezone != work_schedule.timezone` |
| P5 | Участники ключевых команд без активного графика или с длинными исключениями | Невозможно корректно подобрать командный слот | нет active schedule, есть `vacation`/`sick`/`sick_leave` в диапазоне встречи |

Фокус MVP: показывать сначала P0-P2, потому что они напрямую влияют на риск неверно назначенной встречи. P3-P5 можно использовать как объясняющие рекомендации и подсказки для HR, PM и руководителя.

### MVP проекта

В текущем MVP реализовано:

- FastAPI HTTP API;
- PostgreSQL schema через SQLAlchemy models и Alembic migrations;
- VK OAuth login flow с выдачей JWT access token;
- CRUD для сотрудников, команд, участников команд;
- расписания и исключения;
- импорт activity events через CSV/JSON;
- ручное создание activity event;
- дедупликация импортированных событий;
- dashboard summary;
- rule-based рекомендации;
- расчет доступности команды;
- рекомендации слотов встреч с шагом 30 минут;
- pytest suite для auth, CRUD, import, analytics, dashboard, recommendations.

Не реализовано в MVP:

- refresh tokens;
- полноценный RBAC/permissions;
- Celery workers;
- Redis cache/broker;
- Nginx reverse proxy config;
- Prometheus/Grafana;
- реальные интеграции Google Calendar/Jira/HRIS;
- хранение истории метрик;
- persisted recommendations.

### Основные ограничения системы

| Ограничение | Причина | Риск | Production-улучшение |
|---|---|---|---|
| JWT только access token | MVP simplicity | Нельзя безопасно ротировать сессии | Добавить refresh token, token revocation, session table |
| RBAC минимальный | Быстрый MVP | Любой authenticated пользователь может делать write operations | Role/permission matrix |
| Recommendations on demand | Простота и отсутствие фоновых задач | Дорогие расчеты при росте данных | Materialized recommendations, async jobs |
| `employee_metrics` хранит последний snapshot | Dashboard читает быстро | Нет исторической аналитики | `employee_metric_snapshots` с time-series retention |
| Нет connection pool в runtime (`NullPool`) | Подходит для тестов/dev, проще жизненный цикл | Неэффективно в production | AsyncAdaptedQueuePool с лимитами |
| Нет централизованных exception handlers | Ошибки мапятся в endpoints | Дублирование и риск разных форматов ошибок | Global exception handlers |
| Нет reverse proxy | MVP локальный запуск | TLS/rate limits/static security headers не решены | Nginx/Traefik/Ingress |

## 2. Архитектурный обзор

### Общая архитектура

Проект построен как модульный монолит с layered architecture:

- `api` — HTTP boundary, маршруты, FastAPI dependencies;
- `schemas` — Pydantic DTO для request/response;
- `services` — use cases и бизнес-правила;
- `repositories` — SQLAlchemy query layer;
- `models` — ORM mapping и связи;
- `analytics` — чистые расчетные функции без зависимости от FastAPI/SQLAlchemy;
- `importers` — parsing/normalization внешних payload;
- `auth` — VK OAuth client;
- `core` — настройки и security helpers;
- `db` — engine/session lifecycle.

Эта архитектура выбрана потому, что продукт пока не требует микросервисов, но уже содержит несколько доменных областей: сотрудники, расписания, команды, события, метрики, рекомендации. Модульный монолит дает низкую операционную сложность и при этом сохраняет границы ответственности.

### Component diagram

```mermaid
flowchart TB
    Client[Client / Swagger / Integrations]
    FastAPI[FastAPI app]
    API[api.v1 endpoints]
    Deps[api.deps<br/>DB session + JWT auth]
    Schemas[Pydantic schemas]
    Services[Service layer<br/>use cases]
    Repos[Repository layer<br/>SQLAlchemy queries]
    Models[ORM models]
    Analytics[analytics<br/>pure calculations]
    Importers[importers<br/>CSV/JSON parsing]
    Auth[auth.vk_oauth<br/>VK OAuth HTTP client]
    DB[(PostgreSQL)]
    VK[VK OAuth API]

    Client --> FastAPI --> API
    API --> Deps
    API --> Schemas
    API --> Services
    Services --> Repos --> Models --> DB
    Services --> Analytics
    API --> Importers --> Schemas
    Services --> Auth --> VK
```

### Почему выбрана layered architecture

Layered architecture хорошо подходит для API-first backend:

- HTTP-слой не знает SQL-запросов.
- Сервисный слой не зависит от конкретного transport protocol.
- Репозитории инкапсулируют SQLAlchemy.
- Pydantic schemas отделены от ORM models.
- Analytics можно тестировать как чистые функции.

Альтернативы:

| Подход | Плюсы | Минусы | Почему не выбран сейчас |
|---|---|---|---|
| Fat controllers | Быстро писать первый прототип | Бизнес-логика смешивается с HTTP | Проект уже имеет несколько use cases, контроллеры быстро стали бы неподдерживаемыми |
| Active Record | Меньше файлов | Модели знают слишком много о бизнес-операциях | SQLAlchemy 2.0 в проекте используется как Data Mapper |
| Hexagonal architecture | Сильная изоляция domain core | Больше boilerplate | Для MVP избыточно, но текущие слои близки к hexagonal и позволяют эволюцию |
| Microservices | Независимое масштабирование bounded contexts | Сложность deploy, observability, distributed transactions | Для MVP данных и команды разработки это преждевременно |

### Взаимодействие компонентов

HTTP запрос проходит через FastAPI routing, dependency injection создает `AsyncSession` и при необходимости проверяет JWT. Endpoint валидирует payload через Pydantic, вызывает service. Service применяет бизнес-правила, работает с одним или несколькими repositories, делает commit/rollback. Repository выполняет SQLAlchemy statements. Ответ конвертируется в response schema.

### Data flow: create employee

```mermaid
flowchart LR
    A[POST /api/v1/employees] --> B[OAuth2 Bearer dependency]
    B --> C[decode_access_token]
    C --> D[EmployeeRepository.get current employee]
    D --> E[EmployeeCreate validation]
    E --> F[EmployeeService.create]
    F --> G[EmployeeRepository.create]
    G --> H[(employees)]
    H --> I[session.commit]
    I --> J[EmployeeResponse]
```

### Sequence diagram: import activity events

```mermaid
sequenceDiagram
    participant Client
    participant Endpoint as activity_events endpoint
    participant Importer as CSV/JSON importer
    participant Service as ActivityEventService
    participant EmpRepo as EmployeeRepository
    participant EventRepo as ActivityEventRepository
    participant DB as PostgreSQL

    Client->>Endpoint: POST /import/events/json
    Endpoint->>Endpoint: validate JWT dependency
    Endpoint->>Importer: parse_json_activity_events(payload)
    Importer-->>Endpoint: list[ActivityEventCreate]
    Endpoint->>Service: import_events(events)
    Service->>EmpRepo: list_by_ids(employee_ids)
    EmpRepo->>DB: SELECT employees WHERE id IN (...)
    Service->>EventRepo: list_existing_external_keys(keys)
    EventRepo->>DB: SELECT source, external_id
    loop each event
        Service->>Service: validate date range + employee exists
        Service->>EventRepo: create(ActivityEvent)
        EventRepo->>DB: INSERT activity_events
    end
    Service->>DB: COMMIT or ROLLBACK
    Service-->>Endpoint: ActivityEventImportResult
    Endpoint-->>Client: 200 JSON
```

### Sequence diagram: team meeting recommendations

```mermaid
sequenceDiagram
    participant Client
    participant Endpoint as teams endpoint
    participant Service as TeamAvailabilityService
    participant Repos as Repositories
    participant Analytics as analytics.availability
    participant DB as PostgreSQL

    Client->>Endpoint: POST /teams/{team_id}/meeting-recommendations
    Endpoint->>Endpoint: JWT + request validation
    Endpoint->>Service: recommend_meetings(team_id, payload)
    Service->>Repos: load team, members, employees, schedules, exceptions, events
    Repos->>DB: SELECT ...
    Service->>Analytics: calculate_employee_availability(...)
    Analytics-->>Service: free windows per employee
    Service->>Analytics: recommend_meeting_windows(...)
    Analytics-->>Service: top 3 scored slots
    Service-->>Endpoint: list[MeetingRecommendationResponse]
    Endpoint-->>Client: 200 JSON
```

### Dependency graph

```mermaid
flowchart BT
    endpoints --> schemas
    endpoints --> deps
    endpoints --> services
    deps --> security
    deps --> session
    deps --> repositories
    services --> repositories
    services --> analytics
    services --> auth_client
    repositories --> models
    session --> config
    auth_client --> config
    security --> config
    alembic --> models
    alembic --> config
```

### Плюсы и минусы выбранного подхода

Плюсы:

- понятная навигация по проекту;
- бизнес-логика тестируется отдельно от HTTP;
- чистые analytics-модули легко покрывать unit tests;
- ORM и DTO не смешаны;
- возможна будущая миграция к hexagonal architecture.

Минусы:

- больше файлов, чем в простом CRUD-прототипе;
- сервисы вручную управляют транзакциями;
- нет общего Unit of Work abstraction;
- часть маппинга ошибок дублируется в endpoints.

### Какие проблемы архитектура решает

Архитектура предотвращает наиболее дорогие для backend-проекта проблемы:

- разрастание endpoints до "god functions";
- привязку бизнес-правил к HTTP;
- повторение SQL в разных частях приложения;
- смешивание request validation и database mapping;
- невозможность тестировать расчетные алгоритмы без БД.

## 3. Стек технологий

### Текущий runtime stack

| Технология | Зачем используется | Почему выбрана | Альтернативы | Плюсы | Минусы |
|---|---|---|---|---|---|
| Python 3.12 | Основной язык backend | Современная типизация, async ecosystem, поддержка FastAPI | Go, Java/Kotlin, Node.js | Быстрая разработка, богатые библиотеки | Ниже raw performance, чем Go/Java |
| FastAPI | HTTP API, routing, DI, OpenAPI | Хорошо работает с Pydantic и async endpoints | Django REST Framework, Flask, Litestar | Автодокументация, dependency injection, type hints | DI проще, чем в enterprise containers; нужно дисциплинировать слои |
| Uvicorn | ASGI server для локального запуска и production workers | Стандартный сервер для FastAPI | Hypercorn, Gunicorn+UvicornWorker | Быстрый ASGI, reload в dev | В production нужен process manager/worker strategy |
| Pydantic v2 | Валидация request/response/config DTO | FastAPI-native, строгая типизация | Marshmallow, dataclasses, attrs | Быстрая валидация, JSON schema | Требует знания v2 API |
| pydantic-settings | Env-based конфигурация | Единая typed config model | Dynaconf, environs, ручной dotenv | Валидируемые настройки | Secrets надо выносить из `.env` в secret manager |
| SQLAlchemy 2.0 async | ORM и query layer | Data Mapper, typed mapped columns, async DB access | Tortoise ORM, Django ORM, raw SQL, SQLModel | Гибкий SQL, зрелость, миграции через Alembic | Выше порог входа |
| asyncpg | Async PostgreSQL driver | Быстрый драйвер для SQLAlchemy async URL | psycopg async, aiopg | Производительность, зрелость | Специфика async lifecycle |
| PostgreSQL 16 | Primary relational database | Сильные constraints, индексы, UUID, массивы, timezone-aware datetime | MySQL, SQLite, MongoDB | ACID, rich SQL, production reliability | Нужна эксплуатация и миграции |
| Alembic | Миграции БД | Нативная связка с SQLAlchemy | Flyway, Liquibase, Django migrations | Versioned schema, autogenerate | Требует review миграций |
| python-jose | JWT encode/decode | Простая интеграция HS256 JWT | PyJWT, Authlib JWT | Достаточно для MVP | Нужно аккуратно настраивать algorithms/secret rotation |
| httpx | Async HTTP client для VK OAuth | Совместим с async сервисами | aiohttp, requests | Async, удобное тестирование | Нужно управлять timeout/retry в production |
| pytest + pytest-asyncio | Unit/integration tests | Де-факто стандарт Python | unittest, nose | Fixtures, async tests | Требует дисциплины с БД fixtures |
| ruff | Lint/import sorting | Быстрый единый инструмент | flake8, isort, pylint | Очень быстрый | Не заменяет архитектурный review |
| mypy | Static type checking | `strict = true` снижает runtime surprises | pyright | Раннее обнаружение ошибок типов | Иногда требует дополнительной аннотации |
| Docker Compose | Локальный PostgreSQL | Простая воспроизводимость dev env | Podman Compose, локальная установка Postgres | Быстрый старт | Не является production orchestrator |

### Зависимости, заложенные под развитие

| Технология | Статус в проекте | Зачем может понадобиться | Что нужно перед production use |
|---|---|---|---|
| Redis | Есть в `requirements.txt`, контейнера нет | Cache, Celery broker, rate limiting | Добавить compose service, eviction policy, auth/TLS |
| Celery | Есть в `requirements.txt`, worker не реализован | Фоновый пересчет метрик, импорт интеграций | Worker entrypoint, broker/result backend, retries, idempotency |
| structlog | Есть в dependencies, не подключен | Structured JSON logs | Настроить middleware/request context |
| pandas/openpyxl | Есть в dependencies | Excel import/export | Добавить import endpoints и memory limits |
| Nginx | Не реализован | TLS termination, compression, rate limits, headers | Добавить конфиг и deployment topology |
| Kafka | Не используется | Event streaming между сервисами | Нужен только при переходе к event-driven/microservices |

### Почему PostgreSQL, а не NoSQL

Данные проекта реляционные: сотрудник имеет графики, события, метрики, команды через many-to-many. Нужны foreign keys, unique constraints, индексы и транзакции. PostgreSQL предотвращает рассинхронизацию данных на уровне БД. NoSQL можно рассматривать для event log или аналитического хранилища, но primary source of truth лучше оставить relational.

### Почему async SQLAlchemy

API потенциально выполняет I/O-bound операции: БД, OAuth HTTP, импорты. Async дает возможность эффективно обслуживать параллельные запросы без выделения thread per request. Для CPU-heavy analytics async не ускоряет вычисления; такие задачи лучше выносить в Celery или отдельный worker.

## 4. Структура проекта

### Дерево проекта

```text
WorkTime-Sync/
├── app/
│   ├── main.py
│   ├── analytics/
│   │   ├── availability.py
│   │   ├── meeting.py
│   │   ├── metrics.py
│   │   └── recommendations.py
│   ├── api/
│   │   ├── deps.py
│   │   └── v1/
│   │       ├── router.py
│   │       └── endpoints/
│   │           ├── activity_events.py
│   │           ├── auth.py
│   │           ├── dashboard.py
│   │           ├── employees.py
│   │           ├── recommendations.py
│   │           └── teams.py
│   ├── auth/
│   │   └── vk_oauth.py
│   ├── core/
│   │   ├── config.py
│   │   └── security.py
│   ├── db/
│   │   ├── base.py
│   │   └── session.py
│   ├── importers/
│   │   └── activity_events.py
│   ├── models/
│   │   ├── activity_event.py
│   │   ├── base.py
│   │   ├── employee.py
│   │   ├── employee_metric.py
│   │   ├── schedule_exception.py
│   │   ├── team.py
│   │   ├── team_member.py
│   │   └── work_schedule.py
│   ├── repositories/
│   │   ├── activity_events.py
│   │   ├── dashboard.py
│   │   ├── employees.py
│   │   ├── employee_metrics.py
│   │   ├── schedule_exceptions.py
│   │   ├── teams.py
│   │   ├── team_members.py
│   │   └── work_schedules.py
│   ├── schemas/
│   │   ├── activity_event.py
│   │   ├── auth.py
│   │   ├── availability.py
│   │   ├── common.py
│   │   ├── dashboard.py
│   │   ├── employee.py
│   │   ├── employee_metric.py
│   │   ├── recommendation.py
│   │   ├── schedule_exception.py
│   │   ├── team.py
│   │   ├── team_member.py
│   │   └── work_schedule.py
│   └── services/
│       ├── activity_events.py
│       ├── auth.py
│       ├── dashboard.py
│       ├── employees.py
│       ├── exceptions.py
│       ├── recommendations.py
│       ├── schedule_exceptions.py
│       ├── teams.py
│       ├── team_availability.py
│       ├── team_members.py
│       └── work_schedules.py
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 20260524_0001_initial_schema.py
│       └── 20260524_0002_activity_event_dedup_index.py
├── tests/
├── .env.example
├── alembic.ini
├── docker-compose.yml
├── pyproject.toml
├── README.md
└── requirements.txt
```

### Назначение ключевых директорий

| Директория | Назначение | Почему так |
|---|---|---|
| `app/api` | HTTP contract и FastAPI-specific glue | Отделяет transport layer от бизнес-логики |
| `app/services` | Use cases, транзакции, business errors | Сервис становится точкой orchestration |
| `app/repositories` | Все SQLAlchemy SELECT/INSERT/UPDATE/DELETE | SQL изолирован от API и domain calculations |
| `app/models` | ORM classes и relationships | Единый источник DB mapping |
| `app/schemas` | Pydantic request/response DTO | DTO не загрязняют ORM и наоборот |
| `app/analytics` | Чистые функции расчета | Можно тестировать без БД и FastAPI |
| `app/importers` | Парсинг внешних форматов | CSV/JSON особенности не попадают в services |
| `app/auth` | Внешний OAuth клиент | Интеграционный код отделен от auth service |
| `app/core` | Settings/security primitives | Shared low-level infrastructure |
| `app/db` | Engine/session factory | Единая точка управления DB connection lifecycle |
| `alembic` | Versioned schema migrations | Контролируемая эволюция БД |
| `tests` | Regression suite | Поддержка изменений без деградации |

### Как структура помогает поддержке

Если возникает баг валидации входных данных, искать нужно в `schemas` или `importers`. Если ошибка в бизнес-правиле — в `services` или `analytics`. Если неверный SQL или performance issue — в `repositories` и индексах. Такое разделение снижает cognitive load и ускоряет review.

### Как модули взаимодействуют

Endpoint не должен напрямую обращаться к ORM model или писать SQL. Нормальный путь:

```text
endpoint -> schema validation -> service -> repository -> model/session -> database
```

Исключение — response serialization: endpoint получает ORM object от service и конвертирует его через `ResponseSchema.model_validate(obj)`.

## 5. Backend архитектура

### Router layer

Router layer находится в `app/api/v1/endpoints`. Его задачи:

- объявить URL, HTTP method, response model, tags;
- принять Pydantic payload;
- получить dependencies: `AsyncSession`, current employee;
- вызвать service;
- преобразовать доменные ошибки в HTTPException;
- вернуть response schema.

Router layer не должен:

- выполнять SQL-запросы;
- содержать сложные бизнес-правила;
- вручную управлять commit/rollback;
- знать детали внешних API, кроме HTTP-контракта своего endpoint.

### Service layer

Service layer содержит use cases:

- `EmployeeService.create/update/get/list`;
- `WorkScheduleService.create/get_active`;
- `ScheduleExceptionService.create/list_for_employee`;
- `TeamService`;
- `TeamMemberService`;
- `ActivityEventService`;
- `TeamAvailabilityService`;
- `RecommendationService`;
- `DashboardService`;
- `AuthService`.

Именно здесь выполняются проверки:

- path id должен совпадать с body id;
- related entity должна существовать;
- `start_dt < end_dt`;
- duplicate external event пропускается или отклоняется;
- IntegrityError мапится в business error;
- транзакция commit/rollback завершается.

### Repository layer

Repository layer инкапсулирует SQLAlchemy. Пример паттерна:

```python
class EmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, employee_id: UUID) -> Employee | None:
        return await self.session.get(Employee, employee_id)
```

Преимущества:

- SQL-запросы не размазаны по сервисам;
- проще менять query strategy;
- проще тестировать сервисы через fake repositories в будущем;
- можно централизованно оптимизировать N+1 запросы.

Недостатки:

- дополнительный слой;
- для простого CRUD иногда выглядит избыточно;
- сейчас репозитории привязаны к SQLAlchemy session напрямую, то есть это не полный hexagonal port.

### Database layer

`app/db/session.py` создает async engine:

```python
engine = create_async_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
)
```

`pool_pre_ping=True` защищает от stale connections. `NullPool` означает, что connection pool не удерживается приложением. Это удобно для тестов и простого локального окружения, но в production обычно нужен пул:

```python
create_async_engine(
    database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
)
```

### DTO/schemas

Pydantic schemas находятся отдельно от ORM models. Это важно, потому что ORM model описывает persistence, а DTO описывает публичный API contract.

Пример:

```python
class EmployeeCreate(BaseModel):
    vk_user_id: str | None = None
    role: str
    full_name: str
    email: EmailStr | None = None
    position: str | None = None
    timezone: str
    work_format: str
```

`EmployeeResponse` использует `ConfigDict(from_attributes=True)`, чтобы сериализовать SQLAlchemy object без ручного mapping.

### Dependency injection

FastAPI DI используется для:

- DB session: `get_db_session`;
- bearer token: `OAuth2PasswordBearer`;
- current user: `get_current_employee`.

```python
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
CurrentEmployeeDep = Annotated[Employee, Depends(get_current_employee)]
```

Это удобно, потому что dependencies можно переопределять в тестах. В `tests/conftest.py` current employee override включен автоматически, кроме тестов с marker `no_auth_override`.

### Middleware

В текущем коде custom middleware нет. Production-кандидаты:

- request id/correlation id;
- structured access logs;
- CORS middleware;
- trusted host middleware;
- metrics middleware;
- exception mapping middleware.

### Lifecycle hooks

В текущем `create_app()` lifespan hooks не объявлены. Для production можно добавить:

- startup проверку подключения к БД;
- graceful shutdown;
- инициализацию structured logging;
- readiness state.

### Transactions

Сервисы управляют транзакциями вручную:

- после успешной записи вызывают `await session.commit()`;
- при `IntegrityError` вызывают `await session.rollback()`;
- read-only запросы commit не требуют.

Пример:

```python
try:
    employee = await self.employees.create(employee)
    await self.session.commit()
except IntegrityError as exc:
    await self.session.rollback()
    raise InvalidOperationError("employee with this email or VK user id already exists") from exc
```

Плюс: explicit transaction boundary в use case.

Минус: повторяется код rollback/commit. Улучшение — Unit of Work context manager:

```python
async with uow:
    employee = await uow.employees.create(employee)
```

### Unit of Work

Формального Unit of Work класса нет. Но роль UoW фактически выполняет `AsyncSession`, которую service получает через DI. Для MVP этого достаточно. При росте доменной логики стоит ввести UoW abstraction, чтобы:

- централизовать commit/rollback;
- инкапсулировать набор repositories;
- упростить тестирование сервисов;
- добавить outbox pattern для event-driven flow.

### Repository Pattern

Repository Pattern реализован явно. Каждая доменная сущность имеет repository: employees, teams, team_members, schedules, exceptions, activity_events, metrics, dashboard.

### CQRS

Полноценного CQRS нет. Но есть мягкое разделение:

- command-like endpoints: create/update/import/delete;
- query-like endpoints: list/get/dashboard/recommendations/availability.

Переход к CQRS будет оправдан, если:

- dashboard/read models станут тяжелыми;
- появятся отдельные materialized views;
- write model и read model начнут развиваться независимо.

### Event-driven части

Event-driven architecture сейчас не реализована. Activity events — это доменные данные, а не message bus events. Для production можно добавить:

- outbox table;
- background worker для пересчета метрик;
- события `activity_event_imported`, `schedule_updated`;
- Celery/Redis или Kafka в зависимости от требований надежности.

### Полный flow HTTP запроса

```mermaid
flowchart TD
    A[HTTP request] --> B[FastAPI routing]
    B --> C[Dependency resolution]
    C --> D{Protected endpoint?}
    D -- yes --> E[OAuth2 bearer token extraction]
    E --> F[JWT decode + EmployeeRepository.get]
    D -- no --> G[Pydantic request validation]
    F --> G
    G --> H[Endpoint function]
    H --> I[Service method]
    I --> J[Business validation]
    J --> K[Repository calls]
    K --> L[(PostgreSQL)]
    L --> M{Write operation?}
    M -- yes --> N[commit or rollback]
    M -- no --> O[return data]
    N --> O
    O --> P[Pydantic response serialization]
    P --> Q[HTTP response]
```

## 6. База данных

### ERD

```mermaid
erDiagram
    employees ||--o{ work_schedules : has
    employees ||--o{ schedule_exceptions : has
    employees ||--o{ activity_events : has
    employees ||--o| employee_metrics : has_latest
    employees ||--o{ team_members : belongs
    teams ||--o{ team_members : contains

    employees {
        uuid id PK
        varchar vk_user_id UK
        varchar role
        varchar full_name
        varchar email UK
        varchar position
        varchar timezone
        varchar work_format
        timestamptz created_at
        timestamptz updated_at
    }

    teams {
        uuid id PK
        varchar name
        text description
        timestamptz created_at
        timestamptz updated_at
    }

    team_members {
        uuid team_id PK,FK
        uuid employee_id PK,FK
        varchar role_in_team
        timestamptz created_at
    }

    work_schedules {
        uuid id PK
        uuid employee_id FK
        int_array work_days
        time start_time
        time end_time
        varchar timezone
        timestamptz last_updated_at
        boolean is_active
        timestamptz created_at
    }

    schedule_exceptions {
        uuid id PK
        uuid employee_id FK
        varchar type
        timestamptz start_dt
        timestamptz end_dt
        text reason
    }

    activity_events {
        uuid id PK
        uuid employee_id FK
        varchar external_id
        varchar source
        varchar event_type
        varchar title
        timestamptz start_dt
        timestamptz end_dt
        varchar timezone
        boolean is_recurring
        boolean is_outside_schedule
    }

    employee_metrics {
        uuid id PK
        uuid employee_id FK,UK
        timestamptz calculated_at
        int days_since_update
        float actuality_score
        int outside_events_count
        int total_events_count
        float conflict_rate
        float load_level
        float risk_score
        varchar risk_level
    }
```

### Таблицы, связи и ограничения

#### `employees`

Назначение: master entity сотрудника. На нее ссылаются команды, графики, исключения, события и метрики.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | UUID безопасен для distributed creation и публичных API |
| `vk_user_id` | varchar(64) | unique nullable | Связь с VK OAuth пользователем, nullable для ручного создания |
| `role` | varchar(40) | not null | Нужен для будущего RBAC |
| `full_name` | varchar(255) | not null | Отображение в UI и отчетах |
| `email` | varchar(255) | unique nullable | Уникальный корпоративный идентификатор, nullable для VK-only MVP |
| `position` | varchar(150) | nullable | Организационная информация |
| `timezone` | varchar(64) | not null | IANA timezone для расчетов доступности |
| `work_format` | varchar(30) | not null | remote/hybrid/office, влияет на future analytics |
| `created_at` | timestamptz | default now | Audit |
| `updated_at` | timestamptz | default now/onupdate | Audit |

Проблемы, которые предотвращаются:

- duplicate email;
- duplicate VK user;
- orphan child data через ORM cascade.

#### `teams`

Назначение: группировка сотрудников.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | Stable public identifier |
| `name` | varchar(150) | not null | Название команды |
| `description` | text | nullable | Описание без жесткого лимита |
| `created_at` | timestamptz | default now | Audit |
| `updated_at` | timestamptz | default now/onupdate | Audit |

#### `team_members`

Назначение: many-to-many связь `employees` и `teams`.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `team_id` | UUID | PK, FK teams.id | Участие в команде |
| `employee_id` | UUID | PK, FK employees.id | Сотрудник |
| `role_in_team` | varchar(80) | not null | Роль внутри команды |
| `created_at` | timestamptz | default now | Когда добавлен |

Composite PK `(team_id, employee_id)` предотвращает повторное добавление одного сотрудника в ту же команду. Индексы по `team_id` и `employee_id` ускоряют выборки состава команды и команд сотрудника.

#### `work_schedules`

Назначение: рабочие графики сотрудников. Система допускает несколько графиков и выбирает активный с последним `last_updated_at`.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | Идентификатор графика |
| `employee_id` | UUID | FK, index, not null | Связь с сотрудником и быстрый lookup |
| `work_days` | int[] | not null | Дни недели `datetime.weekday()`: 0..6 |
| `start_time` | time | not null | Локальное начало рабочего дня |
| `end_time` | time | not null | Локальное окончание |
| `timezone` | varchar(64) | not null | Может отличаться от employee timezone, это выявляется рекомендацией |
| `last_updated_at` | timestamptz | not null | Актуальность графика |
| `is_active` | boolean | not null | Фильтр активных графиков |
| `created_at` | timestamptz | default now | Audit |

Production-улучшение: добавить CHECK constraints:

```sql
ALTER TABLE work_schedules
ADD CONSTRAINT ck_work_schedule_time CHECK (start_time < end_time);
```

#### `schedule_exceptions`

Назначение: исключения из рабочего графика: отпуск, больничный, временная недоступность.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | Идентификатор исключения |
| `employee_id` | UUID | FK, index | Быстрый lookup по сотруднику |
| `type` | varchar(40) | not null | `vacation`, `sick`, `sick_leave`, etc. |
| `start_dt` | timestamptz | not null | Начало |
| `end_dt` | timestamptz | not null | Конец |
| `reason` | text | nullable | Комментарий |

В `TeamAvailabilityService` исключения типов `vacation`, `sick`, `sick_leave` блокируют доступность.

#### `activity_events`

Назначение: события календаря/активности, импортированные или созданные вручную.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | Идентификатор события |
| `employee_id` | UUID | FK, index | Владелец события |
| `external_id` | varchar(255) | nullable | ID из внешней системы |
| `source` | varchar(60) | not null | Источник: manual/csv/google/etc. |
| `event_type` | varchar(50) | not null | Тип события |
| `title` | varchar(255) | not null | Название |
| `start_dt` | timestamptz | not null | Начало |
| `end_dt` | timestamptz | not null | Конец |
| `timezone` | varchar(64) | not null | Исходная timezone |
| `is_recurring` | boolean | not null | Повторяемое событие |
| `is_outside_schedule` | boolean | not null | Флаг конфликта с расписанием |

Индекс:

```sql
CREATE UNIQUE INDEX uq_activity_events_source_external_id
ON activity_events (source, external_id)
WHERE external_id IS NOT NULL;
```

Почему partial unique index: manual events могут не иметь `external_id`; PostgreSQL unique с nullable не предотвращает несколько NULL. Partial index обеспечивает дедупликацию только для внешних событий.

#### `employee_metrics`

Назначение: последняя рассчитанная метрика сотрудника для dashboard и рекомендаций.

| Поле | Тип | Ограничения | Почему |
|---|---|---|---|
| `id` | UUID | PK | Идентификатор snapshot |
| `employee_id` | UUID | FK, unique, index | Одна актуальная метрика на сотрудника |
| `calculated_at` | timestamptz | not null | Время расчета |
| `days_since_update` | int | not null | Давность графика |
| `actuality_score` | float | not null | 1.0..0.0 freshness score |
| `outside_events_count` | int | not null | События вне графика |
| `total_events_count` | int | not null | Всего событий |
| `conflict_rate` | float | not null | Доля конфликтов |
| `load_level` | float | not null | Busy hours / work hours |
| `risk_score` | float | not null | Итоговый риск |
| `risk_level` | varchar(20) | not null | low/medium/high/critical |

### Cascade rules

В SQLAlchemy relationships для дочерних коллекций сотрудника указан `cascade="all, delete-orphan"`. Это ORM-level cascade, а не database `ON DELETE CASCADE`. В production стоит решить, где должна жить политика удаления:

- ORM cascade удобен для application-managed deletes;
- DB cascade надежнее при прямых SQL-операциях и интеграциях.

### SQL примеры

Создать сотрудника:

```sql
INSERT INTO employees (
    id, role, full_name, email, timezone, work_format
) VALUES (
    gen_random_uuid(), 'employee', 'Ivan Petrov', 'ivan@example.com', 'Europe/Moscow', 'remote'
);
```

Найти активный график:

```sql
SELECT *
FROM work_schedules
WHERE employee_id = :employee_id
  AND is_active IS TRUE
ORDER BY last_updated_at DESC
LIMIT 1;
```

Найти пересекающиеся события:

```sql
SELECT *
FROM activity_events
WHERE employee_id = ANY(:employee_ids)
  AND start_dt < :range_end
  AND end_dt > :range_start
ORDER BY start_dt;
```

### ORM модель

```python
class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        Index(
            "uq_activity_events_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    employee_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("employees.id"),
        index=True,
        nullable=False,
    )
```

### Alembic migration пример

```python
def upgrade() -> None:
    op.create_index(
        "uq_activity_events_source_external_id",
        "activity_events",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
```

## 7. API документация

Base URL: `/api/v1`.

Swagger UI доступен после запуска: `http://127.0.0.1:8000/docs`.

### Общие ошибки

| Код | Когда |
|---|---|
| 400 | Business validation error: duplicate, inconsistent path/body id, invalid date range |
| 401 | Invalid/missing bearer token |
| 404 | Entity not found |
| 422 | Pydantic/FastAPI validation error |

Формат бизнес-ошибки:

```json
{
  "detail": "employee not found"
}
```

### Health

| Method | URL | Auth | Описание |
|---|---|---|---|
| GET | `/health` | no | Root healthcheck |
| GET | `/api/v1/health` | no | API v1 healthcheck |

Response:

```json
{"status": "ok"}
```

### Auth endpoints

#### GET `/auth/vk/login`

Возвращает URL авторизации VK.

Response:

```json
{
  "authorization_url": "https://oauth.vk.com/authorize?client_id=..."
}
```

#### GET `/auth/vk/callback?code=...`

Обменивает VK code на VK access token, получает пользователя VK, создает сотрудника при первом входе и возвращает JWT.

Response:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "employee": {
    "id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
    "vk_user_id": "12345",
    "role": "employee",
    "full_name": "Ivan Petrov",
    "email": null,
    "position": null,
    "timezone": "Europe/Moscow",
    "work_format": "remote",
    "created_at": "2026-05-24T12:00:00Z",
    "updated_at": "2026-05-24T12:00:00Z"
  }
}
```

#### GET `/auth/me`

Auth: Bearer JWT.

Возвращает текущего сотрудника из JWT.

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/v1/auth/me
```

### Employees

#### POST `/employees`

Auth: Bearer JWT.

Request:

```json
{
  "role": "employee",
  "full_name": "Ivan Petrov",
  "email": "ivan@example.com",
  "position": "Backend Developer",
  "timezone": "Europe/Moscow",
  "work_format": "remote"
}
```

Response: `201 EmployeeResponse`.

Validation/business rules:

- `email` валидируется как email;
- `email` и `vk_user_id` должны быть уникальны;
- при duplicate возвращается `400`.

#### GET `/employees`

Auth: no.

Возвращает список сотрудников, сортировка по `created_at DESC`.

#### GET `/employees/{employee_id}`

Auth: no.

Ошибки:

- `404 employee not found`.

#### PATCH `/employees/{employee_id}`

Auth: Bearer JWT.

Request supports partial fields:

```json
{
  "position": "Senior Backend Developer",
  "timezone": "Asia/Yerevan"
}
```

Ошибки:

- `404 employee not found`;
- `400 employee with this email or VK user id already exists`.

### Work schedules

#### POST `/employees/{employee_id}/schedules`

Auth: Bearer JWT.

Request:

```json
{
  "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
  "work_days": [0, 1, 2, 3, 4],
  "start_time": "09:00:00",
  "end_time": "18:00:00",
  "timezone": "Europe/Moscow",
  "last_updated_at": "2026-05-24T12:00:00+03:00",
  "is_active": true
}
```

Rules:

- `employee_id` в path и body должны совпадать;
- employee должен существовать.

#### GET `/employees/{employee_id}/schedules/active`

Auth: no.

Возвращает активный график с максимальным `last_updated_at`.

### Schedule exceptions

#### POST `/employees/{employee_id}/exceptions`

Auth: Bearer JWT.

Request:

```json
{
  "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
  "type": "vacation",
  "start_dt": "2026-06-01T00:00:00+03:00",
  "end_dt": "2026-06-14T23:59:59+03:00",
  "reason": "Annual leave"
}
```

#### GET `/employees/{employee_id}/exceptions`

Auth: no.

Возвращает исключения сотрудника, сортировка `start_dt DESC`.

### Teams

#### POST `/teams`

Auth: Bearer JWT.

Request:

```json
{
  "name": "Platform",
  "description": "Core backend and infrastructure team"
}
```

#### GET `/teams`

Auth: no.

#### GET `/teams/{team_id}`

Auth: no.

#### PATCH `/teams/{team_id}`

Auth: Bearer JWT.

Request:

```json
{
  "description": "Platform engineering team"
}
```

### Team members

#### POST `/teams/{team_id}/members`

Auth: Bearer JWT.

Request:

```json
{
  "team_id": "7f2333d8-6731-46a2-99db-97f9432de223",
  "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
  "role_in_team": "developer"
}
```

Rules:

- path `team_id` must match body `team_id`;
- team and employee must exist;
- duplicate membership returns `400`.

#### DELETE `/teams/{team_id}/members/{employee_id}`

Auth: Bearer JWT.

Response: `204 No Content`.

### Team availability

#### GET `/teams/{team_id}/availability?start_dt=...&end_dt=...`

Auth: no.

Calculates availability windows for every team member.

Example:

```bash
curl "http://127.0.0.1:8000/api/v1/teams/$TEAM_ID/availability?start_dt=2026-05-25T09:00:00%2B03:00&end_dt=2026-05-25T18:00:00%2B03:00"
```

Response:

```json
{
  "team_id": "7f2333d8-6731-46a2-99db-97f9432de223",
  "range_start": "2026-05-25T09:00:00+03:00",
  "range_end": "2026-05-25T18:00:00+03:00",
  "employees": [
    {
      "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
      "timezone": "Europe/Moscow",
      "available_windows": [
        {
          "start_dt": "2026-05-25T10:00:00+03:00",
          "end_dt": "2026-05-25T12:00:00+03:00"
        }
      ]
    }
  ]
}
```

#### POST `/teams/{team_id}/meeting-recommendations`

Auth: Bearer JWT.

Request:

```json
{
  "start_dt": "2026-05-25T09:00:00+03:00",
  "end_dt": "2026-05-25T18:00:00+03:00",
  "duration_minutes": 60
}
```

Rules:

- `duration_minutes > 0`;
- `duration_minutes <= 480`;
- `start_dt < end_dt`;
- recommendations are calculated with 30-minute slot step;
- returns max 3 best slots.

Response:

```json
[
  {
    "start_dt": "2026-05-25T11:00:00+03:00",
    "end_dt": "2026-05-25T12:00:00+03:00",
    "available_employee_ids": ["4d4422ec-13c7-46f2-9d10-a3739df00d61"],
    "unavailable_employee_ids": [],
    "score": 1.0
  }
]
```

### Activity events

#### POST `/import/events/csv`

Auth: Bearer JWT.

Multipart file upload. Required CSV columns:

```text
employee_id,source,event_type,title,start_dt,end_dt,timezone
```

Optional:

```text
external_id,is_recurring,is_outside_schedule
```

Example:

```bash
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@events.csv" \
  http://127.0.0.1:8000/api/v1/import/events/csv
```

Response:

```json
{
  "imported_count": 10,
  "skipped_duplicate_count": 2,
  "errors": []
}
```

#### POST `/import/events/json`

Auth: Bearer JWT.

Request:

```json
[
  {
    "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
    "external_id": "calendar-event-100",
    "source": "google_calendar",
    "event_type": "meeting",
    "title": "Planning",
    "start_dt": "2026-05-25T10:00:00+03:00",
    "end_dt": "2026-05-25T11:00:00+03:00",
    "timezone": "Europe/Moscow",
    "is_recurring": false,
    "is_outside_schedule": false
  }
]
```

#### POST `/events/manual`

Auth: Bearer JWT.

Creates one manual activity event.

#### GET `/employees/{employee_id}/events`

Auth: no.

Returns employee events sorted by `start_dt DESC`.

### Recommendations

#### GET `/recommendations`

Auth: no.

Generates recommendations for all employees on demand.

#### GET `/employees/{employee_id}/recommendations`

Auth: no.

#### GET `/teams/{team_id}/recommendations`

Auth: no.

Recommendation response:

```json
{
  "code": "high_load_level",
  "reason": "Load level is 120% of scheduled work hours.",
  "severity": "high",
  "action": "Reduce meeting load or adjust the schedule.",
  "subject_type": "employee",
  "subject_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61"
}
```

### Dashboard

#### GET `/dashboard/summary`

Auth: no.

Response:

```json
{
  "total_employees": 42,
  "total_teams": 6,
  "employees_by_risk_level": {
    "low": 30,
    "medium": 8,
    "high": 3,
    "critical": 1
  },
  "overloaded_employees_count": 4,
  "outdated_schedules_count": 7,
  "outside_schedule_events_count": 19,
  "last_calculation_at": "2026-05-24T12:00:00Z"
}
```

## 8. Авторизация и безопасность

### Текущая модель auth

Используется VK OAuth для внешней идентификации и локальный JWT для API access.

```mermaid
sequenceDiagram
    participant User
    participant API
    participant VK
    participant DB

    User->>API: GET /auth/vk/login
    API-->>User: authorization_url
    User->>VK: Login and consent
    VK-->>User: redirect with code
    User->>API: GET /auth/vk/callback?code=...
    API->>VK: exchange code for VK access token
    API->>VK: users.get
    API->>DB: find/create employee by vk_user_id
    API-->>User: JWT access token
```

JWT payload:

```json
{
  "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61",
  "role": "employee",
  "exp": "2026-05-25T15:00:00Z"
}
```

### Access/refresh tokens

Сейчас реализован только access token со сроком жизни `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, default `60`.

Плюсы:

- простая реализация;
- достаточно для MVP;
- stateless проверка.

Минусы:

- нельзя отозвать конкретный токен без blacklist/session storage;
- нет refresh flow;
- компрометация токена действует до expiration.

Production-рекомендация:

- access token 5-15 минут;
- refresh token 7-30 дней;
- хранить refresh token hash в БД;
- rotation on every refresh;
- revoke on logout/password/security event.

### Password hashing

В текущем MVP паролей нет, login идет через VK OAuth. В dependencies есть `passlib[bcrypt]`, но она не используется. Если появится email/password login:

- хранить только bcrypt/argon2 hash;
- не хранить plaintext password;
- добавить rate limit на login;
- использовать password policy и breach password checks.

### RBAC и permissions

Поле `employees.role` и JWT claim `role` существуют, но fine-grained RBAC не реализован. Сейчас protected endpoints требуют только authenticated employee.

Production matrix example:

| Operation | employee | manager | admin |
|---|---:|---:|---:|
| Read own profile | yes | yes | yes |
| Create employee | no | no | yes |
| Update own schedule | yes | yes | yes |
| Update any employee | no | team only | yes |
| Import events | own only | team only | yes |
| View dashboard | no | team only | yes |

### Rate limiting

Не реализован. Нужен для:

- OAuth callback abuse;
- brute-force будущего password login;
- import endpoints;
- expensive recommendation endpoints.

Практичные варианты:

- Redis-backed limiter в приложении;
- Nginx/Ingress rate limit;
- API gateway.

### CORS

CORS middleware не настроен. Для browser frontend нужно явно разрешить origin:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://worktime.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
```

Нельзя использовать wildcard origins с credentials в production.

### CSRF

JWT передается через `Authorization: Bearer`, а не cookie, поэтому классический CSRF риск ниже. Если refresh token будет храниться в HttpOnly cookie, потребуется CSRF protection для state-changing endpoints.

### SQL injection protection

SQLAlchemy statements используют bind parameters и expression API:

```python
select(ActivityEvent).where(ActivityEvent.employee_id == employee_id)
```

Это защищает от SQL injection, если не добавлять raw SQL из пользовательского ввода. Единственный raw SQL в проекте — static partial index predicate `external_id IS NOT NULL`, он не зависит от пользователя.

### XSS protection

Backend возвращает JSON и не рендерит HTML. XSS в основном будет ответственностью frontend. Backend все равно должен:

- не возвращать unsafe HTML без необходимости;
- валидировать/ограничивать поля вроде `title`, `description`, `reason`;
- ставить security headers на reverse proxy.

### Secrets management

`.env.example` содержит небезопасный default:

```env
JWT_SECRET_KEY=change-me-in-production
```

Production:

- хранить секреты в Vault/Kubernetes Secrets/GitHub Actions secrets;
- не коммитить реальные `.env`;
- ротировать JWT secret;
- использовать разные секреты на dev/stage/prod;
- ограничить доступ к VK client secret.

## 9. Docker и инфраструктура

### Текущий docker-compose

Сейчас `docker-compose.yml` содержит только PostgreSQL:

```yaml
services:
  postgres:
    image: postgres:16
    container_name: worktime-sync-postgres
    environment:
      POSTGRES_DB: worktime_sync
      POSTGRES_USER: worktime
      POSTGRES_PASSWORD: worktime
    ports:
      - "55432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U worktime -d worktime_sync"]
```

Почему порт `55432`: снижает конфликт с локальным PostgreSQL на стандартном `5432`.

### Контейнеры

| Контейнер | Статус | Назначение |
|---|---|---|
| `postgres` | есть | Primary DB |
| `api` | не описан | Нужно добавить для production/stage compose |
| `redis` | не описан | Нужен для cache/Celery/rate limits |
| `worker` | не описан | Нужен для фоновых задач |
| `nginx` | не описан | Reverse proxy/TLS/security headers |

### Networking

В текущем compose используется default network. API при контейнеризации должен обращаться к Postgres по service name:

```env
DATABASE_URL=postgresql+asyncpg://worktime:worktime@postgres:5432/worktime_sync
```

### Volumes

`postgres_data` хранит данные БД между перезапусками контейнера. Это важно для локальной разработки и stage окружений. В production volume должен быть заменен managed disk/managed PostgreSQL.

### Production deployment

Минимальная production topology:

```mermaid
flowchart LR
    Internet --> LB[Load Balancer / Ingress]
    LB --> Nginx[Nginx / Reverse Proxy]
    Nginx --> API1[API container]
    Nginx --> API2[API container]
    API1 --> PG[(Managed PostgreSQL)]
    API2 --> PG
    API1 --> Redis[(Redis)]
    API2 --> Redis
    Worker[Celery worker] --> PG
    Worker --> Redis
```

### CI/CD

Рекомендуемый pipeline:

1. Checkout.
2. Setup Python 3.12.
3. Install dependencies.
4. Ruff.
5. Mypy.
6. Pytest.
7. Build Docker image.
8. Run Alembic migration check.
9. Push image.
10. Deploy to staging.
11. Smoke tests.
12. Manual/protected deploy to production.

Example GitHub Actions sketch:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: worktime_sync
          POSTGRES_USER: worktime
          POSTGRES_PASSWORD: worktime
        ports:
          - 55432:5432
        options: >-
          --health-cmd "pg_isready -U worktime -d worktime_sync"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - run: ruff check .
      - run: mypy app
      - run: alembic upgrade head
      - run: pytest
```

## 10. Логирование и мониторинг

### Текущее состояние

`structlog` есть в dependencies, но logging pipeline не настроен. FastAPI/Uvicorn будут писать стандартные logs.

### Production structured logging

Рекомендуемый JSON log event:

```json
{
  "timestamp": "2026-05-25T12:00:00Z",
  "level": "info",
  "event": "http_request_completed",
  "request_id": "req-123",
  "method": "POST",
  "path": "/api/v1/import/events/json",
  "status_code": 200,
  "duration_ms": 35,
  "employee_id": "4d4422ec-13c7-46f2-9d10-a3739df00d61"
}
```

### Log levels

| Level | Использование |
|---|---|
| DEBUG | SQL/debug details в dev |
| INFO | request completed, import completed, auth success |
| WARNING | validation anomaly, duplicate import skipped |
| ERROR | unhandled exception, DB error |
| CRITICAL | service cannot start, migration failure |

### Tracing

OpenTelemetry стоит добавить, когда появятся:

- внешние интеграции;
- worker jobs;
- несколько сервисов;
- latency investigations.

Trace spans:

- HTTP request;
- DB query groups;
- VK OAuth requests;
- import parsing;
- recommendation calculation.

### Metrics

Prometheus metrics:

- `http_requests_total`;
- `http_request_duration_seconds`;
- `db_query_duration_seconds`;
- `activity_events_imported_total`;
- `activity_events_duplicates_skipped_total`;
- `recommendations_generated_total`;
- `meeting_recommendation_duration_seconds`;
- `auth_failures_total`.

### Healthchecks

Текущий `/health` возвращает только process-level статус. Production нужно разделить:

- `/live` — процесс жив;
- `/ready` — БД доступна, миграции совместимы;
- `/metrics` — Prometheus scrape endpoint.

## 11. Тестирование

### Текущий test suite

В `tests/` есть проверки:

- health endpoints;
- auth;
- CRUD endpoints;
- schemas;
- activity event import;
- analytics metrics;
- recommendation rules;
- recommendation endpoints;
- dashboard summary;
- team availability.

### Unit tests

Unit tests должны покрывать pure logic без БД:

- `analytics.metrics`;
- `analytics.availability`;
- `analytics.recommendations`;
- import normalization.

Зачем: самые быстрые тесты, ловят ошибки алгоритмов и edge cases.

### Integration tests

Integration tests нужны для:

- repositories;
- services с реальной БД;
- Alembic migrations;
- unique constraints;
- transaction rollback.

Зачем: repository и ORM ошибки часто не видны в unit tests.

### API tests

API tests проверяют:

- routing;
- request validation;
- status codes;
- response models;
- dependency override;
- auth protection.

### Fixtures

Текущий `tests/conftest.py` автоматически подменяет current employee:

```python
app.dependency_overrides[get_current_employee] = fake_current_employee
```

Это ускоряет CRUD tests. Для auth tests marker `no_auth_override` отключает override.

### Mocking

VK OAuth client нужно тестировать через injected fake HTTP client или fake `VKOAuthClient`, чтобы не ходить в реальный VK.

### Coverage

Рекомендуемые минимумы:

- analytics/importers: 90%+;
- services: 80%+;
- endpoints: happy path + key error paths;
- repositories: critical queries.

## 12. Масштабирование

### Bottlenecks

| Узкое место | Почему появится | Решение |
|---|---|---|
| Recommendation endpoints | On-demand сбор данных и расчет по всем сотрудникам | Precompute, cache, paginate |
| Team availability | События/исключения в диапазоне растут | Индексы по `(employee_id, start_dt, end_dt)`, interval strategy |
| Imports | Большой payload держится в памяти | Streaming parser, batch insert |
| DB connections | `NullPool` в production | Включить pool sizing |
| Dashboard | Aggregations по metrics | Materialized read model |

### Horizontal scaling

FastAPI API stateless относительно application memory. JWT stateless, session хранится в БД. Поэтому API можно масштабировать горизонтально за reverse proxy.

Требования:

- общий PostgreSQL;
- общий Redis, если появится cache/rate limit;
- одинаковый JWT secret/key set;
- idempotent imports.

### Vertical scaling

Увеличение CPU/RAM полезно для:

- больших импортов;
- CPU-heavy availability calculations;
- большого количества concurrent requests.

Но вертикальное масштабирование не заменяет query optimization и background jobs.

### Caching

Кандидаты для cache:

- dashboard summary на 30-120 секунд;
- team availability для одинаковых диапазонов;
- recommendations;
- VK user info на короткое время при auth burst.

Нужно учитывать invalidation: schedule/event/member changes должны сбрасывать related cache.

### Async processing and queues

Celery/Redis стоит добавить для:

- пересчета employee_metrics;
- импорта больших файлов;
- синхронизации календарей;
- отправки уведомлений;
- periodic jobs.

### Sharding/replication

На ранней стадии достаточно read replicas для аналитических чтений. Sharding нужен только при очень большом объеме events. Более вероятный первый шаг — partitioning `activity_events` по времени.

### Load balancing

Reverse proxy должен:

- распределять запросы между API replicas;
- ставить TLS;
- ограничивать request body size для imports;
- прокидывать request id;
- отдавать 502/503 при недоступности API.

## 13. Производительность

### Async vs sync

Async помогает при I/O-bound задачах: DB и HTTP. Он не ускоряет CPU-bound расчеты. Если meeting recommendation для больших команд станет тяжелым, нужно:

- профилировать алгоритм;
- ограничивать диапазоны;
- переносить расчеты в worker;
- оптимизировать структуру intervals.

### Connection pooling

`NullPool` не подходит для high-throughput production. Рекомендуется:

```python
create_async_engine(
    url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=1800,
)
```

### Indexing

Текущие индексы:

- `work_schedules.employee_id`;
- `schedule_exceptions.employee_id`;
- `activity_events.employee_id`;
- `employee_metrics.employee_id` unique;
- `team_members.team_id`;
- `team_members.employee_id`;
- partial unique `activity_events(source, external_id)`.

Рекомендуемые дополнительные индексы:

```sql
CREATE INDEX ix_activity_events_employee_time
ON activity_events (employee_id, start_dt, end_dt);

CREATE INDEX ix_schedule_exceptions_employee_time
ON schedule_exceptions (employee_id, start_dt, end_dt);

CREATE INDEX ix_work_schedules_employee_active_updated
ON work_schedules (employee_id, is_active, last_updated_at DESC);
```

### N+1 problem

Сейчас сервисы часто грузят списки bulk-запросами: `list_by_ids`, `list_active_for_employees`, `list_for_employees_in_range`. Это снижает риск N+1. Но при добавлении nested responses нужно использовать:

- `selectinload`;
- explicit joins;
- batch queries.

### Batching

`ActivityEventService.import_events` сейчас делает `create` на каждый event, но в одной транзакции. Для больших импортов стоит перейти на:

- bulk insert;
- chunking по 500-1000 events;
- `ON CONFLICT DO NOTHING` для duplicate external ids;
- streaming CSV parser.

### Query optimization

Перед оптимизацией использовать:

- `EXPLAIN ANALYZE`;
- slow query logs;
- pg_stat_statements;
- application metrics.

## 14. Обработка ошибок

### Текущее состояние

Есть service-level exceptions:

- `NotFoundError`;
- `InvalidOperationError`.

Endpoints вручную преобразуют их в HTTP codes.

### Validation errors

Pydantic/FastAPI автоматически возвращает `422`. Importers собирают ошибки CSV/JSON и возвращают `400` с деталями.

### Business errors

Examples:

- employee not found;
- team not found;
- path/body id mismatch;
- duplicate employee/team membership/activity event;
- invalid date range.

### DB errors

`IntegrityError` обрабатывается в сервисах там, где есть unique/composite PK conflicts. Выполняется rollback.

### Retry logic

Retry logic не реализован. Для production:

- не ретраить validation errors;
- осторожно ретраить transient DB/network errors;
- использовать idempotency key для imports/mutations;
- в Celery retries должны быть bounded и observable.

### Rollback logic

Если import содержит хотя бы одну invalid entity, сервис делает rollback всей партии. Это atomic behavior: либо импорт корректен целиком, либо не меняет БД. Duplicate external ids пропускаются как non-fatal condition.

### Global exception handlers

Рекомендуемый refactor:

```python
@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})
```

Это уберет дублирование из endpoints.

## 15. Конфигурация проекта

### Settings

`app/core/config.py` использует `BaseSettings`. Env file: `.env`.

| Переменная | Назначение | Default |
|---|---|---|
| `APP_DEBUG` | FastAPI debug mode | false |
| `API_V1_PREFIX` | Prefix для v1 API | `/api/v1` |
| `POSTGRES_HOST` | DB host | localhost |
| `POSTGRES_PORT` | DB port | 55432 |
| `POSTGRES_DB` | DB name | worktime_sync |
| `POSTGRES_USER` | DB user | worktime |
| `POSTGRES_PASSWORD` | DB password | worktime |
| `DATABASE_URL` | Полный SQLAlchemy URL | optional |
| `JWT_SECRET_KEY` | JWT signing secret | unsafe default |
| `JWT_ALGORITHM` | JWT algorithm | HS256 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access token TTL | 60 |
| `VK_CLIENT_ID` | VK app id | empty |
| `VK_CLIENT_SECRET` | VK secret | empty |
| `VK_REDIRECT_URI` | OAuth callback URL | localhost |
| `VK_AUTHORIZE_URL` | VK authorize endpoint | VK default |
| `VK_TOKEN_URL` | VK token endpoint | VK default |
| `VK_USER_INFO_URL` | VK users.get endpoint | VK default |
| `VK_API_VERSION` | VK API version | 5.199 |

### Config management

`DATABASE_URL` имеет приоритет над assembled PostgreSQL settings. Это удобно:

- локально можно использовать individual env vars;
- в Docker/Kubernetes проще передать один URL.

### Environments

Рекомендуемые окружения:

| Env | Особенности |
|---|---|
| local | `.env`, debug optional, local compose DB |
| test | isolated DB/schema, fake OAuth |
| staging | production-like, real migrations, test secrets |
| production | secret manager, no debug, managed DB |

## 16. Запуск проекта

### Local setup on Windows PowerShell

```powershell
python -m venv venv
venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env
```

Отредактировать `.env`:

```env
JWT_SECRET_KEY=replace-with-strong-secret
VK_CLIENT_ID=...
VK_CLIENT_SECRET=...
VK_REDIRECT_URI=http://localhost:8000/api/v1/auth/vk/callback
```

### Start PostgreSQL

```powershell
docker compose up -d
```

Проверить health:

```powershell
docker compose ps
```

### Apply migrations

```powershell
venv\Scripts\python.exe -m alembic upgrade head
```

### Run API

```powershell
venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

### Quality checks

```powershell
venv\Scripts\python.exe -m pytest
venv\Scripts\python.exe -m ruff check .
venv\Scripts\python.exe -m mypy app
```

### Migration workflow

```powershell
venv\Scripts\python.exe -m alembic revision --autogenerate -m "add table"
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m alembic current
venv\Scripts\python.exe -m alembic check
```

## 17. Примеры использования

### User flow: создать сотрудника и график

1. Получить JWT через VK OAuth.
2. Создать сотрудника:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/employees \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "employee",
    "full_name": "Ivan Petrov",
    "email": "ivan@example.com",
    "position": "Backend Developer",
    "timezone": "Europe/Moscow",
    "work_format": "remote"
  }'
```

3. Создать рабочий график:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/employees/$EMPLOYEE_ID/schedules \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "employee_id": "'$EMPLOYEE_ID'",
    "work_days": [0, 1, 2, 3, 4],
    "start_time": "09:00:00",
    "end_time": "18:00:00",
    "timezone": "Europe/Moscow",
    "last_updated_at": "2026-05-25T10:00:00+03:00",
    "is_active": true
  }'
```

### User flow: импортировать события и получить рекомендации

```bash
curl -X POST http://127.0.0.1:8000/api/v1/import/events/json \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '[
    {
      "employee_id": "'$EMPLOYEE_ID'",
      "external_id": "calendar-1",
      "source": "google_calendar",
      "event_type": "meeting",
      "title": "Planning",
      "start_dt": "2026-05-25T10:00:00+03:00",
      "end_dt": "2026-05-25T11:00:00+03:00",
      "timezone": "Europe/Moscow",
      "is_recurring": false,
      "is_outside_schedule": false
    }
  ]'
```

```bash
curl http://127.0.0.1:8000/api/v1/employees/$EMPLOYEE_ID/recommendations
```

### User flow: подобрать слот встречи

```bash
curl -X POST http://127.0.0.1:8000/api/v1/teams/$TEAM_ID/meeting-recommendations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "start_dt": "2026-05-25T09:00:00+03:00",
    "end_dt": "2026-05-25T18:00:00+03:00",
    "duration_minutes": 60
  }'
```

## 18. Потенциальные улучшения

### Критичный технический долг

| Приоритет | Улучшение | Почему важно |
|---|---|---|
| High | Настроить production JWT secret management | Текущий default небезопасен |
| High | Добавить RBAC/permissions | Сейчас authenticated write access слишком широк |
| High | Перейти с `NullPool` на production pool config | Иначе будут лишние connect/disconnect overhead |
| High | Добавить global exception handlers | Единый формат ошибок и меньше дублирования |
| Medium | CHECK constraints для дат/времени | БД должна защищать инварианты |
| Medium | Индексы для range queries | Availability будет тормозить на больших данных |
| Medium | Structured logging | Без JSON logs тяжело расследовать инциденты |
| Medium | Health readiness с DB check | Нужен корректный deploy/rolling update |
| Low | Удалить неиспользуемые dependencies или подключить их | Сейчас Redis/Celery/structlog выглядят как planned stack |

### Будущий рефакторинг

1. Ввести Unit of Work.
2. Вынести error mapping в global handlers.
3. Разделить command/query services при росте dashboard.
4. Добавить domain enums для roles, work formats, exception types, event types.
5. Добавить DB-level CHECK constraints.
6. Добавить pagination/filtering для list endpoints.
7. Добавить OpenAPI examples прямо в route decorators.

### Масштабирование до event-driven architecture

Когда появятся внешние интеграции и фоновые расчеты, стоит добавить outbox pattern:

```mermaid
flowchart LR
    API[API transaction] --> DB[(PostgreSQL)]
    API --> Outbox[(outbox_events)]
    Relay[Outbox relay] --> Broker[Redis/Kafka]
    Broker --> Worker[Metrics worker]
    Worker --> Metrics[(employee_metrics)]
```

Преимущество outbox: domain change и event publication становятся атомарными относительно БД.

### Переход на microservices

Microservices стоит рассматривать только после появления независимых bounded contexts и команд:

- Identity/Auth service;
- Schedule service;
- Activity Import service;
- Analytics/Recommendations service;
- Dashboard read model service.

До этого модульный монолит дешевле, надежнее и быстрее для разработки.

### Production hardening checklist

- [ ] Replace default `JWT_SECRET_KEY`.
- [ ] Configure CORS.
- [ ] Add RBAC.
- [ ] Add refresh tokens/session revocation.
- [ ] Add request id middleware.
- [ ] Add structured JSON logging.
- [ ] Add Prometheus metrics.
- [ ] Add DB readiness check.
- [ ] Add API Dockerfile.
- [ ] Add Nginx/Ingress config.
- [ ] Add CI/CD workflow.
- [ ] Add production DB pool settings.
- [ ] Add pagination and filters.
- [ ] Add DB constraints for date/time invariants.
- [ ] Add indexes for range queries.
- [ ] Add background metric recalculation.
- [ ] Add cache invalidation strategy.
