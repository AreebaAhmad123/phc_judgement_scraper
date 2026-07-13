"""processed_ids.json — local + S3 sync."""
import json
import os
import tempfile
from typing import Any

from . import config
from .logging_setup import logger

STATE_VERSION = 1


class ProcessedState:
    def __init__(self, path: str | None = None):
        self.path = path or config.PROCESSED_STATE_PATH
        self._data: dict[str, Any] = {"version": STATE_VERSION, "judgments": {}}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Unreadable processed state %s (%s); starting fresh.", self.path, exc)
            self._data = {"version": STATE_VERSION, "judgments": {}}

    def get(self, stable_id: str) -> dict[str, Any] | None:
        return self._data.get("judgments", {}).get(stable_id)

    def mark_complete(
        self,
        stable_id: str,
        file_name: str,
        court_citation: str | None,
    ) -> None:
        self._data.setdefault("judgments", {})[stable_id] = {
            "fileName": file_name,
            "court_citation": court_citation,
            "status": "complete",
        }

    def update_citation(self, stable_id: str, court_citation: str | None) -> None:
        entry = self._data.setdefault("judgments", {}).get(stable_id)
        if entry:
            entry["court_citation"] = court_citation

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def to_dict(self) -> dict[str, Any]:
        return self._data