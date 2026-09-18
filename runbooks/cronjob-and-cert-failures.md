---
title: CronJob failing or TLS certificate renewal stuck
alerts:
  - CronJobFailed
  - CertManagerCertExpiringSoon
severity: warning
scope: [kubernetes, automation]
last_reviewed: 2026-09-18
---

# CronJob failing or TLS certificate renewal stuck

Two "automation quietly stopped working" alerts, grouped because they share a failure shape: nothing user-facing breaks *yet*, there's a countdown before it does, and the actual cause is very often something else's incident borrowing this one's alert (a CronJob failing because a database is out of connections; a cert renewal stuck because a shared ingress solver is misconfigured). Don't diagnose either in isolation — check what else might be failing for the same underlying reason first.

## Symptoms

- **`CronJobFailed`**: the job's recent runs exit non-zero; `kubectl get jobs` / job history shows consecutive failures. Whatever the job produces (a backup, a report, a cleanup) has not succeeded since its last good run — the alert fires on failure count, but the real risk is measured in "time since last success."
- **`CertManagerCertExpiringSoon`**: a certificate is within its expiry warning window (commonly a few days out) and cert-manager's automatic ACME renewal has already tried and failed at least once — check the `Certificate`/`CertificateRequest`/`Order` status, don't assume it'll just renew on the next attempt.
- Both are silent to end users until the deadline hits — no backup for N days is invisible until you need to restore; a cert renewal stuck in `pending` is invisible until the old cert actually expires and every client gets a TLS error at once.

## Likely causes

**`CronJobFailed`:**
1. **The job depends on something else that's currently broken** — most commonly a database it can't connect to (matching a connection-pool-exhaustion or DB-outage incident elsewhere) — check for a correlated alert on that dependency before assuming the job itself is at fault.
2. **The job's own logic/data changed** — a schema change, a new data shape it doesn't handle, a script bug shipped in the last change to the job.
3. **Resource limits too tight for the job's actual workload** (OOM or timeout mid-run) — especially for jobs whose data volume grows over time (backups, exports) without the job's limits being revisited.
4. **Credentials or a mounted secret expired/rotated** without the CronJob's reference being updated.

**`CertManagerCertExpiringSoon`:**
1. **ACME challenge (HTTP-01/DNS-01) failing** — the solver ingress isn't routing the challenge path correctly, or (for DNS-01) the DNS provider API credentials/permissions are stale.
2. **Rate limiting from the ACME provider** (e.g. too many failed attempts recently) blocking further renewal tries until the window resets.
3. **A shared component the solver depends on is down** — the same ingress controller or DNS zone used by other, currently-working certs might itself be degraded intermittently.
4. **The `Certificate`/`Issuer` resource itself misconfigured** after an edit — wrong secret name, wrong issuer reference.

## First 3 commands

```bash
kubectl -n <ns> logs job/<job-name>-<latest>          # CronJobFailed: the actual error from the last failed run
kubectl -n <ns> describe certificate <cert-name>       # CertManagerCertExpiringSoon: current state and last condition message
kubectl -n <ns> describe order <order-name>            # CertManagerCertExpiringSoon: exact reason the ACME order is stuck (e.g. "pending" + why)
```

## Fix

**`CronJobFailed`:**
- If it's failing due to a dependency (DB, external API), fix that dependency first — the job will succeed on its own next scheduled run once the dependency is healthy; don't spend time on the job's code if the root cause is elsewhere.
- If it's a genuine code/schema issue, fix and redeploy the job image, then trigger a manual run to confirm before waiting for the next schedule: `kubectl create job --from=cronjob/<name> <name>-manual-<date>`.
- If it's resource limits, raise them to match current data volume, especially for jobs whose workload has grown since the limits were last set.
- **If the job is time-sensitive (backups especially):** run it manually now after fixing the cause — don't wait for the next scheduled slot while backup coverage has a gap.

**`CertManagerCertExpiringSoon`:**
- Fix the actual blocked step first (per the `Order`/`Challenge` status) — a broken solver ingress route, stale DNS credentials, or a misconfigured `Issuer` reference.
- Once fixed, you can force a retry rather than waiting for cert-manager's backoff: delete the stuck `CertificateRequest` (cert-manager will recreate it) or annotate the `Certificate` to trigger reissuance.
- If blocked by ACME rate limiting, there's no shortcut — fix the underlying cause so the *next* attempt succeeds, since retrying sooner just consumes more of the rate limit.

**Verify:** for CronJobs, the next run (manual or scheduled) exits 0 and produces its expected output (check the backup/report actually landed, not just the exit code). For certs, `kubectl describe certificate` shows `Ready: True` with a renewed `notAfter` date comfortably in the future.

## Escalate when

- A backup CronJob has had zero successful runs for longer than your recovery-point objective — this is a data-risk incident, not routine automation noise, regardless of the alert's `warning` severity.
- A cert renewal is still stuck with less than a day of validity left — treat as imminent-outage severity; an expired cert takes down every client at once with no gradual warning.
- The blocking cause is shared infrastructure (DNS, a shared solver ingress, ACME rate limits) affecting other certs or jobs too — fix once at the shared layer rather than patching each affected resource individually.

## Related runbooks

- **Postgres connection pool exhausted** — a very common concrete cause of a backup CronJob failing; check whether this alert and a pool-exhaustion alert share a timestamp before treating the job as broken on its own.
- **Service unreachable — scrape failing, DNS failing, or upstream refused** — DNS-01 cert renewal failures can trace back to the same DNS resolution problems covered there.
