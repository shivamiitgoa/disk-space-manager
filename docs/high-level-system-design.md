# High-Level System Design

## Purpose

Disk Space Manager is a local Unix-like command-line tool for understanding and
reducing disk usage on macOS and Linux. It scans a selected filesystem tree,
reports disk usage, identifies removable cache-like files, and archives old
large files to an external drive or local folder while preserving the original
directory structure.

The system is intentionally conservative. Destructive operations are separated
from analysis, support dry-run previews, require explicit confirmation during
normal runs, and record action logs for review.

## Users and Workflows

The primary user is a macOS or Linux user running the CLI directly from a
terminal. The default scan target is the user's home directory, but all
commands accept a narrower `--path` for safer inspection and validation.

The main workflows are:

- Analyze disk usage with `analyze` to understand file counts, total size,
  large files, large directories, and top file extensions.
- Preview and remove cache-like files with `clean`, using dry-run mode when the
  user wants a non-mutating check.
- Archive old large files with `archive`, using either a local archive folder,
  a specified external drive path, or auto-detected external storage.
- Generate a comprehensive read-only report with `full-report`, including scan
  progress, cache candidates, old-file candidates, duplicate candidates, and
  potential savings.

## System Context

The application runs as a Python process on the user's machine. It does not use
a background service, database, remote API, or privileged helper. It interacts
with:

- The local filesystem through `os.scandir`, stat calls, copy, delete, and
  symlink operations.
- Unix-like mount information through macOS `diskutil`, `/Volumes`, or Linux
  `/proc/self/mountinfo`.
- The terminal through Click command parsing and Rich tables, panels, and
  progress indicators.
- Offline media libraries for duplicate analysis; no external services or APIs
  are used.
- A local action log at `~/.disk-space-manager-actions.log`.

## Major Subsystems

### CLI and Presentation

`src/disk_space_manager/cli.py` owns the Click command group and command option
declarations. It delegates work to `workflows.py`, keeping the command layer
thin. `ui.py` owns Rich output, progress displays, summaries, and confirmation
prompts. The root `main.py` is only a checkout compatibility shim.

### Workflow Orchestration

`workflows.py` creates scanner, analyzer, and executor objects, sequences the
read-only and mutating command flows, and gates destructive operations behind
confirmation prompts. `archive_targets.py` resolves archive destinations using
the precedence `--target-path`, then `--external-path`, then auto-detected
external drive.

### Filesystem Scanner

`src/disk_space_manager/scanner.py` traverses a root path and returns file
metadata plus directory size totals. It is optimized for large trees by using
`os.scandir`, scanning top-level subdirectories in parallel, batching progress
events, and storing raw epoch timestamps instead of converting every timestamp
into `datetime` objects.

### Analyzer

`src/disk_space_manager/analyzer.py` classifies scanned file dictionaries. It
detects cache candidates using configured Unix-like directory patterns,
extensions, and filename markers. It detects old files using access time and a
minimum size threshold. It also calculates summary statistics and potential
space savings.

### Duplicate Detector

`src/disk_space_manager/duplicates.py` finds read-only exact duplicates and
advisory near duplicates for `full-report`. Exact matches use streamed SHA-256
hashes for same-size candidates. Near matches use capped offline fingerprints
for text, images, videos, and audio, and skip unsupported or decode-failing
files without aborting the report.

### Action Executor

`src/disk_space_manager/executor.py` performs deletion and archive operations.
It handles dry-run accounting, writes action log entries, deletes cache files
through shared safe delete helpers, and archives old files by copying them to
the target, deleting the original file, and creating a symlink at the original
path.

### External Drive Detection

`src/disk_space_manager/drive_detector.py` discovers writable mounted external
drives. On macOS it uses `diskutil` with a `/Volumes` fallback. On Linux it
parses `/proc/self/mountinfo`, ignores pseudo/system filesystems, and considers
writable non-root mounts under common external-drive locations such as
`/media`, `/mnt`, and `/run/media`. The archive command uses this subsystem only
when the user has not supplied `--target-path` or `--external-path`.

### Progress Estimation

`src/disk_space_manager/progress_estimator.py` turns scanner progress snapshots
into determinate Rich progress values and heuristic ETA text for `full-report`,
where the final number of files is unknown until traversal finishes.

## Data Flow

The core data flow is:

1. The CLI parses options such as scan path, age threshold, and dry-run state.
2. `DiskScanner.scan()` returns a dictionary containing `files`, `directories`,
   `total_scanned`, and `errors`.
3. `FileAnalyzer` consumes scanned file dictionaries to produce usage
   summaries, cache candidates, old-file candidates, and savings estimates.
4. `DuplicateDetector` consumes the same scanned file dictionaries during
   `full-report` to produce exact and near-duplicate groups.
5. `ui.py` renders read-only command results directly.
6. Mutating workflows show summaries, request confirmation unless in dry-run
   mode, then call `ActionExecutor`.
7. `archive_targets.py` resolves archive destinations before archive scans.
8. `ActionExecutor` records each intended or completed action in memory and in
   the action log.

The scanner's file dictionaries are intentionally lightweight. During scanning
they contain path strings, byte size, and raw `atime`, `mtime`, and `ctime`
float timestamps. Analyzer methods add fields such as `reason`, `days_old`,
`age_category`, and `accessed` only for derived result sets.

## Safety Model

Safety is a top-level system requirement because the tool can delete or move
user files.

- `analyze` and `full-report` are read-only.
- Duplicate and near-duplicate sections are advisory report output only.
- `clean` and `archive` require explicit confirmation before mutating files
  unless the global `--dry-run` flag is used.
- Dry-run mode does not delete, move, create archive output, or create symlinks,
  but it does calculate result counts and log intended actions as dry-run
  entries.
- Archive operations preserve directory structure under `archived_files`.
- Archive operations leave symlinks at original file locations so applications
  can continue resolving the old paths.
- If an archive target is inside the scanned tree, the target is excluded from
  scanning to avoid re-archiving previous output.
- The archive command skips symlinks from candidate old files.
- Permission and filesystem errors are handled without aborting the whole scan
  where possible.

Automated validation must use temporary directories or intentionally small test
paths. It must not run destructive `clean` or `archive` operations against a
real home directory, system path, or external drive.

## Performance Design

The scanner is designed for large filesystem trees. Key choices are:

- Use `os.scandir` and `DirEntry.stat()` to avoid unnecessary `Path` object
  churn and repeated stat calls.
- Scan top-level directories concurrently with `ThreadPoolExecutor`, relying on
  filesystem I/O and stat calls to benefit from parallelism.
- Precompute exclusion prefixes once per scan.
- Store raw timestamps and defer `datetime` conversion to report-size result
  subsets.
- Batch progress events from worker threads to avoid excessive cross-thread
  communication.
- Duplicate detection hashes only same-size exact candidates and caps
  near-duplicate fingerprinting by media type to protect report runtime.

The profiling workflow in `scripts/profile_report_generation.py` owns
`downloads/benchmark`, can generate large sparse benchmark datasets, runs
`full-report`, and cleans up by default.

## Operating Boundaries

The system targets Unix-like filesystems on macOS and Linux and assumes Python
3.9 or newer. It depends on Click and Rich for CLI behavior, offline media
libraries for duplicate analysis, pytest for tests, and `uv` for the documented
development workflow. External-drive auto-detection is best-effort and can
always be bypassed with `--target-path` or `--external-path`.

The tool does not guarantee it can inspect every file. Filesystem permissions,
system protections, broken symlinks, unmounted drives, and concurrent file
changes can all affect scan and execution results.
