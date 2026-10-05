# Codeforces {{KEY}} — solve it, then pass the official samples

Problem: **{{NAME}}** (rating {{RATING}}; tags: {{TAGS}})
Statement: {{URL}}
Submit: {{SUBMIT_URL}} · Status: {{STATUS_URL}}
Working directory (your cwd): `{{DIR}}`
Limits: {{TIME_LIMIT}} time, {{MEMORY_LIMIT}} memory.

Files already here:

| file | meaning |
|---|---|
| `statement.html` / `statement.pdf` | official statement exactly as Codeforces serves it |
| `statement.txt` | plain-text statement (limits, tags, links, sample count) |
| `samples/NN.in`, `samples/NN.out` | official sample tests ({{SAMPLES}} sample pairs) — all must pass |
| `input.rs` | fast stdin reader (Rust, std only) provided as the reference implementation |
| `Makefile` | `make build`, `make test`, `make run`, `make bench`, `make fmt` |
| `meta.json` | limits, rating, tags, links, and the runtime reference (if any) |

{{STATUS_REF}}
{{FEEDBACK}}
## Deliverable

Write the complete solution into `./main.rs`. Hard requirements:

1. **Single self-contained file.** `rustc --edition 2021 -O main.rs` must compile it. Standard library only: no `Cargo.toml`, no crates, no `mod name;` / `include!` of other files, no file I/O, no reading anything but stdin.
2. **Embed the reader.** Copy the whole `input.rs` (`Input` struct + `impl`) into `main.rs` — start the file with `#![allow(dead_code)]`, then the reader, then your own code. It reads all of stdin once and yields borrowed tokens (`input.usize()`, `input.i64()`, `input.vector(n)`, `input.text()`, `input.raw()`, `input.is_eof()`). For interactive problems the reader is wrong — use line-based `read_line` plus `stdout().flush()` instead.
3. **stdin → stdout.** Print exactly what the statement asks for, one trailing newline per line. Never special-case the samples: the real tests are different, much larger inputs.

## Performance is a first-class requirement

The judge grades the **real test data at maximum constraints** — the samples prove nothing about speed.

- State the intended complexity before coding, then *measure it*. Write a generator for a maximum-constraint input into `./scratch/max.in` (respect the exact input format and the stated bounds, e.g. the largest `n`, `t`, value ranges) and run `make bench`: it prints wall time, peak memory, the share of the time limit, and the fastest/median accepted time of this problem (when known).
- Targets: **≤ 1/3 of {{TIME_LIMIT}} on your max input**, and within a small factor of the fastest accepted submission above. If the intended solution is clearly linear/log-linear and you are an order of magnitude slower, the constant factor is the bug — find it before finishing.
- Re-measure after every significant change; a "tiny" change that doubles the runtime is a regression.
- Known constant-factor traps to avoid: per-token `String` allocation, `format!`/`println!` inside loops (build one `String` or use `BufWriter`), `Vec<Vec<_>>` where one flat `Vec` with manual indexing works, cloning to appease the borrow checker, `HashMap` for dense small integer keys, `pow`/`%`/division per step, re-sorting inside a loop, recursion where a loop suffices, and `--release`-only optimizations that `rustc -O` does not perform.
- Memory: keep allocations proportional to the input; the memory limit is {{MEMORY_LIMIT}}.
- `./scratch/` never leaks into `main.rs`: the submitted file must stay self-contained.

## Verification (mandatory — this is the definition of done)

- `make build` — must compile cleanly. Fix warnings; no `unsafe` unless truly required.
- `make test` — builds and runs your binary on every sample, printing PASS/FAIL with a unified diff (trailing whitespace is ignored). **All samples must pass.**
- `make bench` — builds and times your binary on the worst-case input. Fix `TOO SLOW` before finishing. After you stop, the harness re-runs the samples and the max input itself and enforces the real time limit.
- Samples are weak evidence. When the problem is greedy/constructive/counting/DP, write a brute force plus a random generator under `./scratch/`, stress-test your solution against them over many random small cases, and fix every counterexample.
- Never edit `samples/`, `statement.*`, `input.rs` or `Makefile` to make things pass.

## Final answer

Reply with the algorithm, the measured complexity, the bench numbers (max input: time and memory), and — once every sample passes and `main.rs` is the final artifact — this exact line:

    STATUS: SOLVED

If you cannot make the samples pass, say precisely what you tried and end with `STATUS: BLOCKED <reason>`.
