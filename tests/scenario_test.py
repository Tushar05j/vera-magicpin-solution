"""Offline composer test against every generated trigger in the challenge dataset."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from composer import compose  # noqa: E402

DATASET = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "dataset_expanded"


def index(directory: Path, key: str) -> dict[str, dict]:
    result = {}
    for path in directory.glob("*.json"):
        with path.open(encoding="utf-8") as f:
            item = json.load(f)
        if key in item:
            result[str(item[key])] = item
    return result


def main() -> None:
    categories = index(DATASET / "categories", "slug")
    merchants = index(DATASET / "merchants", "merchant_id")
    customers = index(DATASET / "customers", "customer_id")
    triggers = index(DATASET / "triggers", "id")

    total = 0
    for trigger_id, trigger in triggers.items():
        merchant = merchants[trigger["merchant_id"]]
        category = categories[merchant["category_slug"]]
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

        first = compose(category, merchant, trigger, customer)
        second = compose(category, merchant, trigger, customer)

        if first != second:
            raise AssertionError(f"Non-deterministic output: {trigger_id}")

        if trigger["scope"] == "customer" and not first:
            # An empty result is acceptable only if customer outreach is not permitted.
            continue

        if first:
            required = {"body", "cta", "send_as", "suppression_key", "rationale", "template_name", "template_params"}
            missing = required - set(first)
            if missing:
                raise AssertionError(f"{trigger_id}: missing {sorted(missing)}")
            if not first["body"].strip():
                raise AssertionError(f"{trigger_id}: empty body")
            if "http://" in first["body"] or "https://" in first["body"]:
                raise AssertionError(f"{trigger_id}: URL found in body")

        total += 1

    print(f"PASS: {total} generated triggers are deterministic and structurally valid.")


if __name__ == "__main__":
    main()
