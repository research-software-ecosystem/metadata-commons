# Research Software Ecosystem (RSEc) metadata commons

The RSEc metadata commons is a public, version-controlled collection of metadata about research software. It aggregates and cross-links records from registries, package repositories, container infrastructures, workflow platforms, and benchmarking services.

The commons is schema-agnostic: source metadata are retained in their native or source-specific representations, while selected transformations and cross-links support discovery, interoperability, and reuse.

Browse and search the aggregated records in the [RSEc Atlas](https://research-software-ecosystem.github.io/RSEc-Atlas/).

## Repository structure

- `data/` — tool-centred metadata bundles; each software entry has its own directory.
- `imports/` — source-specific imported records.
- `schemas/` — schemas used to validate selected metadata.
- `report/` — generated coverage and ecosystem reports.
- `doc/` — technical notes and documentation.

Files in `data/` are named by software entry and metadata source or representation, for example `*.biotools.json`, `*.biocontainers.yaml`, `*.oeb.metrics.json`, and `*.bioschemas.jsonld`.

## Sources and maintenance

The repository brings together metadata from resources including [bio.tools](https://bio.tools), [Bioconda](https://bioconda.github.io/), [BioContainers](https://biocontainers.pro/), [Debian Med](https://wiki.debian.org/DebianMed), [Galaxy](https://galaxyproject.org/), [WorkflowHub](https://workflowhub.eu/), [OpenEBench](https://openebench.bsc.es/), and [BIII.eu](https://biii.eu/).

GitHub Actions automate selected imports, validation, transformations, and reporting. The repository history provides provenance for changes; metadata remain subject to the terms and provenance of their respective sources.

## Built dataset artifacts

Weekly aggregate metadata dumps are published as an OCI artifact in the [GitHub Container Registry](https://github.com/orgs/research-software-ecosystem/packages/container/package/metadata-commons-datasets):

```text
ghcr.io/research-software-ecosystem/metadata-commons-datasets:latest
```

The artifact contains `metadata-commons-datasets.tar.gz` and its SHA-256 checksum. It is not a runnable container image; use an OCI registry client such as [ORAS](https://oras.land/) to download it:

```sh
oras pull ghcr.io/research-software-ecosystem/metadata-commons-datasets:latest
```

In addition to `latest`, each weekly build is tagged using the ISO year and week in `YYYY-WW` format.

## Related repositories

- [RSEc utilities](https://github.com/research-software-ecosystem/utils) — importers, converters, validators, and synchronization workflows.
- [Previous RSEc `content` repository](https://github.com/bio-tools/content) — historical predecessor of this metadata commons.

## Contributing

Please open an issue or pull request, and consult [GOVERNANCE.md](GOVERNANCE.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before contributing.

Many importers, converters, validators, and dataset generators live in the separate [RSEc utilities](https://github.com/research-software-ecosystem/utils) repository. For work involving those tools, it is useful to clone both repositories into one working directory, using the directory names expected by the existing scripts and GitHub Actions:

```sh
mkdir rsec-workspace
cd rsec-workspace
git clone https://github.com/research-software-ecosystem/metadata-commons.git content
git clone https://github.com/research-software-ecosystem/utils.git utils
```

This arrangement behaves somewhat like a submodule checkout, but `utils` is an independent repository and is not registered as a Git submodule. Commit and update changes in each repository separately.
