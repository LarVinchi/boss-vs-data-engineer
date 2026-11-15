"""
THE QUICK PATCH (what you write at 11 PM when the full export crashes the quick fix).

Every line below is a reasonable-looking shortcut. Together they produce
confident, wrong numbers. Compare its output with data/answer_key.json.
"""
import csv
import sys
from datetime import datetime

FORMATS = ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %I:%M %p"]


def parse(text):
    for fmt in FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            pass
    return None


path = sys.argv[1] if len(sys.argv) > 1 else "data/raw/noc_tickets.csv"
incidents = 0
downtime_minutes = 0
customers = 0
locations = set()
skipped = 0

with open(path, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        opened, closed = parse(row["opened_at"]), parse(row["closed_at"])
        if opened is None or closed is None:      # "can't parse it, skip it"
            skipped += 1
            continue
        incidents += 1                            # one ticket = one incident?
        downtime_minutes += (closed - opened).total_seconds() / 60
        try:
            customers += int(row["customers_affected"])
        except ValueError:                        # "approx 59", "75+", blank: count as zero
            pass
        locations.add(row["location"])            # names used exactly as typed

print("Process Complete")
print(f"Incidents:          {incidents}   (skipped {skipped} rows)")
print(f"Locations affected: {len(locations)}")
print(f"Downtime (hours):   {downtime_minutes / 60:.1f}")
print(f"Customers impacted: {customers}")
