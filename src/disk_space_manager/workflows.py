"""Command workflow orchestration for Disk Space Manager."""

import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Optional

from .analyzer import FileAnalyzer
from .archive_targets import ArchiveTargetError, resolve_archive_target
from .duplicates import DuplicateDetector, empty_duplicate_report
from .executor import ActionExecutor
from .scanner import DiskScanner
from . import ui


def run_analyze(path: Optional[Path]) -> None:
    """Run the analyze command workflow."""
    ui.print_header()
    scan_path = _scan_path_or_home(path)
    ui.show_scan_path(scan_path)

    scanner = DiskScanner(scan_path)
    analyzer = FileAnalyzer()
    ui.show_disk_usage_analysis(scanner, analyzer)


def run_clean(path: Optional[Path], age_months: int, dry_run: bool) -> None:
    """Run the clean command workflow."""
    ui.print_header()
    scan_path = _scan_path_or_home(path)
    ui.show_scan_path(scan_path)

    scanner = DiskScanner(scan_path)
    analyzer = _analyzer_for_age(age_months)
    scan_results = ui.scan_with_spinner(scanner)
    cache_files = ui.show_cache_analysis(analyzer, scan_results["files"])

    if not cache_files:
        ui.show_no_cache_files()
        return

    savings = analyzer.calculate_potential_savings(cache_files, [])
    ui.show_clean_summary(len(cache_files), savings)

    if not dry_run and not ui.confirm_cache_delete():
        ui.show_operation_cancelled()
        return

    executor = ActionExecutor(dry_run=dry_run)
    ui.show_deletion_started()
    result = executor.delete_files(cache_files, confirm=False)
    ui.show_deletion_result(result, executor)


def run_archive(
    path: Optional[Path],
    target_path: Optional[Path],
    external_path: Optional[Path],
    age_months: int,
    dry_run: bool,
) -> None:
    """Run the archive command workflow."""
    ui.print_header()
    scan_path = _scan_path_or_home(path)
    ui.show_scan_path(scan_path)

    if not target_path and not external_path:
        ui.show_archive_detection_start()

    try:
        target = resolve_archive_target(
            target_path=target_path,
            external_path=external_path,
        )
    except ArchiveTargetError as exc:
        ui.show_error(str(exc))
        sys.exit(1)

    ui.show_archive_target(target)

    scanner = DiskScanner(scan_path, exclude_paths=[target.root])
    analyzer = _analyzer_for_age(age_months)
    scan_results = ui.scan_with_spinner(scanner)

    files = [
        file_info
        for file_info in scan_results["files"]
        if not os.path.islink(file_info["path"])
    ]
    old_files = ui.show_old_files_analysis(analyzer, files, age_months)

    if not old_files:
        ui.show_no_old_files(age_months)
        return

    savings = analyzer.calculate_potential_savings([], old_files)
    ui.show_archive_summary(len(old_files), savings, target)

    if not dry_run and not ui.confirm_archive_move(target.label):
        ui.show_operation_cancelled()
        return

    executor = ActionExecutor(dry_run=dry_run)
    ui.show_archive_started(target.label)
    result = executor.archive_files(
        old_files,
        target.archive_base,
        scan_path,
        confirm=False,
    )
    ui.show_archive_result(result, target.archive_base, executor)


def run_full_report(
    path: Optional[Path],
    age_months: int,
    include_duplicates: bool = True,
    include_near_duplicates: bool = True,
) -> None:
    """Run the full-report command workflow."""
    ui.print_header()
    scan_path = _scan_path_or_home(path)
    ui.show_scan_path(scan_path)

    scanner = DiskScanner(scan_path)
    analyzer = _analyzer_for_age(age_months)
    duplicate_detector = DuplicateDetector() if include_duplicates else None
    scan_results, cache_files, old_files, duplicate_report = ui.run_full_report_progress(
        scanner,
        analyzer,
        age_months,
        duplicate_detector=duplicate_detector,
        include_near_duplicates=include_near_duplicates,
    )
    if duplicate_report is None:
        duplicate_report = empty_duplicate_report()
    ui.show_full_report(
        scanner,
        analyzer,
        scan_results,
        cache_files,
        old_files,
        age_months,
        duplicate_report=duplicate_report,
        include_duplicates=include_duplicates,
        include_near_duplicates=include_near_duplicates,
    )


def _scan_path_or_home(path: Optional[Path]) -> Path:
    return path or Path.home()


def _analyzer_for_age(age_months: int) -> FileAnalyzer:
    return FileAnalyzer(age_threshold=timedelta(days=age_months * 30))
