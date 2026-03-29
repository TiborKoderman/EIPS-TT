# Manager/Crawler Continuation TODO

Last rebuilt: 2026-03-26

## WebSocket-Only Crawler Migration Plan (2026-03-27)

### Backup snapshot (before standalone removal)

- [x] Created backup branch `backup/standalone-support-20260327` from current `gui` HEAD.
- [x] Created annotated tag `backup-standalone-support-20260327` to preserve pre-removal state.

### Detailed implementation phases

#### Phase A - Server Frontier Ownership (ManagerApp)

- [x] [webserver] Add a dedicated frontier service in ManagerApp that owns queue claim/lease/state transitions.
- [x] [webserver] Expose server endpoints for daemon frontier operations (`next`, `complete`, `failed`, `duplicate`, `add_discovered`, `stats`).
- [x] [webserver] Ensure daemon token-auth on frontier endpoints.
- [x] [webserver] Make all state transitions atomic and lease-token validated.

#### Phase B - Daemon Queue Simplification

- [ ] [crawler] Remove daemon-local frontier queue ownership from crawler runtime.
- [x] [crawler] Remove daemon-local frontier DB sync/lease/write paths from crawler runtime.
- [x] [crawler] Keep crawler workers as WebSocket/HTTP controlled executors only.
- [x] [crawler] Keep page extraction/download/reporting behavior unchanged from worker perspective.

#### Phase C - Remove Standalone Support

- [x] [crawler] Delete standalone mode entry points and standalone queue implementation files.
- [x] [crawler] Refactor `pa1/crawler/src/main.py` to websocket-only daemon path.
- [x] [crawler] Remove standalone CLI flags/docs/scripts references.

#### Phase D - Registration UX Completion

- [x] [webserver] Complete daemon registration section in UI with generated command output.
- [x] [webserver] Support at least bash and docker command templates.
- [x] [webserver] Include manager server IP/host and registration token in generated command.
- [x] [webserver] Include daemon identifier/group in generated command.
- [x] [webserver] Verify generated command can register a remote daemon and be controlled from manager.

#### Phase E - Verification and documentation

- [x] [crawler+webserver+database] Verify frontier, politeness, and scheduling are server-owned and functioning.
- [x] [crawler+webserver] Functional verification with active daemon and multiple workers.
- [x] [webserver] GUI verification (worker controls, queue behavior, registration flow).
- [x] [docs] Update README/module docs for websocket-only architecture.
- [x] [docs] Update `.github/instructions/eips-project-conventions.instructions.md` to match new architecture.

### Feature replacement matrix (what is removed from crawler and must remain server-side)

- [x] Queue ordering and claim policy: server-side frontier service.
- [x] Lease creation/expiry/recovery: server-side frontier service.
- [x] Queue state transitions (`QUEUED/LOCKED/PROCESSING/COMPLETED/DUPLICATE/FAILED`): server-side frontier service.
- [x] Collision/duplicate queue suppression across workers/daemons: server-side frontier service.
- [x] Politeness scheduling (per-IP/per-domain pacing): server-side scheduler/politeness service.
- [ ] Robots allow/disallow gate for queueing: server-side policy gate for discovered URLs.
- [ ] Discovered URL ingest and prioritization: server-side enqueue API.
- [ ] Frontier observability counters and diagnostics: server-side telemetry/query endpoints.

### Acceptance criteria for this migration

- [x] Crawler daemon can run without standalone mode.
- [x] Crawler daemon no longer manages frontier persistence logic.
- [ ] Server can allocate work to multiple workers safely and fairly.
- [ ] Robots-disallowed URLs are persisted as discovered links/pages but are not queued.
- [x] Registration UI can generate copy/paste command for remote daemon registration.
- [x] GUI worker controls remain functional after migration.

## Completed in this pass

- [x] Restored `ThroughputLineChart` component so worker/detail and dashboard throughput charts render.
- [x] Confirmed workers/dashboard logs table layout: severity first, timestamp second, message last.
- [x] Confirmed live log refresh + severity/search filters on Workers and Worker Detail pages.
- [x] Kept Worker Config / Daemon Config entry points inside Workers page header actions.
- [x] Confirmed animated worker activity indicators are reused in Workers tab rows.
- [x] Confirmed manager persists worker logs/metrics to DB and rotates via retention cleanup.
- [x] Added queue collision guards in daemon frontier enqueue paths (global/local + active lease checks).
- [x] Removed crawler frontier DB swap/sync persistence paths; daemon frontier now stays memory + manager relay only.
- [x] Removed standalone crawler code paths (`standalone_runner`, standalone queue impl, standalone preset script).
- [x] Updated crawler main entrypoint to websocket-only execution.
- [x] Completed daemon registration UI command generator with bash/docker output, manager host override, token override, and daemon ID override.
- [x] Added `/api/frontier/dequeue` endpoint for chunk-style claims scoped by worker IDs.
- [x] Added manager client method `DequeueFrontierAsync(...)` for the new dequeue API.
- [x] Improved worker failure telemetry to log fetch/parse stage in status reason and warnings.
- [x] Smoke-tested `/api/frontier/status`, `/api/frontier/claim`, `/api/frontier/complete`, and `/api/frontier/dequeue` against live manager+daemon runtime (including requeue via `status=queued`).
- [x] Unified dashboard frontier queue snapshots to use server-owned status as primary source (fallback to daemon snapshot only on manager status failure).
- [x] Fixed dashboard queue widget visibility so top queued URLs are shown independently from daemon telemetry snapshot availability.
- [x] Aligned top queue query to enum state redesign by selecting only `QUEUED` rows from `crawldb.frontier_queue`.
- [x] Added manager-side batch frontier enqueue path for discovered URLs reported through `/api/crawler/ingest` to centralize ingestion-stage dedupe.
- [x] Added sitemap ingestion hook in manager ingest flow: parse robots sitemap payloads, extract `<loc>` URLs (`urlset`/`sitemapindex`), and enqueue discovered URLs server-side with safety limits.
- [x] Smoke-tested ingestion dedupe by posting repeated discovered URLs to `/api/crawler/ingest` and verifying single `frontier_queue` rows per URL.
- [x] Implemented server-side delegate cooldown scheduling keyed by crawler daemon + resolved site IP, with timed skip-and-retry behavior during frontier claim/dequeue.
- [x] Added ingest race-recovery guards for concurrent `crawldb.page.url` conflicts in `/api/crawler/ingest` and resilient discovered frontier upserts.
- [x] Optimized discovered URL ingestion path with per-batch site-id cache and batched link insert (`unnest(@target_ids)`), reducing per-link round-trips.
- [x] Validated concurrent ingest + queue lifecycle end-to-end (12 parallel ingests, dequeue, complete) with DB verification of deduped queue/page counts.
- [x] Split graph UI into two force-graph modes: static results snapshot and dynamic replay timeline driven by crawler event history.
- [x] Added replay controls (play/pause/reset/speed/scrub) and explicit two-level graph focus controls (sites/pages).
- [x] Updated dashboard queue list rendering/poll cadence so replacement queued URLs reappear immediately after claims.
- [x] Kept terminal `DUPLICATE` frontier rows from being reset back to `QUEUED` during manager enqueue upserts.
- [x] Expanded dashboard frontier diagnostics with `In queue / In memory / Leased` metrics and an IP timeout widget (including mapped domains).
- [x] Added queue row timeout badges and static-height queue viewport to visualize rate-limited URLs without layout jump.
- [x] Rebuilt static site graph mode as a dedicated renderer with unique site nodes, score-based colors, page-count sizing, and numbered inter-site edge weights.
- [x] Moved dashboard IP timeout diagnostics into queue header with fixed-height viewport to avoid layout shifting.
- [x] Added collapsed dashboard snapshot for `LOCKED`/`PROCESSING` frontier items so queued candidates remain visible.
- [x] Updated default crawler seed preset so `https://medover.zurnal24.si/` is the only default-enabled seed.
- [x] Expanded default relevance keywords with fitness-focused terms while keeping medical terms.
- [x] Updated daemon registration script generation to always export websocket + ingest tokens and auto-generate a token when missing.
- [x] Validated dockerized daemon registration workflow with loopback-safe docker command generation.
- [x] Performed assignment-compliant DB dump (`pa1/db`) excluding `crawldb.image` and `crawldb.page_data` payload rows.
- [x] Ran clean DB reset/rebuild and validated multi-worker start flow from GUI (5 total workers, 4 active under concurrency cap).
- [x] Enforced hard minimum 5s server politeness cooldown and wired robots/effective delay reports from worker ingest to manager timeout scheduling.
- [x] Added Slovenian default keyword translations across crawler/manager defaults and validated a clean ground-up run with default seed + default keywords at 4 concurrent workers.
- [x] Added release-grade Docker packaging assets (`docker-compose.yml`, bundled server+crawler Dockerfile, GHCR publish workflow, local release script) and updated install/deploy docs.
- [x] Removed compose-based devcontainer mode and updated root setup docs for host workflows plus local Docker build/run without GHCR publishing.
- [x] Fixed multi-daemon worker routing in manager UI/services (daemon-aware worker actions/groups/detail links), stabilized daemon panel expand behavior, and fixed full-slice dashboard worker pie chart rendering.
- [x] Removed remaining crawler direct-DB code paths and deleted unused compatibility modules (`src/db/*`, `src/core/crawl_processor.py`, `src/daemon/persistence_router.py`, `src/api_server.py`), keeping Python websocket entrypoint startup intact.
- [x] Updated `scripts/prepare-submission.sh` to run without the removed `app` compose service (host execution with explicit `latexmk` prerequisite check).
- [x] Restored collected binary type breakdown (PDF/DOC/DOCX/PPT/PPTX) with zero-safe rendering and binary-page URL fallback classification.
- [x] Moved collected binary type top bar from Dashboard to Collected Pages tab.
- [x] Added collected image type breakdown (JPG/PNG/WEBP/GIF/SVG/BMP/TIFF/ICO/AVIF/OTHER) to Collected Pages.
- [x] Hardened crawler document type detection to include query filename hints and `Content-Disposition` filenames.

## Pending follow-ups

- [x] Run full end-to-end runtime validation with live daemon + manager + DB migration path (`/api/frontier/dequeue`, swap refill behavior, claim/complete lifecycle under load).
- [x] Add automated integration tests for frontier collision scenarios and swap refill edge cases (script: `scripts/integration-frontier-smoke.sh`).
- [ ] Decide whether `crawldb.frontier_queue` needs extra FK columns to directly reference `page` / `page_type` / `page_data` records, then add a migration if required.

## Staged Refactor Reinforcement

### Stage 1 - Documentation and Container Assets (no runtime code changes)

- [x] [crawler] Added module documentation with assignment-mapped responsibilities and flowchart (`pa1/crawler/crawler.md`).
- [x] [daemon] Added module documentation with worker orchestration scope and flowchart (`pa1/crawler/src/daemon/daemon.md`).
- [x] [webserver] Added module documentation with UI/config/auth channel requirements and flowchart (`ManagerApp/webserver.md`).
- [x] [database] Added module documentation with migration policy and flowchart (`db/database.md`).
- [x] [crawler] Added crawler container image definition (`pa1/crawler/Dockerfile`).
- [x] [daemon] Added daemon container image definition packaging full crawler tree (`pa1/crawler/src/daemon/Dockerfile`).
- [x] [webserver] Added manager Blazor container image definition (`ManagerApp/Dockerfile`).
- [x] [database] Added database image definition with initialization script copy (`db/Dockerfile`).

### Stage 2 - Crawler Standalone and Daemon Refactor

- [x] [crawler] Refactor standalone crawler runtime boundaries and entrypoint composition.
- [x] [daemon] Refactor daemon spawn/control orchestration interfaces.

### Stage 3 - Duplicate Logic Consolidation

- [x] [crawler+daemon+webserver] Identify and remove duplicated logic across modules while preserving behavior.

### Stage 4 - Architecture Simplification and Frontier Optimization

#### Phase 1: Database Schema (COMPLETED)

- [x] [database] Created migration 06 with frontier_queue enum state redesign (QUEUED, LOCKED, PROCESSING, COMPLETED, DUPLICATE, FAILED)
- [x] [database] Added optimized indexes for heap-like priority queue behavior
- [x] [database] Added memory_cached flag and lease tracking (locked_at, locked_by_worker_id)
- [x] [database] Added duplicate_of_url_id foreign key for sparse duplicate tracking
- [x] [database] Updated database.md with enum states and access patterns documentation

#### Phase 2: Worker State Machine (COMPLETED)

- [x] [daemon] Created WorkerStateMachine with states: IDLE, ACTIVE, PAUSED, STOPPED
- [x] [daemon] Added state transition validation and callback registration
- [x] [docs] Added comprehensive worker state diagram in daemon-worker-runtime.md (Mermaid stateDiagram-v2)
- [x] [docs] Documented all valid state transitions with triggers and reasons

#### Phase 3: Queue Interface Protocol (COMPLETED)

- [x] [api] Created FrontierQueueProvider protocol for decoupling workers from queue implementation
- [x] [api] Defined QueuedUrl dataclass for frontier entries
- [x] [api] Added protocol methods: next_url(), mark_complete(), mark_failed(), mark_duplicate(), add_discovered_urls(), get_frontier_stats()
- [x] [api] Protocol supports both standalone (direct DB) and websocket (server-managed) modes

#### Phase 4: Unified Crawler Entry Point (COMPLETED)

- [x] [crawler] Refactored pa1/crawler/src/main.py to unified entry point with --mode flag and CRAWLER_MODE environment variable
- [x] [crawler] Added mode routing: standalone (CLI utilities) and websocket (daemon runtime)
- [x] [crawler] Updated daemon/main.py to be compatibility wrapper that delegates to unified main.py
- [x] [crawler] Updated pa1/crawler/Dockerfile to default to websocket mode with documentation
- [x] [crawler] Updated pa1/crawler/src/daemon/Dockerfile with clear comments about compatibility

#### Phase 5: Module Documentation Updates (COMPLETED)

- [x] [crawler] Updated pa1/crawler/crawler.md with unified entry point, mode documentation, and architecture diagrams
- [x] [webserver] Updated ManagerApp/webserver.md with server's frontier queue provider role and default daemon initialization
- [x] [database] Updated db/database.md with frontier queue state machine and optimization details
- [x] [docs] Added comprehensive sections on mode-based execution, queue interface, and worker state integration

#### Phase 6: Refactor WebSocket Mode for Token Auth (COMPLETED)

- [x] [daemon] Implement WebSocket queue operations in server (next_url request handler)
- [x] [daemon] Add token authentication to reverse channel and queue request handlers
- [x] [daemon] Implement FrontierQueueProvider for WebSocket mode (marshals to server API)
- [x] [crawler] Add worker code that uses FrontierQueueProvider abstraction

#### Phase 7: Server Daemon Initialization (COMPLETED)

- [x] [webserver] Implement automatic daemon spawn on manager startup with 1 default worker
- [x] [webserver] Add daemon health check and restart logic
- [x] [webserver] Expose daemon start/stop controls in UI

#### Phase 8: Integration Testing and Validation (IN PROGRESS)

- [ ] [scripts] Create smoke test for standalone mode (CLI utilities)
- [ ] [scripts] Create smoke test for websocket mode (full crawl with manager)
- [x] [scripts] Validate both modes with the simplified frontier queue (manual smoke: standalone canonicalize + websocket startup routing)
- [ ] [scripts] Verify state reporting and worker state machine transitions
- [ ] [docker] Test containerized deployment with docker-compose
- [ ] Run end-to-end functional tests validating all changes
