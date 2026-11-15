"""
Synthetic fibre-network incident data generator.  ALL DATA IS FICTIONAL.

It creates the kind of mess a real operations team produces, from ONE hidden truth:

    data/raw/noc_tickets.csv          manual NOC log (local time, mixed date formats, messy names)
    data/raw/noc_tickets_sample.csv   the small, clean export the quick script was written against
    data/raw/monitoring_alarms.json   automated alarms (UTC), with duplicates and short blips
    data/raw/customers.csv            customer inventory
    data/raw/site_reference.csv       site codes, names, zones, known aliases
    data/answer_key.json              the true totals, so the pipeline can be checked

Seeded: everyone who runs it gets the same files.
"""
import argparse
import csv
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SEED = 2026
ONGOING_ORDER = (1, 0, 2)      # a fixed order keeps the output identical on every run
UTC = timezone.utc
LOCAL_OFFSET = timedelta(hours=1)          # the NOC logs tickets in local time, UTC+1
PERIOD_START = datetime(2026, 9, 30, 23, 0, tzinfo=UTC)     # 1 Oct 00:00 local
AS_OF = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)             # report cut-off

# code: (name, zone, customers, known aliases)
SITES = {
    "HP1": ("Harbour Point Phase 1", "Harbour", 120, ["Harbour Pt Phase 1", "Harbour Point Ph 1", "HP1"]),
    "HP2": ("Harbour Point Phase 2", "Harbour", 95, ["Harbour Pt Phase 2", "Harbour Point Ph 2", "HP2"]),
    "PE": ("Palm Estate", "Gardens", 140, ["Palm Est", "Palm Estate Gate"]),
    "RC": ("Riverside Close", "Riverside", 70, ["Riverside Cl", "Riverside Cls"]),
    "CBP": ("Central Business Park", "Central", 160, ["Central Biz Park", "CBP", "Central Business Pk"]),
    "GV": ("Green Valley", "Gardens", 110, ["Green Vly", "Green Valley Estate"]),
    "NR": ("North Ridge", "Hills", 85, ["N. Ridge", "North Rdge"]),
    "SR": ("Station Road", "Central", 60, ["Station Rd", "Station Road Cabinet"]),
}
TYPOS = ["Harbor Point Phase 1", "Palm Estae", "Riversde Close", "Greenvalley Est"]   # NOT in the alias table

CAUSES = {
    "P1": ["Fibre cut - third party excavation", "Fibre cut", "Backhaul fibre damaged"],
    "P2": ["Power failure at cabinet", "Equipment fault - OLT card", "Weather damage", "Vandalism / theft"],
    "P3": ["Planned maintenance overrun", "Equipment fault", "Power failure"],
}
SEV_WORDS = {
    "P1": ["P1", "Critical", "critical", "Sev 1", "SEV1"],
    "P2": ["P2", "Major", "major", "Sev 2"],
    "P3": ["P3", "Minor", "minor", "Sev 3"],
}
TIME_FORMATS = ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %I:%M %p"]   # NOC uses day-first


def fmt_local(dt, fmt):
    if dt is None:
        return ""
    s = (dt + LOCAL_OFFSET).strftime(fmt)
    return s.replace(" 0", " ") if fmt.startswith("%d %b") else s


def messy_name(rng, code, allow_unmapped):
    std, _, _, aliases = SITES[code]
    if allow_unmapped and rng.random() < 0.07:
        return rng.choice(TYPOS)
    base = rng.choice([std, std, aliases[0], rng.choice(aliases)])
    return rng.choice([lambda s: s, str.lower, str.upper, lambda s: f"  {s}  ", lambda s: s + "."])(base)


def make_truth(rng):
    """Hidden truth: ~46 real outages, never overlapping at the same site."""
    by_site, truth = {c: [] for c in SITES}, []
    codes = list(SITES)
    weights = [3, 2, 4, 2, 5, 3, 2, 2]
    span = int((AS_OF - PERIOD_START).total_seconds() // 60)
    sampled = rng.sample(codes, 3)
    ongoing_sites = [sampled[i] for i in ONGOING_ORDER][::-1]      # list.pop() visits them in ONGOING_ORDER
    while len(truth) < 46:
        code = rng.choices(codes, weights)[0]
        ongoing = False
        if ongoing_sites and len(truth) >= 43:
            code = ongoing_sites.pop()
            ongoing = True
        if ongoing:
            start = AS_OF - timedelta(minutes=rng.randint(180, 2400))
            end = None
            span_end = AS_OF
        else:
            dur = rng.choice([rng.randint(30, 120)] * 6 + [rng.randint(120, 360)] * 3 + [rng.randint(360, 900)])
            start = PERIOD_START + timedelta(minutes=rng.randint(0, span - dur - 60))
            end = start + timedelta(minutes=dur)
            span_end = end
            if end > AS_OF - timedelta(minutes=30):
                continue
        buf = timedelta(minutes=90)
        if any(not (span_end + buf < s or e is None or e + buf < start) for s, e in by_site[code] if True) and not ongoing:
            continue
        if ongoing and any(e is None or e + buf > start for s, e in by_site[code]):
            continue
        sev = rng.choices(["P1", "P2", "P3"], [2, 5, 4])[0]
        by_site[code].append((start, end))
        truth.append({"site": code, "start": start, "end": end, "severity": sev,
                      "cause": rng.choice(CAUSES[sev])})
    truth.sort(key=lambda t: t["start"])
    return truth


def make_customers(rng):
    rows, n = [], 0
    for code, (_, _, count, _) in SITES.items():
        for _ in range(count):
            n += 1
            if rng.random() < 0.08:
                act = date(2026, 10, rng.randint(1, 28))
            else:
                act = date(2025, 1, 1) + timedelta(days=rng.randint(0, 600))
            term = ""
            r = rng.random()
            if r < 0.04:
                term = (date(2026, 9, rng.randint(1, 28))).isoformat()
            elif r < 0.06:
                term = (date(2026, 10, rng.randint(5, 28))).isoformat()
            rows.append({"customer_id": f"C{n:05d}", "site_code": code,
                         "plan": rng.choice(["50Mbps", "100Mbps", "200Mbps"]),
                         "activated_at": act.isoformat(), "terminated_at": term})
    return rows


def active_customers(customers, code, start_utc):
    d = (start_utc + LOCAL_OFFSET).date().isoformat()
    return [c["customer_id"] for c in customers if c["site_code"] == code and c["activated_at"] <= d
            and (c["terminated_at"] == "" or c["terminated_at"] > d)]


def main(out_dir="data"):
    rng = random.Random(SEED)
    out = Path(out_dir)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    customers = make_customers(rng)
    truth = make_truth(rng)
    for t in truth:
        t["customers"] = active_customers(customers, t["site"], t["start"])

    alarms, tickets, aid, tid = [], [], 0, 0
    ticket_only = set()
    for i, t in enumerate(truth):
        has_alarm, has_ticket = rng.random() < 0.85, rng.random() < 0.80
        if not has_alarm and not has_ticket:
            has_ticket = True
        t["has_alarm"], t["has_ticket"] = has_alarm, has_ticket
        sev_n = int(t["severity"][1])
        dur_min = None if t["end"] is None else int((t["end"] - t["start"]).total_seconds() // 60)
        if has_alarm:
            aid += 1
            alarms.append({"alarm_id": f"ALM-{aid:06d}", "device_id": f"{t['site']}-OLT-01", "site_code": t["site"],
                           "severity": sev_n, "raised_at": t["start"], "cleared_at": t["end"]})
            if rng.random() < 0.30 and (dur_min is None or dur_min >= 40):
                aid += 1
                lo = t["start"] + timedelta(minutes=rng.randint(1, 15))
                hi = None if t["end"] is None else t["end"] - timedelta(minutes=rng.randint(0, 10))
                alarms.append({"alarm_id": f"ALM-{aid:06d}", "device_id": f"{t['site']}-ONT-C{rng.randint(1, 4)}",
                               "site_code": t["site"], "severity": sev_n, "raised_at": lo, "cleared_at": hi})
        if has_ticket:
            tid += 1
            fmt = rng.choice(TIME_FORMATS)
            if has_alarm:
                opened = t["start"] + timedelta(minutes=rng.randint(0, 25))
                closed = None if t["end"] is None else t["end"] + timedelta(minutes=rng.randint(5, 40))
            else:
                opened, closed = t["start"], t["end"]
                ticket_only.add(i)
            t["bad_close"] = False
            if has_alarm and closed is not None and rng.random() < 0.07:      # recoverable data-entry error
                closed = opened - timedelta(hours=rng.randint(1, 3))
                t["bad_close"] = True
            n = len(t["customers"])
            cust_field = rng.choice([str(n), str(n), f"approx {max(n - rng.randint(0, 8), 1)}", f"{n}+", ""])
            tickets.append({"ticket_id": f"TKT-{tid:05d}", "opened_at": fmt_local(opened, fmt),
                            "closed_at": fmt_local(closed, fmt),
                            "location": messy_name(rng, t["site"], allow_unmapped=has_alarm),
                            "severity": rng.choice(SEV_WORDS[t["severity"]]),
                            "cause": rng.choice([t["cause"], t["cause"].lower(), t["cause"].upper()]),
                            "customers_affected": cust_field, "reported_by": "NOC"})
            if has_alarm and rng.random() < 0.15:                             # customer care opens a second ticket
                tid += 1
                o2 = opened + timedelta(minutes=rng.randint(5, 20))
                c2 = None if closed is None or t["bad_close"] else closed + timedelta(minutes=rng.randint(0, 15))
                tickets.append({"ticket_id": f"TKT-{tid:05d}", "opened_at": fmt_local(o2, fmt),
                                "closed_at": fmt_local(c2, fmt), "location": messy_name(rng, t["site"], False),
                                "severity": "P3", "cause": "Customers report no service",
                                "customers_affected": "", "reported_by": "Customer care"})

    # junk: test tickets and rows with no usable location / time
    for k in range(1, 5):
        tid += 1
        tickets.append({"ticket_id": f"TKT-TEST-{k}", "opened_at": "2026-10-12 09:00", "closed_at": "2026-10-12 09:05",
                        "location": "TEST SITE", "severity": "P3", "cause": "Test ticket - ignore",
                        "customers_affected": "0", "reported_by": "NOC"})
    for k in range(2):
        tid += 1
        tickets.append({"ticket_id": f"TKT-{tid:05d}", "opened_at": "TBC", "closed_at": "", "location": "",
                        "severity": "P2", "cause": "fibre cut", "customers_affected": "", "reported_by": "NOC"})

    # re-exports: some tickets appear twice
    dupes = rng.sample(tickets, max(1, len(tickets) // 10))
    tickets.extend(dict(d) for d in dupes)
    rng.shuffle(tickets)

    # short blips that are NOT incidents (1-4 minutes, away from real outages)
    noise = 0
    while noise < 40:
        code = rng.choice(list(SITES))
        s = PERIOD_START + timedelta(minutes=rng.randint(0, int((AS_OF - PERIOD_START).total_seconds() // 60) - 10))
        if any(x["site"] == code and x["start"] - timedelta(minutes=60) < s < (x["end"] or AS_OF) + timedelta(minutes=60)
               for x in truth):
            continue
        aid += 1
        alarms.append({"alarm_id": f"ALM-{aid:06d}", "device_id": f"{code}-ONT-C{rng.randint(1, 4)}", "site_code": code,
                       "severity": rng.choice([3, 4]), "raised_at": s, "cleared_at": s + timedelta(minutes=rng.randint(1, 4))})
        noise += 1
    alarms.extend(dict(a) for a in rng.sample(alarms, max(1, len(alarms) // 20)))   # duplicate alarm records
    rng.shuffle(alarms)

    iso = lambda d: None if d is None else d.strftime("%Y-%m-%dT%H:%M:%SZ")
    with (raw / "monitoring_alarms.json").open("w", encoding="utf-8") as f:
        json.dump([{**a, "raised_at": iso(a["raised_at"]), "cleared_at": iso(a["cleared_at"])} for a in alarms], f, indent=1)
    cols = ["ticket_id", "opened_at", "closed_at", "location", "severity", "cause", "customers_affected", "reported_by"]
    with (raw / "noc_tickets.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(tickets)
    with (raw / "customers.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(customers[0]))
        w.writeheader()
        w.writerows(customers)
    with (raw / "site_reference.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["site_code", "site_name", "zone", "aliases"])
        for code, (name, zone, _, al) in SITES.items():
            w.writerow([code, name, zone, "|".join(al)])

    # the small, tidy export the quick script was first written against
    sample = [t for i, t in enumerate(truth) if t["has_ticket"] and t["end"] is not None][:12]
    with (raw / "noc_tickets_sample.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for k, t in enumerate(sample, 1):
            w.writerow({"ticket_id": f"S-{k:04d}", "opened_at": fmt_local(t["start"], TIME_FORMATS[0]),
                        "closed_at": fmt_local(t["end"], TIME_FORMATS[0]), "location": SITES[t["site"]][0],
                        "severity": t["severity"], "cause": t["cause"], "customers_affected": str(len(t["customers"])),
                        "reported_by": "NOC"})

    # the answer key
    ongoing = [t for t in truth if t["end"] is None]
    key_inc, distinct = [], set()
    for t in truth:
        end = t["end"] or AS_OF
        key_inc.append({"site_code": t["site"], "start_utc": iso(t["start"]), "end_utc": iso(t["end"]),
                        "downtime_minutes": int((end - t["start"]).total_seconds() // 60),
                        "impacted_customers": len(t["customers"])})
        distinct.update(t["customers"])
    answer = {
        "note": "Hidden truth behind the synthetic data. Do not use it inside the pipeline; it exists to check the result.",
        "as_of_utc": iso(AS_OF),
        "incidents": len(truth),
        "ongoing_incidents": len(ongoing),
        "total_downtime_minutes": sum(i["downtime_minutes"] for i in key_inc),
        "customer_incident_impacts": sum(i["impacted_customers"] for i in key_inc),
        "distinct_customers_impacted": len(distinct),
        "incident_list": key_inc,
    }
    (out / "answer_key.json").write_text(json.dumps(answer, indent=1), encoding="utf-8")
    print(f"tickets written: {len(tickets)} | alarms written: {len(alarms)} | customers: {len(customers)}")
    print(f"truth: {answer['incidents']} incidents, {answer['total_downtime_minutes']} min downtime, "
          f"{answer['distinct_customers_impacted']} distinct customers")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate synthetic fibre incident data (fictional).")
    ap.add_argument("--out", default="data")
    main(ap.parse_args().out)
