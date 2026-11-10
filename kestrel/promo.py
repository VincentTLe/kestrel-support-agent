"""Night Owl promotion: the window check, code generation and one-code-per-email
are all enforced here. The model can only ask; it cannot issue."""

import json
import os
import secrets
from datetime import datetime, time
from pathlib import Path

from .clock import EASTERN
from .orders import normalize_email

WINDOW_START = time(21, 0)  # 9:00 PM Eastern; the window runs to midnight
WINDOW_TEXT = "9:00 PM to 11:59 PM Eastern Time, every night"
DISCOUNT_PERCENT = 15
CODE_PREFIX = "NIGHTOWL-"
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read aloud


def in_window(now: datetime) -> bool:
    return now.astimezone(EASTERN).time() >= WINDOW_START


class PromoStore:
    """email -> code, kept in a JSON file so a code survives a restart."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._codes: dict[str, dict] = {}
        if self._path.exists():
            try:
                self._codes = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                # Starting empty would hand every email a second code.
                raise ValueError(f"Promo store {self._path} is not valid JSON: {error}") from error

    def get(self, email: str) -> str | None:
        record = self._codes.get(normalize_email(email))
        return record["code"] if record else None

    def issue(self, email: str, now: datetime) -> str:
        taken = {record["code"] for record in self._codes.values()}
        while True:
            code = CODE_PREFIX + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
            if code not in taken:
                break
        self._codes[normalize_email(email)] = {"code": code, "issued_at": now.isoformat()}
        self._save()
        return code

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._codes, indent=2), encoding="utf-8")
        os.replace(temp, self._path)  # atomic: a crash never leaves a half-written file


def request_code(store: PromoStore, email: str, now: datetime) -> dict:
    """Give `email` its Night Owl code if the window is open."""
    if not in_window(now):
        return {
            "status": "outside_window",
            "window": WINDOW_TEXT,
            "current_time_eastern": now.astimezone(EASTERN).strftime("%I:%M %p").lstrip("0"),
        }
    existing = store.get(email)
    code = existing or store.issue(email, now)
    return {
        "status": "existing" if existing else "issued",
        "code": code,
        "discount_percent": DISCOUNT_PERCENT,
        "email": normalize_email(email),
    }
