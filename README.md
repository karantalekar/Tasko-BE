# Tasko backend

FastAPI / Python 3.12+, MongoDB, Redis, Docker Compose. Backend source only; no frontend source was provided and no frontend was modified. The implementation follows the inspected public Tasko template, with explicitly documented product assumptions in [docs/design.md](docs/design.md).

## Start locally

Install Docker Engine/Desktop with Docker Compose v2. Extract this ZIP and open a terminal inside `tasko-backend`.

```bash
cp .env.example .env
# PowerShell alternative: Copy-Item .env.example .env

docker compose up --build -d
docker compose logs -f api
```

The supplied Compose setup replaces the MongoDB and Redis host placeholders with internal service names. Its local HTTP cookie setting is `COOKIE_SECURE=false`. No credentials are preconfigured and no sample users, tasks, or metrics are seeded.

- Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI: http://localhost:8000/openapi.json (also exported in `docs/openapi.json`)
- Liveness: http://localhost:8000/health/live
- Readiness: http://localhost:8000/health/ready

API binds only to the host's loopback interface. MongoDB and Redis have **no published host ports**. MongoDB data lives in `mongo_data`; Redis uses `redis_data` with AOF. `docker compose down` preserves them. Do not use `down -v` unless intentionally deleting development data.

For local Python development, install uv, run `uv sync --frozen`, configure `.env` with your reachable development MongoDB/Redis addresses, then run `uv run uvicorn app.main:app --reload --no-access-log --no-proxy-headers`. The included containers are not reachable via localhost because their database ports are deliberately unpublished; use separate local services or an explicit loopback-only Compose port override.

## Environment

| Variable | Meaning |
|---|---|
| `ENVIRONMENT` | `development` locally; `production` enforces secure cookies |
| `MONGO_URI` | Connection URI; use an application-scoped authenticated TLS URI in production |
| `MONGO_DATABASE` | Database name; Compose sets `tasko` |
| `REDIS_URL` | Redis connection URI; shared by every API instance |
| `ALLOWED_ORIGINS` | JSON array of exact frontend origins; wildcard rejected |
| `COOKIE_SECURE` | `false` only for local HTTP; `true` for HTTPS |
| `SESSION_HOURS` | Absolute session lifetime, default 24 hours, maximum 168 |
| `AUTH_RATE_LIMIT` | Login and registration share a 5 requests/minute/IP bucket by default |
| `API_RATE_LIMIT` | 120 requests/minute/user; anonymous requests use an IP bucket |
| `CACHE_SECONDS` | Analytics cache lifetime, default 30 seconds |
| `CREATE_INDEXES_ON_STARTUP` | Convenience for development; default true |

`.env.example` contains configuration templates, never real credentials. Do not commit `.env`. Session tokens are generated using OS-backed randomness; no shared signing key is needed. Store production connection credentials using your deployment's secret manager.

## Authentication and permissions

Team creation accepts `organization` and `email_domain` (for example, `Whitecode` and
`whitecode.com`). The frontend requires both. For compatibility, API clients that omit
them default to the team name and owner's email domain. Owners can update these fields
using `PUT /api/v1/teams/{team_id}/organization`. The domain must match all existing
member emails; it cannot be cleared. Member additions require an exact, case-insensitive
email domain match. Similar domains, local-part matches, and subdomains are rejected.
Legacy teams with no domain must configure it before adding new members.

Analytics task metrics are scoped to the requesting member's assigned tasks, or the whole
team for its owner. `daily_task_completion` contains only actual completion dates (UTC),
and `monthly_task_completion` omits empty months. `total_working_minutes` sums all
submitted timesheets for the current member (or team for its owner), across all dates;
running timers are separate and are not added again. Caches are scoped by role/member
and invalidated when a timesheet is submitted.

Timesheets are manual work logs, separate from running timers. Members submit entries using
`POST /api/v1/teams/{team_id}/timesheets` with `task_id`, `work_date` (`YYYY-MM-DD`),
integer `minutes` (1–1440), and `notes` (1–2000 characters). The task must be assigned to
the submitting member. The API sets the member identity and stores the original member,
task, and project names. Entries cannot be edited or deleted through the API.

`GET /api/v1/teams/{team_id}/timesheets` returns paginated history, newest work date first.
Members see only their own entries; the team owner can see all member entries and filter
by `member_id`. Both roles can filter by inclusive `date_from` and `date_to`, and use
`limit` (1–100) and `offset` (0–10000). The live Swagger/OpenAPI documentation includes
these routes. Restart the API (or rebuild its Docker image) after updating the source.

Members can create tasks in existing projects in teams they belong to. These tasks are assigned to the creating member automatically; assigning them to another user is rejected. Members can also create calendar events in their teams. Creating projects, teams, and memberships, and editing existing events remains owner-only.

1. Register a user (password length 12–128), choosing `role: "owner"` or `role: "member"`. Omitted roles default to Member. Only Owner accounts can create teams; Member accounts must be added by an existing team owner.
2. Login. The API sets `tasko_session`, an HttpOnly, SameSite=Lax cookie, and returns a CSRF token.
3. Send cookies on requests. Send `X-CSRF-Token` for every authenticated POST/PUT/PATCH.
4. After a frontend reload, GET `/api/v1/auth/session` to recover the current CSRF token.
5. Logout revokes the server-side session immediately and removes its cookie.

Passwords use Argon2. MongoDB stores only SHA-256 hashes of high-entropy session tokens. Sessions expire at an absolute time; access checks expiry even before MongoDB's TTL monitor removes the record. Logging in rotates this browser's prior session; other browser sessions remain valid. There is no persistent plaintext password or bearer token response.

Only Owner accounts can create teams. The creator owns that team. Owners manage projects, create tasks, assign existing registered users, and create/edit calendar events in their own teams. Members can view the team's data and edit their own assigned tasks, but cannot reassign them or manage projects/membership. Everyone can run their own timer on permitted tasks. Unrelated users receive 404 for inaccessible team resources. Task project/assignee references are validated within the same team. Profile updates cannot change account roles. Legacy users are classified on login or session access: users already owning a team remain Owners; others become Members.

The conservative owner/member rule was selected because the live UI does not define access policy. A user's displayed job title is **not** an authorization role. Adding a member requires the exact email of an already registered account; this is an owner-controlled grant, not an email invitation workflow.

Use a same-site frontend/API deployment (for example `app.example.com` and `api.example.com`) with HTTPS. For cross-site HTTPS deployments (Vercel with Render), set COOKIE_SAMESITE=none, COOKIE_SECURE=true, and ALLOWED_ORIGINS to a JSON array of exact frontend origins without trailing slashes. Origin validation and CSRF remain enforced. Browsers blocking third-party cookies may require a same-site deployment or frontend proxy. All frontend fetch calls, including login, must include `credentials: "include"`. Do not put session cookies/tokens in localStorage.

## API examples

These are placeholders for user-supplied values, not shipped credentials. Commands below use Bash and curl. Use Swagger or Postman on Windows if preferred.

```bash
curl -i http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"name":"<your-name>","email":"<your-email>","password":"<your-strong-password>","role":"owner"}'

curl -i -c cookies.txt http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"<your-email>","password":"<your-strong-password>"}'

# Copy csrf_token from login into the header below.
curl -b cookies.txt http://localhost:8000/api/v1/teams \
  -H 'Content-Type: application/json' -H 'X-CSRF-Token: <csrf-token>' \
  -d '{"name":"Engineering"}'

curl -b cookies.txt http://localhost:8000/api/v1/teams/<team-id>/projects \
  -H 'Content-Type: application/json' -H 'X-CSRF-Token: <csrf-token>' \
  -d '{"name":"Website","status":"in_progress"}'

curl -b cookies.txt http://localhost:8000/api/v1/teams/<team-id>/tasks \
  -H 'Content-Type: application/json' -H 'X-CSRF-Token: <csrf-token>' \
  -d '{"title":"Build dashboard","project_id":"<project-id>","priority":"high","tags":["Frontend"],"due_at":"2026-10-20T12:00:00Z"}'

curl -X PATCH -b cookies.txt http://localhost:8000/api/v1/teams/<team-id>/tasks/<task-id> \
  -H 'Content-Type: application/json' -H 'X-CSRF-Token: <csrf-token>' \
  -d '{"status":"completed","version":1}'

curl -G -b cookies.txt http://localhost:8000/api/v1/teams/<team-id>/analytics \
  --data-urlencode 'from=2026-01-01T00:00:00Z' \
  --data-urlencode 'to=2027-01-01T00:00:00Z'
```

Protect/delete the local cookie jar after testing; it contains a live session credential. Replace `<...>` placeholders before running commands. Body IDs are 32-character lowercase hex strings supplied by the API.

Task response (illustrative identifiers):

```json
{
  "id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "team_id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "project_id":"cccccccccccccccccccccccccccccccc",
  "title":"Build dashboard",
  "description":"",
  "assignee_id":null,
  "priority":"high",
  "due_at":"2026-10-20T12:00:00Z",
  "tags":["Frontend"],
  "status":"pending",
  "completed_at":null,
  "created_at":"2026-10-08T06:00:00Z",
  "updated_at":"2026-10-08T06:00:00Z",
  "version":1
}
```

Task PATCH requires the last read version; stale writes return 409. Repeated completed→completed updates preserve the original completion time. Reopening clears `completed_at`; recompletion uses a new timestamp. Status cannot be supplied during creation; tasks begin pending.

## Routes

All rows below have `/api/v1` prefix. All except register/login require authentication.

| Method | Route | Purpose |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/logout` | Account/session lifecycle |
| GET | `/auth/session`, `/users/me` | Current session/profile |
| PATCH | `/users/me` | Change own display name |
| PUT | `/users/me/preferences` | Replace notification/theme preferences |
| GET, POST | `/teams` | List permitted teams / create team |
| GET | `/teams/{team_id}` | Team details |
| GET, POST | `/teams/{team_id}/members` | List / add a registered member |
| GET, POST | `/teams/{team_id}/projects` | List / create projects |
| GET, PUT | `/teams/{team_id}/projects/{project_id}` | Read / replace editable project fields |
| GET, POST | `/teams/{team_id}/tasks` | List / create tasks |
| GET, PATCH | `/teams/{team_id}/tasks/{task_id}` | Read / edit task with optimistic concurrency |
| GET, POST | `/teams/{team_id}/events` | List overlapping events / create event |
| PUT | `/teams/{team_id}/events/{event_id}` | Replace event fields |
| GET | `/teams/{team_id}/time-entries` | List own time entries |
| POST | `/teams/{team_id}/time-entries/start` | Start a timer; one active timer/user |
| POST | `/teams/{team_id}/time-entries/{entry_id}/stop` | Stop own timer |
| GET | `/teams/{team_id}/analytics` | Database-derived analytics |

No destructive delete endpoints were inferred from the template. Complete or change a project's status instead. PUT replaces editable fields and applies defaults to omitted optional fields; PATCH changes only explicitly supplied fields.

List responses: `{"items": [...], "total": 0, "limit": 20, "offset": 0}`. Limits are 1–100, offset 0–10000. Tasks support `status` OR `active`, `project_id`, `assignee_id`, `priority`, literal title search `q`, `due_from`, `due_to`, `sort` and `direction`. Dates are timezone-aware and normalized to UTC. The upper date bound is exclusive. Project lists filter status. Sort fields are allowlisted and include an ID tie-breaker. Counts and pages are separate reads, so simultaneous edits can change totals; offset pagination is intended for moderate datasets. Title search escapes regex metacharacters; consider MongoDB Search if large-scale text search is needed.

Calendar queries require `from` and `to`, include overlapping events, and cover at most 366 days. Time entries filter `running`; their duration is `ended_at - started_at`, or current time minus start for a running entry. Time tracking is separate from task lead-time analytics.

## Analytics semantics

`GET /api/v1/teams/{team_id}/analytics?from=...&to=...`

- Positive range of at most 366 days, timezone-aware timestamps; `[from, to)`.
- Completed total: currently completed tasks whose latest `completed_at` falls in range.
- Average completion: elapsed seconds from creation to latest completion for those tasks, not working hours or timer duration. No samples means `null`.
- Monthly completion: UTC `YYYY-MM` buckets; missing months are explicitly zero. Boundary months include only the requested portion.
- Active projects: current `in_progress` projects, independent of completion range.
- Team members: current owner + current membership count, independent of completion range.
- Project distribution: current counts for `pending`, `in_progress`, `completed`, `on_hold`.
- `generated_at`: when the aggregate was computed; retained when served from cache.

Historical snapshots and period-over-period deltas are **not fabricated**. Reopening a task changes historical completion totals under the current-state definition. A true completion-event history or historical team/project counts requires an explicit product requirement and schema extension. Multiple collections are read without a snapshot transaction, so analytics are eventually consistent during concurrent mutations.

Empty-data example:

```json
{
  "from":"2026-01-01T00:00:00Z",
  "to":"2026-03-01T00:00:00Z",
  "generated_at":"2026-03-01T12:00:00Z",
  "tasks_completed":0,
  "active_projects":0,
  "team_members":1,
  "average_completion_seconds":null,
  "monthly_task_completion":[{"month":"2026-01","count":0},{"month":"2026-02","count":0}],
  "project_status_distribution":{"pending":0,"in_progress":0,"completed":0,"on_hold":0}
}
```

Map `average_completion_seconds / 86400` to the UI's days label, treating null as “No completed tasks”. The owner explains the empty team's member count of 1. No template sample metrics are stored or returned by the application.

## Redis policy

The production limiter executes one atomic Lua script against shared Redis and uses Redis server time. All instances share fixed-window counters. Limits apply to successes and failures; auth attempts use a stricter shared login/register IP bucket. Other requests use a validated session's user ID, falling back to IP for anonymous/invalid cookies. Keys hash identities; cookies cannot be randomized to get fresh authenticated quotas. Fixed windows permit a boundary burst of up to twice the quota across adjacent windows. OPTIONS and health/docs routes are excluded.

Responses expose `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (epoch seconds); blocked responses also include `Retry-After` (seconds) and HTTP 429. Redis failure fails API requests closed with HTTP 503. There is no in-process production limiter.

Analytics cache keys include team, normalized range, and a random generation token. Project/task/member mutations rotate the token; concurrently computed old results cannot overwrite a new generation. Entries expire after 30 seconds by default. If cache invalidation fails transiently, TTL bounds staleness; required rate-limit failures still return 503. No permission decisions or session validation are cached. Redis `noeviction` prevents silent eviction of rate-limit keys; capacity failures return 503 and require operator attention. A full Redis reset loses quotas/cache; plan HA and monitoring for production.

The container disables proxy header trust. Behind a trusted reverse proxy, configure Uvicorn's proxy headers and a narrowly allowlisted `forwarded-allow-ips`, block direct API ingress, and enforce request-size/time limits at that proxy. Do not trust arbitrary X-Forwarded-For from clients. Until configured, IP limits apply to the direct peer (possibly the proxy).

## Indexes, schema changes, and operations

Indexes are idempotently created on local startup. For deployment, run:

```bash
docker compose run --rm api python -m app.indexes
```

Then set `CREATE_INDEXES_ON_STARTUP=false` for serving instances if desired. Use a migration identity with index privileges and a separate runtime identity with least-privilege data access. Index specs live in `app/db.py`; unique indexes reject duplicate email, duplicate membership, and multiple running timers. Session TTL expiry is indexed. Index build failure stops startup. Existing duplicate data must be resolved before adding unique indexes; do not silently drop indexes.

This is schema version 1, intended for a new database. Existing UI placeholders are not a migration source. Before any later schema change: back up, write an explicit idempotent migration against known fields, test on a copy, deploy compatibility code, then apply it. There is no destructive auto-migration.

Liveness checks process reachability. Readiness checks MongoDB and Redis. Structured request logs include request ID, route template, method, status and latency, not request bodies, cookies, authorization headers, connection URIs or query strings. Error responses include request IDs. Production should add external log aggregation/metrics/alerts, authenticated private database networks, TLS, backups with restore tests, pinned container digests and a vulnerability scanning/rebuild process. This development Compose setup is not a public production deployment manifest.

## Tests and verification

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check --config pyproject.toml app tests
```

Default tests use mongomock behind a test-only async adapter and fakeredis with Lua support. Each test has fresh isolated services; the production code always uses PyMongo Async and Redis. No real data is accessed by default. Tests exercise validation/redaction, password hashing, sessions/CSRF, cross-tenant authorization, member restrictions, task concurrency/completion, filters, events, timer uniqueness, analytics date boundaries/averages/zero-fill, cache invalidation, Lua concurrency, HTTP 429 headers and Redis failure handling.

To exercise the same API suite against **real disposable MongoDB and Redis**:

```bash
docker compose -f compose.test.yaml up --build --abort-on-container-exit --exit-code-from tests
docker compose -f compose.test.yaml down
```

This separate Compose project has no production volumes, no published database ports, and a fresh `tasko_test_<uuid>` database per test. Teardown drops only that generated database. Do not point `TEST_MONGO_URI` or `TEST_REDIS_URL` at production. The Docker suite is provided but was not run in the artifact-generation environment because Docker is unavailable. See `docs/verification.md` for the actual executed checks.

## Structure

```text
app/
  main.py             lifecycle, errors, middleware, health
  config.py           validated environment configuration
  schemas.py          Pydantic request/response contracts
  security.py         sessions, CSRF, team authorization
  limiter.py          atomic Redis Lua limiter
  cache.py            cache generations/invalidation
  db.py               IDs, timestamps, indexes
  indexes.py          explicit index setup command
  routes/
    auth.py           auth, profile, preferences
    teams.py          team ownership and membership
    work.py           projects, tasks, calendar, timer
    analytics.py      MongoDB aggregates and cache
 tests/               isolated regression suite
 docs/                design, OpenAPI and verification notes
```

Dependencies are resolved in `uv.lock` from public PyPI and were installed/tested together. Use frozen installs for reproducibility; periodically review/update the lock and rerun both test suites.
