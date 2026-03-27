# Manager/Crawler Continuation TODO

Last rebuilt: 2026-03-26

## Completed in this pass

- [x] Restored `ThroughputLineChart` component so worker/detail and dashboard throughput charts render.
- [x] Confirmed workers/dashboard logs table layout: severity first, timestamp second, message last.
- [x] Confirmed live log refresh + severity/search filters on Workers and Worker Detail pages.
- [x] Kept Worker Config / Daemon Config entry points inside Workers page header actions.
- [x] Confirmed animated worker activity indicators are reused in Workers tab rows.
- [x] Confirmed manager persists worker logs/metrics to DB and rotates via retention cleanup.
- [x] Added queue collision guards in daemon frontier enqueue paths (global/local + active lease checks).
- [x] Added frontier DB sync/swap integration while keeping in-memory queue as primary thread-safe queue.
- [x] Added `/api/frontier/dequeue` endpoint for chunk-style claims scoped by worker IDs.
- [x] Added manager client method `DequeueFrontierAsync(...)` for the new dequeue API.
- [x] Improved worker failure telemetry to log fetch/parse stage in status reason and warnings.

## Pending follow-ups

- [ ] Run full end-to-end runtime validation with live daemon + manager + DB migration path (`/api/frontier/dequeue`, swap refill behavior, claim/complete lifecycle under load).
- [ ] Add automated integration tests for frontier collision scenarios and swap refill edge cases.
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

#### Phase 5: Module Documentation Updates (IN PROGRESS)

- [x] [crawler] Updated pa1/crawler/crawler.md with unified entry point, mode documentation, and architecture diagrams
- [x] [webserver] Updated ManagerApp/webserver.md with server's frontier queue provider role and default daemon initialization
- [x] [database] Updated db/database.md with frontier queue state machine and optimization details
- [x] [docs] Added comprehensive sections on mode-based execution, queue interface, and worker state integration

#### Phase 6: Refactor WebSocket Mode for Token Auth (PENDING)

- [ ] [daemon] Implement WebSocket queue operations in server (next_url request handler)
- [ ] [daemon] Add token authentication to reverse channel and queue request handlers
- [ ] [daemon] Implement FrontierQueueProvider for WebSocket mode (marshals to server API)
- [ ] [crawler] Add worker code that uses FrontierQueueProvider abstraction

#### Phase 7: Server Daemon Initialization (PENDING)

- [ ] [webserver] Implement automatic daemon spawn on manager startup with 1 default worker
- [ ] [webserver] Add daemon health check and restart logic
- [ ] [webserver] Expose daemon start/stop controls in UI

#### Phase 8: Integration Testing and Validation (PENDING)

- [ ] [scripts] Create smoke test for standalone mode (CLI utilities)
- [ ] [scripts] Create smoke test for websocket mode (full crawl with manager)
- [ ] [scripts] Validate both modes with the simplified frontier queue
- [ ] [scripts] Verify state reporting and worker state machine transitions
- [ ] [docker] Test containerized deployment with docker-compose
- [ ] Run end-to-end functional tests validating all changes
