from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from convert import convert


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not (workspace / "submissions" / "submission_36p.py").exists():
        raise FileNotFoundError(
            f"not an M10S seed workspace: {workspace}"
        )

    artifact_dir = Path(__file__).resolve().parent
    checkpoint = convert(workspace)
    destination = workspace / "submissions" / "submission.py"
    shutil.copy2(artifact_dir / "submission.py", destination)
    print(destination)
    print(checkpoint)


if __name__ == "__main__":
    main()

