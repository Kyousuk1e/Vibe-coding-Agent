"""Exercise the actual Python serve/chat entrypoints in separate processes."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import URLError
from urllib.request import urlopen


class EntrypointTests(unittest.TestCase):
    def test_server_and_cli_use_same_custom_port_without_model_calls(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="agent-entrypoint-") as data_dir:
            env = {**os.environ, "PORT": str(port), "DATA_DIR": data_dir,
                   "LLM_PROVIDER": "qwen", "DASHSCOPE_API_KEY": "offline-entrypoint-key",
                   "LLM_BASE_URL": "http://127.0.0.1:1", "LLM_MODEL": "offline",
                   "PYTHONIOENCODING": "utf-8"}
            server = subprocess.Popen([sys.executable, "-m", "agent", "serve"], cwd=root, env=env,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 8
                while True:
                    if server.poll() is not None:
                        self.fail("Server entrypoint exited before listening")
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                            self.assertEqual(json.load(response), {"ok": True})
                        break
                    except (URLError, OSError):
                        if time.monotonic() >= deadline:
                            self.fail("Server did not become ready")
                        time.sleep(0.03)
                client = subprocess.run([sys.executable, "-m", "agent", "chat", "--user", "A"],
                                        cwd=root, env=env, input="/sessions\n/todos\n/exit\n",
                                        text=True, encoding="utf-8", capture_output=True, timeout=8)
                self.assertEqual(client.returncode, 0, client.stderr)
                self.assertIn(f"--url http://127.0.0.1:{port}", client.stdout)
                files = list(Path(data_dir).glob("*.json"))
                self.assertEqual(len(files), 1)
                session = json.loads(files[0].read_text(encoding="utf-8"))
                self.assertEqual(session["userId"], "A")
                self.assertEqual(session["messages"], [])
                self.assertEqual(session["todos"], [])
            finally:
                server.terminate()
                try:
                    server.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate(timeout=5)
