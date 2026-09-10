from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from agent.cli import chat
from agent.config import read_client_url
from agent.jsonutil import dumps, loads


class Response:
    def __init__(self, value):
        self.value = dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, _size):
        return self.value


class ClientTests(unittest.TestCase):
    def test_client_port_from_dotenv_and_environment_without_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("PORT=8788\n", encoding="utf-8")
            self.assertEqual(read_client_url({}, env_file), "http://127.0.0.1:8788")
            self.assertEqual(read_client_url({"PORT": "8789"}, env_file), "http://127.0.0.1:8789")
        self.assertEqual(read_client_url({}, None), "http://127.0.0.1:8787")

    def test_retry_retains_original_request_and_custom_port_in_resume_command(self):
        sent = []

        def transport(request, **kwargs):
            if request.full_url.endswith("/sessions"):
                return Response({"id": "session-1"})
            sent.append(loads(request.data))
            if len(sent) == 1:
                raise URLError("simulated lost response")
            return Response({"trace": [], "answer": "完成", "status": "ok", "steps": 2, "replayed": True})

        output = io.StringIO()
        with patch("agent.cli.urlopen", side_effect=transport), patch("builtins.input", side_effect=["添加待办", "/retry", "/exit"]), redirect_stdout(output):
            chat(base_url="http://127.0.0.1:8789")
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], sent[1])
        self.assertIn("--url http://127.0.0.1:8789", output.getvalue())
        self.assertIn("幂等重放", output.getvalue())

    def test_switching_session_clears_pending_retry(self):
        message_attempts = []

        def transport(request, **kwargs):
            if request.full_url.endswith("/messages"):
                message_attempts.append(loads(request.data))
                raise URLError("simulated network error")
            return Response({"id": "session-2" if request.full_url.endswith("session-2") else "session-1"})

        output = io.StringIO()
        with patch("agent.cli.urlopen", side_effect=transport), patch("builtins.input", side_effect=["添加待办", "/use session-2", "/retry", "/exit"]), redirect_stdout(output):
            chat()
        self.assertEqual(len(message_attempts), 1)
        self.assertIn("没有网络失败", output.getvalue())
