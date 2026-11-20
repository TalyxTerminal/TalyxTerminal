"""Absolute login entry: independent of the Windows startup working directory."""

import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--start-in-tray", action="store_true", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    os.chdir(root)
    sys.path.insert(0, str(root))
    data_root = Path(args.data_root).resolve()
    if data_root == (root / "data/store").resolve():
        os.environ.pop("TALYX_DATA_DIR", None)
    else:
        os.environ["TALYX_DATA_DIR"] = str(data_root)
    from host.desktop.__main__ import main as desktop_main
    sys.argv = [sys.argv[0], "--start-in-tray", "--node", args.node]
    desktop_main()


if __name__ == "__main__":
    main()
