---
title: API latency SLO breach or elevated 5xx rate
alerts:
  - ApiLatencyP99High
  - HighHttp5xxRate
severity: critical
scope: [application]
last_reviewed: 2026-09-18
---

# API latency SLO breach or elevated 5xx rate

The two most common "users are affected" alerts: p99 latency crosses its SLO, or the ingress/edge sees a spike in 5xx responses. They're grouped because they share a diagnosis path — find which upstream is actually responsible — even though one is a degradation and the other is outright failure. They also frequently co-occur: a saturated dependency shows up as slow *and* erroring at the same time, just measured from two different vantage points.

## Symptoms

- **`ApiLatencyP99High`**: p99 climbs well past SLO (e.g. 3.8s against a 1s target) while p50 stays normal — a clear sign it's a subset of requests, not universal slowness. Slow endpoints are typically the ones with an external dependency (DB, downstream API), not CPU-bound ones.
- **`HighHttp5xxRate`**: ingress-level 5xx rate jumps sharply from near-zero to double digits within a couple of minutes. Breakdown matters: mostly `502`s point at the upstream dying/refusing mid-request; mostly `504`s point at the upstream being too slow, not down.
- Both alerts naming the *same* upstream service in their breakdown is a strong signal they're the same incident measured two ways, not two separate problems.
- If p99 is high but p50 and error rate are both normal, this is a tail-latency problem (one slow dependency, a lock, a GC pause pattern) rather than a capacity problem.

## Likely causes

Ranked by frequency, and shared across both alerts since they're usually the same root cause:

1. **A downstream dependency (DB, cache, another service) is saturated or slow** — the API is healthy, it's waiting on something else. DB-backed endpoints slowing down while non-DB endpoints stay fast is the clearest tell.
2. **Connection pool exhaustion** (DB or HTTP client pool) — requests queue waiting for a free connection, which shows as latency first and then as errors once queued requests start timing out.
3. **A bad deploy** — new code with an accidental N+1 query, a missing index, a synchronous call that used to be async.
4. **Downstream returning errors, not just being slow** — a dependency itself is 5xx-ing, and this service is faithfully propagating (or timing out waiting for) that failure.
5. **Resource saturation on the service's own pods** — CPU throttling or memory pressure serializing request handling; check this only after ruling out a downstream, since it's less common than it seems.

## First 3 commands

```bash
# Trace which upstream the slow/erroring requests are actually waiting on
kubectl -n <ns> logs -l app=<service> --tail=200 | grep -i 'error\|timeout\|5[0-9][0-9]'
# Check the obvious first suspect: is a DB/cache connection pool the bottleneck
<db-metrics-query-for-pool-utilization>   # e.g. pg_stat_activity count, or your pool exporter's in-use/total gauge
kubectl -n <ns> top pod -l app=<service>   # rule in/out CPU throttling or memory pressure on the service itself
```

If the ingress breakdown already names the upstream (nginx logs typically do), skip straight to that upstream's own health rather than starting from this service's logs.

## Fix

- **Downstream saturated (DB, cache, another API):** the fix lives in that dependency's own runbook — this alert is usually the *symptom*, not the incident. Check connection-pool exhaustion and cache-memory-pressure runbooks first if either is in the request path.
- **Connection pool exhaustion on this service's side:** raise the pool size only after confirming the downstream can actually handle more connections — otherwise this just moves the bottleneck. Kill long-held idle-in-transaction connections if that's what's eating the pool.
- **Bad deploy:** roll back (`kubectl rollout undo`) and confirm both latency and error rate recover before investigating the code fix at leisure.
- **Downstream itself erroring:** add or tighten a circuit breaker / timeout so this service fails fast and sheds load instead of queueing behind a dependency that's already down — a fast, clean 503 is better for callers than a slow 200 that never comes.
- **Resource saturation on this service:** scale replicas or raise CPU limits; check `KubeDeploymentReplicasMismatch` isn't the actual reason there are fewer healthy pods than expected.

**Verify:** p99 back under SLO with p50 unchanged (confirms the tail issue is resolved, not masked by an unrelated shift), and 5xx rate back to baseline with the specific upstream no longer appearing in the ingress error breakdown.

## Escalate when

- The root cause is a downstream service you don't own (payments, a third-party API) — loop in that team rather than continuing to treat symptoms on your side.
- 5xx rate keeps climbing after a rollback — the last deploy wasn't the cause; look for a concurrent infra change (DNS, network policy, node health) instead.
- This is a payment/checkout path — treat any sustained 5xx rate here as revenue-impacting and escalate faster than the alert severity alone suggests.

## Related runbooks

- **Postgres connection pool exhausted** — one of the most common concrete causes behind both of these alerts when the slow/erroring endpoints are all DB-backed.
- **Redis memory pressure** — a cache eviction storm degrades hit rate and pushes load onto the DB, which then shows up here as latency.
- **Service unreachable — scrape failing, DNS failing, or upstream refused** — check this if the 5xx breakdown points at a dependency that's outright unreachable rather than merely slow.
