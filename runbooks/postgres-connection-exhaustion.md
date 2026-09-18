---
title: Postgres connection pool exhausted
alerts:
  - PostgresConnectionPoolExhausted
severity: critical
scope: [database]
last_reviewed: 2026-09-18
---

# Postgres connection pool exhausted

Fires when active connections to a Postgres instance hit `max_connections`, so new connection attempts fail outright — `remaining connection slots are reserved`. This is one of the highest-blast-radius database alerts: every request path touching this database starts erroring at once, and it usually cascades into latency/5xx alerts on every service that depends on it.

## Symptoms

- `pg_stat_activity` count equals `max_connections`. New connections are refused with `FATAL: remaining connection slots are reserved for non-replication superuser connections` (or the app-level equivalent from your driver/pool).
- A meaningful chunk of those connections are `idle in transaction` for well past any reasonable request duration (seconds, not the 90+ seconds that actually shows up in a real exhaustion) — this is usually the smoking gun, not raw traffic volume.
- Any service using this database starts erroring or timing out simultaneously — this alert is frequently the *root cause* underneath a latency or 5xx alert firing on the application side.
- Scheduled jobs against the same database (backups, batch jobs) fail with the same connection error, sometimes ahead of user-facing symptoms.

## Likely causes

Ranked by frequency:

1. **Connections held open in a transaction and never committed/rolled back** — a code path that opens a transaction, does work, then errors or returns early without closing it. `idle in transaction` age climbing is the clearest signal.
2. **Pool size misconfigured relative to `max_connections`** — too many application replicas each holding a sizeable pool, summing to more than the database allows; scaling out replicas without shrinking per-replica pool size is the classic trigger.
3. **A long-running query or batch job holding connections** far longer than normal, starving the pool for everything else.
4. **Connection leak** — code path that opens a connection and never returns it to the pool at all, not even idle-in-transaction; count climbs steadily over time with no correlated traffic increase.
5. **A dependent job (backup, migration, analytics) opening its own connections** on top of normal app traffic, tipping the total over the limit during its run window.

## First 3 commands

```sql
SELECT count(*), state FROM pg_stat_activity GROUP BY state;             -- how many, and idle-in-transaction vs active
SELECT pid, state, now() - state_change AS age, query
  FROM pg_stat_activity WHERE state = 'idle in transaction'
  ORDER BY age DESC LIMIT 10;                                            -- the actual stuck connections, oldest first
SELECT application_name, count(*) FROM pg_stat_activity GROUP BY application_name ORDER BY 2 DESC;  -- which service/pool is the biggest consumer
```

## Fix

**Buy time first:**
- Terminate the oldest `idle in transaction` connections to free slots immediately: `SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND now() - state_change > interval '2 minutes';` — safe because these connections aren't doing active work, they're stuck.
- If one application/pool dominates the count, that's where to look next before touching anything else.

**Root cause:**
- **Held-open transactions:** fix the code path that isn't closing its transaction on early return/error — usually a missing `finally`/`rollback` around a DB call. Short term, add a statement/idle-in-transaction timeout at the connection or database level (`idle_in_transaction_session_timeout`) so this can't recur unbounded.
- **Pool sizing:** recompute `(replica count × per-replica pool size)` against `max_connections` with headroom for other consumers (jobs, admin access), then resize the app's pool config accordingly — don't just raise `max_connections` as a first move, it treats the symptom and increases DB memory pressure.
- **Long-running query/batch job:** move it off-peak, add a statement timeout, or point it at a read replica if one exists so it stops competing with transactional traffic for the same pool.
- **Leak:** needs a code fix — audit for a connection-acquiring path missing its release; a rolling restart of the leaking service buys time but the leak returns.

**Verify:** `pg_stat_activity` count sits comfortably below `max_connections` with headroom, `idle in transaction` age stays low, and dependent services stop erroring.

## Escalate when

- Terminating idle-in-transaction connections doesn't free meaningful headroom — the exhaustion is from genuine active-connection volume, which is a capacity/scaling conversation, not a quick kill.
- This is the primary/writer instance for a payment or order-critical path — treat as highest severity regardless of the alert's own severity label; every dependent write path is down.
- A statement/idle-in-transaction timeout needs to be added at the database level — get the DB owner's sign-off, since it changes behavior for every connection, not just the one causing this incident.

## Related runbooks

- **API latency SLO breach or elevated 5xx rate** — this is very often the underlying cause when the affected endpoints are all DB-backed; check whether an app-level alert fired around the same time and treat this as the root incident.
- **CronJob failed** — a scheduled job (backup, batch) can be both a victim (fails because the pool is already exhausted) and a contributor (its own connections tipped the pool over); check which direction the causality runs.
