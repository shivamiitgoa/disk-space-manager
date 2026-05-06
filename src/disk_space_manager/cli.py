"""Click command declarations for Disk Space Manager."""

from pathlib import Path
from typing import Optional

import click

from . import ui, workflows
from .config import DEFAULT_AGE_THRESHOLD_MONTHS


@click.group()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be done without making changes",
)
@click.pass_context
def cli(ctx, dry_run: bool) -> None:
    """Disk Space Manager - Clean, analyze, and archive files."""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    if dry_run:
        ui.show_dry_run_banner()


@cli.command()
@click.option(
    "--path",
    type=click.Path(exists=True, path_type=Path),
    help="Directory to analyze (default: home directory)",
)
def analyze(path: Optional[Path]) -> None:
    """Analyze disk usage and show insights."""
    workflows.run_analyze(path)


@cli.command()
@click.option(
    "--path",
    type=click.Path(exists=True, path_type=Path),
    help="Directory to scan (default: home directory)",
)
@click.option(
    "--age-months",
    type=int,
    default=DEFAULT_AGE_THRESHOLD_MONTHS,
    help=f"Age threshold in months (default: {DEFAULT_AGE_THRESHOLD_MONTHS})",
)
@click.pass_context
def clean(ctx, path: Optional[Path], age_months: int) -> None:
    """Identify and remove cache files."""
    workflows.run_clean(path, age_months, dry_run=ctx.obj.get("dry_run", False))


@cli.command()
@click.option(
    "--path",
    type=click.Path(exists=True, path_type=Path),
    help="Directory to scan (default: home directory)",
)
@click.option(
    "--target-path",
    type=click.Path(path_type=Path),
    help="Local folder to use as archive destination",
)
@click.option(
    "--external-path",
    type=click.Path(path_type=Path),
    help="Path to external drive (default: auto-detect)",
)
@click.option(
    "--age-months",
    type=int,
    default=DEFAULT_AGE_THRESHOLD_MONTHS,
    help=f"Age threshold in months (default: {DEFAULT_AGE_THRESHOLD_MONTHS})",
)
@click.pass_context
def archive(
    ctx,
    path: Optional[Path],
    target_path: Optional[Path],
    external_path: Optional[Path],
    age_months: int,
) -> None:
    """Move old files to an external drive or local folder."""
    workflows.run_archive(
        path,
        target_path,
        external_path,
        age_months,
        dry_run=ctx.obj.get("dry_run", False),
    )


@cli.command()
@click.option(
    "--path",
    type=click.Path(exists=True, path_type=Path),
    help="Directory to scan (default: home directory)",
)
@click.option(
    "--age-months",
    type=int,
    default=DEFAULT_AGE_THRESHOLD_MONTHS,
    help=f"Age threshold in months (default: {DEFAULT_AGE_THRESHOLD_MONTHS})",
)
@click.option(
    "--no-duplicates",
    is_flag=True,
    help="Skip exact and near-duplicate file detection",
)
@click.option(
    "--no-near-duplicates",
    is_flag=True,
    help="Skip slower near-duplicate checks but keep exact duplicate detection",
)
def full_report(
    path: Optional[Path],
    age_months: int,
    no_duplicates: bool,
    no_near_duplicates: bool,
) -> None:
    """Generate a full analysis report."""
    workflows.run_full_report(
        path,
        age_months,
        include_duplicates=not no_duplicates,
        include_near_duplicates=not no_near_duplicates and not no_duplicates,
    )
