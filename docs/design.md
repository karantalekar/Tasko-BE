# Design and frontend inspection

## Evidence and limits

Inspected on 2026-10-08:
- Template: https://v0.app/templates/tasko-modern-task-management-dashboard-SGGKgsJdrxG
- Public preview: https://v0-dashboard-ui-redesign-nine.vercel.app/
- Screens inspected through navigation: `/`, `/tasks`, `/analytics`, `/team`, `/calendar`, `/settings`.

The UI branding says Tasko; browser title says Donezo. No repository or source export was available. Therefore this is an independent backend matched to visible workflows, not an integration verified against frontend source. No existing frontend conventions, API client or source structure could be inspected. No unrelated frontend behavior was changed.

| Observed screen | Confirmed data needs | Implemented contract |
|---|---|---|
| Dashboard | Project totals/statuses, team task assignments, due dates, reminder meeting, timer | Project/task/member lists, events, time entries; analytics project counts |
| Tasks | Title, project, due date, tags, high/medium/low priority, completed checkbox, active/all/completed tabs, search | Task CRUD subset (create/read/update), validated references, filters and pagination |
| Analytics | Completed tasks, active projects, team count, average completion, monthly totals, project distribution | One stable aggregation endpoint with explicit time/current-state semantics |
| Team | Names, job titles, task counts, add member | Authenticated user memberships and live active-task counts |
| Calendar | Events/meetings, title, time and duration | Team events with start/end UTC instants and overlap queries |
| Settings | Profile name/email/photo, notification switches, theme | Read profile, edit name, persist notification/theme preferences |

Add Task and Add Member did not open creation forms in the observed preview. Their backend payloads are minimal proposed contracts based on visible data. Calendar event creation/editing is owner-managed. Projects require a name; tasks require a title and project. Descriptions are optional supporting fields. All shared data is owned by a team; users may own or join multiple teams. Team creation exists as necessary backend onboarding even though the preview does not show it. No team switcher frontend is supplied.

## Explicit assumptions

- Owners manage projects, members, assignments and events. Members see their team and edit their assigned tasks; they cannot reassign. No manager/admin role is inferred from job titles.
- Owner is represented by `teams.owner_id`; the memberships collection holds other members. Owner membership is counted exactly once. No transfer/removal flow is invented.
- Active tasks are statuses other than completed. Active projects mean in_progress. Project status is manually managed by owners, not automatically derived from tasks.
- Existing registered accounts are added by exact email. No invitations, verification emails or third-party messaging service is implied.
- Completion duration is elapsed creation-to-latest-completion, not tracked work time. Reopening clears the completion marker. No task-completion history/event-sourcing system is added.
- Time tracker entries belong to a task and user; one running timer per user. Stop then start implements pause/resume as distinct sessions. The template's timer value is not retained.
- Calendar events are internal records, not Google/Outlook integration. Notifications are saved preferences only, not delivered messages.

## Deliberate exclusions

No import pipeline, mobile app integration, export-to-PDF generator, video meeting provider, email/phone sending, presence inference, avatar upload, email change, password reset or email verification is invented. Profile email is readable but immutable until a verification workflow is specified. No contact telephone data or real-time Active/Away presence is fabricated. Help content needs no persistent API. The template's arbitrary weekly activity percentages and comparative trend badges lack definitions and are not reproduced as fabricated metrics. Analytics JSON can be used for a frontend export once the output format is specified. No destructive delete feature was established.

These limitations are intended to keep the backend aligned to confirmed data rather than implying unsupported integrations. A source export would still be needed to wire React components to this API and remove hardcoded UI values.

## Collections and relationships

All IDs are UUID4 lowercase hex strings, stored as `_id`. Public responses rename this field `id`. Server-generated timestamps use UTC. Validation forbids unexpected request fields, preventing owner/team/status injection through mass assignment.

| Collection | Principal fields | Important indexes |
|---|---|---|
| users | name, normalized email, password_hash, preferences, created_at, updated_at | email unique |
| teams | name, owner_id, timestamps | owner_id |
| memberships | team_id, user_id, job_title, timestamps | (team_id,user_id) unique; (user_id,team_id) |
| projects | team_id, name, description, status, due_at, timestamps | (team_id,status); (team_id,created_at,_id) |
| tasks | team_id, project_id, assignee_id, title, description, priority, tags, due_at, status, completed_at, version, timestamps | (team_id,status,completed_at); (team_id,project_id,status); (team_id,assignee_id,due_at); stable created_at list index |
| events | team_id, title, starts_at, ends_at, timestamps | team/time overlap and stable created_at indexes |
| time_entries | team_id, user_id, task_id, started_at, ended_at, running | partial unique user_id when running; (team_id,user_id,started_at) |
| timesheets | team_id, user_id, task_id, work_date, minutes, notes, member/task/project name snapshots, timestamps | (team_id,work_date,_id); (team_id,user_id,work_date,_id) |
| sessions | hashed token as _id, user_id, csrf, expires_at | expires_at TTL; user_id |

Every resource operation checks team membership and scopes MongoDB queries by team_id. Cross-team references are rejected. List endpoints fetch bounded result pages. Aggregations use database-side grouping rather than loading all tasks into Python. No MongoDB transaction is required for current create/update flows: each mutation writes one authoritative document. Task optimistic concurrency is atomic; project/event replacements currently use last-write-wins semantics.

## Reliability and security choices

PyMongo's supported AsyncMongoClient is used, not the older Motor driver. Async clients are opened/closed by FastAPI lifespan. Startup pings dependencies, creates indexes if enabled, and fails if required services are unavailable. Redis is shared by every serving instance; the Lua limiter is the only production limiter. Session validation is always against MongoDB; permission changes are not hidden by authorization caches.

OpenAPI schemas cover responses and common errors. API errors use `{ "error": { "code", "message", "request_id", "details" } }`; validation errors include field locations/types but never echo request values. Logging excludes payloads/secrets and uses server-generated request IDs. Cookie-authenticated mutations use a synchronizer CSRF token plus an origin check when an Origin header is present.

References checked while implementing:
- https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/mongoclient/
- https://www.mongodb.com/docs/languages/python/pymongo-driver/current/reference/migration/
- https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/ (Argon2/password hashing guidance; this implementation uses opaque sessions, not JWTs)

See README for all routes, request/response examples, rate-limit policy, cache semantics, Docker startup and tests.
