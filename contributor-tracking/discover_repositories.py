#!/usr/bin/env python3
"""Extract canonical source repositories that can expose contributor data."""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parent
URL_RE = re.compile(r"(?:https?|git)://[^\s<>\"']+", re.IGNORECASE)
TRIM = ".,;:)]}>'\""
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*")
METADATA_SUFFIXES = (".json", ".jsonld", ".yaml", ".yml", ".ttl")


def clean_segment(value: str) -> str:
    """Keep the leading ASCII forge slug and discard prose/punctuation contamination."""
    match = SLUG_RE.match(unquote(value).strip())
    return match.group(0).rstrip(".") if match else ""


def metadata_source(path: Path) -> str:
    name = path.name.lower()
    suffixes = (
        (".biotools.json", "bio.tools"),
        (".bioschemas.jsonld", "Bioschemas"),
        (".oeb.metrics.json", "OpenEBench"),
        (".biocontainers.yaml", "BioContainers"),
        (".biocontainers.jsonld", "BioContainers"),
        (".biocontainers.ttl", "BioContainers"),
        (".bioconductor.json", "Bioconductor"),
        (".bioconda.jsonld", "Bioconda"),
        (".bioconda.ttl", "Bioconda"),
        (".neubias.raw.json", "NEUBIAS/BIII"),
        (".neubias.bioschemas.jsonld", "NEUBIAS/BIII"),
        (".galaxy.json", "Galaxy"),
        (".galaxy.jsonld", "Galaxy"),
        (".galaxy.ttl", "Galaxy"),
        (".debian.yaml", "Debian"),
        (".debian.jsonld", "Debian"),
        (".debian.ttl", "Debian"),
    )
    for suffix, source in suffixes:
        if name.endswith(suffix):
            return source
    return "Other"


def canonicalize(raw_url: str):
    raw_url = raw_url.strip().rstrip(TRIM)
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [clean_segment(p) for p in parsed.path.split("/") if p]
    parts = [p for p in parts if p]
    lower = [p.lower() for p in parts]
    platform = key = canonical = None

    github_reserved = {
        "about", "apps", "collections", "contact", "customer-stories", "enterprise",
        "events", "explore", "features", "issues", "login", "marketplace", "new",
        "notifications", "organizations", "orgs", "pricing", "pulls", "search",
        "security", "settings", "site", "sponsors", "topics", "trending", "users",
    }
    if host == "github.com" and len(parts) >= 2 and lower[0] not in github_reserved:
        platform, key = "GitHub", "/".join(lower[:2]).removesuffix(".git")
        canonical = f"https://github.com/{key}"
    elif (host == "gitlab.com" or "gitlab" in host) and not host.endswith("gitlab.io") and "gitlab-pages" not in host and len(parts) >= 2:
        base = parts[: parts.index("-")] if "-" in parts else parts[:2]
        platform, key = ("GitLab" if host == "gitlab.com" else "GitLab (self-hosted)"), f"{host}/{'/'.join(p.lower() for p in base).removesuffix('.git')}"
        canonical = f"https://{host}/{'/'.join(base).removesuffix('.git')}"
    elif host == "bitbucket.org" and len(parts) >= 2:
        platform, key = "Bitbucket", "/".join(lower[:2]).removesuffix(".git")
        canonical = f"https://bitbucket.org/{key}"
    elif host == "codeberg.org" and len(parts) >= 2:
        platform, key = "Codeberg", "/".join(lower[:2]).removesuffix(".git")
        canonical = f"https://codeberg.org/{key}"
    elif host == "gitee.com" and len(parts) >= 2:
        platform, key = "Gitee", "/".join(lower[:2]).removesuffix(".git")
        canonical = f"https://gitee.com/{key}"
    elif host == "git.bioconductor.org" and len(parts) >= 2 and lower[0] == "packages":
        platform, key = "Bioconductor Git", lower[1].removesuffix(".git")
        canonical = f"https://git.bioconductor.org/packages/{key}"
    elif host == "sourceforge.net" and len(parts) >= 2 and lower[0] in {"projects", "p"}:
        platform, key = "SourceForge", lower[1]
        canonical = f"https://sourceforge.net/projects/{key}"
    elif host.endswith(".sourceforge.net"):
        project = host.removesuffix(".sourceforge.net").split(".")[0]
        if project not in {"downloads", "svn", "git", "hg", "cvs", "sourceforge"}:
            platform, key = "SourceForge", project
            canonical = f"https://sourceforge.net/projects/{key}"
    elif host == "code.google.com" and "p" in lower:
        index = lower.index("p")
        if len(parts) > index + 1:
            platform, key = "Google Code", lower[index + 1]
            canonical = f"https://code.google.com/archive/p/{key}"
    elif host == "launchpad.net" and parts:
        platform, key = "Launchpad", lower[0]
        canonical = f"https://launchpad.net/{key}"
    elif "savannah" in host:
        query = parse_qs(parsed.query)
        project = (query.get("group") or [parts[0] if parts else ""])[0]
        if project:
            platform, key = "GNU Savannah", project.lower().removesuffix(".git")
            canonical = f"https://savannah.gnu.org/projects/{key}"
    elif host == "r-forge.r-project.org":
        query = parse_qs(parsed.query)
        project = (query.get("group") or query.get("group_id") or [parts[0] if parts else ""])[0]
        if project:
            platform, key = "R-Forge", project.lower()
            canonical = f"https://r-forge.r-project.org/projects/{key}"

    if not platform:
        return None
    return platform, f"{platform}|{key}", canonical, host


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=REPOSITORY_ROOT / "data")
    parser.add_argument("--output", type=Path, default=HERE / "source_code_repositories.csv")
    args = parser.parse_args()

    records = {}
    for path in args.data.rglob("*"):
        if not path.is_file() or not path.name.lower().endswith(METADATA_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        source = metadata_source(path)
        for match in URL_RE.finditer(text):
            result = canonicalize(match.group(0))
            if not result:
                continue
            platform, identity, canonical, host = result
            record = records.setdefault(identity, {
                "source_code_url": canonical,
                "platform": platform,
                "host": host,
                "metadata_sources": set(),
                "metadata_files": set(),
                "observed_urls": set(),
            })
            record["metadata_sources"].add(source)
            record["metadata_files"].add(str(path))
            record["observed_urls"].add(match.group(0).rstrip(TRIM))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = ("source_code_url", "platform", "host", "metadata_sources", "metadata_file_count", "observed_url_count")
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in sorted(records.values(), key=lambda r: (r["platform"], r["source_code_url"].lower())):
            writer.writerow({
                "source_code_url": record["source_code_url"],
                "platform": record["platform"],
                "host": record["host"],
                "metadata_sources": "; ".join(sorted(record["metadata_sources"])),
                "metadata_file_count": len(record["metadata_files"]),
                "observed_url_count": len(record["observed_urls"]),
            })
    print(f"Wrote {len(records)} repositories to {args.output}")


if __name__ == "__main__":
    main()
