"""Runtime reference for one problem, taken from its public status page.

The status page lists recent submissions of the problem with the judge's own
measurements (max time over tests, peak memory, language). Only *aggregates* are
kept — fastest/median time, memory range, language mix — which gives the solver
a realistic performance target without leaking anyone's solution.

Note: any query string on that page (`?order=BY_CONSUMED_TIME_ASC`, `?verdictName=OK`)
is answered with a Cloudflare challenge to non-browser clients, so we fetch the
plain path and filter/aggregate client-side.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from parsel import Selector

from .fetch import Fetcher
from .problemset import status_url


@dataclass(slots=True)
class RuntimeReference:
    url: str
    accepted: int
    fastest_ms: int
    median_ms: int
    slowest_ms: int
    min_memory_kb: int | None
    max_memory_kb: int | None
    languages: list[tuple[str, int]]
    time_limit_ms: int | None = None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "accepted": self.accepted,
            "fastest_ms": self.fastest_ms,
            "median_ms": self.median_ms,
            "slowest_ms": self.slowest_ms,
            "min_memory_kb": self.min_memory_kb,
            "max_memory_kb": self.max_memory_kb,
            "languages": [list(item) for item in self.languages],
            "time_limit_ms": self.time_limit_ms,
        }

    def headline(self) -> str:
        return (
            f"fastest accepted {self.fastest_ms} ms, median {self.median_ms} ms "
            f"of {self.accepted} accepted"
        )


def _digits(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_runtime_reference(
    fetcher: Fetcher,
    contest_id: int,
    index: str,
    *,
    time_limit_ms: int | None = None,
) -> RuntimeReference | None:
    """Best-effort aggregate of the accepted submissions shown on page 1."""
    url = status_url(contest_id, index)
    page = Selector(fetcher.text(url))
    table = page.css("table.status-frame-datatable")
    if not table:
        return None
    headers = [_clean(header) for header in table.css("tr th::text").getall()]
    columns = {
        name: headers.index(name)
        for name in ("Verdict", "Time", "Memory", "Lang")
        if name in headers
    }
    if "Verdict" not in columns or "Time" not in columns:
        return None

    times: list[int] = []
    memories: list[int] = []
    languages: dict[str, int] = {}
    for row in table.css("tr[data-submission-id]"):
        cells = row.css("td")
        if len(cells) <= columns["Time"]:
            continue
        verdict_cell = cells[columns["Verdict"]]
        verdict_html = verdict_cell.get()
        if "verdict-accepted" not in verdict_html and (
            _clean(" ".join(verdict_cell.css("::text").getall())) != "Accepted"
        ):
            continue
        measured = _digits(" ".join(cells[columns["Time"]].css("::text").getall()))
        if measured is None:
            continue
        times.append(measured)
        if "Memory" in columns:
            memory = _digits(" ".join(cells[columns["Memory"]].css("::text").getall()))
            if memory:
                memories.append(memory)
        if "Lang" in columns:
            language = _clean(" ".join(cells[columns["Lang"]].css("::text").getall()))
            if language:
                languages[language] = languages.get(language, 0) + 1

    if not times:
        return None
    top_languages = sorted(languages.items(), key=lambda item: (-item[1], item[0]))[:3]
    return RuntimeReference(
        url=url,
        accepted=len(times),
        fastest_ms=min(times),
        median_ms=round(statistics.median(times)),
        slowest_ms=max(times),
        min_memory_kb=min(memories) if memories else None,
        max_memory_kb=max(memories) if memories else None,
        languages=top_languages,
        time_limit_ms=time_limit_ms,
    )
