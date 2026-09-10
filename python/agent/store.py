"""JSON session persistence shared with the Node.js implementation.

One SessionStore instance owns the locks for one server process. Hold ``lock``
around the entire read/modify/save turn, not just the final file write.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .errors import AgentError


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_id(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}", value) is None:
        raise AgentError("INVALID_ID", f"{label} must contain 1–128 letters, digits, or . _ : @ - and start with a letter or digit.")


def _timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _valid_history(messages):
    pending = set()
    last_was_final = True
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            return False
        role = message.get("role")
        if role not in ("user", "assistant", "tool") or (index == 0 and role != "user"):
            return False
        if role == "user":
            if pending or not last_was_final or not isinstance(message.get("content"), str) or "tool_calls" in message:
                return False
            last_was_final = False
        elif role == "assistant":
            if (pending or "content" not in message
                    or (message["content"] is not None and not isinstance(message["content"], str))
                    or ("tool_calls" in message and not isinstance(message["tool_calls"], list))):
                return False
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict):
                    return False
                call_id = call.get("id")
                function = call.get("function")
                if (call.get("type") != "function" or not isinstance(call_id, str) or not call_id
                        or call_id in pending or not isinstance(function, dict)
                        or not isinstance(function.get("name"), str) or not function["name"]
                        or not isinstance(function.get("arguments"), str)):
                    return False
                pending.add(call_id)
            last_was_final = not pending and isinstance(message["content"], str)
        else:
            call_id = message.get("tool_call_id")
            if (not isinstance(message.get("content"), str) or "tool_calls" in message
                    or not isinstance(call_id, str) or call_id not in pending):
                return False
            pending.remove(call_id)
            last_was_final = False
    return not pending and last_was_final


def validate_session(session):
    if not isinstance(session, dict):
        raise AgentError("CORRUPT_SESSION", "Session must be an object.")
    validate_id(session.get("userId"), "userId")
    validate_id(session.get("id"), "sessionId")
    if (not isinstance(session.get("title"), str) or len(session["title"]) > 200
            or not isinstance(session.get("summary"), str)
            or not isinstance(session.get("messages"), list)
            or not isinstance(session.get("todos"), list)
            or not isinstance(session.get("completedRequests"), dict)
            or not _timestamp(session.get("createdAt")) or not _timestamp(session.get("updatedAt"))):
        raise AgentError("CORRUPT_SESSION", "Session has invalid or missing fields.")
    valid_todos = all(
        isinstance(todo, dict) and isinstance(todo.get("id"), str) and bool(todo["id"])
        and isinstance(todo.get("text"), str) and type(todo.get("done")) is bool
        and ("createdAt" not in todo or _timestamp(todo["createdAt"]))
        for todo in session["todos"]
    )
    if (not _valid_history(session["messages"]) or not valid_todos
            or any(not isinstance(record, dict) for record in session["completedRequests"].values())):
        raise AgentError("CORRUPT_SESSION", "Session contains an invalid message, todo, or replay record.")
    return session


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


class SessionStore:
    def __init__(self, data_dir):
        if not isinstance(data_dir, (str, os.PathLike)) or not str(data_dir):
            raise TypeError("data_dir is required.")
        self.data_dir = Path(data_dir).resolve()
        self.locks = {}

    def file_path(self, user_id, session_id):
        validate_id(user_id, "userId")
        validate_id(session_id, "sessionId")
        user_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return self.data_dir / f"{user_hash}-{session_hash}.json"

    def _read_existing(self, file, user_id, session_id=None):
        try:
            raw = file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            session = validate_session(json.loads(raw, parse_constant=_invalid_constant))
            if (session["userId"] != user_id or (session_id is not None and session["id"] != session_id)
                    or self.file_path(session["userId"], session["id"]) != file):
                raise ValueError("Session identity does not match its storage key.")
            return session
        except (AgentError, ValueError, TypeError, KeyError) as exc:
            raise AgentError("CORRUPT_SESSION", "Stored session is corrupt; the file has been preserved.") from exc

    async def create(self, user_id, title="New conversation"):
        validate_id(user_id, "userId")
        if not isinstance(title, str) or len(title) > 200:
            raise AgentError("INVALID_TITLE", "title must be a string of at most 200 characters.")
        now = utc_now()
        session = {
            "id": str(uuid4()), "userId": user_id, "title": title.strip() or "New conversation",
            "createdAt": now, "updatedAt": now, "messages": [], "summary": "", "todos": [],
            "completedRequests": {},
        }
        await self.save(session)
        return session

    async def get(self, user_id, session_id):
        file = self.file_path(user_id, session_id)
        session = await asyncio.to_thread(self._read_existing, file, user_id, session_id)
        if session is None:
            raise AgentError("SESSION_NOT_FOUND", "Session was not found for this user.")
        return session

    def _list(self, user_id):
        if not self.data_dir.exists():
            return []
        prefix = hashlib.sha256(user_id.encode("utf-8")).hexdigest() + "-"
        sessions = []
        for file in self.data_dir.iterdir():
            if file.name.startswith(prefix) and file.name.endswith(".json"):
                session = self._read_existing(file, user_id)
                if session is not None:
                    sessions.append(session)
        return sorted(sessions, key=lambda session: session["updatedAt"], reverse=True)

    async def list(self, user_id):
        validate_id(user_id, "userId")
        return await asyncio.to_thread(self._list, user_id)

    def _save(self, session):
        validate_session(session)
        file = self.file_path(session["userId"], session["id"])
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # An invalid existing file is diagnostic evidence and must be preserved.
        self._read_existing(file, session["userId"], session["id"])
        session["updatedAt"] = utc_now()
        payload = json.dumps(session, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        temp = file.with_name(f"{file.name}.{uuid4()}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, file)
        finally:
            temp.unlink(missing_ok=True)
        return session

    async def save(self, session):
        # Do not release a surrounding session lock while an uncancellable file
        # worker is still writing after its caller receives cancellation.
        operation = asyncio.create_task(asyncio.to_thread(self._save, session))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            await operation
            raise

    @asynccontextmanager
    async def lock(self, user_id, session_id):
        key = self.file_path(user_id, session_id)
        entry = self.locks.get(key)
        if entry is None:
            entry = {"lock": asyncio.Lock(), "users": 0}
            self.locks[key] = entry
        entry["users"] += 1
        acquired = False
        try:
            await entry["lock"].acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry["lock"].release()
            entry["users"] -= 1
            if entry["users"] == 0:
                self.locks.pop(key, None)
