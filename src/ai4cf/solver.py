"""Drive `pi` (deepseek) on one problem and decide whether it is solved.

One invocation of `solve_problem` performs at most one pi attempt; attempts
accumulate in `cost.json` across invocations, which is what makes the pipeline
resumable: `.done` skips, `.failed` stops after `AI4CF_MAX_ATTEMPTS`.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bench import BenchOutcome, bench_inputs, bench_problem, display_name
from .config import ROOT, Settings
from .fetch import Fetcher
from .problemset import problem_url, status_ordered_url, status_url, submit_url
from .verdict import JudgeVerdict, fetch_latest_verdict, is_failure, load_verdict, save_verdict
from .verify import VerifyOutcome, verify_problem
from .workspace import DONE, FAILED, load_meta, now, read_json, write_json, write_text

PROMPT_TEMPLATE = ROOT / "static" / "prompt.md"
_ACTIVE: set[subprocess.Popen] = set()
_ACTIVE_LOCK = threading.Lock()


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, raw: dict) -> None:
        cost = raw.get("cost") or {}
        self.input_tokens += int(raw.get("input") or 0)
        self.output_tokens += int(raw.get("output") or 0)
        self.cache_read_tokens += int(raw.get("cacheRead") or 0)
        self.cache_write_tokens += int(raw.get("cacheWrite") or 0)
        self.cost_usd += float(cost.get("total") or 0.0)


@dataclass(slots=True)
class Attempt:
    attempt: int
    started_at: str
    finished_at: str
    duration_s: float
    model: str
    exit_code: int | None
    timed_out: bool
    interrupted: bool
    verdict: str
    detail: str
    claimed_solved: bool = False
    usage: Usage = field(default_factory=Usage)


def status_block(meta: dict) -> str:
    """Render the judge-side runtime reference for the prompt."""
    status = meta.get("status") or {}
    if not status.get("fastest_ms"):
        return (
            "## Performance reference\n\n"
            "_No accepted submission was visible on this problem's status page, so there is no\n"
            "judge-side runtime target: derive the intended complexity from the constraints and\n"
            "keep your own `make bench` numbers well under the time limit._\n"
        )
    fastest = int(status["fastest_ms"])
    median = int(status.get("median_ms") or fastest)
    accepted = int(status.get("accepted") or 0)
    limit = status.get("time_limit_ms") or meta.get("time_limit_ms")
    lines = [
        "## Performance reference (accepted submissions of this problem)",
        "",
        f"- fastest accepted: **{fastest} ms**"
        + (f" ({fastest / limit * 100:.0f}% of the {limit} ms time limit)" if limit else ""),
        (
            f"- median of the {accepted} accepted submissions on page 1: {median} ms"
            f" (slowest of that sample: {status.get('slowest_ms')} ms)"
        ),
    ]
    if status.get("min_memory_kb"):
        low = int(status["min_memory_kb"]) / 1024
        high = int(status["max_memory_kb"] or status["min_memory_kb"]) / 1024
        lines.append(f"- accepted memory range: {low:.1f}-{high:.1f} MiB")
    languages = ", ".join(f"{name} x{count}" for name, count in (status.get("languages") or []))
    if languages:
        lines.append(f"- languages: {languages}")
    lines.append(f"- source: {status.get('url')}")
    lines.append("")
    lines.append(
        "These are the judge's own measurements (worst test per submission). Treat them as the"
    )
    lines.append("intended solution's ballpark: matching them is good, being 10x slower is a bug.")
    return "\n".join(lines) + "\n"


@dataclass(slots=True)
class SolveResult:
    path: Path
    key: str
    status: str  # solved | failed | pending | error | skipped
    verdict: str
    attempts: int
    cost_usd: float
    run_cost_usd: float = 0.0
    bench_ms: int | None = None
    bench_over_limit: bool = False
    bench_shapes: int = 0
    detail: str = ""


def terminate_all(grace: float = 10.0) -> None:
    """Kill every pi process this process started (Ctrl+C / SIGTERM path)."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE)
    for proc in procs:
        _kill(proc, grace=grace)


def _kill(proc: subprocess.Popen, grace: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    deadline = time.monotonic() + grace
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
            pass


def cost_path(path: Path) -> Path:
    return path / "cost.json"


def load_cost(path: Path) -> dict:
    return read_json(cost_path(path), {"runs": []})


def append_attempt(path: Path, attempt: Attempt, meta: dict) -> dict:
    record = load_cost(path)
    record.setdefault("key", meta.get("key"))
    record.setdefault("url", meta.get("url"))
    record["model"] = attempt.model
    record.setdefault("runs", []).append({**asdict(attempt), "usage": asdict(attempt.usage)})
    totals = Usage()
    for run in record["runs"]:
        usage = run.get("usage") or {}
        totals.input_tokens += int(usage.get("input_tokens") or 0)
        totals.output_tokens += int(usage.get("output_tokens") or 0)
        totals.cache_read_tokens += int(usage.get("cache_read_tokens") or 0)
        totals.cache_write_tokens += int(usage.get("cache_write_tokens") or 0)
        totals.cost_usd += float(usage.get("cost_usd") or 0.0)
    record["total_cost_usd"] = round(totals.cost_usd, 6)
    record["total_input_tokens"] = totals.input_tokens
    record["total_output_tokens"] = totals.output_tokens
    record["total_cache_read_tokens"] = totals.cache_read_tokens
    record["total_cache_write_tokens"] = totals.cache_write_tokens
    write_json(cost_path(path), record)
    return record


def judge_feedback(path: Path, meta: dict) -> str:
    """What the judge said about the last submission, when it rejected one."""
    verdict = load_verdict(path)
    if verdict is None or not is_failure(verdict):
        return ""
    failed_test = f" on test {verdict.failed_test}" if verdict.failed_test else ""
    lines = [
        "## The judge already rejected a submission of this problem",
        "",
        (
            f"The last submission of `{verdict.handle}` was **{verdict.verdict.replace('_', ' ')}**"
            f"{failed_test} ({verdict.headline()}; {verdict.language})."
        ),
        "",
    ]
    if "MEMORY" in verdict.verdict.upper() and verdict.memory_kb:
        limit = meta.get("memory_limit_mb")
        lines.append(
            f"It peaked at {verdict.memory_kb / 1024:.0f} MiB"
            + (f" against the {limit} MiB limit" if limit else "")
            + ": the approach holds far too much data at once."
        )
    if "TIME" in verdict.verdict.upper() and verdict.time_ms:
        limit = meta.get("time_limit_ms")
        lines.append(
            f"It burned the whole {verdict.time_ms} ms budget"
            + (f" (limit {limit} ms)" if limit else "")
            + ": the worst case is much slower than the samples suggest."
        )
    lines += [
        "That test will be there again, so the fix has to change how much work and memory the",
        "solution uses on that kind of input — not shave a constant factor.",
    ]
    return "\n".join(lines) + "\n"


def refresh_verdict(settings: Settings, path: Path, meta: dict) -> JudgeVerdict | None:
    """Best effort: learn the judge's verdict before retrying a problem."""
    if not settings.handle:
        return None
    contest_id, index = meta.get("contest_id"), meta.get("index")
    if not contest_id or not index:
        return None
    try:
        with Fetcher(settings) as fetcher:
            verdict = fetch_latest_verdict(fetcher, int(contest_id), str(index), settings.handle)
    except Exception:  # noqa: BLE001 - feedback is optional, never block a solve
        return None
    if verdict is not None:
        save_verdict(path, verdict)
    return verdict


def problem_links(meta: dict) -> dict[str, str]:
    """Links for one problem, recomputed for workspaces fetched before they existed."""
    links = meta.get("links")
    if links:
        return links
    contest_id = int(meta.get("contest_id") or 0)
    index = str(meta.get("index") or "")
    if not contest_id or not index:
        return {}
    return {
        "problem": problem_url(contest_id, index),
        "submit": submit_url(contest_id, index),
        "status": status_url(contest_id, index),
        "status_by_time": status_ordered_url(contest_id, index),
    }


def render_prompt(settings: Settings, path: Path, meta: dict, record: dict) -> str:
    template = PROMPT_TEMPLATE.read_text(encoding="utf-8")
    runs = record.get("runs") or []
    attempt = len(runs) + 1
    feedback = "## Previous attempt feedback\n\n_(none — this is the first attempt)_\n"
    if runs:
        last = runs[-1]
        feedback = (
            f"## Previous attempt feedback (`main.rs` on disk is attempt {last['attempt']})\n\n"
            f"The last attempt finished with verdict `{last['verdict']}` after "
            f"{last['duration_s']:.0f}s:\n\n"
            f"```\n{str(last.get('detail') or '')[:2000].strip()}\n```\n\n"
            "Fix forward from the existing `main.rs` instead of restarting from scratch.\n"
        )
    links = problem_links(meta)
    substitutions = {
        "{{KEY}}": meta.get("key", ""),
        "{{NAME}}": meta.get("name") or meta.get("title") or "",
        "{{RATING}}": str(meta.get("rating") or "unrated"),
        "{{TAGS}}": ", ".join(meta.get("tags") or []) or "-",
        "{{URL}}": meta.get("url", ""),
        "{{DIR}}": str(path),
        "{{TIME_LIMIT}}": f"{meta.get('time_limit_ms')} ms"
        if meta.get("time_limit_ms")
        else "unknown",
        "{{MEMORY_LIMIT}}": f"{meta.get('memory_limit_mb')} MB"
        if meta.get("memory_limit_mb")
        else "unknown",
        "{{SAMPLES}}": str(meta.get("samples") or 0),
        "{{TIME_MULT}}": f"{settings.time_mult:g}",
        "{{SUBMIT_URL}}": str(links.get("submit") or ""),
        "{{STATUS_URL}}": str(links.get("status_by_time") or links.get("status") or ""),
        "{{STATUS_REF}}": status_block(meta),
        "{{JUDGE_FEEDBACK}}": judge_feedback(path, meta),
        "{{ATTEMPT}}": str(attempt),
        "{{FEEDBACK}}": feedback,
    }
    for key, value in substitutions.items():
        template = template.replace(key, value)
    return template


def _pi_command(settings: Settings, path: Path) -> list[str]:
    command = [settings.pi_bin, "--mode", "json", "-p", "--session-dir", str(path / ".pi")]
    if settings.pi_model:
        command += ["--model", settings.pi_model]
    if settings.pi_thinking:
        command += ["--thinking", settings.pi_thinking]
    command += ["--no-context-files", "--no-skills", *settings.pi_extra_args]
    return command


def _session_usage(path: Path, since: float) -> Usage:
    """Fallback cost source: pi session logs written during this attempt."""
    usage = Usage()
    session_dir = path / ".pi"
    if not session_dir.is_dir():
        return usage
    for log in session_dir.glob("*.jsonl"):
        try:
            if log.stat().st_mtime < since:
                continue
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = event.get("message") or {}
                if event.get("type") == "message" and message.get("role") == "assistant":
                    usage.add(message.get("usage") or {})
        except OSError:
            continue
    return usage


def _run_pi(settings: Settings, path: Path, prompt: str, attempt_no: int) -> Attempt:
    log_dir = path / ".pi"
    log_dir.mkdir(exist_ok=True)
    stream_path = log_dir / f"attempt-{attempt_no}.jsonl"
    prompt_path = log_dir / f"attempt-{attempt_no}.prompt.md"
    write_text(prompt_path, prompt)

    model = settings.pi_model or "pi-default"
    started_at = now()
    start = time.monotonic()
    usage = Usage()
    last_text = ""
    timed_out = False
    interrupted = False
    exit_code: int | None = None
    detail = ""

    command = [*_pi_command(settings, path), "--", prompt]
    with (
        open(stream_path, "w", encoding="utf-8") as stream,
        open(log_dir / f"attempt-{attempt_no}.err", "w", encoding="utf-8") as errlog,
    ):
        proc = subprocess.Popen(
            command,
            cwd=path,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errlog,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        with _ACTIVE_LOCK:
            _ACTIVE.add(proc)
        events: queue.Queue[str | None] = queue.Queue()

        def reader() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                events.put(line)
            events.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        deadline = start + settings.pi_timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    line = events.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    continue
                if line is None:
                    break
                stream.write(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "message_end":
                    message = event.get("message") or {}
                    if message.get("role") != "assistant":
                        continue
                    usage.add(message.get("usage") or {})
                    text = "".join(
                        part.get("text", "")
                        for part in message.get("content") or []
                        if part.get("type") == "text"
                    )
                    if text.strip():
                        last_text = text
            if timed_out:
                detail = f"pi exceeded PI_TIMEOUT={settings.pi_timeout:g}s"
        except BaseException:  # noqa: BLE001 - KeyboardInterrupt is not an Exception
            interrupted = True
            _kill(proc, grace=2.0)
            exit_code = proc.returncode
        else:
            if timed_out:
                _kill(proc)
            try:
                exit_code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                _kill(proc)
                exit_code = proc.returncode
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(proc)

    if usage.cost_usd == 0.0 and not interrupted:
        usage = _session_usage(path, since=start)
    if last_text.strip():
        write_text(log_dir / f"attempt-{attempt_no}.answer.md", last_text.strip() + "\n")

    attempt = Attempt(
        attempt=attempt_no,
        started_at=started_at,
        finished_at=now(),
        duration_s=round(time.monotonic() - start, 2),
        model=model,
        exit_code=exit_code,
        timed_out=timed_out,
        interrupted=interrupted,
        verdict="pi_timeout" if timed_out else "pi_ran",
        detail=detail,
        claimed_solved="STATUS: SOLVED" in last_text,
        usage=usage,
    )
    return attempt


def _verdict_from_verification(outcome: VerifyOutcome) -> tuple[str, str]:
    if not outcome.built:
        reason = "build_failed" if (outcome.build_output or "").strip() else "no_main_rs"
        return reason, outcome.build_output.strip()[:4000]
    failed = [sample for sample in outcome.samples if not sample.ok]
    if failed:
        return "samples_failed", failed[0].detail
    if not outcome.samples:
        return "no_samples", "no official sample tests on the page"
    return "solved", ""


def solve_problem(
    settings: Settings, path: Path, *, force: bool = False, retry_failed: bool = False
) -> SolveResult:
    meta = load_meta(path)
    key = str(meta.get("key") or path.parent.name + path.name)
    record = load_cost(path)
    attempts = len(record.get("runs") or [])

    if (path / DONE).is_file() and not force:
        return SolveResult(
            path, key, "skipped", "done", attempts, record.get("total_cost_usd", 0.0)
        )
    if (path / FAILED).is_file() and not retry_failed and not force:
        return SolveResult(
            path, key, "skipped", "failed", attempts, record.get("total_cost_usd", 0.0)
        )

    attempt_no = attempts + 1
    if attempts or force:
        # Retrying: the judge may have judged the previous submission in the meantime.
        refresh_verdict(settings, path, meta)
    if shutil.which(settings.pi_bin) is None and not Path(settings.pi_bin).is_file():
        raise RuntimeError(f"PI_BIN={settings.pi_bin!r} not found; check .env")
    prompt = render_prompt(settings, path, meta, record)
    write_text(path / "prompt.md", prompt)
    try:
        attempt = _run_pi(settings, path, prompt, attempt_no)
    except FileNotFoundError as exc:  # PI_BIN is wrong / pi is not installed
        raise RuntimeError(
            f"cannot execute PI_BIN={settings.pi_bin!r} ({exc}); check .env"
        ) from exc
    if attempt.interrupted:
        # Ctrl+C / SIGTERM: pi is already dead; flush what this attempt spent.
        attempt.verdict = "pi_interrupted"
        record = append_attempt(path, attempt, meta)
        return SolveResult(
            path,
            key,
            "pending",
            "interrupted",
            attempt_no,
            record.get("total_cost_usd", 0.0),
            attempt.usage.cost_usd,
            attempt.detail,
        )
    outcome = verify_problem(path, settings)
    verified_verdict, verified_detail = _verdict_from_verification(outcome)
    bench: BenchOutcome | None = None
    if verified_verdict == "solved" and bench_inputs(path):
        try:
            bench = bench_problem(path, settings, already_built=True)
        except Exception:  # noqa: BLE001 - a bench failure must not undo a solved verdict
            bench = None
    if bench is not None and bench.over_limit:
        verified_detail = (
            f"worst max input {display_name(path, bench.input_file)}: {bench.elapsed_ms} ms "
            f"> limit {bench.time_limit_ms} ms"
        )
    # A killed pi explains the outcome better than "main.rs missing".
    verdict = "pi_timeout" if attempt.timed_out else verified_verdict
    attempt.verdict = verdict
    if attempt.detail and verified_detail:
        attempt.detail = f"{attempt.detail}\n{verified_detail}"
    elif not attempt.detail:
        attempt.detail = verified_detail
    record = append_attempt(path, attempt, meta)

    total_cost = record.get("total_cost_usd", 0.0)
    run_cost = attempt.usage.cost_usd
    # No official samples to judge against: accept pi's own verdict, but never
    # claim verification we could not perform.
    unverifiable = verdict == "no_samples" and attempt.claimed_solved
    if verdict == "solved" or unverifiable:
        verified = verdict == "solved"
        (path / FAILED).unlink(missing_ok=True)
        write_json(
            path / DONE,
            {
                "key": key,
                "name": meta.get("name"),
                "rating": meta.get("rating"),
                "url": meta.get("url"),
                "solved_at": now(),
                "attempts": attempt_no,
                "verified": verified,
                "samples": len(outcome.samples),
                "max_runtime_ms": outcome.max_elapsed_ms,
                "cost_usd": total_cost,
                "model": attempt.model,
                "note": "" if verified else "no official sample tests; pi reported STATUS: SOLVED",
                "bench": (
                    {
                        "input": display_name(path, bench.input_file),
                        "ms": bench.elapsed_ms,
                        "rss_kb": bench.rss_kb,
                        "limit_ms": bench.time_limit_ms,
                        "over_limit": bench.over_limit,
                        "shapes": [
                            {
                                "input": display_name(path, run.input_file),
                                "ms": run.elapsed_ms,
                                "timed_out": run.timed_out,
                            }
                            for run in bench.runs
                        ],
                    }
                    if bench is not None
                    else None
                ),
            },
        )
        return SolveResult(
            path=path,
            key=key,
            status="solved" if verified else "unverified",
            verdict="solved" if verified else "solved_unverified",
            attempts=attempt_no,
            cost_usd=total_cost,
            run_cost_usd=run_cost,
            bench_ms=bench.elapsed_ms if bench is not None and not bench.timed_out else None,
            bench_over_limit=bool(bench is not None and bench.over_limit),
            bench_shapes=len(bench.runs) if bench is not None else 0,
            detail=attempt.detail,
        )

    if force:
        # A forced re-run already overwrote main.rs: the old marker is stale now.
        (path / DONE).unlink(missing_ok=True)
    exhausted = attempt_no >= settings.max_attempts
    if exhausted:
        write_json(
            path / FAILED,
            {
                "key": key,
                "url": meta.get("url"),
                "failed_at": now(),
                "attempts": attempt_no,
                "verdict": verdict,
                "detail": attempt.detail[:4000],
                "cost_usd": total_cost,
            },
        )
    status = "failed" if exhausted else "pending"
    return SolveResult(path, key, status, verdict, attempt_no, total_cost, run_cost, attempt.detail)
