"""Regression tests for the iBroadcast bridge's error reporting.

Run from the repository root with: python3 -m unittest discover -s tests
"""

import json
import sys
import tempfile
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge  # noqa: E402

REJECTED_REFRESH = {
    "error": "invalid_grant",
    "error_description": "Invalid grant: refresh token is invalid",
}


def expired_token_file(directory: str) -> Path:
    """Write a token file whose access token has already expired."""
    path = Path(directory) / "token.json"
    path.write_text(json.dumps({
        "access_token": "a" * 64, "refresh_token": "r" * 64,
        "token_type": "Bearer", "expires_in": 3600,
        "expires_at": time.time() - 3600,
    }), encoding="utf-8")
    return path


class RefreshErrorTest(unittest.TestCase):
    """A rejected refresh token must say why and what to do."""

    def test_rejected_refresh_names_the_oauth_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            library = bridge.Library("client", expired_token_file(directory))
            with mock.patch.object(bridge, "token_request",
                                   return_value=REJECTED_REFRESH):
                with self.assertRaises(ValueError) as caught:
                    library.access_token()
        message = str(caught.exception)
        self.assertIn("invalid_grant", message)
        self.assertIn("authorize", message)
        self.assertNotIn("r" * 64, message)


class HandlerLoggingTest(unittest.TestCase):
    """A 502 from the bridge must log the underlying error."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        self.server.library = bridge.Library(
            "client", expired_token_file(self.directory.name)
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.directory.cleanup()

    def request(self, path: str) -> tuple[int, list[str]]:
        """Fetch a path and return the status with any logged errors."""
        logged = []
        original = bridge.Handler.log_error

        def capture(handler, format_string, *args):
            logged.append(format_string % args)
            original(handler, format_string, *args)

        port = self.server.server_address[1]
        with mock.patch.object(bridge, "token_request",
                               return_value=REJECTED_REFRESH), \
                mock.patch.object(bridge.Handler, "log_error", capture):
            try:
                with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                    return response.status, logged
            except HTTPError as error:
                error.close()
                return error.code, logged

    def test_library_route_logs_rejected_refresh(self) -> None:
        status, logged = self.request("/artists")
        self.assertEqual(status, 502)
        self.assertTrue(any("invalid_grant" in line for line in logged), logged)

    def test_browse_route_logs_rejected_refresh(self) -> None:
        status, logged = self.request("/browse?mode=genre")
        self.assertEqual(status, 502)
        self.assertTrue(any("invalid_grant" in line for line in logged), logged)


if __name__ == "__main__":
    unittest.main()
