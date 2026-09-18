---
title: Service unreachable — scrape failing, DNS failing, or upstream refused
alerts:
  - TargetDown
  - CoreDNSResolutionFailures
  - NginxUpstreamConnectionRefused
severity: critical
scope: [kubernetes, networking]
last_reviewed: 2026-09-18
---

# Service unreachable — scrape failing, DNS failing, or upstream refused

Three different alerts that all mean "something couldn't reach something else on the network," at three different layers: Prometheus can't reach a target's metrics endpoint, CoreDNS can't resolve names, or the ingress can't connect to an upstream pod. They're grouped because the triage instinct is the same — isolate which hop is actually broken — even though the fixes diverge completely.

## Symptoms

- **`TargetDown`**: Prometheus scrape fails (`context deadline exceeded`, connection refused/timeout) for a target's metrics port specifically. Critically, the service's *own* `/health` or user traffic may be completely fine — this can be a metrics-path-only problem, not a service outage.
- **`CoreDNSResolutionFailures`**: elevated `SERVFAIL` rate from CoreDNS. Often splits cleanly: in-cluster service names resolve fine, but *external* names (a payment gateway, cloud storage, any name that needs to leave the cluster) intermittently fail — pointing at an upstream resolver, not CoreDNS itself.
- **`NginxUpstreamConnectionRefused`**: ingress logs `connect() failed (111: Connection refused)` for a named upstream. The Service backing that upstream has zero healthy endpoints — the pods exist but aren't in the Service's endpoint list.
- Downstream, any of these can masquerade as an unrelated app-level symptom: a service that depends on the failing DNS lookup or the refused upstream will itself start erroring or timing out, one hop removed from the real cause.

## Likely causes

**`TargetDown` (metrics scrape only):**
1. The metrics endpoint handler is blocking on a slow dependency (e.g. it computes stats from a database call) while the app's actual `/health` stays fast and independent.
2. A `NetworkPolicy` change blocked the Prometheus scraper's source without affecting normal traffic ports/paths.
3. The pod is genuinely overloaded and the metrics port — usually lowest priority — is the first to stop responding under load.

**`CoreDNSResolutionFailures`:**
1. One of the upstream resolvers configured in CoreDNS's forward config (`/etc/resolv.conf` equivalent) is down or unreachable — CoreDNS still answers in-cluster names from its own zone, but forwards external names upstream and that hop is broken.
2. CoreDNS pods themselves under resource pressure (CPU-throttled, OOM-adjacent) and dropping queries under load.
3. A recent change to CoreDNS's `Corefile` (forward targets, cache settings) introduced a bad configuration.

**`NginxUpstreamConnectionRefused`:**
1. **All pods behind the Service are failing their startup/readiness probe** — they exist, they're just never added to the Service's endpoints, so anything routed there gets refused rather than served.
2. The Service selector doesn't match any running pod's labels (a mislabeled rollout).
3. A `NetworkPolicy` blocking ingress-to-pod traffic specifically, while the pods themselves are healthy.

## First 3 commands

```bash
kubectl -n <ns> get endpoints <service>              # zero addresses = confirms "nothing is actually listening", regardless of alert
kubectl -n <ns> get pods -l <service-selector>       # are the backing pods Running/Ready at all?
kubectl -n kube-system logs -l k8s-app=kube-dns --tail=100   # only for CoreDNS alerts — look for forward/timeout errors
```

For `TargetDown` specifically, also curl the metrics port directly from inside the cluster (`kubectl exec` into a debug pod) to see if it's actually hanging versus just refused.

## Fix

**`TargetDown`:** if the app's real traffic is fine, this is lower urgency — fix the metrics handler's blocking call (make it non-blocking or cache the underlying query), or adjust the `NetworkPolicy` if that's what changed. Don't page as a full outage if `/health` and user traffic are unaffected.

**`CoreDNSResolutionFailures`:** if one upstream resolver is bad, remove or deprioritize it in the Corefile `forward` block so CoreDNS stops routing to it; this is usually the fastest mitigation. If CoreDNS pods are resource-starved, scale the CoreDNS Deployment or raise its limits. Revert any recent Corefile change first if the timing lines up.

**`NginxUpstreamConnectionRefused`:** the fix lives with whatever is keeping the backing pods out of the endpoint list — this is really "go fix why the pods aren't Ready" (see the crash-loop or rollout-stuck runbooks for the underlying pod issue), not something to solve at the ingress. If it's a `NetworkPolicy`, correct the policy to allow the ingress controller's traffic.

**Verify:** for `TargetDown`, the scrape succeeds again in Prometheus's target list. For DNS, `SERVFAIL` rate returns to baseline and external-name lookups succeed from a test pod. For upstream-refused, `kubectl get endpoints` shows healthy addresses and the ingress stops logging connection-refused for that upstream.

## Escalate when

- `CoreDNSResolutionFailures` affects in-cluster names too, not just external — that's a more severe CoreDNS-itself failure, not an upstream resolver issue, and risks cascading to everything in the cluster.
- The same upstream keeps refusing connections immediately after pods report `Ready` — suggests a race or a health-check that lies, worth a code-level look rather than repeated restarts.
- A `NetworkPolicy` change is suspected but you don't own it — get the owning team before editing network policy during an active incident; a wrong edit can open more than it fixes.

## Related runbooks

- **Pod crash looping or OOMKilled** / **Deployment rollout stuck or replicas mismatched** — the actual root cause behind `NginxUpstreamConnectionRefused` is almost always documented in one of these two.
- **API latency and elevated 5xx rate** — connectivity failures at the DNS or upstream layer are a common trigger for a 5xx spike measured at the ingress.
