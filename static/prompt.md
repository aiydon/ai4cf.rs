# Codeforces {{KEY}} — solve it, then pass the official samples

Problem: **{{NAME}}** (rating {{RATING}}; tags: {{TAGS}})
Statement: {{URL}}
Working directory (your cwd): `{{DIR}}`
Limits: {{TIME_LIMIT}} time, {{MEMORY_LIMIT}} memory.

Files already here:

| file | meaning |
|---|---|
| `statement.html` / `statement.pdf` | official statement exactly as Codeforces serves it |
| `statement.txt` | plain-text rendering of the statement, limits included |
| `samples/NN.in`, `samples/NN.out` | official sample tests ({{SAMPLES}} sample pairs) — all must pass |
| `input.rs` | fast stdin reader (Rust, std only) provided as the reference implementation |
| `Makefile` | `make build`, `make test`, `make run`, `make fmt` |
| `meta.json` | limits, rating, tags, url |

{{FEEDBACK}}
## Deliverable

Write the complete solution into `./main.rs`. Hard requirements:

1. **Single self-contained file.** `rustc --edition 2021 -O main.rs` must compile it. Standard library only: no `Cargo.toml`, no crates, no `mod name;` / `include!` of other files, no file I/O, no reading anything but stdin.
2. **Embed the reader.** Copy the whole `input.rs` (`Input` struct + `impl`) into `main.rs` — start the file with `#![allow(dead_code)]`, then the reader, then your own code. It reads all of stdin once and yields borrowed tokens (`input.usize()`, `input.i64()`, `input.vector(n)`, `input.text()`, `input.raw()`, `input.is_eof()`), which is what you want for tight parsing. For interactive problems the reader is wrong — use line-based `read_line` plus `stdout().flush()` instead.
3. **stdin → stdout.** Print exactly what the statement asks for, one trailing newline per line. Never special-case the samples (no `if input == "..."` tables, no hardcoded answers): the real tests are different, much larger inputs.
4. **Performance is graded against the statement's constraints, not the samples.** Pick the intended complexity, then keep the constant factor sane: no per-token allocation, avoid `clone`/`format!`/`println!` in hot loops (build a `String` or `Vec<u8>`, or use `BufWriter`), prefer flat arrays and iterative algorithms, watch overflow (`i64`/`u64` as needed). State the complexity you achieved in the final summary.

## Verification (mandatory — this is the definition of done)

- `make build` — must compile cleanly. Fix warnings; do not use `unsafe` unless truly required.
- `make test` — builds and runs your binary on every sample, printing PASS/FAIL with a unified diff (trailing whitespace is ignored). **All samples must pass.**
- After you stop, the harness re-runs every sample on its own and enforces `{{TIME_LIMIT}} × {{TIME_MULT}}` as the wall-clock budget; anything slower than that counts as TLE. Time your worst case, not the samples (they get the same multiplier, on tiny inputs).
- Samples are weak evidence. When the problem is greedy/constructive/counting/DP, write a brute force plus a random generator under `./scratch/`, stress-test your solution against them (compare outputs over many random small cases), and fix every counterexample. `./scratch/` is scratch space and must not be referenced by `main.rs`.
- Never edit `samples/`, `statement.*`, `input.rs` or `Makefile` to make things pass.

## Final answer

Reply with the algorithm, the complexity, and — once every sample passes and `main.rs` is the final artifact — this exact line:

    STATUS: SOLVED

If you cannot make the samples pass, say precisely what you tried and end with `STATUS: BLOCKED <reason>`.
