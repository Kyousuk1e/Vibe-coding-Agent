"""Per-session JSONL diagnostics without credentials or hidden reasoning."""

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from .jsonutil import dumps


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def preview(value, limit=800):
    try:
        text = value if isinstance(value, str) else dumps(value)
    except (TypeError, ValueError):
        text = "[unserializable]"
    return text if len(text) <= limit else text[:limit] + " … [truncated]"


class TraceWriter:
    def __init__(self, data_dir):
        self.directory = Path(data_dir).resolve() / "traces"

    async def write(self, user_id, session_id, events):
        def append():
            self.directory.mkdir(parents=True, exist_ok=True)
            key = "-".join(hashlib.sha256(x.encode()).hexdigest() for x in (user_id, session_id))
            with (self.directory / f"{key}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write("".join(dumps(event) + "\n" for event in events))
        await asyncio.to_thread(append)
