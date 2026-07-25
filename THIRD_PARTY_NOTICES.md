# Third-party notices

The Apache-2.0 license in [`LICENSE`](LICENSE) covers original ReHyra code and
documentation. It does not relicense third-party inputs or derived solution
artifacts.

The Hyra Harness diagram at [`.github/assets/hyra-loop.jpg`](.github/assets/hyra-loop.jpg)
is reproduced without modification from the
[Hyra Team's public launch article](https://hy.tencent.com/research/hyra).
Copyright remains with Tencent and the Hyra Team; the image is not relicensed
under ReHyra's Apache-2.0 license.

Full license texts, pinned source links, artifact mappings, and prominent
modification notices are provided in
[`benchmarks/LICENSES/README.md`](benchmarks/LICENSES/README.md). In particular:

- `benchmarks/solutions/adderboard_gauge_35p/` is a modified derivative of the
  MIT-licensed M10S seed.
- `benchmarks/solutions/autocorrelation_first_1_502862824/`,
  `benchmarks/solutions/erdos_min_overlap_0_380867498/`,
  `benchmarks/solutions/qubit_routing_298353/`, and
  `benchmarks/solutions/sums_diffs_1_160093274/` are modified solution
  artifacts retaining the applicable AGPL-3.0-or-later terms.
- The first-autocorrelation predecessor data also originates from the
  MIT-licensed TTT-Discover project.
- Public Hyra artifacts used for independent replay come from the
  Apache-2.0-licensed upstream artifact repository pinned in
  `benchmarks/evidence/benchmark_manifest.toml`.

The derived bundles were produced or modified by Re-bin Contributors and
published on 2026-07-24. Their exact bytes are intentionally frozen; this
repository-level notice avoids changing their content-addressed hashes.

Benchmark bootstrap commands fetch other upstream projects at pinned commits.
Those checkouts are not vendored here and remain governed by their own
licenses.
