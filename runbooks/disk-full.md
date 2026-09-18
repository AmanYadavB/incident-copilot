---
title: Disk or volume filling up
alerts:
  - NodeFilesystemAlmostOutOfSpace
  - NodeFilesystemSpaceFillingUp
  - NodeFilesystemFilesFillingUp
  - PersistentVolumeFillingUp
severity: critical
scope: [host, kubernetes-pvc]
last_reviewed: 2026-09-06
---

# Disk or volume filling up

Fires when a filesystem is projected to run out of space — a host mount (`/`, `/var`, a data disk) or a Kubernetes PVC. The alert names the mountpoint or claim and usually an estimated time-to-full. The diagnosis (what is eating the space) is the same everywhere; the fix splits into a **host** path and a **Kubernetes PVC** path, both below.

## Symptoms

- node-exporter or kubelet alert: a mountpoint or PVC above ~85–90% used, often with a "runs out in ~Nh at the current write rate" projection.
- Writes start failing with `No space left on device` — in application logs, or as 5xx on any request that writes a file or a log line.
- Effects that often page first, before disk is suspected:
  - the service's own logs stop — nothing after a certain timestamp
  - the database refuses writes: Postgres `could not extend file`, SQLite `database or disk is full`
  - the container runtime can't start pods: `write /var/lib/docker/...: no space left on device`
  - `journald` goes volatile; `apt` / `yum` operations fail
- `df -h` shows the mount at or near 100%. If bytes look fine, check `df -i` — inode exhaustion (millions of tiny files) looks identical from the app side.

## Likely causes

Ranked by how often it is the real one:

1. **Logs that never rotated** — an app or nginx writing to a path `logrotate` does not cover, or `logrotate` failing silently. Also `journald` with no `SystemMaxUse` cap on a chatty host.
2. **Container image / layer buildup** under `/var/lib/docker` or `/var/lib/containerd` — dangling images, stopped containers, build cache, unused volumes. Common wherever each deploy pulls a fresh image.
3. **A runaway or stuck file** — a batch job, export, or debug dump that keeps appending; a core dump or JVM heap dump; a `tcpdump` or trace left running.
4. **Database growth** — Postgres WAL piling up behind an inactive replication slot or a failing `archive_command`; old MySQL binlogs; a large table never vacuumed. This is the usual cause on a database PVC.
5. **Old releases / build artifacts** — previous deploy directories, `.tar.gz` bundles, `pip` / `npm` caches, `/tmp` never cleared.
6. **Deleted-but-open files** — a log deleted while a process still holds the handle, so the space is never freed until that process restarts. The tell: `df` and `du` disagree.

## First 3 commands

**Host mount** — `NodeFilesystemAlmostOutOfSpace`, `NodeFilesystemSpaceFillingUp`, `NodeFilesystemFilesFillingUp`:

```bash
df -h && df -i                                            # which mount is full — bytes AND inodes
du -xh --max-depth=1 /var 2>/dev/null | sort -rh | head   # biggest dirs on the full mount (descend into the largest)
sudo lsof -nP +L1 | sort -k7 -rn | head                   # space held by deleted-but-open files
```

Narrow further with `journalctl --disk-usage`, `docker system df -v`, or `sudo find /var/log -type f -size +100M`.

**Kubernetes PVC** — `PersistentVolumeFillingUp`:

```bash
kubectl -n <ns> exec <pod> -- df -h <mountpath>           # confirm which PVC and how full
kubectl -n <ns> exec <pod> -- du -xh --max-depth=1 <mountpath> | sort -rh | head
kubectl -n <ns> describe pvc <claim>                      # StorageClass and allowVolumeExpansion?
```

If it is a Postgres PVC, also check the WAL / slot state:
`SELECT slot_name, active, wal_status FROM pg_replication_slots;`

## Fix

**Buy time first (safe, low-risk):**

- Truncate — do not delete — an active log so the writing process keeps its handle:
  `sudo truncate -s 0 /var/log/<service>/app.log`
- Cap journald immediately: `sudo journalctl --vacuum-size=200M`
- Compress instead of removing if you might still need it: `gzip /var/log/<old-file>`

**Reclaim by cause — host:**

- **Containers:** `docker system prune -f` clears images, stopped containers, and build cache. Add `--volumes` only after confirming no stopped container holds data you need. `docker image prune -a -f` for all unused images.
- **Deleted-but-open file:** restart the process holding the handle (from the `lsof +L1` output) — a rolling restart for the app, `systemctl restart` for rsyslog / journald.
- **Logs:** fix or add the `logrotate` rule, then force a run: `logrotate -f /etc/logrotate.d/<file>`. Set `SystemMaxUse=` in `/etc/systemd/journald.conf`.
- **Artifacts:** delete old deploy directories, clear `~/.cache/pip`, run `npm cache clean --force`, prune `/tmp` of anything older than a few days.
- **Inodes exhausted:** find the directory with the file explosion — `for d in /var/*; do echo "$(sudo find "$d" | wc -l) $d"; done | sort -rn` — and clear it at the source.

**Reclaim — Kubernetes PVC:**

- If the StorageClass has `allowVolumeExpansion: true`, raise the PVC request and let the volume grow — the fastest real fix:
  `kubectl -n <ns> patch pvc <claim> -p '{"spec":{"resources":{"requests":{"storage":"<bigger>"}}}}'`
- **Postgres WAL:** drop a dead replication slot with the DB owner's sign-off — `SELECT pg_drop_replication_slot('<name>');`. Fix or disable a broken `archive_command`. Never delete files in `pg_wal/` by hand.
- Delete in-container junk (old exports, temp files) via `kubectl exec`.

**Verify:** `df -h` shows real headroom, the alert clears, application writes succeed, and the fill *rate* has stopped — a mount back at 70% but still climbing is not fixed.

## Escalate when

- Reclaim did not hold and the volume refills within an hour — the cause is still active and unidentified.
- The full mount is root (`/`) on a host you cannot safely reboot, or the only path to headroom is deleting data you cannot confirm is safe.
- It is a database volume and the growth is WAL / replication / binlog — loop in the DB owner before dropping slots or changing archive config.
- It is shared or platform-owned storage — a node's `/var/lib/docker`, an NFS mount, an EBS volume that needs resizing. Growing the volume and running `resize2fs` / `xfs_growfs` is the platform team's call.
- The StorageClass has `allowVolumeExpansion: false` — the PVC cannot be grown in place and needs a migration.
- Data loss has already happened (DB corruption, truncated files another team relies on) — declare an incident, stop operating solo.

## Related runbooks

- **Postgres connection / backup failures** (`PostgresConnectionPoolExhausted`, `CronJobFailed` on `db-backup`) — a full database volume causes these downstream.
- **Node not ready** (`KubeNodeNotReady`) — a full `/var` on a node can take the kubelet down.
