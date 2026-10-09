# Verification report

Member analytics and organisation email validation checked 2026-10-09:

- Backend suite: **23 passed**. Added coverage for personal completion dates and working
  hours, separate owner/member analytics caches, timesheet cache invalidation, exact email
  domain matching, domain normalization, owner-only organisation edits, existing-member
  compatibility, and legacy team configuration.
- Frontend `tsc --noEmit --incremental false`: passed.
- `ruff check app tests`: passed.
- Working hours sum all submitted timesheets; completion dates use UTC `completed_at`.
  Empty days/months are omitted. Tests use mongomock/fakeredis, not a live browser or
  real MongoDB integration.

Timesheet feature checked 2026-10-09 on Windows with Python 3.12 and dependencies
installed within the declared `pyproject.toml` ranges (this run did not use `uv.lock`):

- Backend suite: **20 passed**, including member-only submission, assigned-task checks,
  member history privacy, owner history, cross-team isolation, immutable name snapshots,
  CSRF protection, input validation, date filtering, and pagination.
- Ruff checks for the changed Python files: passed.
- Frontend `tsc --noEmit --incremental false`: passed.
- Tests used mongomock/fakeredis. No live browser or real MongoDB integration was run.

Earlier verification:

Checked 2026-10-08 against the included uv.lock on Python 3.12.14.

- `uv sync`: dependencies resolved and installed successfully from public PyPI.
- `uv run pytest -q`: **14 passed**. Test-only mongomock emits UTC deprecation warnings; application timestamps are timezone-aware.
- `uv run ruff check --config pyproject.toml app tests`: passed.
- OpenAPI generation from the actual FastAPI application: passed; exported to `docs/openapi.json`.
- Both Compose YAML files: parsed successfully. This is syntax validation, not a Docker build/run.

The executed tests use isolated MongoDB mocks and fakeredis executing the production Lua script. They verify API behavior but cannot prove all MongoDB server/index behavior or container networking. `compose.test.yaml` runs the same route suite against real disposable MongoDB and Redis; Docker is not installed in the generation environment, so neither that suite nor the local Compose stack was executed here.

No existing repository was available and no real frontend API integration was run. Screens were inspected in the public live preview. The ZIP contains the independent backend, not a rewritten frontend. See design.md for supported workflows, explicit assumptions and omitted integrations.
