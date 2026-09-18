---
title: Pod crash looping or OOMKilled
alerts:
  - KubePodCrashLooping
  - ContainerOOMKilled
severity: critical
scope: [kubernetes]
last_reviewed: 2026-09-18
---

# Pod crash looping or OOMKilled

Fires when a pod's container keeps dying and restarting (`CrashLoopBackOff`) or is being killed by the kernel OOM killer for exceeding its memory limit. Both alerts point at the same object — a pod that cannot stay up — but the diagnosis forks hard depending on whether the exit reason is an application error/`Error` or `OOMKilled`, so check that first.

## Symptoms

- `KubePodCrashLooping`: kube-state-metrics reports a pod's restart count climbing over a short window (e.g. 9 restarts in 12 minutes), status `CrashLoopBackOff`, last termination reason `Error` (or `Completed` if the process exits 0 but isn't supposed to).
- `ContainerOOMKilled`: same restart pattern, but termination reason is `OOMKilled` — the container's working set hit its memory limit and the kernel killed it.
- `kubectl get pods -n <ns>` shows `RESTARTS` climbing and `STATUS` cycling `CrashLoopBackOff` / `Running` / `Error`.
- Dependent effects: requests to the service 5xx or time out while the pod is down; if enough replicas are affected, `KubeDeploymentReplicasMismatch` fires alongside this one.

## Likely causes

Split by termination reason — check `kubectl describe pod` first, it's in the output.

**Reason `Error` / non-zero exit (crash loop):**
1. **Bad deploy** — new image tag has a startup bug, missing env var, broken migration, or a config/secret that didn't roll out with it. Most common when `startsAt` lines up right after a rollout.
2. **Missing or wrong dependency at boot** — can't reach its database/cache/broker on startup and the app doesn't retry, it just exits.
3. **Failing readiness/liveness probe** with a low `failureThreshold` — the app is slow to start (cold cache, JIT warmup) and gets killed before it's ready.
4. **Config or secret drift** — a `ConfigMap`/`Secret` referenced by the pod was edited or deleted without a corresponding rollout.

**Reason `OOMKilled`:**
1. **Traffic or load increase** pushing normal working-set above the configured limit — no code or deploy change, just more volume (check for a recent traffic spike before suspecting a leak).
2. **Memory leak** — working set climbs steadily over the pod's lifetime rather than jumping with load; time-to-OOM shortens release over release.
3. **Limit set too low** for a legitimate spike (batch job, cache warm, large request payload) — the app is healthy, the ceiling is wrong.
4. **No limit/request sizing done at all** — inherited a default that was never tuned for this service's actual footprint.

## First 3 commands

```bash
kubectl -n <ns> describe pod <pod>            # last state, exit code/reason, recent events
kubectl -n <ns> logs <pod> --previous         # logs from the crashed instance, not the new one
kubectl -n <ns> top pod <pod> --containers    # current memory vs. limit, if still up long enough to sample
```

If it's OOM and you need the trend rather than a snapshot, check your metrics backend for `container_memory_working_set_bytes{pod="<pod>"}` over the last few hours instead of relying on one `top` sample.

## Fix

**Crash loop (`Error`):**
- Bad deploy: `kubectl -n <ns> rollout undo deployment/<name>` to roll back, confirm restarts stop, then fix forward in the image/config.
- Missing dependency at boot: fix the startup dependency check (add retry/backoff) as a code fix; short term, confirm the dependency (DB/cache/broker) is actually reachable from that namespace.
- Probe killing a slow-starting app: raise `initialDelaySeconds`/`failureThreshold` on the readiness/liveness probe, or add a `startupProbe` so slow boots aren't punished by the liveness probe.
- Config/secret drift: diff the live `ConfigMap`/`Secret` against what's in source control, restore it, then `kubectl rollout restart deployment/<name>` to pick it up.

**OOMKilled:**
- Load-driven, no leak: raise the memory `limit` (and `request` if it was under-provisioned) — `kubectl -n <ns> set resources deployment/<name> --limits=memory=<new>`. Consider whether horizontal scaling (more replicas) is the better fix if this is sustained traffic growth, not a spike.
- Leak (working set trends up over pod lifetime, not load-correlated): needs a code-level fix; buy time with a scheduled rollout restart while the leak is tracked down, don't just keep raising the limit.
- Legitimate spike, limit too tight: raise the limit to cover the real peak plus headroom, not just enough to survive the last incident.

**Verify:** restart count stays flat for at least one full deploy/traffic cycle, `kubectl get pods` shows steady `Running`, and (for OOM) `container_memory_working_set_bytes` sits with headroom under the new limit rather than sawtoothing against it.

## Escalate when

- Rollback doesn't stop the crash loop — the cause isn't the last deploy, or something else changed at the same time (dependency, config, infra).
- OOMKilled recurs immediately after raising the limit — likely a real leak, not a sizing problem; needs the owning team to profile memory, not another limit bump.
- It's a stateful workload (database, queue broker) — crash-restarting it risks data loss or corruption; don't cycle it repeatedly without the data owner.
- The same rollout is crash-looping across multiple unrelated services at once — suspect a shared dependency (DNS, secret store, node-level issue) rather than a per-service bug.

## Related runbooks

- **Deployment rollout stuck / replica mismatch** (`KubeDeploymentRolloutStuck`, `KubeDeploymentReplicasMismatch`) — a crash-looping pod is a common cause of a rollout never completing.
- **Disk or volume filling up** — a full node disk can also manifest as pods crash-looping if they can't write scratch space; rule it out with `df -h` on the node if the cause isn't obvious in logs.
