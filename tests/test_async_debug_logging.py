from __future__ import annotations

import io
import threading
import time
import unittest
from unittest import mock

from tools.uml_speech_harness import load_uml_module


class AsyncDebugLoggingTest(unittest.TestCase):
    def test_slow_log_file_does_not_block_log_producer(self):
        module, _ = load_uml_module()
        open_started = threading.Event()
        allow_open = threading.Event()

        def slow_open(*args, **kwargs):
            open_started.set()
            allow_open.wait(timeout=1.0)
            return io.StringIO()

        with mock.patch("builtins.open", side_effect=slow_open):
            module._start_debug_log_writer()
            try:
                module._write_debug_log("first")
                self.assertTrue(open_started.wait(timeout=0.5))

                started = time.perf_counter()
                module._write_debug_log("second")
                elapsed = time.perf_counter() - started

                self.assertLess(elapsed, 0.05)
            finally:
                allow_open.set()
                module._stop_debug_log_writer()


if __name__ == "__main__":
    unittest.main()
