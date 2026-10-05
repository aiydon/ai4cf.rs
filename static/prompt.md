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

The judge grades the **real test data at maximum constraints**, and those tests are
*adversarial*, not random: the same solution can run 20-50x slower on a structured input
than on uniform random data of the same size. A "maximum size" input is **not** a
worst-case input, and samples prove nothing about speed.

Do all of this, in order:

1. State the intended complexity, and name the quantity your inner loop actually
   counts (pairs enumerated, hash probes, cells visited, edges relaxed, ...).
2. Write several max-constraint inputs into `./scratch/` (`make bench` measures every
   `scratch/max*.in` and reports the worst), including at least:
   - `max.in` — the shape that maximizes *that quantity for your algorithm*: all values
     equal, all zeros, value/zero stripes, one huge value, a degenerate 1 x N grid, ...
     This is the file that decides TLE.
   - `max2_*.in` — the remaining extremes (all zeros; all equal; exactly two distinct
     values; alternating stripes).
   - `max3_*.in` — the maximum number of test cases `t` with the smallest legal grids
     (per-test overhead), plus the largest grid split across a few tests, if the global
     `sum(n*m)` bound allows it.
   - `max4_*.in` — uniform random at maximum size, as the benign baseline.
3. Iterate until the **worst** shape is ≤ 1/5 of {{TIME_LIMIT}}, and no shape is killed by
   `make bench`. A benign shape at 2% of the limit means nothing while another is at 90%.
4. If the worst shape is far worse than the benign one, the *algorithm* is wrong, not the
   constant factor: bound the work your inner loop can do for **every** input of the stated
   size (argument sketch, not hope), and fix the algorithm until the bound holds.
5. Assume the judge's machine is slower than this one and its tests are worse than yours:
   keep a safety factor, and respect the memory limit ({{MEMORY_LIMIT}}).
6. Constant-factor traps to avoid: per-token `String` allocation, `format!`/`println!`
   inside loops (build one `String` or use `BufWriter`), `Vec<Vec<_>>` where one flat `Vec`
   with manual indexing works, cloning to appease the borrow checker, `HashMap` for dense
   small integer keys, `pow`/`%`/division per step, re-sorting inside a loop, recursion
   where a loop suffices.
7. `./scratch/` never leaks into `main.rs`: the submitted file must stay self-contained.

## Verification (mandatory — this is the definition of done)

- `make build` — must compile cleanly. Fix warnings; no `unsafe` unless truly required.
- `make test` — builds and runs your binary on every sample, printing PASS/FAIL with a unified diff (trailing whitespace is ignored). **All samples must pass.**
- `make bench` — builds and times your binary on **every** `scratch/max*.in`, printing each shape and the worst one; a run that blows past the time limit is killed and reported as `TOO SLOW`. Fix that before finishing. After you stop, the harness re-runs the samples and the worst shape itself and enforces the real time limit.
- Samples are weak evidence. When the problem is greedy/constructive/counting/DP, write a brute force plus a random generator under `./scratch/`, stress-test your solution against them over many random small cases, and fix every counterexample.
- Never edit `samples/`, `statement.*`, `input.rs` or `Makefile` to make things pass.

## Final answer

Reply with the algorithm, the measured complexity, the bench numbers (**every** shape, highlighting the worst, with time and memory), and — once every sample passes and `main.rs` is the final artifact — this exact line:

    STATUS: SOLVED

If you cannot make the samples pass, say precisely what you tried and end with `STATUS: BLOCKED <reason>`.
