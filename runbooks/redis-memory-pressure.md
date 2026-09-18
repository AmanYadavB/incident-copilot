---
title: Redis memory pressure and eviction storm
alerts:
  - RedisMemoryHigh
severity: warning
scope: [cache]
last_reviewed: 2026-09-18
---

# Redis memory pressure and eviction storm

Fires when Redis's memory usage approaches `maxmemory`. With an LRU eviction policy this doesn't crash Redis — it starts silently dropping keys to stay under the limit — which is exactly why it's dangerous: no errors anywhere, just a quietly worsening cache-hit rate that pushes load onto whatever's behind the cache.

## Symptoms

- `used_memory` approaching `maxmemory` (e.g. 96%), `evicted_keys` climbing at a steady, non-trivial rate.
- Cache hit rate dropping over a short window (e.g. 94% → 71% in 10 minutes) — this is the real user-facing signal, not the memory percentage itself.
- Downstream, whatever the cache was protecting (usually the primary database) sees rising query load and possibly its own latency/connection-pool alerts, one hop removed from this one.
- With `allkeys-lru` (or similar), no errors or refused writes — Redis keeps working, just with a shrinking effective cache. With a `noeviction` policy instead, writes start failing outright once memory is full — check which policy is configured, since the failure mode is completely different.

## Likely causes

1. **Legitimate growth in working-set size** — more distinct keys/users than the cache was sized for; no single bad actor, just organic growth past the configured `maxmemory`.
2. **A new or changed code path caching much larger values than before**, or caching something that didn't used to be cached (a bulk object instead of a reference).
3. **Missing or too-long TTLs** — keys that should expire are accumulating instead, crowding out active working-set data.
4. **A single hot/bloated key or key pattern** — e.g. a per-request cache key that should be bounded but isn't (unbounded cardinality from a user-supplied value used directly in the key).
5. **`maxmemory` simply set too low for current scale** — the workload is fine, the ceiling wasn't updated when traffic grew.

## First 3 commands

```bash
redis-cli INFO memory                                   # used_memory vs maxmemory, maxmemory_policy, mem_fragmentation_ratio
redis-cli --bigkeys                                      # find outsized keys/patterns fast (sampling, safe on prod)
redis-cli INFO stats | grep -E 'evicted_keys|keyspace_hits|keyspace_misses'   # eviction rate and hit-rate trend
```

If `--bigkeys` isn't conclusive, sample key patterns with `redis-cli --scan --pattern '<suspect>*' | wc -l` to check for unbounded cardinality rather than a few large values.

## Fix

**Buy time first (if the eviction policy is `noeviction` and writes are failing — otherwise skip, LRU is already degrading gracefully):**
- Manually expire or delete clearly-disposable keys to free headroom immediately, or temporarily switch policy to `allkeys-lru` to stop write failures while the real cause is found — confirm with the cache's owner first, since it changes eviction behavior cluster-wide.

**Root cause:**
- **Organic growth:** raise `maxmemory` to match current working-set size plus headroom — the straightforward, usually-correct fix when nothing else looks wrong.
- **Oversized or newly-large cached values:** fix the code path to cache smaller/more targeted data (a reference or ID instead of a full object), or stop caching what doesn't need to be.
- **Missing/long TTLs:** add or shorten `EXPIRE` on the offending key pattern so stale entries stop accumulating.
- **Unbounded key cardinality:** fix the key-naming scheme so it can't grow without bound (e.g. don't put a raw user-supplied string directly in the key without a cap), and clean up the existing bloat with a targeted `SCAN` + delete.

**Verify:** `used_memory` stabilizes with headroom under `maxmemory`, `evicted_keys` rate flattens, and cache hit rate recovers to its prior baseline. Also check that DB load (if it rose) comes back down — hit-rate recovery should show up there too.

## Escalate when

- Hit rate doesn't recover after fixing the apparent cause — there may be a second contributing factor (e.g. both growth and a bloated key pattern at once).
- The eviction policy is `noeviction` and writes are actively failing — this is a harder outage than a pure LRU degradation and needs faster action, including the temporary policy-switch mitigation above.
- This cache sits in front of a database that's already showing its own load/latency symptoms — coordinate the fix with whoever owns the database side, since raising `maxmemory` alone won't help if the real issue is what's being cached.

## Related runbooks

- **API latency SLO breach or elevated 5xx rate** — a falling cache hit rate is a common quiet contributor to a latency SLO breach on services that sit behind this cache.
- **Postgres connection pool exhausted** — if this cache protects the primary database, an eviction storm can be the reason DB load (and pool pressure) rose in the first place.
