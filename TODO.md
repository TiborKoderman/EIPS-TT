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
