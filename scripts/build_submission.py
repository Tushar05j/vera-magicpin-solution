"""Build the required 30-line submission.jsonl from the challenge dataset.

Usage:
    python scripts/build_submission.py /path/to/dataset/expanded

The script reads the canonical test_pairs.json and resolves each pair against
category, merchant, trigger and customer JSON files. It uses the exact same
composer used by the API, so the submitted JSONL cannot drift from runtime
behaviour.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer import compose  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def index_objects(directory: Path, id_key: str) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for path in directory.glob("*.json"):
        data = load_json(path)
        value = data.get(id_key)
        if value:
            index[str(value)] = data
    return index


def load_categories(dataset: Path) -> dict[str, dict[str, Any]]:
    return index_objects(dataset / "categories", "slug")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/build_submission.py <dataset/expanded>")

    dataset = Path(sys.argv[1]).resolve()
    if not dataset.exists():
        raise SystemExit(f"Dataset directory not found: {dataset}")

    categories = load_categories(dataset)
    merchants = index_objects(dataset / "merchants", "merchant_id")
    customers = index_objects(dataset / "customers", "customer_id")
    triggers = index_objects(dataset / "triggers", "id")
    pairs = load_json(dataset / "test_pairs.json").get("pairs", [])

    output: list[dict[str, Any]] = []

    for pair in pairs:
        test_id = pair["test_id"]
        merchant = merchants[pair["merchant_id"]]
        trigger = triggers[pair["trigger_id"]]
        category = categories[merchant["category_slug"]]
        customer = customers.get(pair.get("customer_id")) if pair.get("customer_id") else None

        result = compose(category, merchant, trigger, customer)
        if not result:
            raise RuntimeError(f"Composer returned no result for {test_id}")

        output.append({
            "test_id": test_id,
            "body": result["body"],
            "cta": result["cta"],
            "send_as": result["send_as"],
            "suppression_key": result["suppression_key"],
            "rationale": result["rationale"],
        })

    target = ROOT / "submission.jsonl"
    with target.open("w", encoding="utf-8") as f:
        for row in output:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    print(f"Wrote {len(output)} canonical cases to {target}")


if __name__ == "__main__":
    main()
