#!/usr/bin/env python3
"""Collect exact contributor emails with ten provider-balanced, resumable lanes."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import signal
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from git_history import CONTRIBUTOR_FIELDS, collect


HERE = Path(__file__).resolve().parent
DEFAULT_INVENTORY = HERE / "source_code_repositories.csv"
DEFAULT_WORK = HERE / "work"
MAX_LANES = 10

STATUS_FIELDS = [
    "repository_id", "source_url", "clone_url", "platform", "provider",
    "metadata_sources", "vcs_type", "state", "result", "accessible",
    "attempt_count", "next_attempt_at", "mirror_path", "contributor_count",
    "error_type", "error", "updated_at", "completed_at",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.min.replace(tzinfo=timezone.utc)


def atomic_csv(path: Path, fields: list[str], rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def repository_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def clone_details(url: str, platform: str) -> tuple[str, str, str]:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    platform = platform.lower()
    if platform == "sourceforge" and parts:
        return "git", f"https://git.code.sf.net/p/{parts[-1]}/code", "git.code.sf.net"
    if platform == "gnu savannah" and parts:
        return "git", f"https://git.savannah.gnu.org/git/{parts[-1]}.git", "git.savannah.gnu.org"
    if platform == "launchpad" and parts:
        return "git", f"https://git.launchpad.net/{parts[-1]}", "git.launchpad.net"
    if platform in {"google code", "r-forge"}:
        return "unsupported", "", urlsplit(url).hostname or platform
    if platform == "bioconductor git":
        return "git", url, urlsplit(url).hostname or "git.bioconductor.org"
    clone_url = url if url.endswith(".git") else url + ".git"
    return "git", clone_url, urlsplit(clone_url).hostname or "unknown"


def new_job(row: dict, work: Path) -> dict:
    url = row["source_code_url"]
    repo_id = repository_id(url)
    vcs, clone_url, provider = clone_details(url, row["platform"])
    unsupported = vcs == "unsupported"
    timestamp = now()
    return {
        "repository_id": repo_id,
        "source_url": url,
        "clone_url": clone_url,
        "platform": row["platform"],
        "provider": provider.lower(),
        "metadata_sources": row.get("metadata_sources", ""),
        "vcs_type": vcs,
        "state": "complete" if unsupported else "pending",
        "result": "unsupported" if unsupported else "",
        "accessible": "unknown",
        "attempt_count": "0",
        "next_attempt_at": "",
        "mirror_path": str(work / "repositories" / f"{repo_id}.git"),
        "contributor_count": "0",
        "error_type": "unsupported_vcs" if unsupported else "",
        "error": "Exact email identities are unavailable through this non-Git source." if unsupported else "",
        "updated_at": timestamp,
        "completed_at": timestamp if unsupported else "",
    }


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_jobs(inventory: Path, status: Path, work: Path) -> dict[str, dict]:
    previous = {row["repository_id"]: row for row in read_csv(status)}
    jobs = {}
    for inventory_row in read_csv(inventory):
        job = new_job(inventory_row, work)
        old = previous.get(job["repository_id"])
        if old:
            current = {field: job[field] for field in (
                "source_url", "clone_url", "platform", "provider", "metadata_sources", "vcs_type"
            )}
            job.update({field: old.get(field, job[field]) for field in STATUS_FIELDS})
            job.update(current)
            if job["state"] == "running":
                job["state"] = "pending"

        result_path = work / "results" / f"{job['repository_id']}.contributors.csv"
        if result_path.exists():
            job.update({
                "state": "complete",
                "result": "contributors_collected",
                "accessible": "true",
                "contributor_count": str(len(read_csv(result_path))),
                "next_attempt_at": "",
                "error_type": "",
                "error": "",
                "completed_at": job["completed_at"] or now(),
            })
        jobs[job["repository_id"]] = job
    return jobs


def save_status(path: Path, jobs: dict[str, dict]) -> None:
    atomic_csv(path, STATUS_FIELDS, sorted(jobs.values(), key=lambda row: row["source_url"].lower()))


def export_results(work: Path) -> tuple[Path, int]:
    output = work / "contributors_by_repository_and_exact_email.csv"
    temporary = output.with_suffix(".csv.tmp")
    count = 0
    work.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=CONTRIBUTOR_FIELDS, lineterminator="\n")
        writer.writeheader()
        for result in sorted((work / "results").glob("*.contributors.csv")):
            for row in read_csv(result):
                writer.writerow({field: row.get(field, "") for field in CONTRIBUTOR_FIELDS})
                count += 1
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, output)
    return output, count


def progress(jobs: dict[str, dict], active: int, started: float, initial_finished: int) -> str:
    total = len(jobs)
    finished = sum(job["state"] == "complete" for job in jobs.values())
    collected = sum(job["result"] == "contributors_collected" for job in jobs.values())
    elapsed = max(time.monotonic() - started, 0.001)
    session_finished = max(finished - initial_finished, 0)
    rate = session_finished / elapsed * 3600
    eta = "--"
    if session_finished:
        eta = str(timedelta(seconds=int((total - finished) / (session_finished / elapsed))))
    percent = finished * 100 / total if total else 100
    width = 24
    filled = int(width * percent / 100)
    bar = "#" * filled + "-" * (width - filled)
    waiting = sum(job["state"] in {"pending", "retry_wait"} for job in jobs.values())
    return (
        f"[{bar}] {percent:6.2f}%  finished {finished:,}  collected {collected:,}  "
        f"non-success {finished - collected:,}  running {active}/{MAX_LANES}  "
        f"waiting {waiting:,}  rate {rate:.1f}/h  ETA {eta}"
    )


def show_status(work: Path) -> None:
    rows = read_csv(work / "repository_collection_status.csv")
    if not rows:
        print("No collection status exists yet.")
        return
    print(f"Repositories: {len(rows):,}")
    for state, count in sorted(Counter(row["state"] for row in rows).items()):
        print(f"  {state:20} {count:8,}")
    print("\nCompleted results:")
    for result, count in sorted(Counter(row["result"] for row in rows if row["state"] == "complete").items()):
        print(f"  {result:25} {count:8,}")
    print("\nProviders with unfinished work:")
    providers = Counter(row["provider"] for row in rows if row["state"] != "complete")
    for provider, count in providers.most_common():
        print(f"  {provider:35} {count:8,}")


def run_pipeline(args) -> None:
    if not 1 <= args.lanes <= MAX_LANES:
        raise SystemExit(f"--lanes must be between 1 and {MAX_LANES}")

    work = args.work_dir.resolve()
    status_path = work / "repository_collection_status.csv"
    work.mkdir(parents=True, exist_ok=True)
    jobs = load_jobs(args.inventory.resolve(), status_path, work)
    save_status(status_path, jobs)

    print(f"Loaded {len(jobs):,} repositories")
    print(f"State:   {status_path}")
    print(f"Results: {work / 'results'}")
    print(f"Mirrors: {work / 'repositories'}")
    print("Press Ctrl-C to pause safely; run the same command to resume.\n")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    executor = ThreadPoolExecutor(max_workers=args.lanes, thread_name_prefix="collector")
    active = {}
    active_providers = set()
    provider_last_started = defaultdict(float)
    started = time.monotonic()
    initial_finished = sum(job["state"] == "complete" for job in jobs.values())
    last_save = last_display = 0.0
    dirty = False

    def finish(future) -> None:
        nonlocal dirty
        repo_id, provider = active.pop(future)
        job = jobs[repo_id]
        result = future.result()
        active_providers.remove(provider)
        job["updated_at"] = now()
        if result["outcome"] == "success":
            job.update({
                "state": "complete", "result": "contributors_collected",
                "accessible": "true", "contributor_count": str(result["count"]),
                "next_attempt_at": "", "error_type": "", "error": "",
                "completed_at": now(),
            })
        elif result["outcome"] == "paused":
            job["state"] = "pending"
        else:
            job.update({
                "accessible": result["accessible"], "error_type": result["kind"],
                "error": f"{result['stage']}: {result['message']}",
            })
            attempts = int(job["attempt_count"])
            if result["retryable"] and attempts < args.max_attempts:
                delay = min(args.retry_base_seconds * 2 ** (attempts - 1), args.retry_max_seconds)
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds").replace("+00:00", "Z")
                job.update({"state": "retry_wait", "next_attempt_at": retry_at})
                if result["kind"] == "rate_limited":
                    for other in jobs.values():
                        if other["provider"] == provider and other["state"] in {"pending", "retry_wait"}:
                            other.update({
                                "state": "retry_wait", "next_attempt_at": retry_at,
                                "error_type": "provider_rate_limited",
                                "error": f"Provider cooldown triggered by {repo_id}", "updated_at": now(),
                            })
            else:
                outcome = {
                    "not_found": "not_found", "private": "authentication_required"
                }.get(result["kind"], "failed")
                job.update({
                    "state": "complete", "result": outcome,
                    "next_attempt_at": "", "completed_at": now(),
                })
        dirty = True

    try:
        while True:
            for future in list(active):
                if future.done():
                    finish(future)

            if not stop.is_set() and len(active) < args.lanes:
                current_time = datetime.now(timezone.utc)
                candidates = {}
                for job in jobs.values():
                    eligible = job["state"] == "pending" or (
                        job["state"] == "retry_wait" and parse_time(job["next_attempt_at"]) <= current_time
                    )
                    if eligible and job["provider"] not in active_providers:
                        existing = candidates.get(job["provider"])
                        if not existing or job["source_url"].lower() < existing["source_url"].lower():
                            candidates[job["provider"]] = job
                providers = sorted(candidates, key=lambda provider: (provider_last_started[provider], provider))
                for provider in providers[: args.lanes - len(active)]:
                    job = candidates[provider]
                    job.update({
                        "state": "running", "result": "", "next_attempt_at": "",
                        "error_type": "", "error": "",
                        "attempt_count": str(int(job["attempt_count"]) + 1),
                        "updated_at": now(),
                    })
                    active_providers.add(provider)
                    provider_last_started[provider] = time.monotonic()
                    future = executor.submit(collect, dict(job), work, stop, args.timeout_hours * 3600)
                    active[future] = (job["repository_id"], provider)
                    dirty = True

            if all(job["state"] == "complete" for job in jobs.values()) and not active:
                break
            if stop.is_set() and not active:
                break

            if dirty and time.monotonic() - last_save >= args.status_interval:
                save_status(status_path, jobs)
                dirty = False
                last_save = time.monotonic()
            if time.monotonic() - last_display >= args.progress_interval:
                line = progress(jobs, len(active), started, initial_finished)
                print(("\r\x1b[2K" if sys.stdout.isatty() else "") + line,
                      end="" if sys.stdout.isatty() else "\n", flush=True)
                last_display = time.monotonic()
            time.sleep(0.2)
    except KeyboardInterrupt:
        stop.set()
        print("\nPause requested; stopping active jobs...", flush=True)
        while active:
            for future in list(active):
                if future.done():
                    finish(future)
            time.sleep(0.2)
    finally:
        stop.set()
        executor.shutdown(wait=True)
        for future in list(active):
            if future.done():
                finish(future)
        for job in jobs.values():
            if job["state"] == "running":
                job["state"] = "pending"
        save_status(status_path, jobs)
        final = progress(jobs, 0, started, initial_finished)
        print(("\r\x1b[2K" if sys.stdout.isatty() else "") + final)

    if all(job["state"] == "complete" for job in jobs.values()):
        output, count = export_results(work)
        print(f"Collection finished. Exported {count:,} exact email identities to {output}")
    else:
        print(f"Collection paused safely. Resume with the same command. State saved to {status_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start or resume collection")
    run.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    run.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    run.add_argument("--lanes", type=int, default=MAX_LANES)
    run.add_argument("--max-attempts", type=int, default=6)
    run.add_argument("--retry-base-seconds", type=int, default=300)
    run.add_argument("--retry-max-seconds", type=int, default=86400)
    run.add_argument("--timeout-hours", type=float, default=24)
    run.add_argument("--status-interval", type=float, default=30)
    run.add_argument("--progress-interval", type=float, default=1)
    status = commands.add_parser("status", help="show collection status")
    status.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    export = commands.add_parser("export", help="rebuild consolidated results")
    export.add_argument("--work-dir", type=Path, default=DEFAULT_WORK)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        run_pipeline(args)
    elif args.command == "status":
        show_status(args.work_dir.resolve())
    else:
        output, count = export_results(args.work_dir.resolve())
        print(f"Exported {count:,} rows to {output}")


if __name__ == "__main__":
    main()
