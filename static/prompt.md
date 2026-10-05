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
{{JUDGE_FEEDBACK}}
{{FEEDBACK}}
## Deliverable

Write the complete solution into `./main.rs`. Hard requirements:

1. **Single self-contained file.** `rustc --edition 2021 -O main.rs` must compile it. Standard library only: no `Cargo.toml`, no crates, no `mod name;` / `include!` of other files, no file I/O, no reading anything but stdin.
2. **Keep it self-contained.** `input.rs` is a fast stdin reader you can embed (copy the `Input` struct and its `impl` into `main.rs`, with `#![allow(dead_code)]`) — or bring your own, as long as the file stands alone. For interactive problems the batched reader is wrong: read line by line and flush.
3. **stdin → stdout.** Print exactly what the statement asks for, one trailing newline per line. Never special-case the samples: the real tests are different, much larger inputs.

## Limits, and the judge's tests

The samples only prove correctness on trivial inputs. The judge's tests are adversarial and
much larger, and the limits are per test file: **{{TIME_LIMIT}}** time, **{{MEMORY_LIMIT}}**
memory. Your program has to fit both on the *worst legal input*, with margin, on a machine
that may be slower than this one.

Decide for yourself what that worst input looks like *for your own approach* — not "the
biggest one", but the structure that maximizes whatever your algorithm does per element of
input (work enumerated, hash probes, allocations, recursion depth, ...). Build a few of them
in `./scratch/` and measure: `make bench` times every `./scratch/*.in` you leave behind and
reports the worst time and peak memory against both limits.

Two things to keep in mind while you design the solution:

- Anything you materialize in bulk (pairs, tuples, hash entries, candidate lists) is charged
  twice, in time and in memory. If the amount you materialize is not bounded by a small
  factor of the input size, an adversarial test will find the gap between the samples and
  the constraints — treat that as an algorithmic bug, not as tuning.
- Correct-but-heavy is a failed submission. If the worst case is far worse than the typical
  case, change the algorithm until the bound holds for *every* legal input, then re-measure.

Report the worst time and memory you actually measured, and how you convinced yourself that
worse inputs cannot exist.

## Verification (mandatory — this is the definition of done)

- `make build` — must compile cleanly. Fix warnings; no `unsafe` unless truly required.
- `make test` — builds and runs your binary on every sample, printing PASS/FAIL with a unified diff (trailing whitespace is ignored). **All samples must pass.**
- `make bench` — builds and times your binary on **every** `./scratch/*.in`, printing each input and the worst one, with peak memory against the limit. Anything over either limit must be fixed before you finish; after you stop, the harness re-runs the samples and the worst input itself and enforces both limits.
- Samples are weak evidence. When the problem is greedy/constructive/counting/DP, write a brute force plus a random generator under `./scratch/`, stress-test your solution against them over many random small cases, and fix every counterexample.
- Never edit `samples/`, `statement.*`, `input.rs` or `Makefile` to make things pass.

## Final answer

Reply with the algorithm, the complexity, the worst measured time and memory, and why you believe worse inputs cannot exist. Once every sample passes and `main.rs` is the final artifact, end with this exact line:

    STATUS: SOLVED

If you cannot make the samples pass, say precisely what you tried and end with `STATUS: BLOCKED <reason>`.
