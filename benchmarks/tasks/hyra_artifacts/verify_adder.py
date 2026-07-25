from __future__ import annotations

import argparse
import json
from pathlib import Path

from .verify import verify_adder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--verifier", required=True)
    parser.add_argument("--public-tests", type=int, default=10_000)
    parser.add_argument("--shadow-tests", type=int, default=10_000)
    parser.add_argument("--shadow-seeds", default="100003,100019")
    args = parser.parse_args()
    result = verify_adder(
        Path(args.artifact).resolve(),
        Path(args.verifier).resolve(),
        public_tests=args.public_tests,
        shadow_tests=args.shadow_tests,
        shadow_seeds=tuple(
            int(item.strip())
            for item in args.shadow_seeds.split(",")
            if item.strip()
        ),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
