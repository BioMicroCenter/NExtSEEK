# chat_nextseek/luria/

Launches nf-core runs on MIT Luria over SSH, and keeps the downloads and Nextflow work folders
those runs leave on Luria's shared storage from piling up.

## Modules

| Module | What it does |
|---|---|
| `submitter.py` | `submit_luria`: renders `run.sh`, stages it with `luria.config`, `params.yml`, the samplesheet (and `fetchngs_helpers.py` when the run downloads), and `sbatch`es it. Afterwards, best-effort, it writes the run's `nessie_status/` labels (`launched.txt` first, then `README.txt`) and, independently of those writes, copies `cleanup.py` to `<working path>/bin/nessie_cleanup.py` |
| `run_script.py` | renders `run.sh` from `templates/run.sh.tmpl`; `render_status_block` is the part that writes `finished.txt` on every exit |
| `fetchngs_helpers.py` | staged beside `run.sh`: `ids`, `fill` and `cached` for the SRA download, `claim` and `release` for the cleanup |
| `status_labels.py` | the label text, and `write_saved_label`, which upload-reingest calls |
| `cleanup.py` | the weekly cleanup script, run by an admin on Luria |
| `ssh.py` | the `ssh`/`scp` wrappers |
| `pipelines/` | vendored, patched pipeline clones and how to provision them |

## On Luria (under `LURIA_WORKING_PATH`)

| Path | Holds | Cleanup |
|---|---|---|
| `runs/<name>_<yymmdd>_<hhmmss>_<n>/` | a run's outputs, which reingest reads | never deleted |
| `runs/.../nessie_status/` | that run's labels | read |
| `work/<name>/` | the run's Nextflow work folder, shared by runs of the same name for `-resume` | deleted by the rules below |
| `fastq_cache/fastq/` | FASTQs fetchngs downloaded, and their `md5/` files | deleted by the rules below |
| `fastq_cache/work/` | fetchngs' own scratch | removed as a folder (fetchngs recreates it) when no download is in progress |
| `fastq_cache/nessie_status/` | download claims, last-used dates, `cleanup.lock` | read |
| `fastq_cache/metadata/` and the rest of the cache | fetchngs' small metadata | not managed |
| `bin/nessie_cleanup.py` | the cleanup script, refreshed on every submit | never deleted |
| `cleanup_logs/` | one log per `--delete` | never deleted |
| `singularity_cache/`, `refs/`, `pipelines/`, `certs/` | infrastructure | never touched |

## Labels

Plain `key: value` text, times in UTC as `2026-10-21T14:00:00Z`.

| File | Written by | Says |
|---|---|---|
| `runs/.../nessie_status/launched.txt` | the submitter, right after `sbatch` | `run_dir`, `work_dir`, `pipeline`, `revision`, `slurm_job_id`, `accessions`, `launched_at`, `launched_from` |
| `runs/.../nessie_status/finished.txt` | `run.sh`'s exit trap | `result` (`succeeded` or `failed`), `exit_code`, `finished_at`. The result is the job script's own exit status: nextflow's, or 1 if the download or fill step failed, or 143 on TERM |
| `runs/.../nessie_status/saved.txt` | upload-reingest, over SSH | `saved_at`, `pipeline_run_id` |
| `runs/.../nessie_status/README.txt` | the submitter | a short note for anyone who finds the folder |
| `fastq_cache/nessie_status/in_use/<accession>/<job id>` | `fetchngs_helpers.py claim`, removed by `release` | the job is using this accession |
| `fastq_cache/nessie_status/last_used/<accession>.txt` | `claim` and `release` | `last_used_at` |

`finished.txt` is written whenever the job script exits, including when SLURM's time limit
ends it (the TERM becomes exit 143, so the run is labelled `failed`). The trap ignores further
TERMs while it labels. It is not written if the job is killed outright (SIGKILL, out of
memory, a node failure, or SLURM's KillWait expiring before the trap finishes): that run has
no `finished.txt`, which is the "ended without `finished.txt`" case below.

A claim that cannot be made (for example the lock stays busy for 10 minutes) prints a warning
to the job's `.err` file and the run carries on, with its downloads unprotected.

A FASTQ belongs to every accession that is a whole token of its name
(`SRX1_SRR1_1.fastq.gz` belongs to `SRX1` and `SRR1`), the same rule `fill` uses.

## What the cleanup deletes, and when

| Item | Deleted when |
|---|---|
| Work folder, run results saved | 7 days after `saved_at` |
| Work folder, run finished, results not saved | 30 days after `finished_at` |
| Work folder, run failed | 30 days after `finished_at` |
| Work folder, run ended without `finished.txt` and its job is no longer queued | 30 days after its newest `.out` log (else `launched_at`) |
| Work folder left as `work/.nessie-deleting-<name>-<time>` by an interrupted cleanup | the next `--delete` |
| Folder left as `fastq_cache/.nessie-deleting-<name>-<time>` by an interrupted cleanup | the next `--delete` |
| Downloaded FASTQ | no queued job claims any of its accessions, and 30 days after the newest `last_used_at` among them. An unreadable `last_used` label keeps the FASTQ and is listed under PROBLEMS |
| `fastq_cache/work/` | no queued job holds any claim; the whole folder is removed |
| Anything unlabelled | never; listed for the admin |

A work folder is deleted only when every run that used it qualifies; the latest date wins. A
run still in progress, a run whose labels cannot be read, or a same-name run folder with no
labels yet that changed in the last 2 days (being launched) keeps it. For a run folder with
no `launched.txt`, "changed" is the newest time of the folder itself, the `.out` and `.err`
logs in it and its `.nextflow.log`, so a run that is still writing is not mistaken for an old
one. A `launched.txt` that names no `work_dir` or no `slurm_job_id` makes its run unreadable:
the work folder is kept and the problem is listed under PROBLEMS.

A run folder that has `nessie_status/` but no `launched.txt` is a run whose label write failed
or has not happened yet (the submitter makes `nessie_status/` before `sbatch`). Its same-name
work folder is always kept: "being launched" for 2 days, then "has no launched.txt; check it
by hand" with a PROBLEMS line. It blocks `--delete` only during those 2 days, so a failed
label write cannot block every pass forever. A job that SLURM has requeued counts as in
progress while its job id is queued, even if it left a `finished.txt`.

Work folders (and `fastq_cache/work/`) are removed by renaming them to
`.nessie-deleting-<name>-<time>` in the same parent and then deleting that folder, so a run of
the same name that starts mid-delete gets a fresh folder. A leftover one (the pass was
interrupted, or the removal failed) is recognised only by its full
`.nessie-deleting-<name>-<14-digit stamp>` name, in `work/` or directly in `fastq_cache/`, and
is offered as its own WILL DELETE item.

A claim whose job is no longer queued is ignored and listed under PROBLEMS as a stale entry.
When `squeue` cannot be read, every claim counts and every run without `finished.txt` counts
as in progress.

`--delete` deletes nothing at all while any Nessie run is in progress, being launched, or
holding a live download claim (`runs_in_progress` in `cleanup.py`). The report lists those
runs under "Nessie runs in progress".

## The weekly cleanup (admin)

1. Log in to Luria as the cluster account Nessie submits with. No other account can delete
   those files.
2. Run the report. It changes nothing (apart from creating `fastq_cache/nessie_status/cleanup.lock`
   to check that locking works):

   ```bash
   python3 <working path>/bin/nessie_cleanup.py
   ```

   If `python3` is older than 3.6, run `module add miniconda3/v4` first.

   First use only: check once that the download lock works across nodes. The report's
   "Download lock" line only says the lock was taken and released on the node you ran it on.
   On the login node hold the lock (this sleeps 120 seconds):

   ```bash
   python3 -c "import fcntl,time; fh=open('<working path>/fastq_cache/nessie_status/cleanup.lock','a'); fcntl.flock(fh, fcntl.LOCK_EX); time.sleep(120)"
   ```

   and meanwhile, from a second login session, try the same file from a compute node:

   ```bash
   srun python3 -c "import fcntl,sys; fh=open('<working path>/fastq_cache/nessie_status/cleanup.lock','a'); fcntl.flock(fh, fcntl.LOCK_EX|fcntl.LOCK_NB)"
   ```

   It must fail with "Resource temporarily unavailable". If it succeeds, the lock does not
   work across nodes: do not run `--delete`.

   Before anyone runs `--delete`, every NExtSEEK instance that shares this `LURIA_WORKING_PATH`
   must be running a version that writes these labels and makes download claims. An instance
   still on older code writes no labels and makes no claims, so its runs and downloads would
   look unlabelled or unused.
3. Read the four sections: WILL DELETE, KEEPING FOR NOW (with days left), UNLABELLED, and
   PROBLEMS. The header says whether the job queue was read, whether the download lock
   works, and which Nessie runs are in progress. If any are, `--delete` will not delete
   anything: come back once they have finished.
4. If the WILL DELETE list is right:

   ```bash
   python3 <working path>/bin/nessie_cleanup.py --delete
   ```

   Just before it deletes each item, the pass checks the item again against fresh labels and
   a fresh job queue (re-read at most every 30 seconds). It skips an item that no longer
   qualifies, and stops the whole pass if a Nessie run has started. It writes its log line by
   line to `<working path>/cleanup_logs/` as it goes, and deletes nothing if it cannot create
   that log.
5. `--delete` exits 1 when it refused to start (the job queue could not be read, a Nessie run
   is in progress, or the log in `cleanup_logs/` could not be created), stopped early, skipped
   downloads because the download lock was busy for 60 seconds, or failed to delete
   something. Read the output and the log, then run it again later.
6. UNLABELLED items (everything from before the labels existed) are never deleted by the
   script. Delete them by hand only when you are sure nobody needs them.

`--delete` deletes nothing if `squeue` cannot be read, and skips downloads if the lock cannot
be taken.

## Invariants

- `cleanup.py` is standard library only, Python 3.6 compatible, and staged verbatim.
  `test_luria_cleanup.py` checks the syntax and the imports.
- The cleanup deletes only directly inside `work/`, `fastq_cache/fastq/` (and its `md5/`),
  `fastq_cache/work/` and leftover `fastq_cache/.nessie-deleting-*` folders, never follows a
  symlink, and never deletes `runs/` or anything unlabelled.
- The label formats are shared by `fetchngs_helpers.py`, `run_script.py`, `status_labels.py`
  and `cleanup.py`. A change to one is a change to all four; the tests parse each writer's
  output with the cleanup's reader.
- Every NExtSEEK instance that shares a `LURIA_WORKING_PATH` must run this version before
  anyone runs `--delete`: an instance on older code writes no labels and makes no claims.
- Every label write and download claim is best-effort: a failed claim prints a warning in the
  job log and the run carries on. A missing or unreadable label must make the cleanup keep,
  never delete, and `--delete` waits while any Nessie run is in progress.

## Testing

In `NessieAI/tests/chat_nextseek/`: `test_luria_cleanup.py` (decisions, the delete pass, and
the real `run.sh` status block and `fetchngs_helpers` claims read back by the cleanup),
`test_luria_run_script.py`, `test_fetchngs_helpers.py`, `test_luria_submitter.py` and
`test_luria_status_labels.py`. In `NessieAI/tests/ns/reingest/`:
`test_saved_label_on_upload.py` and `test_harvest_ignores_status_labels.py`. Commands are in
`NessieAI/tests/README.md` ("Django lane").
