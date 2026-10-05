"""Turn a `ProblemRef` into an on-disk problem workspace.

Layout of `problem/<contest>/<index>/`:

    statement.html|statement.pdf   official statement (whatever CF serves)
    statement.txt                  plain-text rendering, for the agent and for grep
    samples/NN.in, samples/NN.out  official sample tests
    input.rs                       fast input reader, reference for the solution
    Makefile                       build/run/test helpers (delegates to this CLI)
    meta.json                      problem metadata (limits, rating, tags, url)
    prompt.md                      the prompt handed to pi
    main.rs                        the deliverable, written by pi
    .pi/                           pi session logs + per-attempt streams
    cost.json                      every attempt with tokens and USD cost
    .fetched / .done / .failed     resume markers
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import ROOT, Settings
from .fetch import Fetcher
from .problemset import ProblemRef
from .statement import Statement, parse_statement

STATIC = ROOT / "static"
FETCHED = ".fetched"
DONE = ".done"
FAILED = ".failed"
FILES_TO_COPY = ("input.rs", "Makefile")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_text(path: Path, data: str) -> None:
    write_bytes(path, data.encode("utf-8"))


def read_json(path: Path, default: dict | None = None) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default or {})


def write_json(path: Path, data: dict) -> None:
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_meta(path: Path) -> dict:
    """Metadata of a materialized problem; `{}` when the directory is empty."""
    return read_json(path / "meta.json", {})


def problem_dir(settings: Settings, ref: ProblemRef) -> Path:
    return settings.problem_root / str(ref.contest_id) / ref.index


def parse_key(key: str) -> tuple[int, str]:
    """`2262/F`, `2262F` or `2262/f` -> `(2262, "F")`."""
    match = re.fullmatch(r"(?P<contest>\d+)[/\-]?(?P<index>[A-Za-z]\d*)", key.strip())
    if not match:
        raise ValueError(f"cannot parse problem key {key!r}")
    return int(match.group("contest")), match.group("index").upper()


def iter_problem_dirs(settings: Settings) -> list[Path]:
    """Every materialized workspace, sorted by contest then index."""
    root = settings.problem_root
    if not root.is_dir():
        return []
    dirs = [
        contest / index
        for contest in sorted(root.iterdir(), key=lambda p: (not p.name.isdigit(), p.name))
        if contest.is_dir() and contest.name.isdigit()
        for index in sorted(contest.iterdir())
        if index.is_dir() and (index / "meta.json").is_file()
    ]
    return dirs


@dataclass(slots=True)
class MaterializeResult:
    ref: ProblemRef
    path: Path
    status: str  # fetched | exists | error
    detail: str = ""


def statement_document(statement: Statement, meta: dict) -> str:
    """Wrap the CF statement fragment into a standalone, readable HTML page."""
    title = html.escape(str(meta.get("title") or meta.get("key") or "statement"))
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{title}</title>\n"
        "<style>body{max-width:60rem;margin:2rem auto;padding:0 1rem;"
        "font-family:system-ui,sans-serif;line-height:1.5}"
        ".property-title{display:inline;color:#555}pre{background:#f6f6f6;padding:.5rem}</style>\n"
        "</head>\n<body>\n"
        f"{statement.html}\n"
        "</body>\n</html>\n"
    )


def statement_text(statement: Statement, meta: dict, pdf_text: str) -> str:
    limits = []
    if meta.get("time_limit_ms"):
        limits.append(f"time limit: {meta['time_limit_ms']} ms")
    if meta.get("memory_limit_mb"):
        limits.append(f"memory limit: {meta['memory_limit_mb']} MB")
    lines = [
        f"# {statement.title or meta['key']}",
        "",
        f"url: {meta['url']}",
        f"rating: {meta.get('rating') or 'unrated'}",
        f"tags: {', '.join(meta.get('tags') or []) or '-'}",
        f"limits: {'; '.join(limits) or '-'}",
        f"interactive: {'yes' if meta.get('interactive') else 'no'}",
        f"samples: {len(statement.samples)}",
        "",
        statement.text.strip() or "(statement body not available as HTML)",
        "",
        "Sample tests: see ./samples/ (NN.in / NN.out).",
    ]
    if pdf_text:
        lines += ["", "## PDF statement text", "", pdf_text.strip()]
    return "\n".join(lines).rstrip() + "\n"


def _pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - dependency is declared
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception:  # noqa: BLE001 - pypdf raises a zoo on exotic PDFs
        return ""
    return "\n\n".join(page.strip() for page in pages if page.strip())


def materialize(
    ref: ProblemRef, fetcher: Fetcher, settings: Settings, *, force: bool = False
) -> MaterializeResult:
    path = problem_dir(settings, ref)
    if (path / FETCHED).is_file() and not force:
        return MaterializeResult(ref, path, "exists")

    try:
        page = fetcher.text(ref.url)
    except Exception as exc:  # noqa: BLE001 - one bad page must not abort the run
        return MaterializeResult(ref, path, "error", f"{type(exc).__name__}: {exc}")

    statement = parse_statement(page, ref.url, ref.tags)
    path.mkdir(parents=True, exist_ok=True)
    (path / "samples").mkdir(exist_ok=True)

    for old in sorted((path / "samples").glob("*.in")) + sorted((path / "samples").glob("*.out")):
        old.unlink()
    for number, sample in enumerate(statement.samples, start=1):
        write_text(path / "samples" / f"{number:02d}.in", sample.input)
        write_text(path / "samples" / f"{number:02d}.out", sample.output)

    pdf_bytes: bytes | None = None
    if statement.pdf_url:
        try:
            pdf_bytes, _ = fetcher.content(statement.pdf_url)
        except Exception as exc:  # noqa: BLE001 - keep the HTML statement usable
            pdf_bytes = None
            statement.text += f"\n\n(PDF download failed: {type(exc).__name__}: {exc})"

    meta = {
        "key": ref.key,
        "contest_id": ref.contest_id,
        "index": ref.index,
        "name": ref.name or statement.title,
        "rating": ref.rating,
        "tags": list(ref.tags),
        "url": ref.url,
        "title": statement.title,
        "time_limit_ms": statement.time_limit_ms,
        "memory_limit_mb": statement.memory_limit_mb,
        "interactive": statement.interactive,
        "samples": len(statement.samples),
        "pdf_url": statement.pdf_url,
        "fetched_at": now(),
    }

    if statement.html:
        write_text(path / "statement.html", statement_document(statement, meta))
    if pdf_bytes is not None:
        write_bytes(path / "statement.pdf", pdf_bytes)
    write_text(
        path / "statement.txt",
        statement_text(statement, meta, _pdf_text(pdf_bytes) if pdf_bytes else ""),
    )
    write_json(path / "meta.json", meta)

    for name in FILES_TO_COPY:
        source = STATIC / name
        target = path / name
        if source.is_file() and (force or not target.is_file()):
            write_bytes(target, source.read_bytes())

    write_json(
        path / FETCHED,
        {"key": ref.key, "fetched_at": meta["fetched_at"], "samples": meta["samples"]},
    )
    return MaterializeResult(ref, path, "fetched")
