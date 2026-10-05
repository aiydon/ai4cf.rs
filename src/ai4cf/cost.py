"""Aggregate the money spent by the pipeline (`cost.json` per problem)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .workspace import DONE, FAILED, iter_problem_dirs, load_meta, read_json


@dataclass(slots=True)
class ProblemCost:
    key: str
    name: str
    rating: int | None
    status: str
    attempts: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    models: str
    path: Path
    verified: bool | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens


def status_of(path: Path) -> str:
    if (path / DONE).is_file():
        return "done"
    if (path / FAILED).is_file():
        return "failed"
    if (path / "main.rs").is_file():
        return "attempted"
    return "pending"


def scan(settings: Settings) -> list[ProblemCost]:
    rows: list[ProblemCost] = []
    for path in iter_problem_dirs(settings):
        meta = load_meta(path)
        record = read_json(path / "cost.json", {"runs": []})
        runs = record.get("runs") or []
        models = sorted({str(run.get("model") or "-") for run in runs})
        done = read_json(path / DONE, {})
        rows.append(
            ProblemCost(
                key=str(meta.get("key") or path.name),
                name=str(meta.get("name") or meta.get("title") or ""),
                rating=meta.get("rating"),
                status=status_of(path),
                attempts=len(runs),
                cost_usd=float(record.get("total_cost_usd") or 0.0),
                input_tokens=int(record.get("total_input_tokens") or 0),
                output_tokens=int(record.get("total_output_tokens") or 0),
                cache_read_tokens=int(record.get("total_cache_read_tokens") or 0),
                models=",".join(models) if models else "-",
                path=path,
                verified=done.get("verified") if (path / DONE).is_file() else None,
            )
        )
    return rows


def unrecorded_runs(path: Path, recorded: int) -> list[tuple[int, float]]:
    """Attempts whose pi stream exists but which `cost.json` never got to record.

    Covers a `kill -9` of this process as well as the attempt currently running.
    """
    pending: list[tuple[int, float]] = []
    stream_dir = path / ".pi"
    if not stream_dir.is_dir():
        return pending
    for stream in sorted(stream_dir.glob("attempt-*.jsonl")):
        try:
            number = int(stream.stem.split("-")[1])
        except (IndexError, ValueError):
            continue
        if number <= recorded:
            continue
        cost = 0.0
        try:
            for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = event.get("message") or {}
                if event.get("type") == "message_end" and message.get("role") == "assistant":
                    cost += float(
                        ((message.get("usage") or {}).get("cost") or {}).get("total") or 0.0
                    )
        except OSError:
            continue
        pending.append((number, cost))
    return pending


def _short(number: int) -> str:
    for unit, scale in (("M", 1_000_000), ("k", 1_000)):
        if number >= scale:
            return f"{number / scale:.1f}{unit}"
    return str(number)


def report(settings: Settings, *, detail: str | None = None) -> str:
    rows = scan(settings)
    if detail:
        wanted = detail.strip().upper().replace("/", "")
        rows = [row for row in rows if row.key.upper() == wanted]
        if not rows:
            return f"no cost record for {detail}"

    lines: list[str] = []
    pending_total = 0.0
    for row in rows:
        record = read_json(row.path / "cost.json", {})
        status = row.status + ("*" if row.status == "done" and row.verified is False else "")
        lines.append(
            f"{row.key:<10} {status:<9} attempts={row.attempts:<2} "
            f"cost=${row.cost_usd:.4f} tokens={_short(row.total_tokens):>7} "
            f"(in {_short(row.input_tokens)}, out {_short(row.output_tokens)}, "
            f"cache {_short(row.cache_read_tokens)}) rating={row.rating or '-':<5} {row.name[:40]}"
        )
        for run in record.get("runs") or []:
            usage = run.get("usage") or {}
            lines.append(
                f"    attempt {run.get('attempt')}: {run.get('verdict'):<14} "
                f"exit={run.get('exit_code')} {run.get('duration_s', 0):>7.1f}s "
                f"${float(usage.get('cost_usd') or 0):.4f} model={run.get('model')}"
                + ("  [timeout]" if run.get("timed_out") else "")
                + ("  [interrupted]" if run.get("interrupted") else "")
            )
            if run.get("detail") and run.get("verdict") not in {"solved", "pi_ran"}:
                first = str(run["detail"]).strip().splitlines()
                if first:
                    lines.append(f"      detail: {first[0][:120]}")
        for number, cost in unrecorded_runs(row.path, row.attempts):
            pending_total += cost
            lines.append(
                f"    attempt {number}: unrecorded      "
                f"(pi stream only, running or killed) ${cost:.4f}"
            )

    total = sum(row.cost_usd for row in rows)
    solved = sum(row.cost_usd for row in rows if row.status == "done")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    lines.append("")
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    lines.append(f"problems: {summary or 'none'}")
    lines.append(f"spent on solved problems: ${solved:.4f}")
    lines.append(f"spent in total:           ${total:.4f}")
    if pending_total:
        lines.append(f"  + unrecorded attempts:  ${pending_total:.4f}")
    if pending_total:
        lines.append("  unrecorded = pi was still running or was killed before the ledger flush")
    if any(row.status == "done" and row.verified is False for row in rows):
        lines.append("* finished without official sample tests: nothing to verify against")
    return "\n".join(lines)


def status_report(settings: Settings, key: str | None = None) -> str:
    rows = scan(settings)
    if key:
        wanted = key.strip().upper().replace("/", "")
        rows = [row for row in rows if row.key.upper() == wanted]
        if not rows:
            return f"no problem matches {key}"
    if not rows:
        return f"no problems materialized under {settings.problem_root}"
    lines = [
        f"{row.key:<10} {(row.status + ('*' if row.status == 'done' and row.verified is False else '')):<9}"
        f" rating={row.rating or '-':<5} attempts={row.attempts:<2} ${row.cost_usd:.4f} {row.name[:44]}"
        for row in rows
    ]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    lines.append("")
    lines.append("total: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    return "\n".join(lines)
