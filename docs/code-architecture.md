# Code Architecture

## Overview

Disk Space Manager is packaged as a Python CLI under `src/disk_space_manager`.
The repository root stays small: `main.py` is only a compatibility shim for
`uv run python main.py ...`, while the preferred interfaces are:

```bash
uv run disk-space-manager ...
uv run python -m disk_space_manager ...
```

The runtime pipeline is still intentionally direct:

```text
Click command -> workflow -> DiskScanner -> FileAnalyzer -> Rich UI
                         |          \-> DuplicateDetector -> Rich UI
                         v
                  ActionExecutor
```

The architectural goal is separation of concerns without adding heavy
frameworks. Click declarations, workflow sequencing, terminal presentation,
target resolution, scanning, analysis, and file mutation each have clear module
ownership.

## Runtime Package

### `src/disk_space_manager/cli.py`

Defines the Click command group, global `--dry-run` flag, and command options
for `analyze`, `clean`, `archive`, and `full-report`.

This module should stay thin. It parses CLI inputs and delegates to
`workflows.py`; it should not contain scanning, rendering, archive selection, or
file-mutation logic.

### `src/disk_space_manager/workflows.py`

Coordinates command behavior:

- Resolves the scan path, defaulting to `Path.home()`.
- Creates `DiskScanner`, `FileAnalyzer`, and `ActionExecutor` instances.
- Creates `DuplicateDetector` for `full-report` unless duplicate checks are
  disabled.
- Sequences scan, analysis, preview, confirmation, and execution steps.
- Keeps destructive operations behind confirmation unless dry-run mode is
  active.
- Excludes archive targets from archive scans and skips symlink archive
  candidates.

Workflow functions are command-sized orchestration units. Domain details should
stay in scanner, analyzer, executor, or archive target modules.

### `src/disk_space_manager/ui.py`

Owns terminal presentation and prompts:

- Header and dry-run banner.
- Rich tables, summaries, progress bars, and scan ETA display.
- Cache and old-file preview rendering.
- Confirmation prompts for destructive command paths.
- Final execution summaries and action-log location display.

No filesystem mutation should happen in this module.

### `src/disk_space_manager/archive_targets.py`

Resolves archive destinations and returns an `ArchiveTarget` dataclass with:

- `root`: selected target directory.
- `archive_base`: `<root>/archived_files`.
- `label`: user-facing target label.
- `source`: target source, such as `target_path`, `external_path`, or
  `auto_detected`.

Target precedence is deliberate and must remain:

1. `--target-path`
2. `--external-path`
3. Auto-detected external drive

### `src/disk_space_manager/scanner.py`

Owns filesystem traversal. `DiskScanner.scan()` returns:

- `files`: list of dictionaries with `path`, `size`, `atime`, `mtime`, and
  `ctime`.
- `directories`: mapping of directory path strings to byte totals.
- `total_scanned`: total number of scanned files.
- `errors`: non-fatal scan errors.

Important implementation details:

- Uses `os.scandir` and `DirEntry.stat()` for low per-file overhead.
- Scans top-level subdirectories in worker threads.
- Does not follow directory symlinks.
- Precomputes configured and caller-supplied exclusion prefixes once per scan.
- Reports simple file-count progress and detailed `ScanProgress` snapshots.
- Batches worker progress events to avoid excessive cross-thread chatter.

`get_largest_directories()` and `get_largest_files()` operate on the scanner's
in-memory results after a scan.

### `src/disk_space_manager/analyzer.py`

Contains `FileAnalyzer`, which operates on scanner file dictionaries.

Primary responsibilities:

- Detect cache candidates using configured directory patterns, extension
  checks, and filename markers.
- Detect old files using access time, configured age threshold, and minimum
  file size.
- Add result metadata such as cache `reason`, old-file `days_old`,
  `age_category`, and display-ready `accessed` datetime.
- Summarize total size, file count, average file size, and top extensions.
- Calculate potential space savings for cache deletion and old-file archiving.
- Include exact duplicate reclaimable bytes in potential savings when supplied.

Performance-oriented constants are prepared at import time.

### `src/disk_space_manager/duplicates.py`

Contains `DuplicateDetector`, which operates on scanner file dictionaries.

Primary responsibilities:

- Group exact duplicates by size and streamed SHA-256 digest.
- Build capped offline near-duplicate fingerprints for text, images, videos,
  and audio.
- Bucket fingerprints before pair comparisons to avoid a global all-pairs scan.
- Return report dictionaries with duplicate groups, reclaimable or reviewable
  bytes, and skip counts.

This module is read-only and best-effort. Decode failures, unsupported formats,
and over-cap files are skipped rather than failing the report.

### `src/disk_space_manager/executor.py`

Contains `ActionExecutor`, the only module that performs destructive user-file
operations.

Primary responsibilities:

- Track dry-run state.
- Log every intended or completed action in memory and in
  `~/.disk-space-manager-actions.log`.
- Delete files through `utils.safe_delete`.
- Archive files into a target base directory while preserving paths relative to
  the scan root.
- Copy file metadata with `shutil.copy2`, unlink the original file, and create
  a symlink at the original path pointing to the archived copy.

Confirmation is handled before executor methods are called. The executor keeps
the actual mutation and action-log behavior centralized.

### `src/disk_space_manager/drive_detector.py`

Detects writable external volumes:

- macOS uses `diskutil` and falls back to `/Volumes`.
- Linux parses `/proc/self/mountinfo`, skips pseudo/system filesystems, and
  considers common mount roots such as `/media`, `/mnt`, and `/run/media`.
- Manual external paths are validated for existence and writability.

### `src/disk_space_manager/progress_estimator.py`

Converts `ScanProgress` snapshots into `ScanEstimate` objects suitable for
Rich's determinate progress bars.

The estimator starts with a placeholder total, estimates total work from the
ratio of completed to discovered directories, smooths changes, keeps visible
remaining work while directories remain, and snaps to the actual total when the
scan finishes.

### `src/disk_space_manager/config.py`

Contains repository-wide constants:

- Default old-file age threshold.
- Unix-like cache directory patterns.
- Cache-like file extensions.
- System and user-home scan exclusions.
- Minimum file size for archive candidates.
- Duplicate display limits, per-format near-duplicate caps, sampling counts,
  and similarity thresholds.
- Action log path.

Changes here can materially affect safety, scan scope, and user trust, so pair
them with focused tests.

### `src/disk_space_manager/utils.py`

Contains shared helpers for size formatting, metadata reads, excluded path
checks, directory sizing, available-space checks, safe deletion, link/copy
helpers, and archive target path construction.

## Command Flows

### `analyze`

1. `cli.py` parses `--path`.
2. `workflows.run_analyze()` resolves the scan path.
3. `DiskScanner` scans the filesystem.
4. `FileAnalyzer` computes disk usage statistics.
5. `ui.py` renders summary tables, largest directories, and largest files.

This command is read-only.

### `clean`

1. Resolve scan path and age threshold.
2. Scan the filesystem.
3. Find cache candidates.
4. Render preview and savings summary.
5. Confirm deletion unless dry-run mode is active.
6. Delete candidates through `ActionExecutor.delete_files`.
7. Render execution summary and action-log path when actions were logged.

### `archive`

1. Resolve scan path and archive target.
2. Create a local `--target-path` if needed.
3. Build the archive base as `<target>/archived_files`.
4. Scan with `exclude_paths=[archive_target]`.
5. Remove symlink paths from old-file candidates.
6. Find old files above the minimum size threshold.
7. Render preview and savings summary.
8. Confirm move unless dry-run mode is active.
9. Archive candidates through `ActionExecutor.archive_files`.

### `full-report`

1. Resolve scan path and age threshold.
2. Scan with detailed progress and ETA estimation.
3. Find cache candidates with progress.
4. Find old-file candidates with progress.
5. Find exact duplicate groups unless `--no-duplicates` is set.
6. Find near-duplicate groups unless `--no-duplicates` or
   `--no-near-duplicates` is set.
7. Render usage, largest paths, cache, old-file, duplicate, and savings
   sections.

This command is read-only.

## Tests and Profiling

The test suite uses pytest and Click's `CliRunner`. It covers archive behavior,
target precedence, archive target exclusions, repeated archive runs, direct
executor behavior, Linux drive detection, scanner and analyzer progress,
duplicate detection, `full-report` smoke coverage, and profiling helper safety.

`scripts/profile_report_generation.py` is the performance harness. It owns and
recreates `downloads/benchmark`, generates deterministic sparse-file datasets,
runs `python -m disk_space_manager full-report`, and removes the benchmark tree
unless `--keep-benchmark` is specified.

## Extension Points

When adding features:

- Add Click option declarations in `cli.py`.
- Add command sequencing in `workflows.py`.
- Add terminal output and prompts in `ui.py`.
- Keep filesystem traversal concerns in `scanner.py`.
- Put classification and summary logic in `analyzer.py`.
- Put exact and near-duplicate matching logic in `duplicates.py`.
- Put file mutations only in `executor.py`.
- Put archive destination rules in `archive_targets.py`.
- Put external-drive discovery in `drive_detector.py`.
- Update `config.py` for default thresholds, patterns, and exclusions.
- Add tests using temporary paths and `CliRunner`; avoid broad real filesystem
  scans in automated validation.
