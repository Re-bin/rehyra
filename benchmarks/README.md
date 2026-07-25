# Benchmarks

Everything needed to reproduce or inspect the published experiments lives
here:

| Path | Contents |
|---|---|
| `tasks/` | Benchmark bootstrap and evaluator adapters |
| `examples/` | Runnable campaign configurations |
| `solutions/` | Promoted solution artifacts |
| `evidence/` | Machine-readable experimental records |
| `docs/` | Protocol, method, and results |
| `requirements/` | Exact reproduction environments |
| `LICENSES/` | Third-party license texts and artifact mapping |

The ReHyra algorithm itself is in [`../rehyra/`](../rehyra/). Benchmark code
may import ReHyra; ReHyra never imports a specific benchmark.
