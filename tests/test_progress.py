"""Tests for progress bar and time remaining functionality."""

import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from click.testing import CliRunner

from disk_space_manager.scanner import DiskScanner, ScanProgress
from disk_space_manager.analyzer import FileAnalyzer
from disk_space_manager.cli import cli
from disk_space_manager.progress_estimator import ScanProgressEstimator


def _make_test_files(tmp_path, count=200, prefix="file", ext=".txt", content="content"):
    """Create test files in a temporary directory."""
    for i in range(count):
        (tmp_path / f"{prefix}_{i}{ext}").write_text(content)


def _make_file_info_list(tmp_path, count=200, ext=".cache", age_days=400, size=100):
    """Create a list of file info dicts for testing analyzer methods."""
    now = datetime.now()
    ts = (now - timedelta(days=age_days)).timestamp()
    files = []
    for i in range(count):
        p = tmp_path / f"file_{i}{ext}"
        p.write_text("x" * size)
        files.append({
            "path": str(p),
            "size": size,
            "atime": ts,
            "mtime": ts,
            "ctime": ts,
        })
    return files


class TestScannerProgressCallback:
    """Tests for DiskScanner progress_callback."""

    @pytest.fixture(autouse=True)
    def _clear_excluded_dirs(self, monkeypatch):
        monkeypatch.setattr("disk_space_manager.scanner.EXCLUDED_DIRECTORIES", [])
        monkeypatch.setattr("disk_space_manager.scanner.USER_EXCLUDED_DIRECTORIES", [])

    def test_callback_is_invoked_during_scan(self, tmp_path):
        _make_test_files(tmp_path, count=250)
        callback = MagicMock()
        scanner = DiskScanner(tmp_path, progress_callback=callback)
        scanner.scan()
        assert callback.call_count > 0

    def test_callback_receives_increasing_counts(self, tmp_path):
        _make_test_files(tmp_path, count=350)
        counts = []
        scanner = DiskScanner(tmp_path, progress_callback=lambda c: counts.append(c))
        scanner.scan()
        assert len(counts) > 0, "Callback should have been called at least once"
        for i in range(1, len(counts)):
            assert counts[i] >= counts[i - 1], "Counts should be monotonically increasing"

    def test_scan_works_without_callback(self, tmp_path):
        _make_test_files(tmp_path, count=5)
        scanner = DiskScanner(tmp_path)
        result = scanner.scan()
        assert len(result["files"]) == 5

    def test_callback_not_called_for_empty_directory(self, tmp_path):
        callback = MagicMock()
        scanner = DiskScanner(tmp_path, progress_callback=callback)
        scanner.scan()
        assert callback.call_count == 0


class TestScannerDetailedProgressCallback:
    """Tests for detailed scan progress snapshots."""

    @pytest.fixture(autouse=True)
    def _clear_excluded_dirs(self, monkeypatch):
        monkeypatch.setattr("disk_space_manager.scanner.EXCLUDED_DIRECTORIES", [])
        monkeypatch.setattr("disk_space_manager.scanner.USER_EXCLUDED_DIRECTORIES", [])

    def test_detailed_callback_receives_final_snapshot(self, tmp_path):
        _make_test_files(tmp_path, count=25)
        events = []

        scanner = DiskScanner(tmp_path, detailed_progress_callback=events.append)
        result = scanner.scan()

        assert events
        assert events[-1].is_finished is True
        assert events[-1].files_scanned == len(result["files"]) == 25
        assert result["total_scanned"] == 25

    def test_detailed_counts_are_monotonically_increasing(self, tmp_path):
        for dirname in range(4):
            nested = tmp_path / f"dir_{dirname}" / "nested"
            nested.mkdir(parents=True)
            _make_test_files(nested, count=5, prefix=f"file_{dirname}")

        events = []
        scanner = DiskScanner(tmp_path, detailed_progress_callback=events.append)
        scanner.scan()

        assert max(event.directories_discovered for event in events) >= 9
        assert events[-1].directories_completed == events[-1].directories_discovered
        for previous, current in zip(events, events[1:]):
            assert current.files_scanned >= previous.files_scanned
            assert current.directories_discovered >= previous.directories_discovered
            assert current.directories_completed >= previous.directories_completed
            assert current.directories_completed <= current.directories_discovered


class TestScanProgressEstimator:
    """Tests for heuristic scan total estimation."""

    def test_eta_is_available_once_scan_has_rate(self):
        now = 0.0
        estimator = ScanProgressEstimator(upward_smoothing=1.0, clock=lambda: now)
        estimator.update(
            ScanProgress(
                files_scanned=0,
                directories_discovered=1,
                directories_completed=0,
                errors=0,
            )
        )

        now = 10.0
        estimate = estimator.update(
            ScanProgress(
                files_scanned=1_000,
                directories_discovered=10,
                directories_completed=5,
                errors=0,
            )
        )

        assert estimate.eta_seconds == pytest.approx(10.0)
        assert estimate.eta_text == "ETA 0:00:10"

    def test_estimate_increases_when_many_directories_are_discovered(self):
        estimator = ScanProgressEstimator()

        first = estimator.update(
            ScanProgress(
                files_scanned=1_000,
                directories_discovered=10,
                directories_completed=5,
                errors=0,
            )
        )
        second = estimator.update(
            ScanProgress(
                files_scanned=1_100,
                directories_discovered=50,
                directories_completed=5,
                errors=0,
            )
        )

        assert second.total > first.total

    def test_estimate_decreases_as_known_work_drains(self):
        estimator = ScanProgressEstimator()

        first = estimator.update(
            ScanProgress(
                files_scanned=1_000,
                directories_discovered=100,
                directories_completed=10,
                errors=0,
            )
        )
        second = estimator.update(
            ScanProgress(
                files_scanned=1_200,
                directories_discovered=100,
                directories_completed=80,
                errors=0,
            )
        )

        assert second.total < first.total
        assert second.total > second.completed

    def test_completed_never_exceeds_displayed_total_while_running(self):
        estimator = ScanProgressEstimator()

        estimate = estimator.update(
            ScanProgress(
                files_scanned=5_000,
                directories_discovered=10,
                directories_completed=10,
                errors=0,
            )
        )

        assert estimate.total > estimate.completed

    def test_running_scan_keeps_visible_remaining_work_when_directories_left(self):
        estimator = ScanProgressEstimator()

        estimate = estimator.update(
            ScanProgress(
                files_scanned=459_765,
                directories_discovered=1_000,
                directories_completed=980,
                errors=0,
            )
        )

        assert estimate.total > estimate.completed + 1
        assert estimate.completed / estimate.total <= 0.995

    def test_finalization_snaps_to_actual_total(self):
        estimator = ScanProgressEstimator()
        estimator.update(
            ScanProgress(
                files_scanned=1_000,
                directories_discovered=100,
                directories_completed=10,
                errors=0,
            )
        )

        estimate = estimator.update(
            ScanProgress(
                files_scanned=1_234,
                directories_discovered=100,
                directories_completed=100,
                errors=0,
                is_finished=True,
            )
        )

        assert estimate.completed == 1_234
        assert estimate.total == 1_234
        assert estimate.is_estimating is False
        assert estimate.eta_text == "ETA 0:00:00"


class TestAnalyzerCacheProgressCallback:
    """Tests for FileAnalyzer.find_cache_files progress_callback."""

    def test_callback_is_invoked(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=200)
        callback = MagicMock()
        analyzer = FileAnalyzer()
        analyzer.find_cache_files(files, progress_callback=callback)
        assert callback.call_count > 0

    def test_callback_values_are_monotonically_increasing(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=500)
        values = []
        analyzer = FileAnalyzer()
        analyzer.find_cache_files(files, progress_callback=lambda n: values.append(n))
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1]

    def test_final_callback_equals_total_files(self, tmp_path):
        count = 350
        files = _make_file_info_list(tmp_path, count=count)
        values = []
        analyzer = FileAnalyzer()
        analyzer.find_cache_files(files, progress_callback=lambda n: values.append(n))
        assert values[-1] == count

    def test_works_without_callback(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=10)
        analyzer = FileAnalyzer()
        result = analyzer.find_cache_files(files)
        assert isinstance(result, list)

    def test_empty_file_list(self):
        callback = MagicMock()
        analyzer = FileAnalyzer()
        result = analyzer.find_cache_files([], progress_callback=callback)
        assert result == []
        assert callback.call_count == 0


class TestAnalyzerOldFilesProgressCallback:
    """Tests for FileAnalyzer.find_old_files progress_callback."""

    def test_callback_is_invoked(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=200, age_days=400)
        callback = MagicMock()
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))
        analyzer.find_old_files(files, min_size=0, progress_callback=callback)
        assert callback.call_count > 0

    def test_callback_values_are_monotonically_increasing(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=500, age_days=400)
        values = []
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))
        analyzer.find_old_files(files, min_size=0, progress_callback=lambda n: values.append(n))
        for i in range(1, len(values)):
            assert values[i] >= values[i - 1]

    def test_final_callback_equals_total_files(self, tmp_path):
        count = 350
        files = _make_file_info_list(tmp_path, count=count, age_days=400)
        values = []
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))
        analyzer.find_old_files(files, min_size=0, progress_callback=lambda n: values.append(n))
        assert values[-1] == count

    def test_works_without_callback(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=10, age_days=400)
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))
        result = analyzer.find_old_files(files, min_size=0)
        assert isinstance(result, list)

    def test_empty_file_list(self):
        callback = MagicMock()
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))
        result = analyzer.find_old_files([], min_size=0, progress_callback=callback)
        assert result == []
        assert callback.call_count == 0


class TestProgressDoesNotAlterResults:
    """Verify that adding progress callbacks doesn't change analysis results."""

    def test_cache_results_same_with_and_without_callback(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=100)
        analyzer = FileAnalyzer()

        result_without = analyzer.find_cache_files(files)
        result_with = analyzer.find_cache_files(files, progress_callback=lambda n: None)

        assert len(result_without) == len(result_with)
        for a, b in zip(result_without, result_with):
            assert a["path"] == b["path"]
            assert a["size"] == b["size"]

    def test_old_files_results_same_with_and_without_callback(self, tmp_path):
        files = _make_file_info_list(tmp_path, count=100, age_days=400)
        analyzer = FileAnalyzer(age_threshold=timedelta(days=30))

        result_without = analyzer.find_old_files(files, min_size=0)
        result_with = analyzer.find_old_files(files, min_size=0, progress_callback=lambda n: None)

        assert len(result_without) == len(result_with)
        for a, b in zip(result_without, result_with):
            assert a["path"] == b["path"]
            assert a["size"] == b["size"]


class TestFullReportCommand:
    """Integration tests for the full-report command with progress."""

    @pytest.fixture(autouse=True)
    def _clear_excluded_dirs(self, monkeypatch):
        monkeypatch.setattr("disk_space_manager.scanner.EXCLUDED_DIRECTORIES", [])
        monkeypatch.setattr("disk_space_manager.scanner.USER_EXCLUDED_DIRECTORIES", [])

    def test_runs_successfully(self, tmp_path):
        _make_test_files(tmp_path, count=10)
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_runs_with_age_months(self, tmp_path):
        _make_test_files(tmp_path, count=5)
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path), "--age-months", "1"])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_runs_on_empty_directory(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_runs_with_cache_files(self, tmp_path):
        for i in range(5):
            (tmp_path / f"temp_{i}.cache").write_text("cache content")
            (tmp_path / f"data_{i}.txt").write_text("real content")
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_runs_with_nested_directories(self, tmp_path):
        sub = tmp_path / "subdir" / "nested"
        sub.mkdir(parents=True)
        for i in range(5):
            (sub / f"file_{i}.txt").write_text("content")
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_runs_with_many_files(self, tmp_path):
        _make_test_files(tmp_path, count=250)
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"

    def test_duplicate_sections_are_shown_by_default(self, tmp_path):
        (tmp_path / "first.txt").write_text("same duplicate content")
        (tmp_path / "second.txt").write_text("same duplicate content")
        runner = CliRunner()
        result = runner.invoke(cli, ["full-report", "--path", str(tmp_path)])
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"
        assert "Exact Duplicate Files" in result.output
        assert "Near Duplicate Files" in result.output

    def test_no_duplicates_hides_duplicate_sections(self, tmp_path):
        (tmp_path / "first.txt").write_text("same duplicate content")
        (tmp_path / "second.txt").write_text("same duplicate content")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["full-report", "--path", str(tmp_path), "--no-duplicates"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"
        assert "Exact Duplicate Files" not in result.output
        assert "Near Duplicate Files" not in result.output

    def test_no_near_duplicates_keeps_exact_section_only(self, tmp_path):
        (tmp_path / "first.txt").write_text("same duplicate content")
        (tmp_path / "second.txt").write_text("same duplicate content")
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["full-report", "--path", str(tmp_path), "--no-near-duplicates"],
        )
        assert result.exit_code == 0, f"Command failed: {result.output}\n{result.exception}"
        assert "Exact Duplicate Files" in result.output
        assert "Near Duplicate Files" not in result.output
