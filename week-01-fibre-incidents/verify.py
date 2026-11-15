"""Compare the pipeline's output with the hidden answer key (synthetic data only)."""
import json
import sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "output")
key = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else "data/answer_key.json").read_text())
got = json.loads((out / "summary.json").read_text())

checks = [("incidents", key["incidents"], got["incidents"]),
          ("ongoing incidents", key["ongoing_incidents"], got["ongoing_incidents"]),
          ("total downtime (minutes)", key["total_downtime_minutes"], got["total_downtime_minutes"]),
          ("distinct customers impacted", key["distinct_customers_impacted"], got["distinct_customers_impacted"]),
          ("customer-incident impacts", key["customer_incident_impacts"], got["customer_incident_impacts"])]

import csv
with (out / "incident_report.csv").open(newline="", encoding="utf-8") as f:
    rows = {(r["site_code"], r["start_utc"], r["end_utc"], int(r["downtime_minutes"]), int(r["impacted_customers"]))
            for r in csv.DictReader(f)}
want = {(i["site_code"], i["start_utc"], i["end_utc"] or "", i["downtime_minutes"], i["impacted_customers"])
        for i in key["incident_list"]}
checks.append(("incidents matching one-by-one", len(want), len(rows & want)))

ok = True
print(f"{'check':34} {'answer key':>11} {'pipeline':>9}")
for name, want_v, got_v in checks:
    good = want_v == got_v
    ok &= good
    print(f"{name:34} {want_v:>11} {got_v:>9}   {'OK' if good else 'MISMATCH'}")
print("\nALL CHECKS PASS" if ok else "\nSOME CHECKS FAILED")
sys.exit(0 if ok else 1)
