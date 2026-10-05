"""Build `main.rs` and run it against the official sample tests.

Single source of truth for "does this solution pass": the per-problem
`make test` target calls this module through `ai4cf check`.
"""

from __future__ import annotations

import difflib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Settings
from .workspace import load_meta

BUILD_TIMEOUT = 180.0
RUSTC_FLAGS = ("--edition", "2021", "-O")


@dataclass(slots=True)
class SampleOutcome:
    name: str
    ok: bool
    elapsed_ms: int
    detail: str = ""
    stderr: str = ""


@dataclass(slots=True)
class VerifyOutcome:
    path: Path
    built: bool
    build_output: str = ""
    samples: list[SampleOutcome] = field(default_factory=list)
    max_elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.built and bool(self.samples) and all(sample.ok for sample in self.samples)


def normalize(data: bytes) -> str:
    """Trailing whitespace and trailing blank lines are not judged by CF."""
    text = data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def build(path: Path) -> tuple[bool, str]:
    """`rustc --edition 2021 -O main.rs` — proves the file is self-contained."""
    source = path / "main.rs"
    if not source.is_file():
        return False, ""
    binary = path / "target" / "main"
    binary.parent.mkdir(exist_ok=True)
    try:
        proc = subprocess.run(
            ["rustc", *RUSTC_FLAGS, "-o", str(binary), "main.rs"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return False, "rustc not found in PATH"
    except subprocess.TimeoutExpired:
        return False, f"rustc timed out after {BUILD_TIMEOUT:g}s"
    output = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, output


def sample_files(path: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for input_file in sorted((path / "samples").glob("*.in")):
        expected = input_file.with_suffix(".out")
        if expected.is_file():
            pairs.append((input_file, expected))
    return pairs


def _diff(expected: str, actual: str, name: str) -> str:
    diff = list(
        difflib.unified_diff(
            expected.split("\n"),
            actual.split("\n"),
            fromfile=f"{name} (expected)",
            tofile=f"{name} (got)",
            lineterm="",
            n=1,
        )
    )
    return "\n".join(diff[:40])


def verify_problem(path: Path, settings: Settings) -> VerifyOutcome:
    ok, output = build(path)
    outcome = VerifyOutcome(path=path, built=ok, build_output=output)
    if not ok:
        return outcome

    meta = load_meta(path)
    limit_ms = int(meta.get("time_limit_ms") or 2000)
    timeout = max(1.0, limit_ms / 1000.0 * settings.time_mult)
    binary = path / "target" / "main"

    for input_file, expected_file in sample_files(path):
        name = input_file.stem
        with open(input_file, "rb") as stdin, open(expected_file, "rb") as expected:
            wanted = normalize(expected.read())
            start = time.perf_counter()
            try:
                proc = subprocess.run(
                    [str(binary)],
                    stdin=stdin,
                    capture_output=True,
                    cwd=path,
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                outcome.samples.append(
                    SampleOutcome(name, False, round(timeout * 1000), f"TLE: exceeded {timeout:g}s")
                )
                continue
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        outcome.max_elapsed_ms = max(outcome.max_elapsed_ms, elapsed_ms)
        got = normalize(proc.stdout)
        if proc.returncode != 0:
            outcome.samples.append(
                SampleOutcome(
                    name,
                    False,
                    elapsed_ms,
                    f"exit code {proc.returncode}\n{normalize(proc.stderr)[-800:]}",
                    normalize(proc.stderr)[-800:],
                )
            )
        elif got != wanted:
            outcome.samples.append(SampleOutcome(name, False, elapsed_ms, _diff(wanted, got, name)))
        else:
            outcome.samples.append(
                SampleOutcome(name, True, elapsed_ms, stderr=normalize(proc.stderr)[-400:])
            )
    return outcome


def format_outcome(outcome: VerifyOutcome, key: str = "") -> str:
    label = key or outcome.path.name
    lines = [f"== {label} ({outcome.path})"]
    if not outcome.built:
        lines.append("  BUILD FAILED")
        if outcome.build_output:
            lines.extend(f"  | {line}" for line in outcome.build_output.splitlines()[:20])
        return "\n".join(lines)
    if not outcome.samples:
        lines.append("  no sample tests available")
        return "\n".join(lines)
    for sample in outcome.samples:
        mark = "PASS" if sample.ok else "FAIL"
        lines.append(f"  {mark} sample {sample.name} ({sample.elapsed_ms} ms)")
        if not sample.ok:
            lines.extend(f"  | {line}" for line in sample.detail.splitlines()[:30])
        elif sample.stderr:
            lines.extend(f"  | stderr: {line}" for line in sample.stderr.splitlines()[:5])
    lines.append(f"  -> {'OK' if outcome.ok else 'NOT OK'}")
    return "\n".join(lines)
