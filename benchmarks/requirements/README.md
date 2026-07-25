# Reproduction environments

ReHyra itself has no runtime dependencies beyond Python 3.11+. The files in
this directory pin the optional scientific environments used for the published
artifact replay in
[`../evidence/hyra-public-artifact-replay.json`](../evidence/hyra-public-artifact-replay.json).

The recorded replay used Python 3.11.4 and two intentionally separate
environments:

```bash
python3.11 -m venv .venv-science
.venv-science/bin/python -m pip install \
  -r benchmarks/requirements/repro-science.txt

python3.11 -m venv .venv-qubit
.venv-qubit/bin/python -m pip install \
  -r benchmarks/requirements/repro-qubit.txt
```

The adder replay additionally used PyTorch:

```bash
.venv-science/bin/python -m pip install \
  -r benchmarks/requirements/repro-adder.txt
```

`repro-adder.txt` includes the science pins. General users who do not need to
reproduce the frozen report can install the broader `.[science]` or
`.[qubit-replay]` extras from `pyproject.toml`.

Exact pins reproduce the recorded package set; they are not a claim that these
older versions are appropriate for production or untrusted workloads.
