"""Portable storage usage report; uses the volume where this app is running."""
from __future__ import annotations
import os
import shutil
from pathlib import Path

CATEGORIES = ('toolchains', 'memory', 'plugins', 'workspace', 'data', 'backups', 'logs')
MIN_FREE_BYTES = 8 * 1024**3

def directory_bytes(directory: Path, max_files: int = 150_000) -> tuple[int, bool]:
    """Count bytes without following symlinks. Truncated flag protects UI from unbounded walks."""
    if not directory.exists():
        return 0, False
    total, count = 0, 0
    for walk_root, folders, filenames in os.walk(directory, followlinks=False):
        folders[:] = [n for n in folders if not (Path(walk_root)/n).is_symlink()]
        for filename in filenames:
            entry = Path(walk_root) / filename
            if entry.is_symlink():
                continue
            try:
                total += entry.stat().st_size
            except (OSError, PermissionError):
                continue
            count += 1
            if count >= max_files:
                return total, True
    return total, False

def report(root: Path) -> dict:
    root = Path(root).resolve()
    volume = shutil.disk_usage(root)
    per_folder = {}
    partial = []
    for category in CATEGORIES:
        size, cut = directory_bytes(root/category)
        per_folder[category] = size
        if cut:
            partial.append(category)
    return {
        'volume_total': volume.total, 'volume_used': volume.used,
        'volume_free': volume.free, 'low_space': volume.free < MIN_FREE_BYTES,
        'categories': per_folder, 'partial_counts': partial
    }
