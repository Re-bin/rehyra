from __future__ import annotations

import argparse
import subprocess
import urllib.request
from pathlib import Path


HYRA_URL = "https://github.com/Tencent-Hunyuan/hyra-results.git"
HYRA_COMMIT = "bcda2e29200fbeaeb1620acca0670cc445e8688f"
SUNSPOT_URL = "https://www.sidc.be/SILSO/DATA/SN_m_tot_V2.0.csv"


def _git(*argv: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True)


def bootstrap(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    checkout = destination / "hyra-results"
    if not checkout.exists():
        _git("clone", "--no-checkout", HYRA_URL, str(checkout))
    _git("fetch", "--depth", "1", "origin", HYRA_COMMIT, cwd=checkout)
    _git("checkout", "--detach", HYRA_COMMIT, cwd=checkout)
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        text=True,
    ).strip()
    if actual != HYRA_COMMIT:
        raise RuntimeError(f"Hyra result pin mismatch: {actual}")
    sunspot_path = destination / "SN_m_tot_V2.0.csv"
    with urllib.request.urlopen(SUNSPOT_URL, timeout=60) as response:
        sunspot_path.write_bytes(response.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bootstrap(Path(args.output).resolve())


if __name__ == "__main__":
    main()
