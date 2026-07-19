# Contributor tracking

This folder contains the complete contributor-tracking pipeline: repository discovery, its current source-URL inventory, the resumable collector, documentation, and the ignored runtime work area.

The code is split by responsibility: `collect.py` owns CSV state, scheduling, retries, and progress; `git_history.py` owns Git fetching and exact email extraction.

## Refresh the repository inventory

The checked-in `source_code_repositories.csv` contains the current canonical inventory. Regenerate it from the repository's `data/` metadata with:

```bash
python3 contributor-tracking/discover_repositories.py
```

## Run

Python 3 and Git are the only requirements. The collector uses `contributor-tracking/source_code_repositories.csv` by default.

```bash
python3 contributor-tracking/collect.py run
```

Use a larger data volume by moving the work directory outside the repository:

```bash
python3 contributor-tracking/collect.py run --work-dir /large-volume/rsec-contributors
```

Press `Ctrl-C` to pause. Run the same command to resume. `SIGTERM` also requests a safe pause.

The terminal shows overall progress, completed outcomes, occupied lanes, waiting jobs, current throughput, and estimated remaining time.

## Scheduling and downloads

- At most ten repository jobs run concurrently.
- Each active lane uses a different provider hostname. There is never more than one active clone or fetch against the same provider.
- Providers are selected fairly using the time at which each provider last received work.
- Git repositories are stored as mirrors using `--filter=blob:none`, avoiding working trees and normally avoiding file blobs while retaining the complete commit graph and all refs.
- Existing mirrors are updated incrementally instead of being downloaded again.
- Retries release both the lane and provider. Backoff starts at five minutes and is capped at one day. A rate-limit response places every waiting job for that provider into the same transparent cooldown window.
- Google Code and R-Forge entries are recorded as unsupported because this collector cannot obtain exact email identities from their archived or non-Git interfaces without guessing.

Some servers do not support partial Git clones and may ignore the blob filter. Their behavior is recorded in the per-repository log.

## Exact identities

The exact raw email emitted by Git (`%ae` and `%ce`, without `.mailmap`) is the identity. Different email strings are never merged. Names are retained only as observations associated with that exact email. Commit-message bodies are not read.

`email_sha256` is calculated from the lowercased raw email so hashes match across case-only variants; `raw_email` remains unchanged.

No profile, name, affiliation, ORCID, or similarity-based resolution is performed.

## Transparent state and outputs

The work directory contains:

| Path | Purpose |
|---|---|
| `repository_collection_status.csv` | Authoritative pause/resume state for every repository |
| `repositories/` | Cached blobless Git mirrors |
| `results/*.contributors.csv` | Atomic, resumable per-repository results |
| `logs/*.log` | Git command output and per-repository error detail |
| `contributors_by_repository_and_exact_email.csv` | Consolidated result generated at completion or on request |

Only the central scheduler writes the status CSV. Status changes are written to a temporary file, flushed, and atomically renamed, so an interruption leaves a complete readable snapshot.

In `repository_collection_status.csv`, `state` is one of `pending`, `running`, `retry_wait`, or `complete`. Every finished job has `state=complete`. Its outcome is recorded separately in `result` as `contributors_collected`, `not_found`, `authentication_required`, `unsupported`, or `failed`; `accessible` is `true`, `false`, or `unknown`. The latest error type and message are stored directly in `error_type` and `error`.

To inspect progress without starting workers:

```bash
python3 contributor-tracking/collect.py status
```

To rebuild the consolidated contributor CSV from completed per-repository files:

```bash
python3 contributor-tracking/collect.py export
```

On startup, jobs left in `running` state are returned to `pending`. Existing mirrors are fetched incrementally, and any atomic per-repository result already present is marked complete. These filesystem artifacts are the only checkpoints needed for safe resume.
