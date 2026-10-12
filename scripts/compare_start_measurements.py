"""Compare start-light logs from several browsers. Nothing is uploaded.

Each file is one browser's own export. A lamp without an estimated PC time
is skipped. This script does not invent that time and it does not talk to
the race.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from slot_racing.modules.races.start_metrics import browser_spread, load_session


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Verwendung: compare_start_measurements.py datei.json [weitere.json …]")
        return 2
    logs = []
    for name in argv:
        document = json.loads(Path(name).read_text(encoding="utf-8"))
        logs.append(load_session(document))
    found = browser_spread(logs)
    if not found:
        print("Keine Lampen mit geschätzter PC-Zeit.")
        return 0
    print("Sequenz Phase Anzahl Min Max Median Spanne P95 P99")
    for item in found:
        stats = item.spread
        print(
            item.sequence_id,
            item.phase,
            stats.count,
            _cell(stats.minimum),
            _cell(stats.maximum),
            _cell(stats.median),
            _cell(stats.span),
            _cell(stats.p95),
            _cell(stats.p99),
        )
    return 0


def _cell(value: int | None) -> str:
    if value is None:
        return "-"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
