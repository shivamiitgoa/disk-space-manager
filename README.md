# Disk Space Manager

A Python CLI tool for Unix-like systems that helps manage disk space by
analyzing usage, identifying removable cache and temporary files, and archiving
old files to an external drive or local folder.

The tool is designed for macOS and Linux. It keeps destructive actions
explicit: clean and archive workflows support dry-run previews, require
confirmation before changing files, and record action logs.

## Features

- **Disk Usage Analysis**: Scan and visualize disk usage with detailed
  breakdowns.
- **Cache File Detection**: Identify common cache and temporary files that can
  be reviewed and removed.
- **Old File Archiving**: Find files not accessed in 6+ months and archive them
  to an external drive or local folder.
- **Duplicate Detection**: Report exact duplicate files and advisory
  near-duplicates for text, images, videos, and audio during full reports.
- **High Performance**: Handles large trees efficiently using `os.scandir`,
  parallel scanning, and optimized analysis.
- **Safety First**: Requires explicit confirmation before destructive actions.
- **Rich CLI**: Terminal UI with progress bars, tables, and color-coded output.
- **External Drive Detection**: Auto-detects writable external drives on macOS
  and common Linux mount locations.
- **Action Logging**: Logs delete and archive actions for review and audit.

## Installation

1. Clone this repository:

```bash
git clone <repository-url>
cd disk-space-manager
```

2. Sync dependencies:

```bash
uv sync
```

If you do not have `uv` yet, install it with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Usage

The preferred command is the installed package script:

```bash
uv run disk-space-manager --help
```

`uv run python main.py ...` is still available as a compatibility shim when
running from a checkout.

### Analyze Disk Usage

Scan and analyze disk usage without making changes:

```bash
uv run disk-space-manager analyze
```

Scan a specific directory:

```bash
uv run disk-space-manager analyze --path /path/to/directory
```

### Clean Cache Files

Identify and remove cache files after confirmation:

```bash
uv run disk-space-manager clean
```

With a custom age threshold:

```bash
uv run disk-space-manager clean --age-months 3
```

### Archive Old Files

Move old files to an auto-detected external drive:

```bash
uv run disk-space-manager archive
```

Archive to a local folder:

```bash
uv run disk-space-manager archive --target-path /path/to/archive/folder
```

Archive to a specific mounted external drive:

```bash
uv run disk-space-manager archive --external-path /Volumes/MyDrive
uv run disk-space-manager archive --external-path /media/$USER/MyDrive
```

With a custom age threshold:

```bash
uv run disk-space-manager archive --age-months 12 --target-path ./my_archive
```

### Full Report

Generate a comprehensive read-only report:

```bash
uv run disk-space-manager full-report
```

Duplicate and near-duplicate checks run by default. Skip both duplicate phases,
or keep exact duplicates while skipping slower near-duplicate checks:

```bash
uv run disk-space-manager full-report --no-duplicates
uv run disk-space-manager full-report --no-near-duplicates
```

### Dry Run Mode

Preview mutating operations without changing files:

```bash
uv run disk-space-manager --dry-run clean
uv run disk-space-manager --dry-run archive
```

## Commands

- `analyze` - Analyze disk usage and show insights.
- `clean` - Identify and remove cache files after confirmation.
- `archive` - Move old files to an external drive or local folder.
- `full-report` - Generate a comprehensive analysis report.

## Options

- `--path PATH` - Directory to scan (default: home directory).
- `--target-path PATH` - Local folder to use as archive destination.
- `--external-path PATH` - Mounted external drive path (default:
  auto-detect).
- `--age-months N` - Age threshold in months (default: 6).
- `--no-duplicates` - Skip exact and near-duplicate checks for `full-report`.
- `--no-near-duplicates` - Skip near-duplicate checks but keep exact duplicate
  checks for `full-report`.
- `--dry-run` - Show what would be done without making changes.

## Safety Features

1. **Confirmation Prompts**: Mutating actions require explicit confirmation.
2. **Preview Mode**: Summaries show what will be affected before confirmation.
3. **Action Logging**: Actions are logged to
   `~/.disk-space-manager-actions.log`.
4. **Dry Run**: Mutating workflows can be tested without file changes.
5. **Error Handling**: Permission errors and inaccessible files are handled
   gracefully where possible.

## How It Works

### Scanning and Performance

The scanner is optimized for large filesystems:

- `os.scandir()` is used instead of `Path.iterdir()` to avoid creating Python
  `Path` objects for every file and to leverage cached directory-entry data.
- Top-level subdirectories are scanned concurrently with `ThreadPoolExecutor`.
- Excluded prefixes are precomputed once per scan.
- Raw float timestamps are stored during scanning; conversion to `datetime`
  happens only for displayed result subsets.
- Each file is statted once through `DirEntry.stat()`.

### Cache File Detection

Cache candidates are detected from:

- Common Unix-like cache directory patterns, including macOS
  `Library/Caches`, Linux `.cache`, temp directories, and trash folders.
- Cache-like file extensions such as `.cache`, `.tmp`, `.temp`, `.log`, `.old`,
  and `.bak`.
- Filenames containing cache-related markers.

### Old File Detection

Files are considered old if they:

- Have not been accessed in the specified time period (default: 6 months).
- Are larger than 1 MB, to avoid moving many small files.

### Duplicate Detection

Full reports include read-only duplicate analysis:

- Exact duplicates are found by grouping same-size files and streaming SHA-256
  hashes only for candidate groups.
- Near-duplicate text files use normalized token shingles and SimHash.
- Near-duplicate images use perceptual hashes through Pillow and ImageHash.
- Near-duplicate videos use sampled frame perceptual hashes through OpenCV.
- Near-duplicate audio uses offline waveform and spectral fingerprints through
  SoundFile and NumPy.
- Near-duplicate results are advisory and shown separately from the main
  potential savings total.

### Archiving Process

When archiving files to an external drive or local folder:

1. Files are copied to `<target>/archived_files` while preserving directory
   structure relative to the scan path.
2. Original files are removed after successful copy.
3. Symlinks are created at the original locations pointing to archived files.
4. Archive targets inside the scanned directory are excluded from scanning to
   avoid re-archiving previous output.

## External Drive Detection

Auto-detection is best-effort and only used when `--target-path` and
`--external-path` are omitted.

- macOS uses `diskutil` and falls back to mounted directories under `/Volumes`.
- Linux parses `/proc/self/mountinfo`, ignores pseudo/system filesystems, and
  looks for writable non-root mounts under `/media`, `/mnt`, and `/run/media`.

You can always bypass detection with `--target-path` or `--external-path`.

## Configuration

Default settings live in `src/disk_space_manager/config.py`:

- `DEFAULT_AGE_THRESHOLD_MONTHS`: Default age for old files.
- `CACHE_DIRECTORY_PATTERNS`: Patterns for cache directories.
- `CACHE_FILE_EXTENSIONS`: File extensions considered cache-like.
- `EXCLUDED_DIRECTORIES`: Directories excluded from scanning.
- `DUPLICATE_*` and `NEAR_DUPLICATE_*`: Duplicate display limits, file-size
  caps, sampling counts, and similarity thresholds.
- `ACTION_LOG_FILE`: Action log path.

## Requirements

- Python 3.9+
- macOS or Linux
- A local folder or mounted external drive for archiving
- Offline media libraries for duplicate analysis: Pillow, ImageHash,
  OpenCV headless, SoundFile, and NumPy
- [uv](https://docs.astral.sh/uv/) for dependency management

## Action Log

All delete and archive actions are logged to
`~/.disk-space-manager-actions.log` with timestamps, paths, sizes, status, and
dry-run state.

## Architecture

```text
main.py                                  Checkout compatibility shim
src/disk_space_manager/cli.py            Click command declarations
src/disk_space_manager/workflows.py      Command workflow orchestration
src/disk_space_manager/ui.py             Rich terminal presentation
src/disk_space_manager/archive_targets.py Archive target resolution
src/disk_space_manager/scanner.py        Filesystem scanning
src/disk_space_manager/analyzer.py       File categorization and estimates
src/disk_space_manager/duplicates.py     Duplicate and near-duplicate analysis
src/disk_space_manager/executor.py       File operations and logging
src/disk_space_manager/drive_detector.py External drive auto-detection
src/disk_space_manager/config.py         Configuration constants
src/disk_space_manager/utils.py          Shared utilities
```

## Agent Context

`AGENTS.md` is the root guide for AI agents working in this repository. Shared
agent skills live in `.agents/skills/`, and shared command prompts live in
`.agents/commands/`.

## Limitations

- Requires appropriate file permissions.
- Some system files may be inaccessible.
- Some near-duplicate media formats may be skipped if local decoders cannot
  read them.
- External drives must be mounted and writable when using `--external-path` or
  auto-detection.
- Scanning speed is bounded by filesystem I/O.

## License

[MIT License](LICENSE)

## Contributing

Contributions are welcome. Please feel free to submit a pull request.
