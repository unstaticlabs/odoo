"""Evaluate tag proposals against independently labelled, held-out documents."""

import argparse
import json
from math import isfinite
from pathlib import Path


def evaluate(records, threshold):
    if not isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Threshold must be between 0 and 1.")
    reviewed = accepted = incorrect = missed = 0
    for row, record in enumerate(records, 1):
        scores = record.get("tag_scores")
        labels = record.get("expected_tag_ids")
        if not isinstance(scores, dict) or not isinstance(labels, list):
            raise ValueError(f"Row {row} needs tag_scores and expected_tag_ids.")
        if any(type(tag) is not int or tag <= 0 for tag in labels):
            raise ValueError(f"Row {row} contains an invalid expected tag ID.")
        selected = set()
        for key, score in scores.items():
            if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or int(key) <= 0:
                raise ValueError(f"Row {row} contains an invalid scored tag ID.")
            if type(score) not in (float, int) or not 0 <= score <= 1:
                raise ValueError(f"Row {row} contains an invalid classifier score.")
            if score >= threshold:
                selected.add(int(key))
        expected = set(labels)
        reviewed += 1
        accepted += len(selected & expected)
        incorrect += len(selected - expected)
        missed += len(expected - selected)
    return {
        "documents": reviewed, "threshold": threshold, "correct": accepted,
        "incorrect": incorrect, "missed": missed,
        "precision": accepted / (accepted + incorrect) if accepted + incorrect else None,
        "recall": accepted / (accepted + missed) if accepted + missed else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reviews", type=Path, help="JSON lines, one per reviewed document")
    parser.add_argument("--threshold", type=float, required=True)
    args = parser.parse_args()
    with args.reviews.open(encoding="utf-8") as rows:
        print(json.dumps(evaluate((json.loads(row) for row in rows), args.threshold), indent=2))
