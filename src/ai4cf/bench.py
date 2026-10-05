"""Measure `main.rs` on a worst-case input (`make bench`).

The judge only ever shows us sample tests, so performance has to be checked
locally: run the binary on a max-constraint input (the solver writes it to
`./scratch/max.in`) and report wall time and peak RSS next to the problem limit
and the fastest accepted submissions (see `status.py`).
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .verify import build, sample_files
from .workspace import load_meta

BENCH_INPUTS = ("scratch/max.in", "scratch/big.in", "scratch/bench.in")


@dataclass(slots=True)
class BenchOutcome:
    path: Path
    input_file: Path | None
    built: bool
    build_output: str = ""
    elapsed_ms: int = 0
    rss_kb: int = 0
    exit_code: int | None = None
    timed_out: bool = False
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    reference_fastest_ms: int | None = None
    reference_median_ms: int | None = None

    @property
    def from_sample(self) -> bool:
        """True when there was no `scratch/max.in` and a sample had to stand in."""
        return bool(self.input_file) and self.input_file.parent.name != "scratch"

    @property
    def over_limit(self) -> bool:
        return bool(self.time_limit_ms) and self.elapsed_ms > self.time_limit_ms

    @property
    def ok(self) -> bool:
        return self.built and not self.timed_out and self.exit_code == 0 and not self.over_limit


def bench_input(path: Path) -> Path | None:
    """The worst-case input the solver left behind, or the largest sample."""
    for candidate in BENCH_INPUTS:
        file = path / candidate
        if file.is_file():
            return file
    samples = [pair[0] for pair in sample_files(path)]
    return max(samples, key=lambda file: file.stat().st_size) if samples else None


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
        start = _now_ms()
        watchdog.start()
        try:
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            watchdog.cancel()
        elapsed = _now_ms() - start
        proc.returncode = os.waitstatus_to_exitcode(status)
    return elapsed, usage.ru_maxrss, proc.returncode, timed_out


def _now_ms() -> int:
    import time

    return round(time.perf_counter() * 1000)


def bench_problem(
    path: Path,
    settings: Settings,
    *,
    input_file: Path | None = None,
    already_built: bool = False,
) -> BenchOutcome:
    ok, output = (True, "") if already_built else build(path)
    outcome = BenchOutcome(path=path, input_file=input_file, built=ok, build_output=output)
    if not ok:
        return outcome

    meta = load_meta(path)
    outcome.time_limit_ms = meta.get("time_limit_ms")
    outcome.memory_limit_mb = meta.get("memory_limit_mb")
    status = meta.get("status") or {}
    outcome.reference_fastest_ms = status.get("fastest_ms")
    outcome.reference_median_ms = status.get("median_ms")

    chosen = input_file or bench_input(path)
    if chosen is None:
        outcome.built = False
        outcome.build_output = "no bench input: write ./scratch/max.in (max constraints) first"
        return outcome
    outcome.input_file = chosen

    limit_ms = int(outcome.time_limit_ms or 2000)
    timeout = max(5.0, limit_ms / 1000.0 * settings.bench_mult)
    elapsed, rss_kb, exit_code, timed_out = _run_timed(
        path / "target" / "main", path, chosen, timeout
    )
    outcome.elapsed_ms = elapsed
    outcome.rss_kb = rss_kb
    outcome.exit_code = exit_code
    outcome.timed_out = timed_out
    return outcome


def format_bench(outcome: BenchOutcome, key: str = "") -> str:
    label = key or outcome.path.name
    lines = [f"== {label} bench"]
    if not outcome.built:
        lines.append("  NOT RUN")
        if outcome.build_output:
            lines.extend(f"  | {line}" for line in outcome.build_output.splitlines()[:15])
        return "\n".join(lines)
    input_name = str(outcome.input_file.relative_to(outcome.path)) if outcome.input_file else "-"
    size = f"{outcome.input_file.stat().st_size / 1024:.0f} KiB" if outcome.input_file else "-"
    limit = outcome.time_limit_ms or 0
    share = f"{outcome.elapsed_ms / limit * 100:.0f}% of limit" if limit else "no limit known"
    reference = ""
    if outcome.reference_fastest_ms:
        reference = (
            f"  [status: fastest {outcome.reference_fastest_ms} ms"
            f", median {outcome.reference_median_ms} ms]"
        )
    lines.append(f"  input    : {input_name} ({size})")
    if outcome.from_sample:
        lines.append(
            "  !! no scratch/max.in: this measures a sample, not the worst case —"
            " generate a max-constraint input before trusting the number"
        )
    if outcome.timed_out:
        lines.append(f"  time     : > {outcome.elapsed_ms} ms — killed (limit {limit} ms)")
    else:
        lines.append(f"  time     : {outcome.elapsed_ms} ms ({share}){reference}")
    memory = f"{outcome.rss_kb / 1024:.1f} MiB" if outcome.rss_kb else "-"
    memory_limit = f" / limit {outcome.memory_limit_mb} MiB" if outcome.memory_limit_mb else ""
    lines.append(f"  memory   : {memory}{memory_limit}")
    lines.append(f"  exit     : {outcome.exit_code}")
    verdict = "OK" if outcome.ok else "TOO SLOW" if outcome.over_limit else "NOT OK"
    lines.append(f"  -> {verdict}")
    return "\n".join(lines)
