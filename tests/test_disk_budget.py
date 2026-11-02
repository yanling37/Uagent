from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from portable_ai.disk_budget import directory_bytes, report

class DiskBudgetTests(unittest.TestCase):
    def test_counts_categories(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'toolchains').mkdir()
            (root/'toolchains'/'java.zip').write_bytes(b'x'*1024)
            data=report(root)
            self.assertEqual(data['categories']['toolchains'], 1024)
            self.assertGreater(data['volume_total'], 0)
            self.assertGreaterEqual(data['volume_free'], 0)

    def test_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as base:
            root=Path(base)
            (root/'target').write_bytes(b'x'*2048)
            (root/'workspace').mkdir()
            try:
                (root/'workspace'/'link').symlink_to(root/'target')
            except (OSError, NotImplementedError):
                self.skipTest('symlinks not available')
            size, partial=directory_bytes(root/'workspace')
            self.assertEqual(size,0)
            self.assertFalse(partial)
