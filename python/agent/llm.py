"""Real standard-library HTTP client with bounded retries and sanitized errors.

transport(url, *, headers, body, timeout) may be sync or async and returns
(status, response_headers, body_bytes). timeout and injected sleep use seconds.
The default urllib transport runs in a worker thread. asyncio timeout/cancellation
discards its late result; Python cannot forcibly stop that worker thread or revoke
an HTTP request already accepted by the provider.
"""
import asyncio
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import inspect
import json
import math
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Config
from .errors import AgentError
from .jsonutil import dumps

MAX_RESPONSE_BYTES = 1_048_576


class _ResponseTooLarge(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(base_url):
    try:
        if not isinstance(base_url, str) or any(ord(char) < 32 for char in base_url):
            raise ValueError
        url = urlsplit(base_url)
        hostname = url.hostname
        _ = url.port  # Validate a malformed/non-numeric port before issuing HTTP.
        loopback = hostname in ("localhost", "127.0.0.1", "::1")
        if not hostname or (url.scheme != "https" and not (url.scheme == "http" and loopback)) or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError
        return urlunsplit((url.scheme, url.netloc, url.path.rstrip("/") + "/chat/completions", "", ""))
    except (ValueError, TypeError):
        raise AgentError("LLM_CONFIG", "LLM URL must use HTTPS (HTTP only for localhost), without credentials, query, or fragment.") from None


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise AgentError("LLM_CONFIG", f"{name} must be an integer between {minimum} and {maximum}.")


def _default_transport(url, *, headers, body, timeout):
    deadline = time.monotonic() + timeout
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        response = build_opener(_NoRedirect()).open(request, timeout=timeout)
    except HTTPError as error:
        # Error bodies are neither read nor propagated, including redirect responses.
        with error:
            return error.code, dict(error.headers.items()), b""
    with response:
        response_headers = dict(response.headers.items())
        try:
            declared_size = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            declared_size = 0
        if declared_size > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge()
        chunks, size = [], 0
        read = getattr(response, "read1", response.read)
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError()
            chunk = read(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _ResponseTooLarge()
        return response.status, response_headers, b"".join(chunks)


def _retry_delay(header, attempt):
    if header:
        try:
            seconds = float(header)
            if math.isfinite(seconds):
                return min(5.0, max(0.0, seconds))
        except (ValueError, TypeError):
            pass
        try:
            when = parsedate_to_datetime(header)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return min(5.0, max(0.0, (when - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            pass
    return min(0.25 * 2 ** attempt, 2.0)


def _reject_constant(_value):
    raise ValueError()


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError()
    return number


class ChatClient:
    def __init__(self, config: Config, transport=None, sleep=None):
        if not isinstance(config.api_key, str) or not config.api_key.strip() or "\r" in config.api_key or "\n" in config.api_key:
            raise AgentError("LLM_CONFIG", "A nonempty server-side LLM API key is required.")
        if config.provider not in ("qwen", "openai"):
            raise AgentError("LLM_CONFIG", "LLM provider must be qwen or openai.")
        if not isinstance(config.model, str) or not config.model.strip() or len(config.model) > 200:
            raise AgentError("LLM_CONFIG", "A valid LLM model is required.")
        _integer(config.timeout_ms, "timeout_ms", 1, 120000)
        _integer(config.max_retries, "max_retries", 0, 5)
        _integer(config.max_output_tokens, "max_output_tokens", 1, 128000)
        if transport is not None and not callable(transport):
            raise AgentError("LLM_CONFIG", "transport must be callable.")
        if sleep is not None and not callable(sleep):
            raise AgentError("LLM_CONFIG", "sleep must be callable.")
        self.endpoint = _endpoint(config.base_url)
        self.provider, self.model = config.provider, config.model
        self.timeout_ms, self.max_retries = config.timeout_ms, config.max_retries
        self.max_output_tokens = config.max_output_tokens
        self._api_key = config.api_key.strip()
        self._transport = transport or _default_transport
        self._sleep = sleep or asyncio.sleep

    async def _request(self, body):
        kwargs = {
            "headers": {"Content-Type": "application/json", "Accept-Encoding": "identity", "Authorization": f"Bearer {self._api_key}"},
            "body": body, "timeout": self.timeout_ms / 1000,
        }
        if inspect.iscoroutinefunction(self._transport):
            return await self._transport(self.endpoint, **kwargs)
        result = await asyncio.to_thread(self._transport, self.endpoint, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def complete(self, *, messages, tools=None):
        if tools is None:
            tools = []
        if not isinstance(messages, list) or not messages or not isinstance(tools, list):
            raise AgentError("LLM_CONFIG", "messages must be nonempty and tools must be an array.")
        body = {"model": self.model, "messages": messages, "stream": False}
        if self.provider == "qwen":
            body.update(max_tokens=self.max_output_tokens, enable_thinking=False)
        else:
            body["max_completion_tokens"] = self.max_output_tokens
        try:
            if tools:
                body["tools"] = deepcopy(tools)
                if self.provider == "qwen":
                    for tool in body["tools"]:
                        if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
                            tool["function"].pop("strict", None)
                body.update(tool_choice="auto", parallel_tool_calls=False)
            encoded = dumps(body).encode("utf-8")
        except (TypeError, ValueError, RecursionError, UnicodeError):
            raise AgentError("LLM_CONFIG", "LLM request could not be serialized.") from None
        for attempt in range(self.max_retries + 1):
            retryable, retry_after = False, None
            try:
                raw = await asyncio.wait_for(self._request(encoded), self.timeout_ms / 1000)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, socket.timeout):
                error = AgentError("LLM_TIMEOUT", "LLM request timed out.")
                retryable = True
            except _ResponseTooLarge:
                error = AgentError("LLM_PROTOCOL", "LLM response exceeded the size limit.")
            except Exception as caught:
                timed_out = isinstance(caught, URLError) and isinstance(caught.reason, (TimeoutError, socket.timeout))
                error = AgentError("LLM_TIMEOUT" if timed_out else "LLM_NETWORK", "LLM request timed out." if timed_out else "LLM network request failed.")
                retryable = True
            else:
                if not isinstance(raw, (tuple, list)) or len(raw) != 3:
                    raise AgentError("LLM_PROTOCOL", "LLM transport returned an invalid response.")
                status, headers, response_body = raw
                if type(status) is not int or not 100 <= status <= 599 or not isinstance(headers, Mapping):
                    raise AgentError("LLM_PROTOCOL", "LLM transport returned an invalid response.")
                headers = {str(name).lower(): str(value) for name, value in headers.items()}
                if not 200 <= status < 300:
                    retry_after = headers.get("retry-after")
                    retryable = status == 429 or status >= 500
                    code = "LLM_AUTH" if status in (401, 403) else "LLM_RATE_LIMIT" if status == 429 else "LLM_HTTP"
                    error = AgentError(code, f"LLM request failed with HTTP {status}.", status=status)
                else:
                    try:
                        declared_size = int(headers.get("content-length", "0"))
                    except ValueError:
                        declared_size = 0
                    if not isinstance(response_body, bytes) or len(response_body) > MAX_RESPONSE_BYTES or declared_size > MAX_RESPONSE_BYTES:
                        raise AgentError("LLM_PROTOCOL", "LLM response is invalid or exceeded the size limit.")
                    try:
                        parsed = json.loads(response_body.decode("utf-8"), parse_constant=_reject_constant, parse_float=_finite_float)
                    except (UnicodeError, ValueError, RecursionError):
                        raise AgentError("LLM_PROTOCOL", "LLM returned invalid JSON.") from None
                    if not isinstance(parsed, dict):
                        raise AgentError("LLM_PROTOCOL", "LLM response must be a JSON object.")
                    return parsed
            if not retryable or attempt == self.max_retries:
                raise error from None
            delayed = self._sleep(_retry_delay(retry_after, attempt))
            if inspect.isawaitable(delayed):
                await delayed
