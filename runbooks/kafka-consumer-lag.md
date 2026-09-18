---
title: Kafka consumer group lag high
alerts:
  - KafkaConsumerGroupLagHigh
severity: warning
scope: [messaging]
last_reviewed: 2026-09-18
---

# Kafka consumer group lag high

Fires when a consumer group's lag (messages produced minus messages consumed) grows past threshold and is still climbing. The key diagnostic question is always the same: did **production** suddenly spike, or did **consumption** suddenly slow down? Those point at completely different fixes, and the metrics needed to tell them apart are both available immediately.

## Symptoms

- Lag climbs fast over a short window (e.g. 3k → 247k messages in 20 minutes) on a specific topic/consumer group.
- All consumer group members show as alive/healthy in the group metadata — this rules out "consumers crashed" as the cause and points at a slowdown or an upstream volume spike instead.
- Per-consumer processing rate (messages/sec) visibly drops at the same time lag starts climbing — this is the tell that consumption slowed, not that production spiked.
- Downstream effect depends on what the consumer does: delayed order fulfillment, delayed notifications, stale materialized views — always check what breaks *because* this lag exists before deciding urgency.

## Likely causes

1. **A downstream dependency the consumer calls per-message got slow** — a DB write, an external API call inside the processing loop — so each message takes longer even though nothing crashed. This is the most common cause when member count and health are unchanged but throughput drops.
2. **A poison-pill message** — one message the consumer can't process (bad schema, unexpected null) causing repeated retries/backoff on that partition while lag piles up behind it.
3. **Rebalancing thrash** — frequent consumer group rebalances (from flapping health checks or scaling events) repeatedly pausing consumption cluster-wide.
4. **Genuine production spike** — upstream producer volume increased faster than consumers can keep up, with consumers otherwise healthy and running at normal per-message speed.
5. **Under-provisioned partitions/consumers** — the group has always been marginally sized, and any small production increase now tips it over.

## First 3 commands

```bash
kafka-consumer-groups --describe --group <group> --bootstrap-server <broker>   # per-partition lag and current offsets
# Check consumer-side processing rate and errors around the same window
kubectl -n <ns> logs -l app=<consumer> --since=20m | grep -iE 'error|retry|timeout'
# Compare producer rate vs consumer rate on the topic to tell "spike" from "slowdown"
<topic-metrics: messages-in-rate vs messages-out-rate over the lag window>
```

If lag is concentrated on one or two partitions rather than spread evenly, suspect a poison-pill or a hot-key skew on those specific partitions rather than a group-wide slowdown.

## Fix

- **Downstream dependency slow:** the fix is in that dependency, not Kafka — see the relevant runbook for the DB/cache/API in question. Short term, if the consumer supports it, scale out consumer instances (up to the partition count — more consumers than partitions doesn't help) to parallelize the wait.
- **Poison-pill message:** identify the stuck offset, and either fix the consumer to skip/dead-letter unparseable messages going forward, or manually skip the specific bad offset if the message is confirmed unrecoverable and safe to drop — get sign-off before skipping if the data matters (e.g. financial events).
- **Rebalancing thrash:** find what's triggering repeated rebalances (flapping liveness probe, autoscaler churn) and stabilize that first — fixing consumer code won't help if the group can't stay assigned long enough to make progress.
- **Genuine production spike:** if consumers are healthy and just outnumbered, scale consumer instances up to the partition count, or increase partition count (requires coordination, changes key-to-partition mapping) if this is a sustained volume increase rather than a one-off spike.

**Verify:** lag stops growing and trends back toward zero (or your normal steady-state), per-partition lag isn't concentrated on one stuck partition, and consumer processing rate is back to baseline.

## Escalate when

- Lag is on a consumer group backing a customer-facing SLA (e.g. order fulfillment, payment webhooks) — treat urgency based on downstream impact, not the alert's own `warning` severity.
- A poison-pill message needs to be skipped and the topic carries financial or otherwise unrecoverable data — get the data owner's explicit sign-off before dropping it.
- Lag keeps growing after scaling consumers to the partition count — the ceiling is the partition count itself, and repartitioning is a bigger, coordinated change, not a quick fix.

## Related runbooks

- **API latency SLO breach or elevated 5xx rate** / **Postgres connection pool exhausted** — if the consumer's per-message work involves a DB call or an HTTP call to another service, check whether that dependency has its own incident causing this lag.
