"""Tests for duplicate and near-duplicate detection."""

import builtins
from datetime import datetime

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw

from disk_space_manager.config import NEAR_DUPLICATE_TEXT_MAX_BYTES
from disk_space_manager.duplicates import DuplicateDetector


def _file_info(path):
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "atime": stat.st_atime,
        "mtime": stat.st_mtime,
        "ctime": stat.st_ctime,
    }


def _paths_in_groups(groups):
    return [
        {file_info["path"] for file_info in group["files"]}
        for group in groups
    ]


class TestExactDuplicates:
    def test_same_content_is_grouped(self, tmp_path):
        first = tmp_path / "first.bin"
        second = tmp_path / "second.bin"
        different = tmp_path / "different.bin"
        first.write_bytes(b"same content")
        second.write_bytes(b"same content")
        different.write_bytes(b"other bytes!")

        report = DuplicateDetector().find_exact_duplicates(
            [_file_info(first), _file_info(second), _file_info(different)]
        )

        assert report["group_count"] == 1
        assert report["duplicate_file_count"] == 1
        assert report["reclaimable_size"] == first.stat().st_size
        assert {first.as_posix(), second.as_posix()} in _paths_in_groups(report["groups"])

    def test_same_size_different_content_is_not_grouped(self, tmp_path):
        first = tmp_path / "first.bin"
        second = tmp_path / "second.bin"
        first.write_bytes(b"abc")
        second.write_bytes(b"xyz")

        report = DuplicateDetector().find_exact_duplicates(
            [_file_info(first), _file_info(second)]
        )

        assert report["group_count"] == 0
        assert report["reclaimable_size"] == 0

    def test_zero_byte_files_do_not_create_savings(self, tmp_path):
        first = tmp_path / "first.empty"
        second = tmp_path / "second.empty"
        first.write_bytes(b"")
        second.write_bytes(b"")

        report = DuplicateDetector().find_exact_duplicates(
            [_file_info(first), _file_info(second)]
        )

        assert report["group_count"] == 0
        assert report["duplicate_file_count"] == 0
        assert report["reclaimable_size"] == 0
        assert report["skipped"]["empty"] == 2

    def test_hashing_reads_files_in_chunks(self, tmp_path, monkeypatch):
        first = tmp_path / "first.bin"
        second = tmp_path / "second.bin"
        first.write_bytes(b"0123456789")
        second.write_bytes(b"0123456789")
        read_sizes = []
        real_open = builtins.open

        class WrappedFile:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def read(self, size=-1):
                read_sizes.append(size)
                return self.handle.read(size)

            def __getattr__(self, name):
                return getattr(self.handle, name)

        def wrapped_open(path, *args, **kwargs):
            return WrappedFile(real_open(path, *args, **kwargs))

        monkeypatch.setattr(builtins, "open", wrapped_open)

        report = DuplicateDetector(hash_chunk_size=4).find_exact_duplicates(
            [_file_info(first), _file_info(second)]
        )

        assert report["group_count"] == 1
        assert read_sizes
        assert set(read_sizes) <= {4}

    def test_unreadable_files_are_skipped(self, tmp_path, monkeypatch):
        first = tmp_path / "first.bin"
        second = tmp_path / "second.bin"
        first.write_bytes(b"same")
        second.write_bytes(b"same")
        real_open = builtins.open

        def guarded_open(path, *args, **kwargs):
            if str(path) == str(second):
                raise PermissionError("blocked")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", guarded_open)

        report = DuplicateDetector().find_exact_duplicates(
            [_file_info(first), _file_info(second)]
        )

        assert report["group_count"] == 0
        assert report["skipped"]["unreadable"] == 1


class TestNearDuplicates:
    def test_similar_text_files_are_grouped(self, tmp_path):
        shared = (
            "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda "
            "mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega "
        )
        first = tmp_path / "notes-a.txt"
        second = tmp_path / "notes-b.txt"
        different = tmp_path / "different.txt"
        first.write_text(shared + "project notes about duplicate cleanup")
        second.write_text(shared + "project notes about duplicate clean-up")
        different.write_text(
            "red blue green yellow orange purple black white silver gold "
            "north south east west spring summer autumn winter"
        )

        report = DuplicateDetector().find_near_duplicates(
            [_file_info(first), _file_info(second), _file_info(different)]
        )

        assert {first.as_posix(), second.as_posix()} in _paths_in_groups(report["groups"])
        assert all(different.as_posix() not in group for group in _paths_in_groups(report["groups"]))

    def test_similar_images_are_grouped(self, tmp_path):
        first = tmp_path / "image-a.png"
        second = tmp_path / "image-b.png"
        different = tmp_path / "different.png"
        _write_pattern_image(first, accent=(255, 0, 0))
        _write_pattern_image(second, accent=(250, 20, 20))
        _write_distinct_image(different)

        report = DuplicateDetector().find_near_duplicates(
            [_file_info(first), _file_info(second), _file_info(different)]
        )

        assert {first.as_posix(), second.as_posix()} in _paths_in_groups(report["groups"])
        assert all(different.as_posix() not in group for group in _paths_in_groups(report["groups"]))

    def test_similar_audio_files_are_grouped(self, tmp_path):
        first = tmp_path / "tone-a.wav"
        second = tmp_path / "tone-b.wav"
        _write_tone(first, frequency=440.0, amplitude=0.5)
        _write_tone(second, frequency=440.0, amplitude=0.48)

        report = DuplicateDetector().find_near_duplicates(
            [_file_info(first), _file_info(second)]
        )

        assert {first.as_posix(), second.as_posix()} in _paths_in_groups(report["groups"])

    def test_video_uses_sampled_frame_hashes(self, tmp_path, monkeypatch):
        first = tmp_path / "video-a.mp4"
        second = tmp_path / "video-b.mp4"
        different = tmp_path / "different.mp4"
        first.write_bytes(b"video-a")
        second.write_bytes(b"video-b")
        different.write_bytes(b"video-c")
        detector = DuplicateDetector()

        def fake_hashes(path):
            if path == str(first):
                return [0] * 5, 10.0
            if path == str(second):
                return [1] * 5, 10.5
            return [(1 << 64) - 1] * 5, 10.0

        monkeypatch.setattr(detector, "_extract_video_hashes", fake_hashes)

        report = detector.find_near_duplicates(
            [_file_info(first), _file_info(second), _file_info(different)]
        )

        assert {first.as_posix(), second.as_posix()} in _paths_in_groups(report["groups"])
        assert all(different.as_posix() not in group for group in _paths_in_groups(report["groups"]))

    def test_unsupported_large_and_decode_failures_are_skipped(self, tmp_path):
        unsupported = tmp_path / "archive.bin"
        invalid_image = tmp_path / "bad.png"
        unsupported.write_bytes(b"not media")
        invalid_image.write_bytes(b"not an image")
        big_text_info = {
            "path": str(tmp_path / "big.txt"),
            "size": NEAR_DUPLICATE_TEXT_MAX_BYTES + 1,
            "atime": datetime.now().timestamp(),
            "mtime": datetime.now().timestamp(),
            "ctime": datetime.now().timestamp(),
        }

        report = DuplicateDetector().find_near_duplicates(
            [_file_info(unsupported), _file_info(invalid_image), big_text_info]
        )

        assert report["group_count"] == 0
        assert report["skipped"]["unsupported"] == 1
        assert report["skipped"]["decode_error"] == 1
        assert report["skipped"]["too_large"] == 1


def _write_pattern_image(path, accent):
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    for index in range(0, 96, 12):
        draw.line((0, index, 95, 95 - index), fill=accent, width=3)
        draw.rectangle((index, index // 2, index + 8, index // 2 + 12), fill="navy")
    image.save(path)


def _write_distinct_image(path):
    image = Image.new("RGB", (96, 96), "black")
    draw = ImageDraw.Draw(image)
    for index in range(0, 96, 8):
        draw.ellipse((index, index, index + 20, index + 12), fill="gold")
    image.save(path)


def _write_tone(path, frequency, amplitude):
    sample_rate = 8_000
    seconds = 1.0
    times = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    samples = amplitude * np.sin(2 * np.pi * frequency * times)
    sf.write(path, samples.astype("float32"), sample_rate)
