"""Parse a problem page into a statement + official sample tests.

Two shapes exist on codeforces.com:
  * HTML statements (`div.problem-statement`) — the common case;
  * PDF statements (statement block missing or replaced by an embedded viewer).
Samples come from the page in both cases (`div.sample-test`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import lxml.html
from parsel import Selector

_TIME = re.compile(r"([\d.]+)\s*second", re.IGNORECASE)
_MEMORY = re.compile(r"([\d.]+)\s*megabyte", re.IGNORECASE)
_BLOCK_TAGS = frozenset(
    {"br", "p", "div", "pre", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
)
STATEMENT_BODY = (
    "div:not(.header):not(.input-specification):not(.output-specification)"
    ":not(.sample-test):not(.note)"
)


@dataclass(slots=True)
class Sample:
    input: str
    output: str


@dataclass(slots=True)
class Statement:
    title: str
    time_limit_ms: int | None
    memory_limit_mb: int | None
    samples: list[Sample] = field(default_factory=list)
    html: str = ""
    text: str = ""
    pdf_url: str | None = None
    interactive: bool = False


def html_to_text(html: str) -> str:
    """Flatten HTML into readable plain text (block tags become line breaks)."""
    if not html.strip():
        return ""
    fragment = lxml.html.fragment_fromstring(html, create_parent="div")
    for element in fragment.iter():
        if element.tag in _BLOCK_TAGS:
            element.tail = "\n" + (element.tail or "")
    text = fragment.text_content()
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def absolutize(html: str, base: str) -> str:
    """Rewrite relative `src`/`href` so `statement.html` renders standalone."""
    fragment = lxml.html.fragment_fromstring(html, create_parent="div")
    for element in fragment.iter():
        for attribute in ("src", "href", "data-src"):
            value = element.get(attribute)
            if value and not value.startswith(("http://", "https://", "data:", "#")):
                element.set(attribute, urljoin(base, value))
    return lxml.html.tostring(fragment, encoding="unicode")


def _pre_text(pre_html: str) -> str:
    """Text of one `<pre>`: handles both `<br>`- and `<div>`-separated lines."""
    fragment = lxml.html.fragment_fromstring(pre_html, create_parent="div")
    for element in fragment.iter():
        if element.tag == "br" or "test-example-line" in (element.get("class") or ""):
            element.tail = "\n" + (element.tail or "")
    text = fragment.text_content().replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def _samples(sel: Selector) -> list[Sample]:
    nodes = sel.xpath(
        '//div[contains(concat(" ", normalize-space(@class), " "), " sample-test ")]'
        '//div[contains(concat(" ", normalize-space(@class), " "), " input ")'
        ' or contains(concat(" ", normalize-space(@class), " "), " output ")]'
    )
    samples: list[Sample] = []
    pending: str | None = None
    for node in nodes:
        pre = node.css("pre")
        if not pre:
            continue
        body = _pre_text(pre[0].get())
        if "input" in (node.attrib.get("class") or "").split():
            pending = body
        elif pending is not None:
            samples.append(Sample(input=pending, output=body))
            pending = None
    return samples


def _detect_pdf(page: Selector, stmt: Selector | None, page_url: str) -> str | None:
    """Statement PDF URL, if any.

    A statement block whose viewer embeds a `.pdf` (any attribute carrying such
    a URL) is a PDF statement. When the block is missing altogether, anchors are
    the only trace left — CF labels those links "Statements in PDF".
    """
    scope_html = stmt.get() if stmt is not None else page.get()
    root = lxml.html.fragment_fromstring(scope_html, create_parent="div")
    for element in root.iter():
        for value in element.attrib.values():
            if ".pdf" in value.lower():
                return urljoin(page_url, value.strip())
    if stmt is None:
        anchors = page.css("a")
        for anchor in anchors:
            href = anchor.attrib.get("href") or ""
            text = " ".join(anchor.css("::text").getall()).lower()
            if ".pdf" in href.lower() and "statement" in text:
                return urljoin(page_url, href.strip())
        for anchor in anchors:
            href = anchor.attrib.get("href") or ""
            if ".pdf" in href.lower():
                return urljoin(page_url, href.strip())
    return None


def parse_statement(page_html: str, page_url: str, tags: tuple[str, ...] = ()) -> Statement:
    page = Selector(page_html)
    stmt = page.css("div.problem-statement")
    stmt_sel = stmt[0] if stmt else None

    title = ""
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    if stmt_sel is not None:
        title = (stmt_sel.css("div.header div.title::text").get() or "").strip()
        time_text = " ".join(stmt_sel.css("div.time-limit ::text").getall())
        memory_text = " ".join(stmt_sel.css("div.memory-limit ::text").getall())
        if match := _TIME.search(time_text):
            time_limit_ms = round(float(match.group(1)) * 1000)
        if match := _MEMORY.search(memory_text):
            memory_limit_mb = round(float(match.group(1)))

    html = ""
    text = ""
    if stmt_sel is not None:
        html = absolutize(stmt_sel.get(), page_url)
        sections = []
        for selector, label in (
            (STATEMENT_BODY, "Statement"),
            ("div.input-specification", "Input"),
            ("div.output-specification", "Output"),
            ("div.note", "Note"),
        ):
            for block in stmt_sel.css(selector):
                body = html_to_text(block.get())
                if body:
                    sections.append(f"## {label}\n{body}" if label else body)
        text = "\n\n".join(sections)

    samples = _samples(page)
    pdf_url = _detect_pdf(page, stmt_sel, page_url)
    interactive = "interactive" in {tag.lower() for tag in tags}
    if not interactive:
        lowered = page_html.lower()
        interactive = (
            "interactive problem" in lowered or "flush" in lowered and "interactor" in lowered
        )

    if html and not pdf_url:
        # A statement without samples is still usable, but never silently trust it.
        text = text if samples else text + "\n\n## Samples\n(no sample tests on the page)"

    return Statement(
        title=title,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=memory_limit_mb,
        samples=samples,
        html=html,
        text=text,
        pdf_url=pdf_url,
        interactive=interactive,
    )
