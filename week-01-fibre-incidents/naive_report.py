"""
THE QUICK FIX (written on Friday against the small sample export).

Boss: "I need a report showing our fibre network incidents, affected locations,
       downtime and impacted customers. I need it by tomorrow morning."

It reads the NOC ticket log and adds things up. On the sample it works.
Try it on the full export:   python naive_report.py data/raw/noc_tickets.csv
"""
import csv
import sys
from datetime import datetime

path = sys.argv[1] if len(sys.argv) > 1 else "data/raw/noc_tickets_sample.csv"

incidents = 0
downtime_minutes = 0
customers = 0
locations = set()

with open(path, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        opened = datetime.strptime(row["opened_at"], "%Y-%m-%d %H:%M")
        closed = datetime.strptime(row["closed_at"], "%Y-%m-%d %H:%M")
        incidents += 1
        downtime_minutes += (closed - opened).total_seconds() / 60
        customers += int(row["customers_affected"])
        locations.add(row["location"])

print("Process Complete")
print(f"Incidents:          {incidents}")
print(f"Locations affected: {len(locations)}")
print(f"Downtime (hours):   {downtime_minutes / 60:.1f}")
print(f"Customers impacted: {customers}")
