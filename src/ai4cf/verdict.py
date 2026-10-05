"""Judge feedback: what Codeforces said about the *last submission* of a problem.

Once a solution is submitted by hand, the local checks are no longer the last word —
"memory limit exceeded on test 20" is information the next attempt must see. This
pulls the newest submission of the configured handle from the official API (query
strings on the HTML status page are Cloudflare-challenged, the API is not).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from .fetch import Fetcher
from .problemset import BASE
from .workspace import read_json, write_json

API_URL = BASE + "/api/contest.status"


@dataclass(slots=True)
class JudgeVerdict:
    verdict: str
    language: str
    passed_tests: int | None
    time_ms: int | None
    memory_kb: int | None
    submission_id: int
    submitted_at: str
    handle: str

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "language": self.language,
            "passed_tests": self.passed_tests,
            "time_ms": self.time_ms,
            "memory_kb": self.memory_kb,
            "submission_id": self.submission_id,
            "submitted_at": self.submitted_at,
            "handle": self.handle,
        }

    @property
    def failed_test(self) -> int | None:
        """The 1-based test that failed, when the judge reports it."""
        return None if self.passed_tests is None else self.passed_tests + 1

    def headline(self) -> str:
        parts = [self.verdict.replace("_", " ").lower()]
        if (test := self.failed_test) is not None:
            parts.append(f"on test {test}")
        if self.time_ms is not None:
            parts.append(f"after {self.time_ms} ms")
        if self.memory_kb:
            parts.append(f"{self.memory_kb / 1024:.0f} MiB")
        return " ".join(parts)


def fetch_latest_verdict(
    fetcher: Fetcher, contest_id: int, index: str, handle: str
) -> JudgeVerdict | None:
    """Newest submission of `handle` for this problem, or None if there is none."""
    response = fetcher.get(
        API_URL,
        params={"contestId": contest_id, "handle": handle, "from": 1, "count": 100},
        min_interval=2.0,  # the public API asks for at most one request per 2 s
    ).json()
    if response.get("status") != "OK":
        raise RuntimeError(f"codeforces API: {response.get('comment') or response.get('status')}")

    wanted = index.upper()
    submissions = [
        item
        for item in response.get("result") or []
        if str((item.get("problem") or {}).get("index", "")).upper() == wanted
    ]
    if not submissions:
        return None
    newest = max(submissions, key=lambda item: item.get("creationTimeSeconds") or 0)
    return JudgeVerdict(
        verdict=str(newest.get("verdict") or "UNKNOWN"),
        language=str(newest.get("programmingLanguage") or ""),
        passed_tests=newest.get("passedTestCount"),
        time_ms=newest.get("timeConsumedMillis"),
        memory_kb=(newest.get("memoryConsumedBytes") or 0) // 1024 or None,
        submission_id=int(newest.get("id") or 0),
        submitted_at=datetime.fromtimestamp(
            newest.get("creationTimeSeconds") or 0, tz=UTC
        ).isoformat(timespec="seconds"),
        handle=handle,
    )


def load_verdict(path) -> JudgeVerdict | None:
    """The verdict stored in the workspace, if any."""
    data = read_json(path / "verdict.json", {})
    if not data.get("verdict"):
        return None
    return JudgeVerdict(
        verdict=str(data["verdict"]),
        language=str(data.get("language") or ""),
        passed_tests=data.get("passed_tests"),
        time_ms=data.get("time_ms"),
        memory_kb=data.get("memory_kb"),
        submission_id=int(data.get("submission_id") or 0),
        submitted_at=str(data.get("submitted_at") or ""),
        handle=str(data.get("handle") or ""),
    )


def save_verdict(path, verdict: JudgeVerdict) -> None:
    write_json(path / "verdict.json", verdict.as_dict())


def is_failure(verdict: JudgeVerdict) -> bool:
    return verdict.verdict.upper() not in {"OK", "ACCEPTED", ""}
