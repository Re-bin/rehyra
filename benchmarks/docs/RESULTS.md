# Results

This file contains checked evidence summaries. Raw run directories remain local
under `runs/`; compact machine-readable evidence is stored under `benchmarks/evidence/`.
Every promoted ReHyra solution is published under `benchmarks/solutions/` and indexed by
`benchmarks/solutions/manifest.toml`. Tasks where no proposal displaced the seed are
recorded explicitly rather than omitted.

## Hyra public artifact replay

The low-cost, non-AI4Fun targets were independently checked from Tencent's
Apache-2.0 artifact repository at commit
`bcda2e29200fbeaeb1620acca0670cc445e8688f`.

| Task | Recomputed | Published |
|---|---:|---:|
| First Autocorrelation C1 ↓ | 1.502850302 | 1.502850 |
| Second Autocorrelation R ↑ | 0.962901101 | 0.962901 |
| Erdős Minimum Overlap C5 ↓ | 0.380859422 | 0.380859 |
| Sums-vs-Diffs C(A) ↑ | 1.159714799 | 1.15971 |
| Qubit Routing added CNOTs ↓ | 258,369 | 258,369 |
| Sunspot hidden-era R² ↑ | 0.779666516 | 0.78 |
| Smallest Adder parameters ↓ | 15 | 15 |

All seven public artifacts matched the published value at its stated
precision. The 15-parameter adder was evaluated with the upstream verifier on
10,000 random examples plus 10 edge cases for each of three seeds. It passed
10,005/10,010 for every seed (99.9500%), declared and exposed 15 trainable
parameters, contained no direct input addition, and exposed no nonstandard
floating buffers. A final trained-category claim still requires manual review.

The Qubit artifact has 61 changed circuit outputs while the frozen suite has 72
cases. The 11 omitted baseline-pass-through cases contribute exactly three
CNOTs: 258,366 explicit artifact SWAP-equivalents plus 3 equals the published
258,369. All 61 outputs pass pinned Qiskit topology and semantic-equivalence
validation.

The published AC2 values exceed the upstream evaluator's per-element cap. A
global scale normalization is required before evaluation; the objective is
scale-invariant and then reproduces 0.962901101.

Sunspot uses 1979–2025 as the half-century confirmation era. It reproduces the
reported 0.78, but this is public historical data in 2026 and therefore cannot
serve as a clean model-pretraining holdout.

The public Sums-vs-Diffs artifact has 181,131 elements, while the upstream
SimpleTES search prompt caps candidates at 512. The public-comparison adapter
now keeps that upstream contract as a separate development mode and removes
the artificial cap for a like-for-like mathematical comparison. These
distinctions are formalized in `benchmarks/docs/BENCHMARK_PROTOCOL.md`.

Machine-readable evidence:
`benchmarks/evidence/hyra-public-artifact-replay.json`.

## Final-artifact warm-start seeds

Six public artifacts that are executable under their task contract can now be
bootstrapped as ordinary, provider-neutral task seeds. Before any Agent call,
the generated seeds reproduce AC1 1.502850302, AC2 0.962901101, Erdős
0.380859422, Sums 1.159714799, the qualified 15-parameter Adder, and Sunspot's
0.779666516 confirmation R². Their task prompts explicitly label this as
final-artifact continuation, not autonomous rediscovery.

Qubit Routing is intentionally different. Tencent published 61 routed circuit
outputs, not executable general policy source. The artifact replay exactly
recovers 258,369 added CNOTs, but wrapping a case lookup table as a routing
policy would be misleading, so no executable warm-start claim is made.

Machine-readable evidence:
`benchmarks/evidence/hyra-final-warm-start-seeds-001.json`.

### Adder one-Proposal continuation

The Adder Context selected a sensitivity-guided 15→14 parameter merge. The
Proposal evaluated all plausible ties, trained the most recoverable candidates,
and found that none reached the required ≥99% margin. It therefore restored
the 15-parameter fallback before submission. The submitted file is byte-for-byte
identical to the public seed, passed the public and both shadow test sets, and
passed the transcript and offline evidence audits. This is a successful
non-regression result, not a new 14-parameter result.

Machine-readable evidence:
`benchmarks/evidence/adder-final-warm-start-codex-001.json`.

### Sunspot one-Proposal continuation

A one-Proposal warm-start run refit only the five coefficients of Hyra's public
symbolic law. It received feedback on 1932–1978 only. The refit was slightly
worse on that visible era, so the ordinary search incumbent correctly remained
the public seed. After the run, both the seed and the only generated candidate
were evaluated once on the unreported 1979–2025 confirmation era:

| Artifact | Visible-era R² | Confirmation R² ↑ |
|---|---:|---:|
| Public Hyra seed | 0.766369174 | 0.779666516 |
| Generated refit, run 1 | 0.755592611 | **0.787917864** |
| Generated refit, run 2 | 0.757062375 | **0.784694672** |
| Generated refit, run 3 (v0.3.0) | 0.764101371 | **0.782278136** |

The three confirmation improvements are 1.058318%, 0.644911%, and 0.334966%.
Confirmation was not fed back into search, and each independent run generated
exactly one candidate, so none of the results is a best-of-many holdout
selection. However, the full Sunspot history is public in 2026 and may exist in
model pretraining. The three-of-three result establishes repeatable
filesystem-causal improvement under the reconstructed scorer, not
uncontaminated temporal generalization.

Machine-readable evidence:
`benchmarks/evidence/sunspot-final-warm-start-codex-001.json` and
`benchmarks/evidence/sunspot-final-warm-start-codex-replication-001.json`. The v0.3.0
hardening regression check, including its 7/7 public replay and sealed run
root, is in `benchmarks/evidence/rehyra-v0.3.0-effect-check-001.json`.

## State-fixed science follow-ups

Three frozen four-Proposal campaigns started from independently pinned
predecessors and used their public-compatible objectives:

| Task | Predecessor | Autonomous best | Public Hyra | Outcome |
|---|---:|---:|---:|---|
| First Autocorrelation C1 ↓ | 1.502862898256 | 1.502862824395 | 1.502850302071 | improved seed; below Hyra |
| Second Autocorrelation R ↑ | 0.962693774996 | 0.962693774996 | 0.962901101096 | retained seed; below Hyra |
| Erdős Minimum Overlap C5 ↓ | 0.380867675827 | 0.380867498161 | 0.380859422365 | improved seed; below Hyra |

For AC1, three qualified proposals successively improved the predecessor and
the final score was independently reproduced with a NumPy FFT. One frozen
proposal slot failed during a DNS infrastructure outage and remains counted.
The gain was real but only 0.000004915%, too small to match Hyra.

For AC2, all four proposals qualified but were equal to the predecessor within
floating-point noise. The selector correctly retained the seed instead of
claiming an improvement.

For Erdős, attempts 1 and 2 successively improved the predecessor. Attempt 3
used a more complex second-order nullspace strategy but regressed slightly, so
the selector rejected it. The last Context call used that failure to return to
the stronger normalized-LP strategy; attempt 4 qualified but did not displace
attempt 2. An independent direct NumPy correlation reproduced the incumbent
within `6e-17`. Together these runs show that a valid ReHyra loop can
accumulate verified gains, learn from a strategy regression, and preserve a
strong incumbent, but a four-Proposal budget is not enough to reproduce
Tencent's final artifacts.

Machine-readable evidence:
`benchmarks/evidence/autocorrelation-first-heldout-codex-001.json` and
`benchmarks/evidence/autocorrelation-second-heldout-codex-001.json`, and
`benchmarks/evidence/erdos-heldout-codex-001.json`.

## Public-contract Sums-vs-Diffs improvement

A frozen four-Proposal `gpt-5.6-sol` campaign started from the pinned
SimpleTES predecessor at 1.1448868597133133. It was not given Hyra's final
artifact or exact target value. Its best autonomous construction achieved:

| Construction | \|A\| | \|A+A\| | \|A-A\| | C(A) ↑ |
|---|---:|---:|---:|---:|
| Published Hyra artifact | 181,131 | 1,439,434 | 1,081,975 | 1.1597147989125474 |
| This campaign | 502,004 | 3,999,449 | 3,003,457 | **1.1600932741088714** |

The absolute improvement over the published artifact is
0.00037847519632405735, or 0.032635%. The candidate is a distinct layered
residue construction over `[-1_000_000, 999_997]`, not a copy of Hyra's
artifact over `[0, 719719]`.

The frozen floating-point FFT evaluator and an independent exact integer NTT
verifier returned identical support sizes and score. Offline validation checked
36 artifacts, all nine Agent transcripts passed the external-tool audit, and a
target-value/artifact-size leakage scan found no matching Agent-visible files.
One Proposal failed during a DNS outage; that failure remains in the immutable
ledger and consumed its frozen budget. Resume reused completed attempts.

This establishes a result against Tencent's public artifact current on
2026-07-10. It is not described as an externally confirmed world record.

Machine-readable evidence:
`benchmarks/evidence/sums-diffs-public-contract-codex-001.json`.

### Independent Sums replication

A second frozen four-Proposal campaign used the same predecessor, public
contract, model class, sequential concurrency, and proposal budget. It improved
monotonically from 1.1448868597133133 to 1.1491736840731577, but did **not**
match the public Hyra artifact or the first campaign:

| Campaign | Best C(A) ↑ | Versus public Hyra |
|---|---:|---:|
| First frozen campaign | 1.1600932741088714 | +0.032635% |
| Independent replication | 1.1491736840731577 | -0.908940% |

The replication passed offline validation of 39 artifacts, all nine Agent
transcript audits, and the independent integer NTT check. It therefore cannot
be dismissed as an evaluator error. The correct conclusion is that the
Hyra-beating construction is a valid discovered artifact, but the four-Proposal
search policy does not yet exceed Hyra reliably. Both runs are retained; no
best-of-two aggregate is reported as a replication statistic.

Machine-readable evidence:
`benchmarks/evidence/sums-diffs-public-contract-codex-replication-001.json`.

## Coverage verdict

Every frozen low-cost non-AI4Fun task now has both an independent public
artifact replay and a real Agent-driven search run. These answer different
questions:

| Task | Artifact replay | Initial cold/search result | Public artifact | Initial cold matched? |
|---|---:|---:|---:|---:|
| Smallest Adder ↓ | 15 | 35 clean | 15 | no |
| Qubit Routing ↓ | 258,369 | 298,353 | 258,369 | no |
| First Autocorrelation ↓ | 1.502850302 | 1.514612842 | 1.502850302 | no |
| Second Autocorrelation ↑ | 0.962901101 | 0.916208969 | 0.962901101 | no; different contract |
| Erdős Minimum Overlap ↓ | 0.380859422 | 0.380911848 | 0.380859422 | no; 0.0138% gap |
| Sums-vs-Diffs ↑ | 1.159714799 | 1.093350419 | 1.159714799 | no; different contract |
| Sunspot raw R² ↑ | 0.779666516 | 0.696763545 | 0.779666516 | no; contaminated holdout |

Therefore the checked conclusion is:

- **Yes:** all seven public artifacts reproduce at published precision.
- **Yes:** all seven tasks exercised a real Context → Proposal → execution →
  feedback loop; no result is inferred from evaluator replay alone.
- **No:** the initial frozen, low-budget cold-search suite did not independently
  rediscover all seven Hyra artifacts.
- **Yes for Sums-vs-Diffs:** the later contract-matched four-Proposal campaign
  independently exceeded the public Hyra artifact, as reported above.
- **No:** exact one-for-one reproduction of Tencent's private search run is not
  a supportable claim because its harness, prompts, models, scheduler, and
  budgets are not public.

The generated-evaluator fixture separately exercises the outer
Architect → Critic → solution search → evaluator-evolution loop. The seven
benchmark-owned evaluators remain fixed by design; evolving them would change
the benchmark rather than reproduce Hyra's search result.

## Predecessor-seeded public-spec search

The stronger follow-up starts from the independently pinned predecessor named
in Hyra's comparison rather than from a weak benchmark seed. Source and deep
continuation campaigns are counted together; every continuation copied its
selected incumbent into a new immutable snapshot.

| Task | Evaluated predecessor | Autonomous best | Hyra target | Proposals | Matched? |
|---|---:|---:|---:|---:|---:|
| Smallest Adder ↓ | 36 | 33 | 15 | 12 | no |
| First Autocorrelation ↓ | 1.502862898 | 1.502862654 | 1.502850302 | 10 | no |
| Second Autocorrelation ↑ | 0.962693774996 | 0.962693775551 | 0.962901101 | 10 | no |
| Erdős Minimum Overlap ↓ | 0.380867675827 | 0.380867492176 | 0.380859422 | 12 | no |
| Sums-vs-Diffs ↑ | 1.144886860 | 1.145282556 | 1.159714799 | 12 | no |
| Qubit Routing ↓ | not completed locally | n/a | 258,369 | 0 | no |
| Sunspot raw R² ↑ | causal cold-start contract | 0.696763545 | 0.779666516 | 4 | no |

The Adder, AC1, AC2, Erdős, and Sums campaigns all passed offline manifest and
Agent-transcript validation. Adder, AC1, Erdős, and Sums improved their
evaluated starting point; AC2's increase was numerically valid but negligible.
None reproduced Hyra's final value under these 10–12 Proposal budgets.

The Sums row in this historical table used the original 512-element upstream
search contract. The later public-comparison campaign removed that artificial
cap and supersedes it for the artifact head-to-head; see
“Public-contract Sums-vs-Diffs improvement” above.

Qubit is a hardware-limited incomplete campaign, not a failed search result.
The exact 269,037-CNOT predecessor did not finish even seed qualification on
the local machine under progressively raised 1,200, 1,680, and 2,700 second
guards. Trial counts remained frozen at the full 20-layout/20-routing contract;
the harness did not silently substitute a cheaper evaluator. The separate
cold-start Qubit campaign did complete and improved 304,854 to 298,353.

Sunspot has no clean 2026 model-information holdout because the full historical
series is public. Its four-Proposal run is reported only as a filesystem-causal
cold-start search, not as a contract-matched predecessor rediscovery.

Machine-readable evidence:

- `benchmarks/evidence/adderboard-predecessor-deep-codex-001.json`
- `benchmarks/evidence/autocorrelation-first-predecessor-deep-codex-001.json`
- `benchmarks/evidence/autocorrelation-second-predecessor-deep-codex-001.json`
- `benchmarks/evidence/erdos-predecessor-deep-codex-001.json`
- `benchmarks/evidence/sums-diffs-predecessor-deep-codex-001.json`
- `benchmarks/evidence/qubit-routing-predecessor-local-timeout-001.json`
- `benchmarks/evidence/sunspot-causal-cold-codex-003.json`

This result supports **public workflow conformance and autonomous local
improvement**, but not a claim that Tencent's private search quality or compute
budget has been reproduced.

## Cold-start Sums-vs-Diffs search

A frozen four-proposal `gpt-5.6-sol` run under the current SimpleTES
512-element contract improved the original seed from 1.059793095 to
1.093350419 (+3.17%). The best candidate has 469 elements, 4,746 distinct
sums, and 3,895 distinct differences. Offline manifest validation passed.

This is autonomous cold-start evidence, but it is not a head-to-head
reproduction of the published 1.159714799 artifact: that artifact has 181,131
elements and is outside the frozen 512-element contract.

This historical cold-start result is retained as an ablation. It is superseded
for public-artifact comparison by the no-cap public-contract campaign above.

Machine-readable evidence:
`benchmarks/evidence/sums-diffs-cold-start-codex-001.json`.

## Causal Sunspot cold-start search

A four-proposal `gpt-5.6-sol` run could read only the training series and
received search feedback on 1932–1978. The 1979–2025 confirmation era stayed
outside the Agent workspace and was evaluated only after the run ended.

The run improved its penalized search score from 0.344927019 to 0.596527830
(+72.95%). It discovered the compact law
`c + a*x1 + b*x2 + d*x24`, including a 24-month seasonal term. On the
filesystem-sealed confirmation era it achieved raw R² 0.696763545 and
penalized score 0.676763545. Offline manifest validation passed.

This is evidence that the loop can discover and validate a new symbolic
artifact without seeing future files. It is **not** a clean model-generalization
claim: the full sunspot history is public in 2026 and may be present in model
pretraining. It also remains below the reproduced Hyra artifact's raw R²
0.779666516.

Machine-readable evidence:
`benchmarks/evidence/sunspot-causal-cold-codex-003.json`.

## Cold-start Qubit Routing search

A four-proposal `gpt-5.6-sol` run used the full 72-circuit SimpleTES suite with
20 layout and 20 routing trials. Every candidate passed all topology and
semantic-equivalence checks. The loop improved the frozen seed from 304,854 to
298,353 added CNOTs (-2.13%). The best candidate combined the strongest
two-ply swap policy found by one proposal with a safeguarded interaction-aware
initial layout found by another, demonstrating Experience-Bank recombination.
Offline manifest validation passed.

The cold search did **not** reproduce the public Hyra artifact's 258,369 added
CNOTs: it remains 39,984 CNOTs (15.48%) above that target. The artifact itself
was independently replayed exactly, but that is a different evidence level.

Machine-readable evidence:
`benchmarks/evidence/qubit-routing-cold-codex-001.json`.

## Cold-start Erdős Minimum Overlap search

A four-proposal `gpt-5.6-sol` run improved C5 from 0.504247683 to
0.380911848 (-24.46%). All five evaluated artifacts qualified and offline
manifest validation passed.

This is very close to, but does not equal, the independently replayed Hyra
artifact at 0.380859422. The absolute gap is 0.000052426, or 0.0138% above the
target. Displaying only four decimal places would conceal that gap, so this
report retains full precision and marks cold-search matching as false.

Machine-readable evidence:
`benchmarks/evidence/erdos-cold-codex-002.json`.

## Cold-start Second Autocorrelation search

Under the current executable SimpleTES contract, a two-proposal
`gpt-5.6-sol` run improved R from 0.905015019 to 0.916208969 (+1.24%). One
Proposal completed and qualified; the other exceeded the frozen 1,800-second
Agent budget, was killed as a process group, and remains recorded as a failed
attempt. Offline manifest validation, including the failure record, passed.

The best cold candidate is numerically 0.046692133 below the normalized public
Hyra artifact replay at 0.962901101. This is not a fair contract-matched
head-to-head: the public vector contains values above the current SimpleTES
per-element cap, whereas the executable cold candidates must obey it.

Machine-readable evidence:
`benchmarks/evidence/autocorrelation-second-cold-codex-001.json`.

## Cold-start First Autocorrelation search

A two-proposal `gpt-5.6-sol` run improved C1 from 1.524790629 to
1.514612842 (-0.67%). All evaluated artifacts qualified and offline manifest
validation passed. The winning Proposal used analytical gradients and a
smooth-maximum continuation; the competing exact-line-search proposal
regressed to 1.556334552 and therefore did not displace the incumbent.

The cold result did not reproduce the independently replayed Hyra artifact at
1.502850302. It is 0.011762540, or 0.78%, above that target.

Machine-readable evidence:
`benchmarks/evidence/autocorrelation-first-cold-codex-002.json`.

## Real generated-evaluator bilevel run

The model-independent integer fixture exercised the complete evaluator-free
path with real `gpt-5.6-sol` calls:

1. Architect created a strict deterministic evaluator.
2. An independent Critic approved it.
3. Context proposed the analytic direction.
4. Proposal changed the seed from `x=0` (score -18,769) to `x=137`
   (the unique global optimum, score 0).
5. The second Architect retained the evaluator.
6. The second Critic approved the unchanged evaluator.
7. The last approved evaluator stayed active and Context voluntarily stopped
   the unused proposal budget.

An earlier diagnostic run exposed two fail-closed integration defects: an
evolution-time Critic rejection was treated as fatal, and an Architect running
inside the repository could infer an undeclared output key from source files.
The clean rerun used a workspace outside the checkout, received the exact
frozen output contract through the ADK, kept the identical evaluator hash
across the second cycle, and passed offline validation. Model-free regression
tests cover rejection recovery and the frozen Architect contract.

Machine-readable evidence:
`benchmarks/evidence/generated-evaluator-codex-003.json`. The diagnostic failure/recovery
record remains at `benchmarks/evidence/generated-evaluator-codex-001.json`.

## Frozen seed qualification

The public M10S trained seed reports and exposes 36 trainable parameters.
Parameter enumeration independently counts 36.

Full verification:

| Split | Seed | Random tests | Edge cases | Accuracy |
|---|---:|---:|---:|---:|
| official | 2025 | 10,000 | 10 | 100.00% |
| hidden-1 | 100003 | 10,000 | 10 | 100.00% |
| hidden-2 | 100019 | 10,000 | 10 | 100.00% |

Total: 30,030 correct out of 30,030.

The automatic audit found no direct `a + b` implementation and confirmed that
declared and actual parameter counts match. A final leaderboard claim would
still require manual architectural and trained-category review.

## Codex ReHyra runs

### One-proposal smoke

A fresh Context Agent selected a one-parameter structural ablation. A fresh
Proposal Agent then removed the circular-embedding amplitude and absorbed the
scale into the tied attention and MLP weights.

| Artifact | Parameters | official | hidden-1 | hidden-2 |
|---|---:|---:|---:|---:|
| frozen M10S seed | 36 | 100.00% | 100.00% | 100.00% |
| Codex proposal | 35 | 100.00% | 100.00% | 100.00% |

Each percentage above covers 10,000 random examples plus 10 fixed edge cases.
The candidate therefore passed 30,030 out of 30,030 full-verification cases.

The candidate contains 35 trainable scalar values and no persistent learned
buffer. On 21,312 random autoregressive prefixes, its output argmax matched the
36-parameter source on every position. This supports the Proposal Agent's
explanation that the removed amplitude is a redundant gauge scale, rather than
an accuracy-reducing ablation.

The run used one `gpt-5.6-sol` Context call and one independent
`gpt-5.6-sol` Proposal call at high reasoning effort. The Proposal call took
394.8 seconds. Detailed hashes, usage and verification evidence are in
`benchmarks/evidence/codex-smoke-35p-full-verification.json`.

This result is promising but not an official leaderboard claim. The local
Codex sandbox provides advisory rather than attested isolation, the generated
metadata needs clearer derived-work attribution, and AdderBoard's source audit
still requires a human rules review.

### Four-proposal asynchronous pilot

The frozen pilot ran four Proposal Agents with concurrency two. Queue slots
were refilled as individual proposals completed, so the later proposals saw
new evidence without waiting for an entire generation.

The raw verifier produced this monotonic curve:

| Attempt | Raw parameters | Minimum pilot accuracy | Raw qualified |
|---|---:|---:|---:|
| seed | 36 | 100.00% | yes |
| proposal-1 | 34 | 99.80% | yes |
| proposal-2 | 31 | 100.00% | yes |
| proposal-3 | 28 | 100.00% | yes |
| proposal-4 | 24 | 100.00% | yes |

The 24p and 28p artifacts also passed independent full verification. The 24p
artifact passed 30,026/30,030 cases; the 28p artifact passed 29,986/30,030.

These are **raw verifier results, not trained-category records**. The 34p
candidate fixed learned query entries. The 31p, 28p and 24p lineages moved
increasing numbers of parent-calibrated weights into fixed buffers; the 24p
artifact fixes all seven learned normalization scalars. The automatic
parameter counter correctly reports trainable tensors but cannot decide that
semantic category question. Post-run audit therefore labels all four proposals
`hybrid_manual_review`.

The audited conclusion is:

- raw verifier best: 24p;
- clean symmetry-preserving result: 35p from the separate smoke;
- no claim that the four-proposal pilot established a new trained-category
  record.

The pilot took 25.2 minutes of wall time. Four Proposal calls consumed
8,367,415 input tokens (7,949,824 cached), 81,847 output tokens and 24,728
reasoning-output tokens. Three Context calls consumed 60,370 input and 2,015
output tokens. See `benchmarks/evidence/codex-pilot-audit.json`.

### Audit-aware Context replay

The initial loop retained complete files but exposed only score-centric compact
metadata to the Context Agent. That omission allowed the raw 31p result to
become the next parent even though its own Proposal summary disclosed that five
constants came from the trained parent.

The feedback path now returns Proposal diagnostics and evaluator review flags.
In a real Context-only replay, `gpt-5.6-sol` selected the clean 35p candidate
for an exact-symmetry search. It treated the raw 34p route only as a hypothesis
requiring fresh-from-scratch training and explicitly rejected calibrated fixed
values as proof. The replay is stored under
`benchmarks/evidence/audit-aware-context-replay/`.
