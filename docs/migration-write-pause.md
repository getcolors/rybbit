# Final migration write pause

Source review: Rybbit v2.9.0, commit
`f90f6dcc63e4f75324e80c53454c91b54b0244f5`, inspected 12 October 2026.
The deployment pins container digests; verify their source behavior before using
this procedure with another release.

## What can be verified

The remote `freeze` action disables the backup timer, stops any running backup
service, then stops Caddy before stopping application writers. It observes at
least 60 seconds of unchanged database/replay aggregates, no active INSERT or
ALTER statements, and no pending ClickHouse mutations. It then gives the backend
45 seconds to stop, requires a zero exit code without OOM, scans captured logs
privately for queue/shutdown failures, and waits for final mutations to finish.
Failures leave ingress fenced; they never automatically restart production.
Database services remain available for the final snapshot. The action returns
only aggregate counts and safe status fields.

This is stronger evidence than an immediate application stop. **It does not prove
that every accepted event reached the database.** The receipt explicitly reports
`queue_depth_verified: false`; a quiet database is not a queue-depth measurement.

Upstream's [pageview queue](https://github.com/rybbit-io/rybbit/blob/f90f6dcc63e4f75324e80c53454c91b54b0244f5/server/src/services/tracker/pageviewQueue.ts)
is an in-memory array processed every second in batches of up to 5,000. It has no
exposed depth or public drain method and drops a batch after an insertion error.
Bot-event queues follow the same pattern. These are not Redis work queues.
Redis holds session, identity, rate-limit and anomaly state; counting Redis keys
cannot establish that application ingestion has drained.

The [worker shutdown](https://github.com/rybbit-io/rybbit/blob/f90f6dcc63e4f75324e80c53454c91b54b0244f5/server/src/index.ts)
closes the HTTP server and drains identity backfills, with a ten-second timeout;
it does not explicitly drain the pageview array. The
[cluster primary](https://github.com/rybbit-io/rybbit/blob/f90f6dcc63e4f75324e80c53454c91b54b0244f5/server/src/cluster.ts)
waits up to thirty seconds for workers. A rigorous zero-loss guarantee needs an
upstream queue-depth/drain acknowledgement or an independent durable replay
mechanism. Neither is claimed by this controller.

## Restore evidence

Use the identical pinned database/application versions for a fresh replacement.
The native ClickHouse archive preserves schemas and types; do not convert JSON,
Array or aggregate-function columns through CSV/JSON export. Restore the complete
database into an empty destination. PostgreSQL restoration treats SQL errors as
failures. The remote receipt prevents retrying an interrupted partial restore on
top of existing data.

Compare aggregate PostgreSQL user/organization/site counts, event rows, replay
event rows, and `session_replay_metadata_v2 FINAL` session counts, summed
`event_count`, and summed `compressed_size_bytes` before application startup.
`FINAL` matters because replay metadata uses `AggregatingMergeTree` and merges
may change physical row counts without changing logical totals. Replay TTL expiry
can legitimately affect comparisons across time; explain any mismatch rather
than accepting it silently. Aggregate equality alone does not prove playback:
verify representative historical replay through the authenticated application.

Do not rerun the historical replay-v2 backfill on a current v2.9 archive. Its
insert is not idempotent and doubles additive metadata. Redis is absent from these
archives, so active session/identity state may reset. Application signing and
database secrets are transferred separately before first database startup.

## Rollback boundary

Before the replacement accepts production writes, the fenced source and retained
snapshot remain a rollback point. After replacement writes begin, changing DNS
back loses replacement-only events and account changes unless a tested reverse
migration or deduplicated replay transfers them. DNS reversal alone is not
lossless rollback. Retain the source, final snapshot, protected application
secrets and both controller authorities until the rollback window is closed.
