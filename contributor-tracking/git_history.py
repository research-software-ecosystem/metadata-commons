"""Blobless Git fetching and exact author/committer email extraction."""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


CONTRIBUTOR_FIELDS = [
    "repository_id", "canonical_repository_url", "platform", "provider",
    "metadata_sources", "raw_email", "email_sha256", "observed_names",
    "author_commit_count", "committer_commit_count", "first_contribution",
    "last_contribution", "default_branch", "refs_scanned", "collected_at",
]


class CollectionError(Exception):
    def __init__(self, kind: str, message: str, retryable: bool = True, accessible: str = "unknown"):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.accessible = accessible


class Paused(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_error(value: str) -> str:
    return " ".join(value.replace("\x00", " ").split())[-2000:]


def environment() -> dict[str, str]:
    result = os.environ.copy()
    result.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C.UTF-8",
    })
    return result


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM) if os.name == "posix" else process.terminate()
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
        process.wait()
    except ProcessLookupError:
        pass


def classify_error(message: str) -> CollectionError:
    lower = message.lower()
    if "429" in lower or "rate limit" in lower or "too many requests" in lower:
        return CollectionError("rate_limited", message)
    if "authentication failed" in lower or "could not read username" in lower or "access denied" in lower:
        return CollectionError("private", message, False, "false")
    if "repository not found" in lower or "not found" in lower or "does not appear to be a git repository" in lower:
        return CollectionError("not_found", message, False, "false")
    return CollectionError("git_error", message)


def run_git(command: list[str], log_path: Path, stop: threading.Event, timeout: float) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("ab") as log:
        log.write((f"\n[{now()}] {' '.join(command)}\n").encode())
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT,
            env=environment(), start_new_session=True,
        )
        while process.poll() is None:
            if stop.is_set():
                stop_process(process)
                raise Paused()
            if time.monotonic() - started > timeout:
                stop_process(process)
                raise CollectionError("timeout", f"Git command exceeded {timeout / 3600:.1f} hours")
            time.sleep(0.5)
    if process.returncode:
        tail = log_path.read_bytes()[-8000:].decode("utf-8", errors="ignore")
        raise classify_error(clean_error(tail))


def fetch(job: dict, log: Path, stop: threading.Event, timeout: float) -> Path:
    mirror = Path(job["mirror_path"])
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if mirror.exists():
        run_git([
            "git", "-C", str(mirror), "fetch", "--prune", "--force",
            "--filter=blob:none", "origin", "+refs/*:refs/*",
        ], log, stop, timeout)
        return mirror

    partial = mirror.with_suffix(".git.partial")
    if partial.exists():
        shutil.rmtree(partial)
    run_git([
        "git", "clone", "--mirror", "--filter=blob:none", job["clone_url"], str(partial)
    ], log, stop, timeout)
    os.replace(partial, mirror)
    return mirror


def git_value(mirror: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(mirror), *arguments], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
        env=environment(), check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def timestamp(value: str) -> str:
    if not value:
        return ""
    return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_result(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTRIBUTOR_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def extract(job: dict, mirror: Path, output: Path, log: Path, stop: threading.Event, timeout: float) -> int:
    contributors = defaultdict(lambda: {
        "names": set(), "author": 0, "committer": 0, "first": "", "last": "",
    })

    def observe(email: str, name: str, role: str, observed_at: str) -> None:
        if not email:
            return
        item = contributors[email]
        if name:
            item["names"].add(name)
        item[role] += 1
        observed_at = timestamp(observed_at)
        if observed_at and (not item["first"] or observed_at < item["first"]):
            item["first"] = observed_at
        if observed_at and (not item["last"] or observed_at > item["last"]):
            item["last"] = observed_at

    command = [
        "git", "-c", "i18n.logOutputEncoding=UTF-8", "-C", str(mirror), "log", "--all",
        "--no-show-signature", "--format=%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI",
    ]
    started = time.monotonic()
    with log.open("ab") as log_handle:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=log_handle,
            env=environment(), start_new_session=True,
        )
        for raw in process.stdout:
            if stop.is_set():
                stop_process(process)
                raise Paused()
            if time.monotonic() - started > timeout:
                stop_process(process)
                raise CollectionError("timeout", "History extraction exceeded the job timeout", accessible="true")
            fields = raw.rstrip(b"\r\n").split(b"\x00")
            if len(fields) != 6:
                continue
            observe(fields[1].decode(), fields[0].decode(errors="ignore"), "author", fields[2].decode("ascii"))
            observe(fields[4].decode(), fields[3].decode(errors="ignore"), "committer", fields[5].decode("ascii"))
        process.wait()
    if process.returncode:
        raise CollectionError("git_log_error", f"git log exited with status {process.returncode}", accessible="true")

    branch = git_value(mirror, "symbolic-ref", "--short", "HEAD")
    collected_at = now()
    rows = [{
        "repository_id": job["repository_id"],
        "canonical_repository_url": job["source_url"],
        "platform": job["platform"],
        "provider": job["provider"],
        "metadata_sources": job["metadata_sources"],
        "raw_email": email,
        "email_sha256": hashlib.sha256(email.encode()).hexdigest(),
        "observed_names": " | ".join(sorted(item["names"])),
        "author_commit_count": item["author"],
        "committer_commit_count": item["committer"],
        "first_contribution": item["first"],
        "last_contribution": item["last"],
        "default_branch": branch,
        "refs_scanned": "all",
        "collected_at": collected_at,
    } for email, item in sorted(contributors.items())]
    write_result(output, rows)
    return len(rows)


def collect(job: dict, work: Path, stop: threading.Event, timeout: float) -> dict:
    result_path = work / "results" / f"{job['repository_id']}.contributors.csv"
    log_path = work / "logs" / f"{job['repository_id']}.log"
    if result_path.exists():
        with result_path.open(newline="", encoding="utf-8") as handle:
            return {"outcome": "success", "count": sum(1 for _ in csv.DictReader(handle))}
    stage, fetched = "fetch", False
    try:
        mirror = fetch(job, log_path, stop, timeout)
        stage, fetched = "extract", True
        return {"outcome": "success", "count": extract(job, mirror, result_path, log_path, stop, timeout)}
    except Paused:
        return {"outcome": "paused"}
    except CollectionError as error:
        return {
            "outcome": "error", "kind": error.kind, "message": clean_error(str(error)),
            "retryable": error.retryable, "accessible": "true" if fetched else error.accessible,
            "stage": stage,
        }
    except UnicodeDecodeError as error:
        return {
            "outcome": "error", "kind": "invalid_email_encoding", "message": clean_error(str(error)),
            "retryable": False, "accessible": "true" if fetched else "unknown", "stage": stage,
        }
    except Exception as error:
        return {
            "outcome": "error", "kind": type(error).__name__, "message": clean_error(str(error)),
            "retryable": True, "accessible": "true" if fetched else "unknown", "stage": stage,
        }
