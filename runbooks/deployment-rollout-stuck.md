---
title: Deployment rollout stuck or replicas mismatched
alerts:
  - KubeDeploymentRolloutStuck
  - KubeDeploymentReplicasMismatch
severity: warning
scope: [kubernetes]
last_reviewed: 2026-09-18
---

# Deployment rollout stuck or replicas mismatched

Fires when a Deployment isn't converging on its desired state — either a rollout has been in progress too long without finishing, or the available replica count doesn't match what's requested. Both point at "the scheduler or the new pods aren't cooperating," but the fix depends on which side is broken: pods that come up but never pass readiness, versus pods that never get scheduled at all.

## Symptoms

- `KubeDeploymentRolloutStuck`: `kubectl rollout status` doesn't complete; the Deployment sits at partial progress (e.g. 2/5 updated replicas) well past the time a normal rollout takes. New pods show `Running` but the readiness gate (e.g. `GET /ready`) keeps returning non-200, so they never join the Service's endpoints.
- `KubeDeploymentReplicasMismatch`: `available` replicas stay below `desired` for an extended window. `kubectl get pods` shows some pods `Pending` rather than `Running`.
- With `maxUnavailable=0` (the common safe default), a stuck rollout is often not user-facing yet — old pods keep serving — which is exactly why it can sit unnoticed until it blocks the next deploy.
- A replica mismatch caused by scheduling pressure shows up alongside rising cluster-wide CPU/memory request utilization, not just on this one Deployment.

## Likely causes

**Rollout stuck (new pods `Running` but never `Ready`):**
1. **New code fails its readiness check** — a dependency the readiness probe checks (DB migration not yet applied, a required downstream not reachable) isn't satisfied by the new revision.
2. **Readiness probe misconfigured for the new revision** — wrong port/path after a change, or a stricter check than the app can currently pass.
3. **Resource starvation on the new pods specifically** — they're `Running` but so CPU-throttled they never finish startup work in time to answer the probe.

**Replicas mismatch (pods stuck `Pending`):**
1. **Cluster out of schedulable capacity** — `FailedScheduling: Insufficient cpu/memory` in pod events; this Deployment is just the one that happened to need a scheduling slot when capacity ran out.
2. **Taints/tolerations or node-affinity mismatch** — nodes with room don't match what this Deployment's pods tolerate or require.
3. **PodDisruptionBudget or anti-affinity rules** preventing placement that would otherwise fit.
4. **Image pull failure** (`ImagePullBackOff`) — registry auth expired, tag doesn't exist, or a private registry is unreachable from that node pool.

## First 3 commands

```bash
kubectl -n <ns> rollout status deployment/<name>          # confirm it's actually stuck, not just slow
kubectl -n <ns> describe pod <new-pod>                     # readiness probe failures OR FailedScheduling reason, right at the bottom under Events
kubectl -n <ns> get pods -o wide | grep <name>              # Pending vs Running split, which nodes
```

If scheduling is the suspect: `kubectl describe nodes | grep -A5 Allocated` across the cluster to see where capacity actually stands, not just this namespace.

## Fix

**Stuck rollout, readiness failing:**
- If it's a bad revision: `kubectl -n <ns> rollout undo deployment/<name>` — with `maxUnavailable=0` this is safe, old pods are still serving.
- If the readiness check itself is wrong for a legitimate new revision (e.g. new dependency not yet provisioned): fix the dependency or the probe config, then let the rollout continue — it resumes on its own once new pods pass.
- If it's resource starvation: bump CPU `requests` so the scheduler gives the pod enough share to start, not just enough to eventually run.

**Replicas mismatch, pods `Pending`:**
- Capacity shortage: free room by scaling the node group, or triage — pause less-critical rollouts/scale-downs elsewhere in the cluster to unblock this one first.
- Taint/affinity mismatch: fix the pod spec's tolerations/`nodeSelector`, or the node pool's taints, whichever is actually wrong for this workload's intent.
- Image pull failure: check registry credentials/`imagePullSecrets` and that the tag exists; a broken CI push is a common cause right after a release.

**Verify:** `kubectl rollout status` reports success, `available` replicas equal `desired`, and no pods remain `Pending`/`CrashLoopBackOff` from this Deployment.

## Escalate when

- Rollback doesn't clear a stuck rollout — the readiness dependency (DB, downstream service) is broken independent of this Deployment's code.
- Cluster-wide capacity is genuinely exhausted (multiple Deployments `Pending` at once) — this needs a capacity/autoscaling decision, not a per-service fix.
- A `PodDisruptionBudget` is blocking a rollout you need to force through during an active incident — get sign-off before overriding it, it exists to prevent an outage.

## Related runbooks

- **Pod crash looping or OOMKilled** — a Deployment that rolls out pods which then crash-loop will also show as replicas mismatch; check that runbook if the new pods aren't just failing readiness but restarting outright.
- **Node not ready** — if `FailedScheduling` events reference nodes that are actually `NotReady`, the real incident is there, not in this Deployment.
