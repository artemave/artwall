import contextlib
import io
import unittest

from artwall.__main__ import main


class ArgGuards(unittest.TestCase):
    def test_min_interval_without_throttle_is_rejected(self):
        # --min-interval only tunes the --throttle check, so on its own it would be
        # a silent no-op; argparse should exit(2) instead of quietly running.
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(stderr):
            main(["--min-interval", "5"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--min-interval has no effect without --throttle", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
