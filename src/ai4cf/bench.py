"""Measure `main.rs` on worst-case inputs (`make bench`).

The judge shows us only samples, so performance has to be checked locally — and
"maximum constraints" alone is not enough: a solution can be 25x slower on an
adversarial *structure* (all-equal values, value/zero stripes, maximum `t`, ...)
than on uniform random data of the same size. Therefore every file matching
`scratch/*.in` is measured, and the worst run is what counts.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .verify import build, sample_files
from .workspace import load_meta

BENCH_GLOB = "*.in"  # every input the solver leaves in scratch/ is measured

# Fewer shapes than this means the "worst case" claim rests on one input — which is
# exactly how a solution passes locally and then TLEs on the judge (see README).
MIN_SHAPES = 3


def coverage_warning(shape_count: int) -> str | None:
    """Warn when too few input shapes were measured to claim a worst case."""
    if shape_count == 0 or shape_count >= MIN_SHAPES:
        return None
    return (
        f"only {shape_count} max-input shape(s) measured (< {MIN_SHAPES}): the worst case "
        "is not covered — add adversarial scratch/*.in inputs"
    )


def display_name(path: Path, file: Path | None) -> str:
    """Relative name when the input lives in the workspace, absolute otherwise."""
    if file is None:
        return "-"
    try:
        return str(file.relative_to(path))
    except ValueError:
        return str(file)


def bench_inputs(path: Path) -> list[Path]:
    """Every worst-case input the solver left behind, or the largest sample."""
    scratch = path / "scratch"
    found = (
        [file for file in sorted(scratch.glob(BENCH_GLOB)) if file.is_file()]
        if scratch.is_dir()
        else []
    )
    if found:
        return found
    samples = [pair[0] for pair in sample_files(path)]
    return [max(samples, key=lambda file: file.stat().st_size)] if samples else []


@dataclass(slots=True)
class BenchRun:
    input_file: Path
    elapsed_ms: int = 0
    rss_kb: int = 0
    exit_code: int | None = None
    timed_out: bool = False


@dataclass(slots=True)
class BenchOutcome:
    path: Path
    built: bool
    runs: list[BenchRun] = field(default_factory=list)
    build_output: str = ""
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    reference_fastest_ms: int | None = None
    reference_median_ms: int | None = None
    explicit_input: bool = False

    @property
    def worst(self) -> BenchRun | None:
        if not self.runs:
            return None
        return max(self.runs, key=lambda run: (run.timed_out, run.elapsed_ms))

    @property
    def input_file(self) -> Path | None:
        return self.worst.input_file if self.worst else None

    @property
    def elapsed_ms(self) -> int:
        return self.worst.elapsed_ms if self.worst else 0

    @property
    def rss_kb(self) -> int:
        return max((run.rss_kb for run in self.runs), default=0)

    @property
    def exit_code(self) -> int | None:
        return self.worst.exit_code if self.worst else None

    @property
    def timed_out(self) -> bool:
        return any(run.timed_out for run in self.runs)

    @property
    def failed(self) -> bool:
        return any(not run.timed_out and run.exit_code not in (0, None) for run in self.runs)

    @property
    def from_sample(self) -> bool:
        """True when a sample had to stand in for the missing worst-case input."""
        return (
            not self.explicit_input
            and bool(self.input_file)
            and self.input_file.parent.name != "scratch"
        )

    @property
    def over_limit(self) -> bool:
        return bool(self.time_limit_ms) and self.elapsed_ms > self.time_limit_ms

    @property
    def over_memory(self) -> bool:
        return bool(self.memory_limit_mb) and self.rss_kb / 1024 > self.memory_limit_mb

    @property
    def ok(self) -> bool:
        return (
            self.built
            and not self.timed_out
            and not self.failed
            and not self.over_limit
            and not self.over_memory
        )


def _run_timed(
    binary: Path, path: Path, input_file: Path, timeout: float
) -> tuple[int, int, int, bool]:
    """Run once; return `(elapsed_ms, peak_rss_kb, exit_code, timed_out)`.

    `os.wait4` is used instead of `Popen.wait` because it reports rusage for
    *this* child only (rustc's peak RSS must not leak into the measurement).
    """
    with open(input_file, "rb") as stdin, open(os.devnull, "wb") as sink:
        proc = subprocess.Popen(
            [str(binary)],
            stdin=stdin,
            stdout=sink,
            cwd=path,
            start_new_session=True,
        )
        timed_out = False

        def stop() -> None:
            nonlocal timed_out
            timed_out = True
            try:
                os.killpg(os.getpgid(proc.pid), 15)
            except (ProcessLookupError, PermissionError):
                proc.kill()

        watchdog = threading.Timer(timeout, stop)
        start = round(time.perf_counter() * 1000)
        watchdog.start()
        try:
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            watchdog.cancel()
        elapsed = round(time.perf_counter() * 1000) - start
        proc.returncode = os.waitstatus_to_exitcode(status)
    return elapsed, usage.ru_maxrss, proc.returncode, timed_out


def bench_problem(
    path: Path,
    settings: Settings,
    *,
    input_file: Path | None = None,
    already_built: bool = False,
) -> BenchOutcome:
    ok, output = (True, "") if already_built else build(path)
    outcome = BenchOutcome(path=path, built=ok, build_output=output)
    if not ok:
        return outcome

    meta = load_meta(path)
    outcome.time_limit_ms = meta.get("time_limit_ms")
    outcome.memory_limit_mb = meta.get("memory_limit_mb")
    status = meta.get("status") or {}
    outcome.reference_fastest_ms = status.get("fastest_ms")
    outcome.reference_median_ms = status.get("median_ms")

    inputs = [input_file] if input_file else bench_inputs(path)
    outcome.explicit_input = input_file is not None
    if not inputs:
        outcome.built = False
        outcome.build_output = (
            "no bench input: write a worst-case input to ./scratch/<name>.in first"
        )
        return outcome

    limit_ms = int(outcome.time_limit_ms or 2000)
    timeout = max(5.0, limit_ms / 1000.0 * settings.bench_mult)
    binary = path / "target" / "main"
    for chosen in inputs:
        elapsed, rss_kb, exit_code, timed_out = _run_timed(binary, path, chosen, timeout)
        outcome.runs.append(
            BenchRun(
                input_file=chosen,
                elapsed_ms=elapsed,
                rss_kb=rss_kb,
                exit_code=exit_code,
                timed_out=timed_out,
            )
        )
    return outcome


def format_bench(outcome: BenchOutcome, key: str = "") -> str:
    label = key or outcome.path.name
    lines = [f"== {label} bench"]
    if not outcome.built:
        if outcome.build_output:
            lines.append("  NOT RUN")
            lines.extend(f"  | {line}" for line in outcome.build_output.splitlines()[:15])
        else:
            lines[-1] += " (build failed)"
            lines.extend(f"  | {line}" for line in outcome.build_output.splitlines()[:15])
        return "\n".join(lines)

    limit = outcome.time_limit_ms or 0
    for run in outcome.runs:
        name = display_name(outcome.path, run.input_file)
        size = f"{run.input_file.stat().st_size / 1024:.0f} KiB"
        share = f"{run.elapsed_ms / limit * 100:.0f}% of limit" if limit else "no limit known"
        head = "  time     : " if len(outcome.runs) == 1 else "  "
        if run.timed_out:
            lines.append(
                f"{head}{name} ({size}): > {run.elapsed_ms} ms — killed (limit {limit} ms)"
            )
        else:
            mark = ""
            if outcome.time_limit_ms and run.elapsed_ms > outcome.time_limit_ms:
                mark = "  !! over the time limit"
            lines.append(f"{head}{name} ({size}): {run.elapsed_ms} ms ({share}){mark}")
    if outcome.from_sample:
        lines.append(
            "  !! nothing in ./scratch/: this measures a sample, not the worst case —"
            " generate adversarial max-constraint inputs before trusting the number"
        )
    worst = outcome.worst
    if worst is not None and len(outcome.runs) > 1:
        name = display_name(outcome.path, worst.input_file)
        reference = ""
        if outcome.reference_fastest_ms:
            reference = (
                f" — status: fastest {outcome.reference_fastest_ms} ms"
                f", median {outcome.reference_median_ms} ms"
            )
        lines.append(f"  worst    : {name} at {worst.elapsed_ms} ms" + reference)
    memory = f"{outcome.rss_kb / 1024:.1f} MiB" if outcome.rss_kb else "-"
    memory_limit = ""
    if outcome.memory_limit_mb:
        used = outcome.rss_kb / 1024 / outcome.memory_limit_mb * 100
        memory_limit = f" / limit {outcome.memory_limit_mb} MiB ({used:.0f}%)"
    flag = "  !! over the memory limit" if outcome.over_memory else ""
    lines.append(f"  memory   : {memory}{memory_limit}{flag}")
    if outcome.failed:
        lines.append(f"  exit     : non-zero ({outcome.exit_code})")
    if outcome.ok:
        verdict = "OK"
    elif outcome.over_limit or outcome.timed_out:
        verdict = "TOO SLOW"
    elif outcome.over_memory:
        verdict = "OVER MEMORY"
    else:
        verdict = "NOT OK"
    lines.append(f"  -> {verdict}")
    return "\n".join(lines)
