"""Rich terminal presentation for Disk Space Manager."""

from typing import Dict, List, Optional, Tuple

from rich import box
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .config import (
    DUPLICATE_DISPLAY_LIMIT,
    DUPLICATE_GROUP_FILE_DISPLAY_LIMIT,
    MIN_FILE_SIZE_TO_MOVE,
)
from .duplicates import empty_duplicate_report
from .progress_estimator import ScanProgressEstimator
from .utils import format_size


console = Console()


class ScanAwareTimeRemainingColumn(TimeRemainingColumn):
    """Use explicit scan ETA when available, otherwise fall back to Rich."""

    def render(self, task):
        eta_text = task.fields.get("eta")
        if eta_text and not task.finished:
            return Text(str(eta_text), style="progress.remaining")
        return super().render(task)


def print_header() -> None:
    """Print application header."""
    header = """
    ╔═══════════════════════════════════════════╗
    ║        Disk Space Manager v1.0          ║
    ║   Clean, Analyze, and Archive Files     ║
    ╚═══════════════════════════════════════════╝
    """
    console.print(header, style="bold cyan")


def show_dry_run_banner() -> None:
    console.print("[yellow]🔍 DRY-RUN MODE: No changes will be made[/yellow]\n")


def show_scan_path(scan_path) -> None:
    console.print(f"[cyan]Scanning: {scan_path}[/cyan]\n")


def show_error(message: str) -> None:
    console.print(f"[red]❌ Error: {message}[/red]")


def show_archive_detection_start() -> None:
    console.print("[bold yellow]🔍 Detecting external drive...[/bold yellow]")


def show_archive_target(target) -> None:
    if target.source == "target_path":
        console.print(f"[green]✅ Using local archive folder: {target.root}[/green]")
    else:
        console.print(f"[green]✅ Using external drive: {target.root}[/green]")


def scan_with_spinner(scanner, description: str = "Scanning filesystem...") -> Dict:
    """Run a scan behind a simple Rich spinner."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(description, total=None)
        scan_results = scanner.scan()
        progress.update(task, completed=True)
    return scan_results


def show_disk_usage_analysis(scanner, analyzer) -> Dict:
    """Scan and show disk usage analysis."""
    console.print("\n[bold yellow]📊 Analyzing Disk Usage...[/bold yellow]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning filesystem...", total=None)
        scan_results = scanner.scan()
        progress.update(task, completed=True)

    if scan_results["errors"]:
        show_scan_errors(scan_results["errors"])

    usage_stats = analyzer.analyze_disk_usage(
        scan_results["files"], scan_results["directories"]
    )
    show_disk_usage_sections(scanner, usage_stats)
    return scan_results


def show_scan_errors(errors: List[str]) -> None:
    console.print(
        f"\n[yellow]⚠️  {len(errors)} errors encountered during scan[/yellow]"
    )


def show_disk_usage_sections(scanner, usage_stats: Dict) -> None:
    console.print("\n[bold green]📈 Disk Usage Summary[/bold green]")
    stats_table = Table(box=box.ROUNDED)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="green")
    stats_table.add_row("Total Files Scanned", f"{usage_stats['file_count']:,}")
    stats_table.add_row("Total Size", usage_stats["total_size_formatted"])
    stats_table.add_row(
        "Average File Size", format_size(usage_stats["average_file_size"])
    )
    console.print(stats_table)

    if usage_stats["top_extensions"]:
        console.print("\n[bold green]📁 Top File Types by Size[/bold green]")
        ext_table = Table(box=box.ROUNDED)
        ext_table.add_column("Extension", style="cyan")
        ext_table.add_column("Count", style="yellow", justify="right")
        ext_table.add_column("Total Size", style="green", justify="right")
        for ext, data in usage_stats["top_extensions"]:
            ext_table.add_row(ext, f"{data['count']:,}", format_size(data["size"]))
        console.print(ext_table)

    console.print("\n[bold green]📂 Largest Directories[/bold green]")
    dir_table = Table(box=box.ROUNDED)
    dir_table.add_column("Directory", style="cyan")
    dir_table.add_column("Size", style="green", justify="right")
    for dir_path, size in scanner.get_largest_directories(10):
        dir_table.add_row(str(dir_path), format_size(size))
    console.print(dir_table)

    console.print("\n[bold green]📄 Largest Files[/bold green]")
    file_table = Table(box=box.ROUNDED)
    file_table.add_column("File", style="cyan")
    file_table.add_column("Size", style="green", justify="right")
    for file_info in scanner.get_largest_files(10):
        file_table.add_row(str(file_info["path"]), format_size(file_info["size"]))
    console.print(file_table)


def show_cache_analysis(analyzer, files: List[Dict]) -> List[Dict]:
    """Show cache file analysis and return cache candidates."""
    console.print("\n[bold yellow]🧹 Analyzing Cache Files...[/bold yellow]")

    cache_files = analyzer.find_cache_files(files)
    savings = analyzer.calculate_potential_savings(cache_files, [])

    console.print(f"\n[bold green]Found {len(cache_files)} cache files[/bold green]")
    console.print(
        f"Potential space savings: [green]{savings['cache_size_formatted']}[/green]"
    )

    if cache_files:
        console.print("\n[bold]Sample Cache Files (first 20):[/bold]")
        cache_table = Table(box=box.ROUNDED)
        cache_table.add_column("File", style="cyan")
        cache_table.add_column("Size", style="green", justify="right")
        cache_table.add_column("Reason", style="yellow")
        for file_info in cache_files[:20]:
            cache_table.add_row(
                str(file_info["path"]),
                format_size(file_info["size"]),
                file_info.get("reason", "cache"),
            )
        console.print(cache_table)

    return cache_files


def show_old_files_analysis(
    analyzer, files: List[Dict], age_months: int
) -> List[Dict]:
    """Show old-file analysis and return archive candidates."""
    console.print(
        "\n[bold yellow]📦 Analyzing Old Files "
        f"(not accessed in {age_months}+ months)...[/bold yellow]"
    )

    old_files = analyzer.find_old_files(files, MIN_FILE_SIZE_TO_MOVE)
    savings = analyzer.calculate_potential_savings([], old_files)

    console.print(f"\n[bold green]Found {len(old_files)} old files[/bold green]")
    console.print(f"Total size: [green]{savings['old_files_size_formatted']}[/green]")

    if old_files:
        console.print("\n[bold]Sample Old Files (first 20):[/bold]")
        old_table = Table(box=box.ROUNDED)
        old_table.add_column("File", style="cyan")
        old_table.add_column("Size", style="green", justify="right")
        old_table.add_column("Last Accessed", style="yellow")
        old_table.add_column("Age", style="magenta")
        for file_info in old_files[:20]:
            days_old = file_info.get("days_old", 0)
            old_table.add_row(
                str(file_info["path"]),
                format_size(file_info["size"]),
                file_info["accessed"].strftime("%Y-%m-%d"),
                f"{days_old} days",
            )
        console.print(old_table)

    return old_files


def show_no_cache_files() -> None:
    console.print("\n[green]✅ No cache files found to clean![/green]")


def show_no_old_files(age_months: int) -> None:
    console.print(
        f"\n[green]✅ No old files found (not accessed in {age_months}+ months)![/green]"
    )


def show_clean_summary(file_count: int, savings: Dict) -> None:
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  • Files to delete: {file_count}")
    console.print(f"  • Space to free: {savings['cache_size_formatted']}")


def show_archive_summary(file_count: int, savings: Dict, target) -> None:
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  • Files to move: {file_count}")
    console.print(f"  • Space to archive: {savings['old_files_size_formatted']}")
    console.print(f"  • Target: {target.root}")


def confirm_cache_delete() -> bool:
    return Confirm.ask(
        "\n[bold red]⚠️  Delete these cache files?[/bold red]", default=False
    )


def confirm_archive_move(target_label: str) -> bool:
    return Confirm.ask(
        f"\n[bold red]⚠️  Move these files to {target_label}?[/bold red]",
        default=False,
    )


def show_operation_cancelled() -> None:
    console.print("[yellow]Operation cancelled.[/yellow]")


def show_deletion_started() -> None:
    console.print("\n[bold yellow]🗑️  Deleting cache files...[/bold yellow]")


def show_deletion_result(result: Dict, executor) -> None:
    console.print("\n[green]✅ Deletion complete![/green]")
    console.print(f"  • Deleted: {result['deleted']} files")
    console.print(f"  • Failed: {result['failed']} files")
    console.print(f"  • Space freed: {result['total_size_formatted']}")
    show_action_log_if_present(executor)


def show_archive_started(target_label: str) -> None:
    console.print(f"\n[bold yellow]📦 Moving files to {target_label}...[/bold yellow]")


def show_archive_result(result: Dict, archive_base, executor) -> None:
    console.print("\n[green]✅ Archive complete![/green]")
    console.print(f"  • Moved: {result['moved']} files")
    console.print(f"  • Failed: {result['failed']} files")
    console.print(f"  • Space archived: {result['total_size_formatted']}")
    console.print(f"  • Location: {archive_base}")
    show_action_log_if_present(executor)


def show_action_log_if_present(executor) -> None:
    if executor.action_log:
        console.print(f"\n[dim]Action log: {executor.log_file}[/dim]")


def run_full_report_progress(
    scanner,
    analyzer,
    age_months: int,
    duplicate_detector=None,
    include_near_duplicates: bool = True,
) -> Tuple[Dict, List[Dict], List[Dict], Optional[Dict]]:
    """Run scan and analyses with full-report progress display."""
    phase_total = 3
    if duplicate_detector is not None:
        phase_total += 1
        if include_near_duplicates:
            phase_total += 1
    phase = 1

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        ScanAwareTimeRemainingColumn(),
        console=console,
    ) as progress:
        estimator = ScanProgressEstimator()
        scan_task = progress.add_task(
            f"[cyan]Phase {phase}/{phase_total}:[/cyan] "
            "Scanning filesystem... (estimating)",
            total=estimator.placeholder_total,
            eta="ETA estimating",
        )

        def on_scan_progress(scan_progress):
            estimate = estimator.update(scan_progress)
            if scan_progress.is_finished:
                return
            estimate_label = (
                "estimating total"
                if estimate.is_estimating
                else f"~{estimate.total:,} est."
            )
            progress.update(
                scan_task,
                completed=estimate.completed,
                total=estimate.total,
                eta=estimate.eta_text,
                description=(
                    f"[cyan]Phase {phase}/{phase_total}:[/cyan] "
                    "Scanning filesystem... "
                    f"({scan_progress.files_scanned:,} files, "
                    f"{scan_progress.directories_remaining:,} dirs left, "
                    f"{estimate_label})"
                ),
            )

        scanner.detailed_progress_callback = on_scan_progress
        scan_results = scanner.scan()
        files = scan_results["files"]
        total_files = len(files)
        progress.update(
            scan_task,
            total=total_files,
            completed=total_files,
            eta="",
            description=(
                f"[green]Phase {phase}/{phase_total}:[/green] Scan complete "
                f"({total_files:,} files found)"
            ),
        )
        phase += 1

        analysis_total = max(total_files, 1)
        cache_task = progress.add_task(
            f"[cyan]Phase {phase}/{phase_total}:[/cyan] Identifying cache files...",
            total=analysis_total,
        )
        cache_files = analyzer.find_cache_files(
            files,
            progress_callback=lambda n: progress.update(cache_task, completed=n),
        )
        progress.update(
            cache_task,
            completed=analysis_total,
            description=(
                f"[green]Phase {phase}/{phase_total}:[/green] "
                f"Found {len(cache_files):,} cache files"
            ),
        )
        phase += 1

        old_task = progress.add_task(
            f"[cyan]Phase {phase}/{phase_total}:[/cyan] "
            f"Finding old files (>{age_months} months)...",
            total=analysis_total,
        )
        old_files = analyzer.find_old_files(
            files,
            MIN_FILE_SIZE_TO_MOVE,
            progress_callback=lambda n: progress.update(old_task, completed=n),
        )
        progress.update(
            old_task,
            completed=analysis_total,
            description=(
                f"[green]Phase {phase}/{phase_total}:[/green] "
                f"Found {len(old_files):,} old files"
            ),
        )
        phase += 1

        duplicate_report = empty_duplicate_report()
        if duplicate_detector is not None:
            exact_task = progress.add_task(
                f"[cyan]Phase {phase}/{phase_total}:[/cyan] "
                "Finding exact duplicates...",
                total=analysis_total,
            )
            exact_report = duplicate_detector.find_exact_duplicates(
                files,
                progress_callback=lambda n: progress.update(
                    exact_task,
                    completed=min(n, analysis_total),
                ),
            )
            duplicate_report["exact"] = exact_report
            progress.update(
                exact_task,
                completed=analysis_total,
                description=(
                    f"[green]Phase {phase}/{phase_total}:[/green] "
                    f"Found {exact_report['group_count']:,} exact duplicate groups"
                ),
            )
            phase += 1

            if include_near_duplicates:
                near_task = progress.add_task(
                    f"[cyan]Phase {phase}/{phase_total}:[/cyan] "
                    "Finding near duplicates...",
                    total=analysis_total,
                )
                near_report = duplicate_detector.find_near_duplicates(
                    files,
                    progress_callback=lambda n: progress.update(
                        near_task,
                        completed=min(n, analysis_total),
                    ),
                )
                duplicate_report["near"] = near_report
                progress.update(
                    near_task,
                    completed=analysis_total,
                    description=(
                        f"[green]Phase {phase}/{phase_total}:[/green] "
                        f"Found {near_report['group_count']:,} near duplicate groups"
                    ),
                )

    return scan_results, cache_files, old_files, duplicate_report


def show_full_report(
    scanner,
    analyzer,
    scan_results: Dict,
    cache_files: List[Dict],
    old_files: List[Dict],
    age_months: int,
    duplicate_report: Optional[Dict] = None,
    include_duplicates: bool = True,
    include_near_duplicates: bool = True,
) -> None:
    """Render the full report after all scan and analysis phases finish."""
    duplicate_report = duplicate_report or empty_duplicate_report()
    exact_report = duplicate_report["exact"]
    near_report = duplicate_report["near"]

    if scan_results["errors"]:
        show_scan_errors(scan_results["errors"])

    usage_stats = analyzer.analyze_disk_usage(
        scan_results["files"], scan_results["directories"]
    )
    show_disk_usage_sections(scanner, usage_stats)

    cache_savings = analyzer.calculate_potential_savings(cache_files, [])
    console.print("\n[bold green]🧹 Cache Files[/bold green]")
    console.print(f"Found {len(cache_files)} cache files")
    console.print(
        f"Potential space savings: [green]{cache_savings['cache_size_formatted']}[/green]"
    )
    if cache_files:
        console.print("\n[bold]Sample Cache Files (first 20):[/bold]")
        cache_table = Table(box=box.ROUNDED)
        cache_table.add_column("File", style="cyan")
        cache_table.add_column("Size", style="green", justify="right")
        cache_table.add_column("Reason", style="yellow")
        for cache_file in cache_files[:20]:
            cache_table.add_row(
                str(cache_file["path"]),
                format_size(cache_file["size"]),
                cache_file.get("reason", "cache"),
            )
        console.print(cache_table)

    old_savings = analyzer.calculate_potential_savings([], old_files)
    console.print(
        f"\n[bold green]📦 Old Files (not accessed in {age_months}+ months)[/bold green]"
    )
    console.print(f"Found {len(old_files)} old files")
    console.print(f"Total size: [green]{old_savings['old_files_size_formatted']}[/green]")
    if old_files:
        console.print("\n[bold]Sample Old Files (first 20):[/bold]")
        old_table = Table(box=box.ROUNDED)
        old_table.add_column("File", style="cyan")
        old_table.add_column("Size", style="green", justify="right")
        old_table.add_column("Last Accessed", style="yellow")
        old_table.add_column("Age", style="magenta")
        for old_file in old_files[:20]:
            days_old = old_file.get("days_old", 0)
            old_table.add_row(
                str(old_file["path"]),
                format_size(old_file["size"]),
                old_file["accessed"].strftime("%Y-%m-%d"),
                f"{days_old} days",
            )
        console.print(old_table)

    if include_duplicates:
        show_exact_duplicate_section(exact_report)
        if include_near_duplicates:
            show_near_duplicate_section(near_report)

    exact_reclaimable_size = (
        exact_report["reclaimable_size"] if include_duplicates else 0
    )
    exact_duplicate_file_count = (
        exact_report["duplicate_file_count"] if include_duplicates else 0
    )
    savings = analyzer.calculate_potential_savings(
        cache_files,
        old_files,
        exact_duplicate_reclaimable_size=exact_reclaimable_size,
        exact_duplicate_file_count=exact_duplicate_file_count,
    )
    console.print("\n[bold green]💾 Potential Space Savings[/bold green]")
    savings_table = Table(box=box.ROUNDED)
    savings_table.add_column("Category", style="cyan")
    savings_table.add_column("Files", style="yellow", justify="right")
    savings_table.add_column("Size", style="green", justify="right")
    savings_table.add_row(
        "Cache Files",
        f"{savings['cache_file_count']:,}",
        savings["cache_size_formatted"],
    )
    savings_table.add_row(
        "Old Files (Archive)",
        f"{savings['old_files_count']:,}",
        savings["old_files_size_formatted"],
    )
    if include_duplicates:
        savings_table.add_row(
            "Exact Duplicates",
            f"{savings['exact_duplicate_file_count']:,}",
            savings["exact_duplicate_reclaimable_size_formatted"],
        )
    total_file_count = (
        savings["cache_file_count"]
        + savings["old_files_count"]
        + savings["exact_duplicate_file_count"]
    )
    savings_table.add_row(
        "[bold]Total Potential Savings[/bold]",
        f"[bold]{total_file_count:,}[/bold]",
        f"[bold]{savings['total_savings_formatted']}[/bold]",
    )
    console.print(savings_table)

    if include_duplicates and include_near_duplicates and near_report["reviewable_size"]:
        console.print(
            "[dim]Near duplicate reviewable bytes are not included in total savings: "
            f"{format_size(near_report['reviewable_size'])}[/dim]"
        )


def show_exact_duplicate_section(exact_report: Dict) -> None:
    """Render exact duplicate groups."""
    console.print("\n[bold green]🧬 Exact Duplicate Files[/bold green]")
    console.print(
        f"Found {exact_report['group_count']} duplicate groups "
        f"({exact_report['duplicate_file_count']} duplicate files)"
    )
    console.print(
        "Reclaimable by keeping one per group: "
        f"[green]{format_size(exact_report['reclaimable_size'])}[/green]"
    )
    _show_skip_summary(exact_report["skipped"])

    if not exact_report["groups"]:
        return

    console.print(
        f"\n[bold]Largest Exact Duplicate Groups (first {DUPLICATE_DISPLAY_LIMIT}):[/bold]"
    )
    table = Table(box=box.ROUNDED)
    table.add_column("Files", style="yellow", justify="right")
    table.add_column("Size Each", style="green", justify="right")
    table.add_column("Reclaimable", style="green", justify="right")
    table.add_column("Sample Paths", style="cyan")

    for group in exact_report["groups"][:DUPLICATE_DISPLAY_LIMIT]:
        table.add_row(
            f"{group['file_count']:,}",
            format_size(group["size"]),
            format_size(group["reclaimable_size"]),
            _sample_paths(group["files"]),
        )
    console.print(table)


def show_near_duplicate_section(near_report: Dict) -> None:
    """Render advisory near-duplicate groups."""
    console.print("\n[bold green]🔎 Near Duplicate Files[/bold green]")
    console.print(
        f"Found {near_report['group_count']} near duplicate groups "
        f"({near_report['reviewable_file_count']} reviewable files)"
    )
    console.print(
        "Review before deleting: "
        f"[green]{format_size(near_report['reviewable_size'])}[/green]"
    )
    _show_skip_summary(near_report["skipped"])

    if not near_report["groups"]:
        return

    console.print(
        f"\n[bold]Largest Near Duplicate Groups (first {DUPLICATE_DISPLAY_LIMIT}):[/bold]"
    )
    table = Table(box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("Confidence", style="magenta")
    table.add_column("Files", style="yellow", justify="right")
    table.add_column("Reviewable", style="green", justify="right")
    table.add_column("Reason", style="yellow")
    table.add_column("Sample Paths", style="cyan")

    for group in near_report["groups"][:DUPLICATE_DISPLAY_LIMIT]:
        table.add_row(
            group["kind"],
            group["confidence"],
            f"{group['file_count']:,}",
            format_size(group["reviewable_size"]),
            group["reason"],
            _sample_paths(group["files"]),
        )
    console.print(table)


def _sample_paths(files: List[Dict]) -> str:
    paths = [str(file_info["path"]) for file_info in files]
    shown = paths[:DUPLICATE_GROUP_FILE_DISPLAY_LIMIT]
    if len(paths) > DUPLICATE_GROUP_FILE_DISPLAY_LIMIT:
        shown.append(f"... +{len(paths) - DUPLICATE_GROUP_FILE_DISPLAY_LIMIT} more")
    return "\n".join(shown)


def _show_skip_summary(skipped: Dict[str, int]) -> None:
    visible_skips = {
        label: count
        for label, count in skipped.items()
        if count and label != "empty"
    }
    if not visible_skips:
        return
    summary = ", ".join(
        f"{label.replace('_', ' ')}: {count:,}"
        for label, count in visible_skips.items()
    )
    console.print(f"[dim]Skipped files: {summary}[/dim]")
