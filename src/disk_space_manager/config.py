"""Configuration constants and settings for the disk cleaner."""

from pathlib import Path
from datetime import timedelta

# Default age threshold for files to be considered "old" (6 months)
DEFAULT_AGE_THRESHOLD_MONTHS = 6
DEFAULT_AGE_THRESHOLD = timedelta(days=DEFAULT_AGE_THRESHOLD_MONTHS * 30)

# Common Unix-like cache directory patterns
CACHE_DIRECTORY_PATTERNS = [
    "**/Library/Caches/**",
    "**/Library/Application Support/**/Cache/**",
    "**/.cache/**",
    "**/tmp/**",
    "**/var/tmp/**",
    "**/var/folders/**",
    "**/.local/share/Trash/**",
]

# Cache file extensions
CACHE_FILE_EXTENSIONS = [
    ".cache",
    ".tmp",
    ".temp",
    ".log",
    ".old",
    ".bak",
]

# Directories to exclude from scanning
EXCLUDED_DIRECTORIES = [
    "/System",
    "/Library/Application Support/App Store",
    "/Library/Application Support/Apple",
    "/private",
    "/dev",
    "/proc",
    "/sys",
    "/Volumes",
    "/.Trash",
    "/.fseventsd",
    "/.Spotlight-V100",
    "/.TemporaryItems",
    "/.DocumentRevisions-V100",
]

# Directories to exclude from user home
USER_EXCLUDED_DIRECTORIES = [
    "Library/Application Support/App Store",
    "Library/Application Support/Apple",
    "Library/Application Support/CallHistoryDB",
    "Library/Application Support/com.apple.TCC",
]

# Minimum file size to consider for moving (1 MB)
MIN_FILE_SIZE_TO_MOVE = 1024 * 1024

# Duplicate and near-duplicate detection settings
DUPLICATE_HASH_CHUNK_SIZE = 1024 * 1024
DUPLICATE_DISPLAY_LIMIT = 10
DUPLICATE_GROUP_FILE_DISPLAY_LIMIT = 6
DUPLICATE_PROGRESS_BATCH_SIZE = 100

NEAR_DUPLICATE_TEXT_MAX_BYTES = 2 * 1024 * 1024
NEAR_DUPLICATE_IMAGE_MAX_BYTES = 50 * 1024 * 1024
NEAR_DUPLICATE_VIDEO_MAX_BYTES = 250 * 1024 * 1024
NEAR_DUPLICATE_AUDIO_MAX_BYTES = 100 * 1024 * 1024
NEAR_DUPLICATE_AUDIO_MAX_SAMPLES = 262_144
NEAR_DUPLICATE_AUDIO_SAMPLE_WINDOWS = 6
NEAR_DUPLICATE_VIDEO_SAMPLE_FRAMES = 5
NEAR_DUPLICATE_HASH_BAND_BITS = 8
NEAR_DUPLICATE_DURATION_BUCKET_SECONDS = 5

NEAR_DUPLICATE_TEXT_MIN_TOKENS = 12
NEAR_DUPLICATE_TEXT_SIMHASH_DISTANCE = 8
NEAR_DUPLICATE_IMAGE_HASH_DISTANCE = 8
NEAR_DUPLICATE_AUDIO_HASH_DISTANCE = 10
NEAR_DUPLICATE_VIDEO_FRAME_HASH_DISTANCE = 10
NEAR_DUPLICATE_DURATION_TOLERANCE_SECONDS = 2.0
NEAR_DUPLICATE_DURATION_TOLERANCE_RATIO = 0.08

# Action log file
ACTION_LOG_FILE = Path.home() / ".disk-space-manager-actions.log"
