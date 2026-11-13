import csv
from pathlib import Path

from solution.store import Store


HEADER = [
    "campaign_id",
    "lead_id",
    "final_state",
    "attempts",
    "last_status",
    "last_disposition",
]


def write_final_state(store: Store, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with store._lock:
        rows = store.connection.execute(
            """SELECT campaign_id, lead_id, state, attempts,
                      last_status, last_disposition
               FROM leads
               ORDER BY campaign_id, lead_id"""
        ).fetchall()

    unfinished = [row for row in rows if row["state"] == "ready"]
    if unfinished:
        raise RuntimeError(
            f"Cannot write final output: {len(unfinished)} leads are unfinished"
        )

    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(HEADER)

        for row in rows:
            writer.writerow(
                [
                    row["campaign_id"],
                    row["lead_id"],
                    row["state"],
                    row["attempts"],
                    row["last_status"],
                    row["last_disposition"],
                ]
            )