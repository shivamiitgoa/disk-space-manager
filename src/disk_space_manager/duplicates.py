"""Exact and near-duplicate file detection."""

import hashlib
import os
import re
from typing import Callable, Dict, List, Optional, Set, Tuple

from .config import (
    DUPLICATE_HASH_CHUNK_SIZE,
    DUPLICATE_PROGRESS_BATCH_SIZE,
    NEAR_DUPLICATE_AUDIO_HASH_DISTANCE,
    NEAR_DUPLICATE_AUDIO_MAX_BYTES,
    NEAR_DUPLICATE_AUDIO_MAX_SAMPLES,
    NEAR_DUPLICATE_AUDIO_SAMPLE_WINDOWS,
    NEAR_DUPLICATE_DURATION_BUCKET_SECONDS,
    NEAR_DUPLICATE_DURATION_TOLERANCE_RATIO,
    NEAR_DUPLICATE_DURATION_TOLERANCE_SECONDS,
    NEAR_DUPLICATE_HASH_BAND_BITS,
    NEAR_DUPLICATE_IMAGE_HASH_DISTANCE,
    NEAR_DUPLICATE_IMAGE_MAX_BYTES,
    NEAR_DUPLICATE_TEXT_MAX_BYTES,
    NEAR_DUPLICATE_TEXT_MIN_TOKENS,
    NEAR_DUPLICATE_TEXT_SIMHASH_DISTANCE,
    NEAR_DUPLICATE_VIDEO_FRAME_HASH_DISTANCE,
    NEAR_DUPLICATE_VIDEO_MAX_BYTES,
    NEAR_DUPLICATE_VIDEO_SAMPLE_FRAMES,
)


TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".rb",
        ".php",
        ".java",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".go",
        ".rs",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".sql",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".log",
    }
)

IMAGE_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".heic",
        ".heif",
    }
)

VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".m4v",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".mpeg",
        ".mpg",
        ".wmv",
        ".flv",
    }
)

AUDIO_EXTENSIONS = frozenset(
    {
        ".wav",
        ".flac",
        ".ogg",
        ".oga",
        ".aiff",
        ".aif",
        ".au",
        ".mp3",
        ".m4a",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def empty_duplicate_report() -> Dict:
    """Return an empty duplicate report matching detector output shape."""
    return {
        "exact": _empty_exact_report(),
        "near": _empty_near_report(),
    }


class DuplicateDetector:
    """Detect exact and near-duplicate files from scanner results."""

    def __init__(self, hash_chunk_size: int = DUPLICATE_HASH_CHUNK_SIZE):
        self.hash_chunk_size = hash_chunk_size

    def find_exact_duplicates(
        self,
        files: List[Dict],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Dict:
        """Find byte-for-byte duplicate files by size and SHA-256 digest."""
        skipped = _new_skip_counts()
        size_buckets: Dict[int, List[Dict]] = {}

        for file_info in files:
            size = int(file_info.get("size") or 0)
            if size <= 0:
                skipped["empty"] += 1
                continue
            size_buckets.setdefault(size, []).append(file_info)

        processed = 0
        groups = []
        for size, candidates in size_buckets.items():
            if len(candidates) < 2:
                continue

            hash_buckets: Dict[str, List[Dict]] = {}
            for file_info in candidates:
                digest = self._sha256_file(file_info["path"])
                processed += 1
                if progress_callback and (
                    processed % DUPLICATE_PROGRESS_BATCH_SIZE == 0
                ):
                    progress_callback(processed)
                if digest is None:
                    skipped["unreadable"] += 1
                    continue
                hash_buckets.setdefault(digest, []).append(file_info)

            for digest, members in hash_buckets.items():
                if len(members) < 2:
                    continue
                sorted_members = _sorted_files(members)
                groups.append(
                    {
                        "kind": "exact",
                        "hash": digest,
                        "files": sorted_members,
                        "file_count": len(sorted_members),
                        "size": size,
                        "total_size": size * len(sorted_members),
                        "reclaimable_size": size * (len(sorted_members) - 1),
                    }
                )

        if progress_callback and processed:
            progress_callback(processed)

        groups.sort(key=lambda group: group["reclaimable_size"], reverse=True)
        duplicate_file_count = sum(group["file_count"] - 1 for group in groups)
        reclaimable_size = sum(group["reclaimable_size"] for group in groups)

        return {
            "groups": groups,
            "group_count": len(groups),
            "duplicate_file_count": duplicate_file_count,
            "reclaimable_size": reclaimable_size,
            "skipped": skipped,
        }

    def find_near_duplicates(
        self,
        files: List[Dict],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Dict:
        """Find advisory near-duplicate groups across supported file types."""
        skipped = _new_skip_counts()
        fingerprints = []
        total = len(files)

        for index, file_info in enumerate(files, 1):
            fingerprint = self._fingerprint_file(file_info, skipped)
            if fingerprint is not None:
                fingerprints.append(fingerprint)
            if progress_callback and (
                index % DUPLICATE_PROGRESS_BATCH_SIZE == 0 or index == total
            ):
                progress_callback(index)

        if len(fingerprints) < 2:
            return {**_empty_near_report(), "skipped": skipped}

        pairs = self._candidate_pairs(fingerprints)
        union_find = _UnionFind(len(fingerprints))
        for left_index, right_index in pairs:
            if self._similarity(fingerprints[left_index], fingerprints[right_index]):
                union_find.union(left_index, right_index)

        grouped_indexes: Dict[int, List[int]] = {}
        for index in range(len(fingerprints)):
            grouped_indexes.setdefault(union_find.find(index), []).append(index)

        groups = []
        for indexes in grouped_indexes.values():
            if len(indexes) < 2:
                continue
            first = fingerprints[indexes[0]]
            members = _sorted_files([fingerprints[index]["file"] for index in indexes])
            total_size = sum(int(file_info.get("size") or 0) for file_info in members)
            largest_size = max(int(file_info.get("size") or 0) for file_info in members)
            reason, confidence = self._group_reason(indexes, fingerprints)
            groups.append(
                {
                    "kind": first["kind"],
                    "files": members,
                    "file_count": len(members),
                    "total_size": total_size,
                    "reviewable_size": max(total_size - largest_size, 0),
                    "reason": reason,
                    "confidence": confidence,
                }
            )

        groups.sort(key=lambda group: group["reviewable_size"], reverse=True)
        reviewable_file_count = sum(group["file_count"] - 1 for group in groups)
        reviewable_size = sum(group["reviewable_size"] for group in groups)

        return {
            "groups": groups,
            "group_count": len(groups),
            "reviewable_file_count": reviewable_file_count,
            "reviewable_size": reviewable_size,
            "skipped": skipped,
        }

    def build_report(
        self,
        files: List[Dict],
        include_near_duplicates: bool = True,
        exact_progress_callback: Optional[Callable[[int], None]] = None,
        near_progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Dict:
        """Build exact and optionally near-duplicate report sections."""
        exact = self.find_exact_duplicates(files, exact_progress_callback)
        near = (
            self.find_near_duplicates(files, near_progress_callback)
            if include_near_duplicates
            else _empty_near_report()
        )
        return {"exact": exact, "near": near}

    def _sha256_file(self, path: str) -> Optional[str]:
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(self.hash_chunk_size), b""):
                    digest.update(chunk)
        except (OSError, PermissionError):
            return None
        return digest.hexdigest()

    def _fingerprint_file(
        self, file_info: Dict, skipped: Dict[str, int]
    ) -> Optional[Dict]:
        path = str(file_info["path"])
        size = int(file_info.get("size") or 0)
        if size <= 0:
            skipped["empty"] += 1
            return None

        ext = os.path.splitext(path)[1].lower()
        if ext in TEXT_EXTENSIONS:
            return self._fingerprint_text(file_info, skipped)
        if ext in IMAGE_EXTENSIONS:
            return self._fingerprint_image(file_info, skipped)
        if ext in VIDEO_EXTENSIONS:
            return self._fingerprint_video(file_info, skipped)
        if ext in AUDIO_EXTENSIONS:
            return self._fingerprint_audio(file_info, skipped)

        skipped["unsupported"] += 1
        return None

    def _fingerprint_text(
        self, file_info: Dict, skipped: Dict[str, int]
    ) -> Optional[Dict]:
        if int(file_info.get("size") or 0) > NEAR_DUPLICATE_TEXT_MAX_BYTES:
            skipped["too_large"] += 1
            return None

        try:
            with open(file_info["path"], "rb") as handle:
                raw = handle.read(NEAR_DUPLICATE_TEXT_MAX_BYTES + 1)
        except (OSError, PermissionError):
            skipped["unreadable"] += 1
            return None

        if len(raw) > NEAR_DUPLICATE_TEXT_MAX_BYTES:
            skipped["too_large"] += 1
            return None

        text = raw.decode("utf-8", errors="ignore").lower()
        tokens = _TOKEN_RE.findall(text)
        if len(tokens) < NEAR_DUPLICATE_TEXT_MIN_TOKENS:
            skipped["too_small"] += 1
            return None

        shingles = _token_shingles(tokens)
        return {
            "kind": "text",
            "file": file_info,
            "hash": _simhash(shingles),
            "token_count": len(tokens),
        }

    def _fingerprint_image(
        self, file_info: Dict, skipped: Dict[str, int]
    ) -> Optional[Dict]:
        if int(file_info.get("size") or 0) > NEAR_DUPLICATE_IMAGE_MAX_BYTES:
            skipped["too_large"] += 1
            return None

        try:
            from PIL import Image, ImageOps
            import imagehash
        except ImportError:
            skipped["dependency_missing"] += 1
            return None

        try:
            with Image.open(file_info["path"]) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                perceptual_hash = imagehash.phash(image, hash_size=8)
        except (OSError, ValueError):
            skipped["decode_error"] += 1
            return None

        return {
            "kind": "image",
            "file": file_info,
            "hash": int(str(perceptual_hash), 16),
        }

    def _fingerprint_video(
        self, file_info: Dict, skipped: Dict[str, int]
    ) -> Optional[Dict]:
        if int(file_info.get("size") or 0) > NEAR_DUPLICATE_VIDEO_MAX_BYTES:
            skipped["too_large"] += 1
            return None

        frame_hashes, duration = self._extract_video_hashes(file_info["path"])
        if frame_hashes is None:
            skipped["decode_error"] += 1
            return None
        if not frame_hashes:
            skipped["too_small"] += 1
            return None

        return {
            "kind": "video",
            "file": file_info,
            "hashes": frame_hashes,
            "duration": duration,
        }

    def _extract_video_hashes(self, path: str) -> Tuple[Optional[List[int]], float]:
        try:
            import cv2
            from PIL import Image
            import imagehash
        except ImportError:
            return None, 0.0

        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            return None, 0.0

        try:
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if frame_count <= 0:
                return None, 0.0

            duration = frame_count / fps if fps > 0 else 0.0
            sample_count = min(NEAR_DUPLICATE_VIDEO_SAMPLE_FRAMES, frame_count)
            positions = _evenly_spaced_positions(frame_count, sample_count)
            frame_hashes = []
            for position in positions:
                capture.set(cv2.CAP_PROP_POS_FRAMES, position)
                ok, frame = capture.read()
                if not ok:
                    continue
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(frame)
                frame_hashes.append(int(str(imagehash.phash(image, hash_size=8)), 16))
        finally:
            capture.release()

        return frame_hashes, duration

    def _fingerprint_audio(
        self, file_info: Dict, skipped: Dict[str, int]
    ) -> Optional[Dict]:
        if int(file_info.get("size") or 0) > NEAR_DUPLICATE_AUDIO_MAX_BYTES:
            skipped["too_large"] += 1
            return None

        try:
            import numpy as np
            import soundfile as sf
        except ImportError:
            skipped["dependency_missing"] += 1
            return None

        try:
            with sf.SoundFile(file_info["path"]) as sound_file:
                frames = int(sound_file.frames)
                sample_rate = int(sound_file.samplerate)
                if frames <= 0 or sample_rate <= 0:
                    skipped["too_small"] += 1
                    return None
                duration = frames / sample_rate
                samples = self._read_audio_sample_windows(sound_file, np)
        except Exception:
            skipped["decode_error"] += 1
            return None

        if samples.size < 64:
            skipped["too_small"] += 1
            return None

        return {
            "kind": "audio",
            "file": file_info,
            "hash": _audio_fingerprint(samples, np),
            "duration": duration,
        }

    def _read_audio_sample_windows(self, sound_file, np):
        frames = int(sound_file.frames)
        sample_budget = min(frames, NEAR_DUPLICATE_AUDIO_MAX_SAMPLES)
        windows = min(NEAR_DUPLICATE_AUDIO_SAMPLE_WINDOWS, max(1, sample_budget))
        window_size = max(1, sample_budget // windows)
        positions = _evenly_spaced_positions(frames, windows)
        chunks = []

        for position in positions:
            start = min(position, max(frames - window_size, 0))
            sound_file.seek(start)
            chunk = sound_file.read(
                frames=window_size,
                dtype="float32",
                always_2d=True,
            )
            if chunk.size == 0:
                continue
            mono = np.mean(chunk, axis=1)
            chunks.append(mono)

        if not chunks:
            return np.array([], dtype="float32")
        return np.concatenate(chunks).astype("float32", copy=False)

    def _candidate_pairs(self, fingerprints: List[Dict]) -> Set[Tuple[int, int]]:
        buckets: Dict[Tuple, List[int]] = {}
        for index, fingerprint in enumerate(fingerprints):
            for key in _bucket_keys(fingerprint):
                buckets.setdefault(key, []).append(index)

        pairs = set()
        for indexes in buckets.values():
            if len(indexes) < 2:
                continue
            for left_position, left_index in enumerate(indexes[:-1]):
                for right_index in indexes[left_position + 1 :]:
                    pairs.add(
                        (min(left_index, right_index), max(left_index, right_index))
                    )
        return pairs

    def _similarity(self, left: Dict, right: Dict) -> Optional[Dict]:
        if left["kind"] != right["kind"]:
            return None

        kind = left["kind"]
        if kind == "text":
            distance = _hamming_distance(left["hash"], right["hash"])
            if distance <= NEAR_DUPLICATE_TEXT_SIMHASH_DISTANCE:
                return _similarity_result(distance, "SimHash distance")
            return None

        if kind == "image":
            distance = _hamming_distance(left["hash"], right["hash"])
            if distance <= NEAR_DUPLICATE_IMAGE_HASH_DISTANCE:
                return _similarity_result(distance, "perceptual hash distance")
            return None

        if kind == "audio":
            if not _durations_are_close(left["duration"], right["duration"]):
                return None
            distance = _hamming_distance(left["hash"], right["hash"])
            if distance <= NEAR_DUPLICATE_AUDIO_HASH_DISTANCE:
                return _similarity_result(
                    distance,
                    "audio fingerprint distance",
                    left["duration"],
                    right["duration"],
                )
            return None

        if kind == "video":
            if not _durations_are_close(left["duration"], right["duration"]):
                return None
            distance = _average_frame_distance(left["hashes"], right["hashes"])
            if distance <= NEAR_DUPLICATE_VIDEO_FRAME_HASH_DISTANCE:
                return _similarity_result(
                    distance,
                    "average frame hash distance",
                    left["duration"],
                    right["duration"],
                )
            return None

        return None

    def _group_reason(
        self, indexes: List[int], fingerprints: List[Dict]
    ) -> Tuple[str, str]:
        best = None
        for left_position, left_index in enumerate(indexes[:-1]):
            for right_index in indexes[left_position + 1 :]:
                result = self._similarity(
                    fingerprints[left_index], fingerprints[right_index]
                )
                if result and (best is None or result["distance"] < best["distance"]):
                    best = result

        if best is None:
            return "similar offline fingerprint", "medium"

        distance = float(best["distance"])
        confidence = (
            "high" if distance <= best["high_confidence_distance"] else "medium"
        )
        return best["reason"], confidence


def _empty_exact_report() -> Dict:
    return {
        "groups": [],
        "group_count": 0,
        "duplicate_file_count": 0,
        "reclaimable_size": 0,
        "skipped": _new_skip_counts(),
    }


def _empty_near_report() -> Dict:
    return {
        "groups": [],
        "group_count": 0,
        "reviewable_file_count": 0,
        "reviewable_size": 0,
        "skipped": _new_skip_counts(),
    }


def _new_skip_counts() -> Dict[str, int]:
    return {
        "empty": 0,
        "unsupported": 0,
        "too_large": 0,
        "too_small": 0,
        "unreadable": 0,
        "decode_error": 0,
        "dependency_missing": 0,
    }


def _sorted_files(files: List[Dict]) -> List[Dict]:
    return sorted(files, key=lambda file_info: str(file_info["path"]))


def _token_shingles(tokens: List[str], width: int = 3) -> List[str]:
    if len(tokens) < width:
        return tokens
    return [
        " ".join(tokens[index : index + width])
        for index in range(len(tokens) - width + 1)
    ]


def _simhash(terms: List[str]) -> int:
    weights = [0] * 64
    for term in terms:
        value = int.from_bytes(
            hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest(),
            "big",
        )
        for bit in range(64):
            if value & (1 << bit):
                weights[bit] += 1
            else:
                weights[bit] -= 1

    fingerprint = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            fingerprint |= 1 << bit
    return fingerprint


def _audio_fingerprint(samples, np) -> int:
    samples = np.nan_to_num(samples.astype("float32", copy=False))
    samples = samples - np.mean(samples)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0:
        samples = samples / peak

    rms_features = [
        float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
        for chunk in np.array_split(samples, 32)
    ]

    spectrum = np.abs(np.fft.rfft(samples[: min(samples.size, 65_536)]))
    spectral_features = [
        float(np.mean(chunk)) if chunk.size else 0.0
        for chunk in np.array_split(spectrum, 32)
    ]

    features = np.array(rms_features + spectral_features, dtype="float32")
    median = float(np.median(features))
    fingerprint = 0
    for bit, feature in enumerate(features):
        if feature > median:
            fingerprint |= 1 << bit
    return fingerprint


def _evenly_spaced_positions(total: int, count: int) -> List[int]:
    if count <= 1:
        return [0]
    return [
        int(round((total - 1) * index / (count - 1)))
        for index in range(count)
    ]


def _bucket_keys(fingerprint: Dict) -> List[Tuple]:
    kind = fingerprint["kind"]
    keys = []

    if kind in {"text", "image", "audio"}:
        hash_value = fingerprint["hash"]
        for band_key in _hash_band_keys(kind, hash_value):
            if kind == "audio":
                for duration_bucket in _duration_buckets(fingerprint["duration"]):
                    keys.append((*band_key, duration_bucket))
            else:
                keys.append(band_key)
        return keys

    if kind == "video":
        for frame_index, hash_value in enumerate(fingerprint["hashes"]):
            for band_key in _hash_band_keys(kind, hash_value):
                for duration_bucket in _duration_buckets(fingerprint["duration"]):
                    keys.append((*band_key, frame_index, duration_bucket))

    return keys


def _hash_band_keys(kind: str, hash_value: int) -> List[Tuple]:
    band_bits = NEAR_DUPLICATE_HASH_BAND_BITS
    mask = (1 << band_bits) - 1
    band_count = 64 // band_bits
    return [
        (kind, band_index, (hash_value >> (band_index * band_bits)) & mask)
        for band_index in range(band_count)
    ]


def _duration_buckets(duration: float) -> List[int]:
    bucket = int(duration // NEAR_DUPLICATE_DURATION_BUCKET_SECONDS)
    return [bucket - 1, bucket, bucket + 1]


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _durations_are_close(left: float, right: float) -> bool:
    tolerance = max(
        NEAR_DUPLICATE_DURATION_TOLERANCE_SECONDS,
        max(left, right) * NEAR_DUPLICATE_DURATION_TOLERANCE_RATIO,
    )
    return abs(left - right) <= tolerance


def _average_frame_distance(left_hashes: List[int], right_hashes: List[int]) -> float:
    if not left_hashes or not right_hashes:
        return float("inf")
    distances = [
        _hamming_distance(left_hash, right_hash)
        for left_hash, right_hash in zip(left_hashes, right_hashes)
    ]
    return sum(distances) / len(distances)


def _similarity_result(
    distance: float,
    label: str,
    left_duration: Optional[float] = None,
    right_duration: Optional[float] = None,
) -> Dict:
    reason = f"{label} {distance:.1f}/64"
    if left_duration is not None and right_duration is not None:
        reason = f"{reason}, duration diff {abs(left_duration - right_duration):.1f}s"
    return {
        "distance": distance,
        "reason": reason,
        "high_confidence_distance": 4.0,
    }


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            self.parent[left_root] = right_root
        elif self.rank[left_root] > self.rank[right_root]:
            self.parent[right_root] = left_root
        else:
            self.parent[right_root] = left_root
            self.rank[left_root] += 1
