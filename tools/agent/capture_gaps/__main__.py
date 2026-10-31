import argparse
import json
from pathlib import Path
from .planner import evaluate


def main():
    parser = argparse.ArgumentParser(description="Evaluate an explicit capture gap; never dispatch a request.")
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    try:
        result = evaluate(json.loads(args.input.read_text(encoding="utf-8")))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.exit(2, f"Invalid capture-gap input: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
