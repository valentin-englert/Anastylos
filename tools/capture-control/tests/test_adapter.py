"""SDK binding check: nine tools when `mcp` is installed.

The `mcp==2.3.0` install needs CONTRACT-OWNER manifest/SPDX approval,
so this test skips with an explicit reason until then. Handler logic
is covered without the SDK in test_handlers.py.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import mcp  # noqa: F401
except ImportError:
    mcp = None


class AdapterTests(unittest.TestCase):
    def test_nine_tools_registered(self):
        if mcp is None:
            self.skipTest(
                "mcp SDK not installed; needs CONTRACT-OWNER "
                "manifest/SPDX approval + pip install mcp==2.3.0")
        import adapter

        self.assertEqual(len(adapter.mcp._tools), 9)


if __name__ == "__main__":
    unittest.main()
