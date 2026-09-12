# Buy or Wait? Financial Agent

This submission uses a hybrid evidence-and-verification architecture. Unstructured image evidence is extracted into `image_evidence.json`; messages are parsed as untrusted evidence using narrow financial/date patterns. All money decisions are then made by a deterministic 90-day cash-flow simulator, so an instruction hidden in a message or receipt cannot alter the rules.

## Run

Python 3.10+ is the only runtime dependency. From the repository root:

```bash
python3 code/main.py
python3 code/evaluation/main.py --output output.csv --dataset dataset
```

For regression testing against the 25 public examples:

```bash
python3 code/main.py --samples --output sample_pred.csv
python3 code/evaluation/main.py --samples --output sample_pred.csv
```

Custom paths are supported with `--dataset` and `--output`.

## Decision pipeline

1. Join profile, events, fixed exchange rates, messages, image evidence, and offered payment plans by their documented identifiers.
2. Exclude cancelled, failed, unrealized, and pending-credit records. Reserve pending/scheduled debits and count scheduled confirmed credits.
3. Infer recurrence only from repeated, recent history. Stable monthly/weekly cadence is projected; variable debits use a conservative recent high. Salary changes, termination notices, rent amendments, invoice confirmation, and one-cycle pay reductions are applied from messages.
4. Compute `amount_safe_to_pay` from the lowest projected baseline balance, before optional spending changes.
5. Search full, partial, exact seller installment, waiting, and up-to-three permitted flexible-spending changes. Every candidate is independently simulated and must preserve the user's minimum balance and meet the completion deadline.
6. Rank safe candidates using the order in the problem statement and validate the output contract.

No request IDs, expected labels, or output answers are hardcoded. `image_evidence.json` is a reproducible evidence cache for the 16 supplied receipts/statements, not a prediction cache.
