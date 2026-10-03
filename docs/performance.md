# Runtime performance

`AI_MAX_CONCURRENCY` defaults to 4 (valid range 1–24). Saturated evaluations are skipped without consuming their cadence; the next tick may retry. The ingress keeps one worker and at most one newest pending context per symbol/timeframe. Order verification, sizing, and dispatch remain serialized by the existing order lock.

Each authenticated dashboard connection has one writer, a 32-event queue, and one coalesced heartbeat. Slow clients time out after 0.8 seconds; event overflow closes the connection so the browser reconnects and receives an authoritative snapshot. Heartbeat coalescing preserves pending history changes. The initial snapshot sends the newest 50 history rows; subsequent packets send only changed rows plus their authoritative ID order, including edits below the first row and deletions. Deltas are calculated against successfully delivered history after heartbeat coalescing, so pending changes cannot be lost.

`GET /api/positions?offset=50&limit=50` returns older newest-first rows, the total row count, and the requested offset/limit. API limits are 1–100. The dashboard has latest/previous/next history controls; history statistics describe the displayed page. Settlement and recovery calculations retain their original data sources.

Live decisions recheck round identity, current source timestamp, indicator readiness, selected market, confirmed strike, and expiry after AI and after pre-flight verification. Executable quotes are refreshed before recovery budgeting; pre-flight operations exceeding five seconds are rejected conservatively. Simulation expiry is also checked.

## Measurements

Authenticated `GET /api/status` exposes `performance`: bounded windows of 512 samples per stage with mean, p95, and p99 milliseconds; AI capacity/current usage; queued dashboard messages; skip/rejection/disconnection counters; and ingress normalization, source age, and dispatch age. Durations use a monotonic clock. These are process-local rolling windows, not lifetime aggregates.

Measured on the local Python 3.14 runtime: an EMA(9/21/50), RSI(14), ATR(14) bundle over 61 candles and 10,000 changing active ticks took **430.41 ms before** and **257.97 ms after**, median of five runs (**1.67×**, **40.1% less computation time**). Completed history is cached; active close/high/low are applied freshly. This microbenchmark excludes network latency and does not measure trading returns or overall bot throughput.

## Verification

Run `python -m pytest tests -q -p no:cacheprovider` and the dashboard TypeScript check/build. Regression coverage includes original numerical golden outputs, bounded caches, active-candle changes, burst coalescing, slow clients, expired sessions, ordered events, queue overflow, AI saturation/recovery/cancellation, stale live decisions, round rollover, expiry, and history pagination. All exchange/AI interactions in the new tests are mocked.
