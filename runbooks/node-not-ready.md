---
title: Kubernetes node NotReady
alerts:
  - KubeNodeNotReady
severity: critical
scope: [kubernetes, host]
last_reviewed: 2026-09-18
---

# Kubernetes node NotReady

Fires when a node's kubelet stops posting status to the API server (`Ready` condition becomes `Unknown` or `False`). This is a host-level failure, not an application one — every pod on the node is at risk, and single-replica workloads scheduled there go down immediately. Treat this as an infra incident first; don't chase application symptoms on that node until the node itself is explained.

## Symptoms

- Node's `Ready` condition flips to `Unknown` (kubelet stopped reporting) or `False` (kubelet reporting but unhealthy).
- Abrupt, simultaneous drop in *all* metrics from that node (CPU, memory, network) — a real hang or network partition looks like this, versus a gradual resource-exhaustion climb beforehand.
- Pods on the node show `Terminating` stuck, or after the pod-eviction timeout, get rescheduled elsewhere (visible as new pods starting up on other nodes).
- Any single-replica workload that was on this node goes fully down until rescheduled — this is usually what actually pages, before anyone looks at node health directly.
- `kubectl get nodes` shows the node `NotReady`; `kubectl describe node` events show `NodeStatusUnknown`, `Kubelet stopped posting node status`.

## Likely causes

Ranked by frequency:

1. **Kubelet process hung or crashed** — OOM on the node itself (not a pod's container limit, the *node's* memory), a kernel issue, or a runaway process starving the kubelet of CPU/scheduling.
2. **Network partition** — the node can't reach the API server (security group / NACL change, a switch/routing issue, a CNI plugin failure) even though the node itself is otherwise fine.
3. **Disk pressure on the node** — kubelet reports `NotReady` under severe disk pressure before anything else notices; check this before assuming it's compute.
4. **Underlying host/instance failure** — cloud provider hardware issue, an instance that's been terminated or is being reclaimed (spot/preemptible).
5. **containerd/Docker runtime hang** — the container runtime itself wedges, kubelet can't manage pods even though the OS is up.

## First 3 commands

```bash
kubectl describe node <node>                                   # condition history, last heartbeat, recent events
kubectl get pods --all-namespaces --field-selector spec.nodeName=<node>   # what's at risk on this node right now
ssh <node> 'systemctl status kubelet; journalctl -u kubelet -n 100 --no-pager'   # if reachable at all — this alone tells you hung vs. unreachable
```

If SSH itself fails or hangs, that's your answer for network partition / host-down — stop trying to diagnose over a connection that isn't there and move to the cloud provider's instance status instead.

## Fix

- **Kubelet hung, node otherwise reachable:** `systemctl restart kubelet` on the node. If it immediately hangs again, the underlying resource pressure (memory/disk/CPU) needs fixing first, not just a kubelet bounce.
- **Disk pressure:** clear space per the disk-full runbook's node-level steps, then kubelet typically self-recovers once pressure clears.
- **Network partition:** this is an infra-networking fix (security group, routing table, CNI daemonset health) — not something to solve from the node itself if it can't be reached.
- **Node genuinely dead / cloud instance failure:** don't wait for it — cordon and drain if the control plane still lets you (`kubectl cordon <node>`, `kubectl drain <node> --ignore-daemonsets --force`), so the scheduler moves workloads off immediately rather than waiting out the default 5-minute pod-eviction timeout. If the node can't be drained because the API can't reach it either, delete the node object once you've confirmed it's actually gone (not just slow) so the scheduler stops counting it as capacity: `kubectl delete node <node>`.
- **containerd/Docker hang:** `systemctl restart containerd` (or the relevant runtime) — often needed alongside a kubelet restart, not instead of it.

**Verify:** `kubectl get nodes` shows `Ready`, previously-affected pods are back to `Running` (either rescheduled elsewhere or restarted in place), and no single-replica workload is still down because it was pinned to this node.

## Escalate when

- Multiple nodes go `NotReady` at the same time — this is very unlikely to be per-node hardware; suspect the control plane, a shared network path, or a cluster-wide change (CNI upgrade, security group edit) and stop treating it as isolated.
- The node can't be cordoned/drained because the API server itself can't reach it — needs cloud-provider-level intervention (force-stop/terminate the instance) to actually free the workloads.
- A single-replica stateful workload was on the affected node — don't let it silently reschedule without confirming its data volume reattaches cleanly; loop in that service's owner before assuming automatic recovery is enough.

## Related runbooks

- **Disk or volume filling up** — disk pressure on a node is one of the more common root causes of `NotReady` and is easy to rule in/out fast.
- **Pod crash looping or OOMKilled** — pods rescheduled off a recovered node can briefly look crash-looped while they cold-start elsewhere; don't confuse that with a real app-level regression.
