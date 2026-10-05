"""`ai4cf` command line: download / solve / verify / bench / verdict / fmt / cost / status."""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from .bench import bench_problem, coverage_warning, format_bench
from .config import ROOT, Settings
from .cost import report, scan, status_report
from .fetch import Fetcher, FetchError
from .problemset import ProblemRef, iter_problemset
from .solver import SolveResult, solve_problem, terminate_all
from .verdict import fetch_latest_verdict, save_verdict
from .verify import format_outcome, sample_files, verify_problem
from .workspace import (
    DONE,
    FAILED,
    FETCHED,
    iter_problem_dirs,
    load_meta,
    materialize,
    problem_dir,
)


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM: stop pi *before* tearing the worker pool down.

    `ThreadPoolExecutor.__exit__` waits for running workers, so a worker blocked
    on pi would otherwise hold the whole process until PI_TIMEOUT.
    """

    def handler(signum: int, frame: object) -> None:
        terminate_all(grace=2.0)
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, handler)


def _parallel(
    items: list, worker, jobs: int, desc: str, postfix=lambda result: "", stop=lambda result: False
) -> list:
    results: list = []
    with tqdm(items, desc=desc, unit="prob", dynamic_ncols=True) as bar:
        if jobs <= 1:
            for item in bar:
                result = worker(item)
                results.append(result)
                bar.set_postfix_str(postfix(result))
                if stop(result):
                    break
        else:
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                futures = [pool.submit(worker, item) for item in items]
                for future in as_completed(futures):
                    result = future.result()
                    results.append(result)
                    bar.update(1)
                    bar.set_postfix_str(postfix(result))
                    if stop(result):
                        for pending in futures:
                            pending.cancel()
                        break
    return results


def _problem_filter(args) -> str | None:
    value = getattr(args, "problem", None) or os.environ.get("AI4CF_PROBLEM", "")
    return value.upper().replace("/", "") if value.strip() else None


def _selected_dirs(settings: Settings, args, *, what: str) -> list[Path]:
    wanted = _problem_filter(args)
    force = bool(getattr(args, "force", False))
    retry_failed = bool(getattr(args, "retry_failed", False))
    dirs: list[Path] = []
    for path in iter_problem_dirs(settings):
        key = str(load_meta(path).get("key") or path.name).upper().replace("/", "")
        if wanted and key != wanted:
            continue
        done = (path / DONE).is_file()
        failed = (path / FAILED).is_file()
        if what == "solve":
            if done and not force:
                continue
            if failed and not (retry_failed or force):
                continue
        elif what == "any":
            pass
        elif what == "verify":
            if not getattr(args, "all", False) and not done:
                continue
            if not (path / "main.rs").is_file():
                continue
        dirs.append(path)
    if getattr(args, "limit", None) is not None and args.limit > 0:
        dirs = dirs[: args.limit]
    return dirs


# ── commands ──────────────────────────────────────────────────────────────────


def _ref_from_meta(path: Path) -> ProblemRef | None:
    """Rebuild a `ProblemRef` from an existing workspace (no listing needed)."""
    meta = load_meta(path)
    if not meta.get("contest_id") or not meta.get("index"):
        return None
    return ProblemRef(
        contest_id=int(meta["contest_id"]),
        index=str(meta["index"]),
        name=str(meta.get("name") or ""),
        rating=meta.get("rating"),
        tags=tuple(meta.get("tags") or ()),
    )


def cmd_download(settings: Settings, args) -> int:
    wanted = _problem_filter(args)
    if wanted:
        return _download_one(settings, args, wanted)
    limit = settings.limit_or_none()
    # Enough listing pages so that `limit` *new* problems can be found (minus the
    # ones already on disk), instead of walking the whole problemset every run.
    already = sum(1 for path in iter_problem_dirs(settings) if (path / FETCHED).is_file())
    stop_after = None if limit is None else limit + already
    with Fetcher(settings) as fetcher:
        bar = tqdm(desc=f"problemset tags={settings.tags}", unit="page", dynamic_ncols=True)
        refs: list[ProblemRef] = []
        try:
            refs = iter_problemset(
                fetcher,
                settings,
                max_pages=args.page_limit,
                stop_after=stop_after,
                on_page=lambda page, found: (
                    bar.update(1),
                    bar.set_postfix(problems=len(refs) + found),
                ),
            )
        finally:
            bar.close()
        print(f"listing: {len(refs)} problems for tags={settings.tags!r}")
        pending = [ref for ref in refs if not (problem_dir(settings, ref) / FETCHED).is_file()]
        print(f"already downloaded: {len(refs) - len(pending)}; new: {len(pending)}")
        if limit is not None:
            pending = pending[:limit]
        if not pending:
            return 0
        results = _parallel(
            pending,
            lambda ref: materialize(ref, fetcher, settings, force=args.force),
            settings.workers,
            "download",
            lambda result: f"{result.ref.key} {result.status}",
        )
    fetched = [r for r in results if r.status == "fetched"]
    errors = [r for r in results if r.status == "error"]
    for result in errors:
        print(f"ERROR {result.ref.key}: {result.detail}", file=sys.stderr)
    print(f"downloaded {len(fetched)} problem(s), {len(errors)} error(s)")
    return 1 if errors else 0


def _download_one(settings: Settings, args, wanted: str) -> int:
    paths = [
        path
        for path in iter_problem_dirs(settings)
        if str(load_meta(path).get("key") or path.name).upper().replace("/", "") == wanted
    ]
    if not paths:
        print(f"no workspace for {wanted}; run `make download` first")
        return 2
    with Fetcher(settings) as fetcher:
        for path in paths:
            ref = _ref_from_meta(path)
            if ref is None:
                print(f"{path}: meta.json is incomplete")
                return 2
            result = materialize(ref, fetcher, settings, force=True)
            print(f"{result.status}: {result.path} ({ref.key})")
            return 0 if result.status in {"fetched", "exists"} else 1
    return 1


def cmd_solve(settings: Settings, args) -> int:
    dirs = _selected_dirs(settings, args, what="solve")
    if not dirs:
        print("nothing to solve (all problems are done, or nothing downloaded yet)")
        return 0
    print(
        f"solving {len(dirs)} problem(s) with jobs={settings.workers}, model={settings.pi_model or 'pi default'}"
    )
    results: list[SolveResult] = _parallel(
        dirs,
        lambda path: solve_problem(
            settings, path, force=args.force, retry_failed=args.retry_failed
        ),
        settings.workers,
        "solve",
        lambda result: f"{result.key} {result.verdict} ${result.cost_usd:.3f}",
        stop=lambda result: result.verdict == "interrupted",
    )
    for result in results:
        if result.verdict in {"solved", "solved_unverified", "interrupted"}:
            continue
        head = (result.detail or "").strip().splitlines()
        print(
            f"FAIL {result.key}: {result.verdict} (attempt {result.attempts}) — {head[0][:160] if head else ''}"
        )
    for result in results:
        if warning := coverage_warning(result.bench_shapes):
            print(f"WARN {result.key}: {warning}")
        if result.bench_over_limit:
            print(
                f"WARN {result.key}: samples pass but the max input takes "
                f"{result.bench_ms} ms — over the time limit, expect TLE on the judge"
            )
    solved = [r for r in results if r.verdict == "solved"]
    unverified = [r for r in results if r.verdict == "solved_unverified"]
    if unverified:
        print(
            "no official samples, accepted on pi's word (verified=false): "
            + ", ".join(r.key for r in unverified)
        )
    spent = sum(r.run_cost_usd for r in results)
    print(f"solved {len(solved)}/{len(results)}; cost of this run: ${spent:.4f}")
    print(f"project total: ${sum(row.cost_usd for row in scan(settings)):.4f}")
    if any(result.verdict == "interrupted" for result in results):
        print(
            "interrupted — pi stopped and this attempt was recorded; rerun to resume",
            file=sys.stderr,
        )
        return 130
    return 0


def cmd_all(settings: Settings, args) -> int:
    code = cmd_download(settings, args)
    if code != 0:
        print("download had errors; continuing with what is on disk", file=sys.stderr)
    return max(code, cmd_solve(settings, args))


def cmd_verify(settings: Settings, args) -> int:
    dirs = [path for path in _selected_dirs(settings, args, what="verify") if sample_files(path)]
    skipped = len(_selected_dirs(settings, args, what="verify")) - len(dirs)
    if not dirs:
        print(f"nothing to verify ({skipped} problem(s) have no sample tests)")
        return 0
    outcomes = _parallel(
        dirs,
        lambda path: verify_problem(path, settings),
        settings.workers,
        "verify",
        lambda outcome: (
            f"{outcome.path.parent.name}{outcome.path.name} {'ok' if outcome.ok else 'fail'}"
        ),
    )
    failures = [outcome for outcome in outcomes if not outcome.ok]
    if args.verbose:
        for outcome in outcomes:
            print(format_outcome(outcome, load_meta(outcome.path).get("key", "")))
    else:
        for outcome in failures:
            print(format_outcome(outcome, load_meta(outcome.path).get("key", "")))
    note = f" ({skipped} skipped: no sample tests)" if skipped else ""
    print(
        f"verified {len(outcomes) - len(failures)}/{len(outcomes)} problem(s) pass every sample{note}"
    )
    return 1 if failures else 0


def cmd_bench(settings: Settings, args) -> int:
    path = Path(args.path).resolve()
    if not (path / "main.rs").is_file():
        print(f"{path}: main.rs not found")
        return 2
    input_file = Path(args.input).resolve() if args.input else None
    outcome = bench_problem(path, settings, input_file=input_file)
    print(format_bench(outcome, str(load_meta(path).get("key") or path.name)))
    if not outcome.built:
        return 2
    if outcome.timed_out or outcome.failed:
        return 1
    return 1 if outcome.over_limit else 0


def cmd_verdict(settings: Settings, args) -> int:
    handle = args.handle or settings.handle
    if not handle:
        print("set AI4CF_HANDLE in .env (or pass --handle) to read your submissions")
        return 2
    dirs = _selected_dirs(settings, args, what="any")
    if not dirs:
        print("no matching workspace; run `make download` first")
        return 2
    with Fetcher(settings) as fetcher:
        for path in dirs:
            meta = load_meta(path)
            verdict = fetch_latest_verdict(
                fetcher, int(meta["contest_id"]), str(meta["index"]), handle
            )
            if verdict is None:
                print(f"{meta.get('key')}: no submission by {handle} for this problem")
                continue
            save_verdict(path, verdict)
            print(f"{meta.get('key')}: {verdict.headline()} — {verdict.language}")
    return 0


def cmd_check(settings: Settings, args) -> int:
    path = Path(args.path).resolve()
    if not (path / "main.rs").is_file():
        print(f"{path}: main.rs not found")
        return 2
    outcome = verify_problem(path, settings)
    print(format_outcome(outcome, str(load_meta(path).get("key") or path.name)))
    return 0 if outcome.ok else 1


def cmd_fmt(settings: Settings, args) -> int:
    if shutil.which("rustfmt") is None:
        print("rustfmt not found in PATH", file=sys.stderr)
        return 2
    targets = [ROOT / "static" / "input.rs"]
    targets += [
        path / "main.rs" for path in iter_problem_dirs(settings) if (path / "main.rs").is_file()
    ]
    command_base = ["rustfmt", "--edition", "2021"] + (["--check"] if args.check else [])
    failures = []
    for target in targets:
        proc = subprocess.run(
            [*command_base, str(target)],
            cwd=target.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            failures.append((target, (proc.stdout + proc.stderr).strip()))
    for target, output in failures:
        print(f"FORMAT FAIL {target}\n{output}")
    print(f"formatted {len(targets) - len(failures)}/{len(targets)} file(s)")
    return 1 if failures else 0


def cmd_cost(settings: Settings, args) -> int:
    print(report(settings, detail=args.problem))
    return 0


def cmd_status(settings: Settings, args) -> int:
    print(status_report(settings, _problem_filter(args)))
    return 0


# ── argument parsing ──────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai4cf", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, func) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text, description=help_text)
        child.set_defaults(func=func)
        child.add_argument(
            "-n", "--limit", type=int, default=None, help="override AI4CF_LIMIT (0 = all)"
        )
        child.add_argument(
            "-j", "--jobs", type=int, default=None, help="override AI4CF_JOBS (0 = auto)"
        )
        return child

    download = add("download", "crawl the problemset and create problem workspaces", cmd_download)
    download.add_argument(
        "-p", "--page-limit", type=int, default=None, help="stop after N listing pages"
    )
    download.add_argument("--force", action="store_true", help="re-download existing workspaces")
    download.add_argument(
        "-P",
        "--one-problem",
        dest="problem",
        default=None,
        help="refresh a single workspace, e.g. 2245/H",
    )

    solve = add("solve", "run pi on every pending problem", cmd_solve)
    solve.add_argument("--force", action="store_true", help="solve again even if marked done")
    solve.add_argument("--retry-failed", action="store_true", help="retry problems marked failed")
    solve.add_argument("-p", "--problem", default=None, help="only this problem, e.g. 2262/F")

    both = add("all", "download then solve", cmd_all)
    both.add_argument("--force", action="store_true", help="re-download and re-solve")
    both.add_argument("--retry-failed", action="store_true", help="retry problems marked failed")
    both.add_argument("--page-limit", type=int, default=None, help="stop after N listing pages")
    both.add_argument("-p", "--problem", default=None, help=argparse.SUPPRESS)

    verify = add("verify", "run every solved problem against its sample tests", cmd_verify)
    verify.add_argument("-p", "--problem", default=None, help="only this problem, e.g. 2262/F")
    verify.add_argument(
        "-a", "--all", action="store_true", help="also verify unsolved/attempted problems"
    )
    verify.add_argument("-v", "--verbose", action="store_true", help="print every sample outcome")
    verify.add_argument("--force", action="store_true", help=argparse.SUPPRESS)

    verdict = add(
        "verdict", "pull the judge's verdict on your last submission of a problem", cmd_verdict
    )
    verdict.add_argument("-p", "--problem", default=None, help="only this problem, e.g. 2245/H")
    verdict.add_argument("--handle", default=None, help="override AI4CF_HANDLE")

    bench = sub.add_parser("bench", help="time one problem on its worst-case input")
    bench.set_defaults(func=cmd_bench)
    bench.add_argument("path", nargs="?", default=".")
    bench.add_argument("-i", "--input", default=None, help="input file (default: scratch/max.in)")
    bench.add_argument("-n", "--limit", type=int, default=None, help=argparse.SUPPRESS)
    bench.add_argument("-j", "--jobs", type=int, default=None, help=argparse.SUPPRESS)

    check = sub.add_parser(
        "check", help="build and test one problem directory (used by its Makefile)"
    )
    check.set_defaults(func=cmd_check)
    check.add_argument("path", nargs="?", default=".")
    check.add_argument("-n", "--limit", type=int, default=None, help=argparse.SUPPRESS)
    check.add_argument("-j", "--jobs", type=int, default=None, help=argparse.SUPPRESS)

    fmt = add("fmt", "rustfmt every main.rs", cmd_fmt)
    fmt.add_argument("--check", action="store_true", help="only report files that need formatting")

    cost = add("cost", "cost report for every problem", cmd_cost)
    cost.add_argument("-p", "--problem", default=None, help="detail for one problem, e.g. 2262/F")

    status = add("status", "per-problem state (done/failed/pending)", cmd_status)
    status.add_argument("-p", "--problem", default=None, help="only this problem, e.g. 2262/F")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.load()
    overrides = {}
    if getattr(args, "limit", None) is not None:
        overrides["limit"] = args.limit
    if getattr(args, "jobs", None) is not None:
        overrides["jobs"] = args.jobs
    if overrides:
        settings = dataclasses.replace(settings, **overrides)
    _install_signal_handlers()
    try:
        return int(args.func(settings, args) or 0)
    except KeyboardInterrupt:
        terminate_all()
        print(
            "\ninterrupted — pi stopped, all progress kept on disk; rerun to resume",
            file=sys.stderr,
        )
        return 130
    except (RuntimeError, FetchError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
