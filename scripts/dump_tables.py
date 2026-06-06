"""Print the VLM table transcriptions from the latest local ingest (dev loop).

Reads ``var/crops/*.csv`` (one CSV per table, written by the pipeline) and prints
each one plus its parsed numeric-cell count, so you can eyeball transcription
quality for a card/table without publishing or opening the dashboard.
"""

import pathlib

from llm_metrics import paths, vlm_table


def main() -> None:
    csvs = sorted(pathlib.Path(paths.CROPS).glob("*.csv"))
    if not csvs:
        print(f"no table CSVs under {paths.CROPS} -- run the pipeline first")
        return
    total = 0
    for p in csvs:
        text = p.read_text()
        cells = vlm_table.parse_csv(text)
        total += len(cells)
        print("=" * 80)
        print(f"{p.name}  ({len(cells)} numeric cells)")
        print("-" * 80)
        print(text.strip())
    print("=" * 80)
    print(f"{len(csvs)} tables, {total} numeric cells total")


if __name__ == "__main__":
    main()
