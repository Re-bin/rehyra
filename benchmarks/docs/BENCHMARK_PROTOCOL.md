# Low-Cost Benchmark Protocol

This project deliberately separates four claims that are easy to conflate.

## Evidence levels

1. **Public artifact replay** asks whether Tencent's published final artifact
   reproduces its published metric under an independent checker. It tests the
   artifact and adapter, not this search harness.
2. **Predecessor-seeded search** starts from the independently reproduced
   `Prev best` named in Hyra's table, not from Hyra's final artifact. It asks
   whether a frozen search budget can autonomously reproduce or exceed Hyra's
   reported improvement from the same published starting line.
3. **Final-artifact warm start** starts from Hyra's published final artifact
   and asks whether asynchronous search preserves or improves it. It tests
   orchestration, selection, and non-regression, not autonomous rediscovery.
4. **De-novo search** starts from the benchmark's original weak seed and gives
   the Context and Proposal Agents a frozen budget. This is the only evidence
   that the harness autonomously discovered the whole improvement from the
   benchmark's initial program.

Reports and documentation must use those labels. A replay or final-artifact
warm start must never be described as an autonomous Hyra result. A
predecessor-seeded result is autonomous improvement but not de-novo discovery.

## Frozen low-cost non-AI4Fun suite

| Task | Frozen predecessor | Hyra target | Search contract |
|---|---:|---:|---|
| Smallest Adder ↓ | 36 parameters | 15 | upstream verifier + trained-category audit |
| Qubit Routing ↓ | 269,037 | 258,369 | pinned full 72-circuit SimpleTES suite |
| First Autocorrelation ↓ | 1.502870 | 1.502850 | pinned TTT-Discover sequence + FFT-equivalent metric |
| Second Autocorrelation ↑ | 0.962694 | 0.962901 | pinned SimpleTES sequence + scale-invariant FFT metric |
| Erdős Minimum Overlap ↓ | 0.380868 | 0.380859 | pinned SimpleTES construction + upstream metric |
| Sums-vs-Diffs ↑ | 1.14489 | 1.15971 | pinned SimpleTES construction + unbounded FFT support metric |
| Sunspot Symbolic ↑ | persistence forecast, 0.47 | 0.78 | causal search split + sealed confirmation era |

The predecessor adapter makes two metric contracts explicit:

- The published second-autocorrelation vector exceeds the current SimpleTES
  per-element cap. The declared comparison contract uses the published
  scale-invariant objective and records any global scale normalization.
- The published Sums-vs-Diffs set has 181,131 elements while the pinned
  SimpleTES task text caps candidates at 512. The formal public-comparison
  contract therefore uses an FFT implementation of the published finite-set
  metric and removes that unrelated cardinality cap. The original 512-element
  contract remains a separate development task and cannot support a claim of
  matching Hyra's public result.
- These adapters reproduce the published artifacts and pinned predecessors,
  but they are a documented metric reconstruction. Tencent did not publish its
  private evaluator executable, so they are not described as byte-identical to
  that executable.
- The Sunspot confirmation era is public historical data in 2026 and can
  already be present in model pretraining. The local filesystem split is
  causal, not a clean model-information holdout.

## Publication rules

- Freeze repository commits, predecessor artifact hashes, evaluator commands,
  model, reasoning effort,
  proposal count, concurrent slots, evaluator-query budget, wall clock, and
  task contract before a cold-start comparison.
- Count every Context, Proposal, Architect, and Critic call.
- Keep failed attempts in the Experience Bank.
- For a Sums-vs-Diffs comparison claim, independently compile
  `benchmarks/tasks/simpletes/verify_sums_exact.cpp` and run it on the frozen captured
  state. Its integer NTT support counts do not share the floating-point FFT
  implementation used during search.
- Compare historical-best curves, not only the final selected artifact.
- Use a separate hidden confirmation contract when one exists.
- Label local-process isolation `advisory`; it is not a contamination proof.
- Run causal/search workspaces outside the source checkout, expose only the
  declared seed workspace, and assert that Agent-visible feedback contains no
  evaluator command or sealed-data path. This reduces accidental discovery but
  still does not replace an attested filesystem sandbox.
- Report artifact replay, predecessor-seeded search, final-artifact warm start,
  and de-novo search in separate tables.

Tencent has not published the original harness, prompts, scheduling policy,
models, proposal budget, or compute budget. Consequently, this repository can
conform to every public-observable Hyra behavior and reproduce public
artifacts, but it cannot truthfully claim bit-for-bit reproduction of Tencent's
private search run.
