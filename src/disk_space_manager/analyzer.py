"""File analysis and categorization."""

import os
import re
import fnmatch
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable

from .config import (
    DEFAULT_AGE_THRESHOLD,
    CACHE_DIRECTORY_PATTERNS,
    CACHE_FILE_EXTENSIONS,
    MIN_FILE_SIZE_TO_MOVE,
)
from .utils import format_size

# Pre-compile cache directory regex patterns (avoids per-file fnmatch overhead)
_CACHE_DIR_COMPILED = [re.compile(fnmatch.translate(p)) for p in CACHE_DIRECTORY_PATTERNS]

# Quick substring markers for early rejection before expensive regex
_CACHE_DIR_MARKERS = (
    'Library/Caches',
    '.cache/',
    '/tmp/',
    'var/tmp/',
    'var/folders/',
    '.local/share/Trash/',
    '/Cache/',
)

# Frozen set for O(1) extension lookup instead of O(n) list scan
_CACHE_EXT_SET = frozenset(e.lower() for e in CACHE_FILE_EXTENSIONS)

# Cache name substrings
_CACHE_NAME_SUBS = ('cache', 'tmp', 'temp', '.log')


class FileAnalyzer:
    """Analyzes files and categorizes them."""
    
    def __init__(self, age_threshold: Optional[timedelta] = None):
        """Initialize analyzer.
        
        Args:
            age_threshold: Age threshold for considering files old (default: 6 months)
        """
        self.age_threshold = age_threshold or DEFAULT_AGE_THRESHOLD
        self.now = datetime.now()
        self._now_ts = self.now.timestamp()
    
    def find_cache_files(self, files: List[Dict], progress_callback: Optional[Callable] = None) -> List[Dict]:
        """Identify cache files that can be safely removed."""
        cache_files = []
        total = len(files)
        cache_exts = _CACHE_EXT_SET
        markers = _CACHE_DIR_MARKERS
        compiled = _CACHE_DIR_COMPILED
        name_subs = _CACHE_NAME_SUBS
        
        for i, file_info in enumerate(files, 1):
            if progress_callback and i % 5000 == 0:
                progress_callback(i)
            
            path_str = file_info['path']
            
            # Quick marker check before expensive regex for cache dirs
            is_cache_dir = False
            if any(m in path_str for m in markers):
                is_cache_dir = any(p.match(path_str) for p in compiled)
            
            # O(1) extension check with frozenset
            _, ext = os.path.splitext(path_str)
            is_cache_ext = ext.lower() in cache_exts
            
            # Filename pattern check using os.path (avoids Path object creation)
            basename_lower = os.path.basename(path_str).lower()
            is_cache_name = any(s in basename_lower for s in name_subs)
            
            if is_cache_dir or is_cache_ext or is_cache_name:
                cache_files.append({
                    **file_info,
                    'reason': self._get_cache_reason(is_cache_dir, is_cache_ext, is_cache_name)
                })
        
        if progress_callback and total > 0:
            progress_callback(total)
        
        return cache_files
    
    def _get_cache_reason(self, is_dir: bool, is_ext: bool, is_name: bool) -> str:
        """Get reason why file is considered cache."""
        reasons = []
        if is_dir:
            reasons.append("cache directory")
        if is_ext:
            reasons.append("cache extension")
        if is_name:
            reasons.append("cache in name")
        return ", ".join(reasons) if reasons else "cache pattern"
    
    def find_old_files(self, files: List[Dict], min_size: int = MIN_FILE_SIZE_TO_MOVE, progress_callback: Optional[Callable] = None) -> List[Dict]:
        """Find files that haven't been accessed in the threshold period."""
        old_files = []
        cutoff_ts = (self.now - self.age_threshold).timestamp()
        now_ts = self._now_ts
        total = len(files)
        
        for i, file_info in enumerate(files, 1):
            if progress_callback and i % 5000 == 0:
                progress_callback(i)
            if file_info['size'] < min_size:
                continue
            
            # Compare raw float timestamps (avoids datetime creation per file)
            atime = file_info['atime']
            if atime < cutoff_ts:
                days_old = int((now_ts - atime) / 86400)
                old_files.append({
                    **file_info,
                    'days_old': days_old,
                    'age_category': self._categorize_age(days_old),
                    'accessed': datetime.fromtimestamp(atime),
                })
        
        if progress_callback and total > 0:
            progress_callback(total)
        
        old_files.sort(key=lambda x: x['size'], reverse=True)
        return old_files
    
    def _categorize_age(self, days: int) -> str:
        """Categorize file age."""
        if days < 180:
            return "6 months"
        elif days < 365:
            return "1 year"
        elif days < 730:
            return "2 years"
        else:
            return "very old"
    
    def analyze_disk_usage(self, files: List[Dict], directories: Dict) -> Dict:
        """Analyze overall disk usage and provide insights."""
        total_size = sum(f['size'] for f in files)
        file_count = len(files)
        
        # Group by extension
        by_extension = {}
        for file_info in files:
            _, ext = os.path.splitext(file_info['path'])
            ext = ext.lower() or 'no extension'
            bucket = by_extension.get(ext)
            if bucket is None:
                bucket = {'count': 0, 'size': 0}
                by_extension[ext] = bucket
            bucket['count'] += 1
            bucket['size'] += file_info['size']
        
        # Sort extensions by size
        top_extensions = sorted(
            by_extension.items(),
            key=lambda x: x[1]['size'],
            reverse=True
        )[:10]
        
        return {
            'total_size': total_size,
            'total_size_formatted': format_size(total_size),
            'file_count': file_count,
            'top_extensions': top_extensions,
            'average_file_size': total_size / file_count if file_count > 0 else 0,
        }
    
    def calculate_potential_savings(
        self,
        cache_files: List[Dict],
        old_files: List[Dict],
        exact_duplicate_reclaimable_size: int = 0,
        exact_duplicate_file_count: int = 0,
    ) -> Dict:
        """Calculate potential disk space savings."""
        cache_size = sum(f['size'] for f in cache_files)
        old_files_size = sum(f['size'] for f in old_files)
        total_savings = cache_size + old_files_size + exact_duplicate_reclaimable_size
        
        return {
            'cache_size': cache_size,
            'cache_size_formatted': format_size(cache_size),
            'cache_file_count': len(cache_files),
            'old_files_size': old_files_size,
            'old_files_size_formatted': format_size(old_files_size),
            'old_files_count': len(old_files),
            'exact_duplicate_reclaimable_size': exact_duplicate_reclaimable_size,
            'exact_duplicate_reclaimable_size_formatted': format_size(
                exact_duplicate_reclaimable_size
            ),
            'exact_duplicate_file_count': exact_duplicate_file_count,
            'total_savings': total_savings,
            'total_savings_formatted': format_size(total_savings),
        }
