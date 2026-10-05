"""Configuration: every knob lives in the project-root `.env` (see `.env`)."""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

_RATING_RANGE = re.compile(r"^(?P<lo>\d*)-(?P<hi>\d*)$")
_RATING_EXACT = re.compile(r"^\d+$")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = _str(name)
    return float(raw) if raw else default


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    problem_root: Path
    tags: str
    limit: int
    jobs: int
    max_attempts: int
    proxy: str
    fetch_delay: float
    fetch_retries: int
    fetch_timeout: float
    user_agent: str
    time_mult: float
    bench_mult: float
    status_reference: bool
    handle: str
    pi_bin: str
    pi_model: str
    pi_thinking: str
    pi_timeout: float
    pi_extra_args: tuple[str, ...]

    @classmethod
    def load(cls, env_file: Path | None = None) -> Settings:
        """Read `.env` (without clobbering real environment variables)."""
        load_dotenv(env_file or ROOT / ".env", override=False)
        root = ROOT
        problem_dir = _str("AI4CF_PROBLEM_DIR", "problem")
        return cls(
            root=root,
            problem_root=(root / problem_dir).resolve(),
            tags=_str("AI4CF_TAGS", "3500-"),
            limit=_int("AI4CF_LIMIT", 0),
            jobs=_int("AI4CF_JOBS", 1),
            max_attempts=_int("AI4CF_MAX_ATTEMPTS", 3),
            proxy=_str("AI4CF_PROXY"),
            fetch_delay=_float("AI4CF_FETCH_DELAY", 1.0),
            fetch_retries=_int("AI4CF_FETCH_RETRIES", 4),
            fetch_timeout=_float("AI4CF_FETCH_TIMEOUT", 30.0),
            user_agent=_str("AI4CF_USER_AGENT", DEFAULT_USER_AGENT),
            time_mult=_float("AI4CF_TIME_MULT", 3.0),
            bench_mult=_float("AI4CF_BENCH_MULT", 10.0),
            status_reference=_int("AI4CF_STATUS_REFERENCE", 1) != 0,
            handle=_str("AI4CF_HANDLE"),
            pi_bin=_str("PI_BIN", "pi"),
            pi_model=_str("PI_MODEL"),
            pi_thinking=_str("PI_THINKING"),
            pi_timeout=_float("PI_TIMEOUT", 3600.0),
            pi_extra_args=tuple(shlex.split(_str("PI_EXTRA_ARGS"))),
        )

    @property
    def workers(self) -> int:
        """`AI4CF_JOBS=0` means "as many as the machine has cores"."""
        return self.jobs if self.jobs > 0 else (os.cpu_count() or 1)

    def rating_range(self) -> tuple[int | None, int | None]:
        """Rating bounds implied by the `tags` filter (`3500-` -> `(3500, None)`)."""
        for token in re.split(r"[+\s]+", self.tags):
            token = token.strip()
            if not token:
                continue
            exact = _RATING_EXACT.match(token)
            if exact:
                return int(token), int(token)
            span = _RATING_RANGE.match(token)
            if span:
                lo = int(span.group("lo")) if span.group("lo") else None
                hi = int(span.group("hi")) if span.group("hi") else None
                if lo is not None or hi is not None:
                    return lo, hi
        return None, None

    def limit_or_none(self) -> int | None:
        """`AI4CF_LIMIT` as a `None`-able cap (`0` = unlimited)."""
        return self.limit if self.limit > 0 else None
