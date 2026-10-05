"""Crawl the Codeforces problemset listing (`/problemset/page/N?tags=...`)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from parsel import Selector

from .config import Settings
from .fetch import Fetcher

BASE = "https://codeforces.com"
PAGE_URL = BASE + "/problemset/page/{page}?tags={tags}"
PROBLEM_URL = BASE + "/problemset/problem/{contest}/{index}"
SUBMIT_URL = BASE + "/problemset/submit/{contest}/{index}"
STATUS_URL = BASE + "/problemset/status/{contest}/problem/{index}"
STATUS_ORDERED_URL = STATUS_URL + "/page/{page}?order={order}"
TAGS_URL = BASE + "/problemset/page/{page}?tags={tags}"

_ROW_LINK = re.compile(r"/problemset/problem/(?P<contest>\d+)/(?P<index>[A-Z0-9]+)")
_TAG_LINK = "/problemset?tags="


def problem_url(contest_id: int, index: str) -> str:
    return PROBLEM_URL.format(contest=contest_id, index=index)


def submit_url(contest_id: int, index: str) -> str:
    """Submit page (needs a logged-in browser session)."""
    return SUBMIT_URL.format(contest=contest_id, index=index)


def status_url(contest_id: int, index: str) -> str:
    """Status page, plain path — the only shape that is not Cloudflare-challenged."""
    return STATUS_URL.format(contest=contest_id, index=index)


def status_ordered_url(
    contest_id: int, index: str, *, order: str = "BY_CONSUMED_TIME_ASC", page: int = 1
) -> str:
    """Status page sorted by consumed time — for humans in a browser.

    `order` accepts `BY_CONSUMED_TIME_ASC|DESC`, `BY_PROGRAM_LENGTH_ASC|DESC`,
    `BY_JUDGED_ASC|DESC`, `BY_CREATION_TIME_DESC`; add `&verdictName=OK` to keep
    only accepted submissions (`WRONG_ANSWER`, `RUNTIME_ERROR`, ... otherwise).
    """
    return STATUS_ORDERED_URL.format(contest=contest_id, index=index, page=page, order=order)


def tags_url(tags: str, page: int = 1) -> str:
    return TAGS_URL.format(page=page, tags=quote(tags, safe="+-.,"))


@dataclass(frozen=True, slots=True)
class ProblemRef:
    contest_id: int
    index: str
    name: str
    rating: int | None
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """`2262F` — the short form used everywhere in logs."""
        return f"{self.contest_id}{self.index}"

    @property
    def url(self) -> str:
        return problem_url(self.contest_id, self.index)


def page_url(page: int, tags: str) -> str:
    return PAGE_URL.format(page=page, tags=quote(tags, safe="+-.,"))


def links(ref: ProblemRef) -> dict[str, str]:
    """Every URL a human (or the prompt) may want for one problem."""
    return {
        "problem": ref.url,
        "submit": submit_url(ref.contest_id, ref.index),
        "status": status_url(ref.contest_id, ref.index),
        "status_by_time": status_ordered_url(ref.contest_id, ref.index),
    }


def parse_row(row: Selector) -> ProblemRef | None:
    link = row.css("td.id a::attr(href)").get() or row.css("a::attr(href)").get() or ""
    match = _ROW_LINK.search(link)
    if not match:
        return None

    name = row.css("td:nth-child(2) a::text").get()
    if not name:
        name = row.css("td.id a::text").get() or ""
    name = re.sub(r"\s+", " ", name).strip()

    rating_text = row.css("span.ProblemRating::text").get()
    if not rating_text:
        # Older listing kept the difficulty in a `*3500` tag-box.
        for tag in row.css("span.tag-box::text").getall():
            if tag.strip().startswith("*"):
                rating_text = tag.strip().lstrip("*")
                break
    rating = int(rating_text) if rating_text and rating_text.strip().isdigit() else None

    tags: list[str] = []
    for href, tag in zip(
        row.css("a::attr(href)").getall(), row.css("a::text").getall(), strict=False
    ):
        if href.startswith(_TAG_LINK):
            tag = re.sub(r"\s+", " ", tag).strip()
            if tag and not tag.startswith("*"):
                tags.append(tag)
    for tag in row.css("span.tag-box::text").getall():
        tag = tag.strip()
        if tag and not tag.startswith("*"):
            tags.append(tag)

    return ProblemRef(
        contest_id=int(match.group("contest")),
        index=match.group("index"),
        name=name,
        rating=rating,
        tags=tuple(tags),
    )


def iter_problemset(
    fetcher: Fetcher,
    settings: Settings,
    *,
    max_pages: int | None = None,
    limit: int | None = None,
    stop_after: int | None = None,
    on_page=None,
) -> list[ProblemRef]:
    """Walk the listing pages for `settings.tags` and return the problems.

    Stops at the first page without new problems, so an eventual layout change
    degrades to "fewer problems", never to an endless loop. `stop_after` keeps
    the crawl short when the caller only needs the first few problems.
    """
    lo, hi = settings.rating_range()
    refs: list[ProblemRef] = []
    seen: set[str] = set()
    page = 1
    while max_pages is None or page <= max_pages:
        html = fetcher.text(page_url(page, settings.tags))
        rows = Selector(html).css("table.problems tr")
        found = 0
        for row in rows:
            ref = parse_row(row)
            if ref is None or ref.key in seen:
                continue
            if (lo is not None and ref.rating is not None and ref.rating < lo) or (
                hi is not None and ref.rating is not None and ref.rating > hi
            ):
                continue
            seen.add(ref.key)
            refs.append(ref)
            found += 1
        if on_page is not None:
            on_page(page, found)
        if found == 0:
            break
        if stop_after is not None and len(refs) >= stop_after:
            break
        page += 1
    return refs[:limit] if limit else refs
