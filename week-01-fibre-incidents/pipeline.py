"""
Week 1 - "The Quick Fix That Wasn't"
Fibre network incident report: three messy sources  ->  one trustworthy report.

THE REQUEST:  "I need a report showing our fibre network incidents, affected
               locations, downtime, and impacted customers. By tomorrow morning."

Read it top to bottom. Five steps, in the order they run. Each answers a failure the quick fix had:

    1. EXTRACT      read the raw files (and stop clearly if they are not what we expect)
    2. DEDUPE       the same ticket or alarm exported twice counts once
    3. STANDARDIZE  one time zone, one set of location names, one severity scale
       + VALIDATE   reject what we cannot trust, and write down why
    4. RECONCILE    alarms + tickets describing the same outage become ONE incident
    5. REPORT       downtime without double counting, impacted customers, a plain summary

All data in this project is synthetic (see generate_data.py).
"""
import argparse
import csv
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("fibre")

UTC = timezone.utc
LOCAL_OFFSET = timedelta(hours=1)               # NOC tickets are typed in local time (UTC+1)
TRANSIENT_MINUTES = 5                           # alarms shorter than this are blips, not outages
ALARM_MERGE_GAP = timedelta(minutes=5)          # alarms this close together are the same outage
TICKET_MATCH_WINDOW = timedelta(minutes=30)     # a ticket belongs to an outage if opened within this of it
DEFAULT_AS_OF = "2026-11-01T09:00:00Z"          # report cut-off: ongoing outages are measured up to here
TICKET_TIME_FORMATS = ["%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%d %b %Y %I:%M %p"]   # the NOC types day-first

REPORT_FIELDS = ["incident_id", "site_code", "location", "zone", "start_utc", "end_utc", "start_local",
                 "end_local", "status", "downtime_minutes", "impacted_customers", "severity", "cause",
                 "sources", "ticket_ids", "alarm_count", "quality_flags"]


# --------------------------------------------------------------------------
# 1. EXTRACT
# --------------------------------------------------------------------------
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_alarms(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):                      # wrong shape = stop, do not guess
        raise ValueError(f"{path}: expected a JSON list of alarms, got {type(data).__name__}")
    return data


# --------------------------------------------------------------------------
# 2. DEDUPE  (runs on the raw rows, before anything is standardized)
# --------------------------------------------------------------------------
def dedupe(rows, key):
    """Keep the first row per id. Returns (unique_rows, duplicates_removed, conflicts).
    A 'conflict' is a repeated id whose content differs; we keep the first and report it."""
    seen, unique, dupes, conflicts = {}, [], 0, 0
    for r in rows:
        k = (r.get(key) or "").strip()
        if k and k in seen:
            dupes += 1
            if r != seen[k]:
                conflicts += 1
            continue
        if k:
            seen[k] = r
        unique.append(r)
    return unique, dupes, conflicts


# --------------------------------------------------------------------------
# 3. STANDARDIZE and VALIDATE  (one time zone, one set of names, reasons for every rejection)
# --------------------------------------------------------------------------
def normalize_text(text):
    """'  Harbour Pt. Phase 1 ' -> 'harbour pt phase 1'  (lower case, no punctuation, single spaces)."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def build_site_lookup(reference_rows):
    """Every known spelling of a location -> its site code. The alias table is the team's memory."""
    lookup, sites = {}, {}
    for r in reference_rows:
        code = r["site_code"].strip()
        sites[code] = {"name": r["site_name"].strip(), "zone": r["zone"].strip()}
        for alias in [r["site_name"], code, *r["aliases"].split("|")]:
            if alias.strip():
                lookup[normalize_text(alias)] = code
    return lookup, sites


def parse_local_time(text):
    """NOC ticket time (local, several formats) -> timezone-aware UTC datetime, or None."""
    for fmt in TICKET_TIME_FORMATS:
        try:
            return (datetime.strptime((text or "").strip(), fmt) - LOCAL_OFFSET).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def parse_utc(text):
    """Monitoring time '2026-10-03T14:05:00Z' -> aware UTC datetime, or None."""
    try:
        return datetime.strptime((text or "").strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


SEVERITY_WORDS = {"p1": "P1", "critical": "P1", "sev 1": "P1", "sev1": "P1", "1": "P1",
                  "p2": "P2", "major": "P2", "sev 2": "P2", "sev2": "P2", "2": "P2",
                  "p3": "P3", "minor": "P3", "sev 3": "P3", "sev3": "P3", "low": "P3", "3": "P3"}


def normalize_severity(text):
    return SEVERITY_WORDS.get(normalize_text(text))


CAUSE_RULES = [("customer", "Customer-reported (cause unknown)"), ("cut", "Fibre cut"), ("damaged", "Fibre cut"),
               ("excavat", "Fibre cut"), ("power", "Power failure"), ("equipment", "Equipment fault"),
               ("maint", "Planned maintenance overrun"), ("vandal", "Vandalism or theft"),
               ("theft", "Vandalism or theft"), ("weather", "Weather damage"), ("test", "Test ticket")]


def normalize_cause(text):
    t = normalize_text(text)
    for needle, label in CAUSE_RULES:
        if needle in t:
            return label
    return "Other / unknown"


def standardize_ticket(row, lookup):
    """Return (ticket, None) or (None, reason). Policy lives here and in reconcile()."""
    ticket_id = (row.get("ticket_id") or "").strip()
    if not ticket_id:
        return None, "missing ticket_id"
    loc = (row.get("location") or "").strip()
    if not loc:
        return None, "missing location"
    site = lookup.get(normalize_text(loc))
    if site is None:
        return None, f"unmapped location: {loc}"
    opened = parse_local_time(row.get("opened_at"))
    if opened is None:
        return None, f"unparsable opened_at: {row.get('opened_at')!r}"
    closed_text = (row.get("closed_at") or "").strip()
    closed = parse_local_time(closed_text) if closed_text else None
    if closed_text and closed is None:
        return None, f"unparsable closed_at: {closed_text!r}"
    return {"ticket_id": ticket_id, "site": site, "opened": opened, "closed": closed,
            "bad_close": closed is not None and closed < opened,      # closed before opened: a typing error
            "severity": normalize_severity(row.get("severity")),
            "cause": normalize_cause(row.get("cause"))}, None


def standardize_alarm(row, valid_sites):
    alarm_id = (row.get("alarm_id") or "").strip()
    if not alarm_id:
        return None, "missing alarm_id"
    site = (row.get("site_code") or "").strip().upper()
    if site not in valid_sites:
        return None, f"unknown site_code: {site or '(blank)'}"
    raised = parse_utc(row.get("raised_at"))
    if raised is None:
        return None, f"unparsable raised_at: {row.get('raised_at')!r}"
    cleared = None
    if row.get("cleared_at"):
        cleared = parse_utc(row.get("cleared_at"))
        if cleared is None:
            return None, f"unparsable cleared_at: {row.get('cleared_at')!r}"
        if cleared < raised:
            return None, "cleared before raised"
    try:
        severity = int(row.get("severity"))
    except (TypeError, ValueError):
        return None, f"bad severity: {row.get('severity')!r}"
    return {"alarm_id": alarm_id, "device": row.get("device_id"), "site": site,
            "severity": severity, "raised": raised, "cleared": cleared}, None


def standardize_customer(row):
    cid = (row.get("customer_id") or "").strip()
    if not cid:
        return None, "missing customer_id"
    try:
        act = date.fromisoformat(row["activated_at"])
        term = date.fromisoformat(row["terminated_at"]) if (row.get("terminated_at") or "").strip() else None
    except (KeyError, ValueError):
        return None, "bad date"
    return {"customer_id": cid, "site": (row.get("site_code") or "").strip(), "activated": act, "terminated": term}, None


# --------------------------------------------------------------------------
# 4. RECONCILE  (several records, one real-world outage)
# --------------------------------------------------------------------------
def drop_transient_alarms(alarms):
    keep, ignored = [], 0
    for a in alarms:
        short = a["cleared"] is not None and (a["cleared"] - a["raised"]) < timedelta(minutes=TRANSIENT_MINUTES)
        if short or a["severity"] >= 4:
            ignored += 1
        else:
            keep.append(a)
    return keep, ignored


def cluster_alarms(alarms, as_of):
    """Overlapping or back-to-back alarms at one site = one outage (two devices reporting the same fault)."""
    by_site = defaultdict(list)
    for a in alarms:
        by_site[a["site"]].append(a)
    clusters = []
    for site, items in by_site.items():
        items.sort(key=lambda a: a["raised"])
        cur = None
        for a in items:
            a_end = a["cleared"] or as_of
            if cur and a["raised"] <= cur["until"] + ALARM_MERGE_GAP:
                cur["alarms"].append(a)
                cur["until"] = max(cur["until"], a_end)
                cur["open"] = cur["open"] or a["cleared"] is None
            else:
                if cur:
                    clusters.append(cur)
                cur = {"site": site, "start": a["raised"], "until": a_end, "open": a["cleared"] is None,
                       "alarms": [a], "tickets": []}
        if cur:
            clusters.append(cur)
    return clusters


def attach_tickets(clusters, tickets):
    """A ticket joins the outage at its site whose time window it falls in. Returns unattached tickets."""
    loose = []
    for t in tickets:
        best, best_gap = None, None
        for c in clusters:
            if c["site"] != t["site"]:
                continue
            if c["start"] - TICKET_MATCH_WINDOW <= t["opened"] <= c["until"] + TICKET_MATCH_WINDOW:
                gap = abs(t["opened"] - c["start"])
                if best is None or gap < best_gap:
                    best, best_gap = c, gap
        if best is None:
            loose.append(t)
        else:
            best["tickets"].append(t)
    return loose


def ticket_only_incidents(tickets, as_of):
    """Tickets with no alarm behind them: group overlapping ones at the same site."""
    by_site = defaultdict(list)
    for t in tickets:
        by_site[t["site"]].append(t)
    out = []
    for site, items in by_site.items():
        items.sort(key=lambda t: t["opened"])
        cur = None
        for t in items:
            t_end = t["closed"] or as_of
            if cur and t["opened"] <= cur["until"]:
                cur["tickets"].append(t)
                cur["until"] = max(cur["until"], t_end)
                cur["open"] = cur["open"] or t["closed"] is None
            else:
                if cur:
                    out.append(cur)
                cur = {"site": site, "start": t["opened"], "until": t_end, "open": t["closed"] is None,
                       "alarms": [], "tickets": [t]}
        if cur:
            out.append(cur)
    return out


def finish_incident(c, as_of, customers_by_site, sites):
    """Turn a cluster into one incident: times, downtime, impacted customers, cause, severity, flags."""
    tickets = sorted(c["tickets"], key=lambda t: t["opened"])
    end = None if c["open"] else c["until"]
    flags = []
    if c["open"]:
        flags.append("ongoing")
    if not tickets:
        flags.append("no_ticket")
    if not c["alarms"]:
        flags.append("no_alarm")
    if len(tickets) > 1:
        flags.append("multiple_tickets")
    if any(t["bad_close"] for t in tickets):
        flags.append("ticket_close_time_ignored")
    primary = next((t for t in tickets if t["cause"] != "Customer-reported (cause unknown)"), tickets[0] if tickets else None)
    severity = next((t["severity"] for t in tickets if t["severity"]), None)
    if severity is None and c["alarms"]:
        severity = f"P{min(a['severity'] for a in c['alarms'])}"
    local_day = (c["start"] + LOCAL_OFFSET).date()          # who was a customer on the day it started?
    impacted = [x["customer_id"] for x in customers_by_site.get(c["site"], [])
                if x["activated"] <= local_day and (x["terminated"] is None or x["terminated"] > local_day)]
    sources = "alarm+ticket" if c["alarms"] and tickets else ("alarm" if c["alarms"] else "ticket")
    return {"site": c["site"], "location": sites[c["site"]]["name"], "zone": sites[c["site"]]["zone"],
            "start": c["start"], "end": end,
            "downtime_minutes": int(((end or as_of) - c["start"]).total_seconds() // 60),
            "impacted_ids": impacted, "severity": severity or "unknown",
            "cause": primary["cause"] if primary else "Unknown (no ticket)",
            "sources": sources, "ticket_ids": [t["ticket_id"] for t in tickets],
            "alarm_count": len(c["alarms"]), "flags": flags}


# --------------------------------------------------------------------------
# 5. REPORT
# --------------------------------------------------------------------------
def fmt_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def fmt_local(dt):
    return (dt + LOCAL_OFFSET).strftime("%Y-%m-%d %H:%M") if dt else ""


def summarize(incidents, quality, as_of):
    distinct = {cid for i in incidents for cid in i["impacted_ids"]}
    per_site = defaultdict(lambda: {"incidents": 0, "downtime_minutes": 0, "customers": set()})
    for i in incidents:
        s = per_site[i["site"]]
        s["incidents"] += 1
        s["downtime_minutes"] += i["downtime_minutes"]
        s["customers"].update(i["impacted_ids"])
    locations = [{"site_code": code, "location": next(i["location"] for i in incidents if i["site"] == code),
                  "incidents": v["incidents"], "downtime_hours": round(v["downtime_minutes"] / 60, 1),
                  "distinct_customers": len(v["customers"])} for code, v in per_site.items()]
    locations.sort(key=lambda r: -r["downtime_hours"])
    total = sum(i["downtime_minutes"] for i in incidents)
    return {"as_of_utc": fmt_utc(as_of),
            "incidents": len(incidents),
            "ongoing_incidents": sum(1 for i in incidents if i["end"] is None),
            "locations_affected": len(per_site),
            "total_downtime_minutes": total,
            "total_downtime_hours": round(total / 60, 1),
            "customer_incident_impacts": sum(len(i["impacted_ids"]) for i in incidents),
            "distinct_customers_impacted": len(distinct),
            "by_location": locations,
            "by_cause": dict(Counter(i["cause"] for i in incidents).most_common()),
            "data_quality": quality}


def write_markdown(summary, path):
    s = summary
    lines = [
        "# Fibre network incident report", "",
        f"Counted up to **{s['as_of_utc']}**. Ongoing outages are measured up to that moment.", "",
        "## Headline", "",
        f"- **{s['incidents']} incidents** across **{s['locations_affected']} locations** "
        f"({s['ongoing_incidents']} still ongoing)",
        f"- **{s['total_downtime_hours']:,} hours** of total downtime (overlapping alarms are never counted twice)",
        f"- **{s['distinct_customers_impacted']:,} different customers** lost service at least once "
        f"({s['customer_incident_impacts']:,} customer-incidents, because a customer hit twice counts twice)", "",
        "## Where it hurt most", "", "| Location | Incidents | Downtime (hours) | Different customers hit |", "|---|---|---|---|"]
    lines += [f"| {r['location']} | {r['incidents']} | {r['downtime_hours']} | {r['distinct_customers']} |"
              for r in s["by_location"]]
    lines += ["", "## Causes", "", "| Cause | Incidents |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in s["by_cause"].items()]
    q = s["data_quality"]
    lines += ["", "## How much we trust the inputs", "",
              f"- Tickets read: {q['tickets_read']} | repeated exports removed: {q['ticket_duplicates_removed']} | rejected: {q['tickets_rejected']}",
              f"- Alarms read: {q['alarms_read']} | repeated records removed: {q['alarm_duplicates_removed']} | rejected: {q['alarms_rejected']} | short blips ignored: {q['transient_alarms_ignored']}",
              f"- Customer rows read: {q['customers_read']} | rejected: {q['customers_rejected']}",
              f"- Incidents seen by alarm and ticket: {q['incidents_alarm_and_ticket']} | alarm only: {q['incidents_alarm_only']} | ticket only: {q['incidents_ticket_only']}",
              "", "Every rejected record and the reason is in `rejected_records.csv`.", "",
              "## Rules this report follows", "",
              "- Automated alarm times win over typed ticket times when both exist.",
              f"- Alarms shorter than {TRANSIENT_MINUTES} minutes (or severity 4) are blips, not outages.",
              "- Customers affected comes from the customer list, not from the free-text number in tickets.",
              "- Ticket times are local (UTC+1) and day-first; the report shows both UTC and local."]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(raw_dir, out_dir, as_of_text=DEFAULT_AS_OF):
    raw, out = Path(raw_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    as_of = parse_utc(as_of_text)
    if as_of is None:
        raise ValueError(f"--as-of must look like 2026-11-01T09:00:00Z, got {as_of_text!r}")
    rejects = []                                           # (source, record id, reason, raw)

    # 1. extract
    ref_rows = read_csv(raw / "site_reference.csv")
    ticket_rows = read_csv(raw / "noc_tickets.csv")
    alarm_rows = read_alarms(raw / "monitoring_alarms.json")
    customer_rows = read_csv(raw / "customers.csv")
    lookup, sites = build_site_lookup(ref_rows)

    # 4. dedupe (raw rows) then 2/3. standardize + validate
    ticket_rows, t_dupes, t_conf = dedupe(ticket_rows, "ticket_id")
    alarm_rows, a_dupes, a_conf = dedupe(alarm_rows, "alarm_id")
    tickets, alarms, customers = [], [], []
    for row in ticket_rows:
        rec, why = standardize_ticket(row, lookup)
        (tickets.append(rec) if rec else rejects.append(("ticket", row.get("ticket_id"), why, row)))
    for row in alarm_rows:
        rec, why = standardize_alarm(row, sites)
        (alarms.append(rec) if rec else rejects.append(("alarm", row.get("alarm_id"), why, row)))
    for row in customer_rows:
        rec, why = standardize_customer(row)
        (customers.append(rec) if rec else rejects.append(("customer", row.get("customer_id"), why, row)))
    customers_by_site = defaultdict(list)
    for c in customers:
        customers_by_site[c["site"]].append(c)

    # 5. reconcile
    alarms, transient = drop_transient_alarms(alarms)
    clusters = cluster_alarms(alarms, as_of)
    loose = attach_tickets(clusters, tickets)
    usable = []
    for t in loose:
        if t["bad_close"]:                                  # nothing else confirms the times: do not guess
            rejects.append(("ticket", t["ticket_id"], "closed before opened and no alarm confirms the times", t))
        else:
            usable.append(t)
    clusters += ticket_only_incidents(usable, as_of)
    incidents = [finish_incident(c, as_of, customers_by_site, sites) for c in clusters]
    incidents.sort(key=lambda i: (i["start"], i["site"]))

    # 6. report
    with (out / "incident_report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        w.writeheader()
        for n, i in enumerate(incidents, 1):
            w.writerow({"incident_id": f"INC-{n:04d}", "site_code": i["site"], "location": i["location"],
                        "zone": i["zone"], "start_utc": fmt_utc(i["start"]), "end_utc": fmt_utc(i["end"]),
                        "start_local": fmt_local(i["start"]), "end_local": fmt_local(i["end"]),
                        "status": "ongoing" if i["end"] is None else "resolved",
                        "downtime_minutes": i["downtime_minutes"], "impacted_customers": len(i["impacted_ids"]),
                        "severity": i["severity"], "cause": i["cause"], "sources": i["sources"],
                        "ticket_ids": "|".join(i["ticket_ids"]), "alarm_count": i["alarm_count"],
                        "quality_flags": "|".join(i["flags"])})
    with (out / "rejected_records.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "record_id", "reason", "raw"])
        for source, rid, why, raw_rec in rejects:
            w.writerow([source, rid, why, json.dumps(raw_rec, default=str, ensure_ascii=False)])

    by_source = Counter(i["sources"] for i in incidents)
    quality = {"tickets_read": len(ticket_rows) + t_dupes, "ticket_duplicates_removed": t_dupes,
               "ticket_conflicting_duplicates": t_conf,
               "tickets_rejected": sum(1 for r in rejects if r[0] == "ticket"),
               "alarms_read": len(alarm_rows) + a_dupes, "alarm_duplicates_removed": a_dupes,
               "alarm_conflicting_duplicates": a_conf,
               "alarms_rejected": sum(1 for r in rejects if r[0] == "alarm"),
               "transient_alarms_ignored": transient,
               "customers_read": len(customer_rows),
               "customers_rejected": sum(1 for r in rejects if r[0] == "customer"),
               "incidents_alarm_and_ticket": by_source["alarm+ticket"], "incidents_alarm_only": by_source["alarm"],
               "incidents_ticket_only": by_source["ticket"]}
    summary = summarize(incidents, quality, as_of)
    (out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    write_markdown(summary, out / "summary.md")
    log.info("done: %d incidents, %.1f h downtime, %d distinct customers, %d rejected records",
             summary["incidents"], summary["total_downtime_hours"], summary["distinct_customers_impacted"], len(rejects))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the fibre incident report from the raw files.")
    p.add_argument("--raw", default="data/raw", help="folder with the raw files (default: data/raw)")
    p.add_argument("--out", default="output", help="folder for the report (default: output)")
    p.add_argument("--as-of", default=DEFAULT_AS_OF, help="report cut-off, e.g. 2026-11-01T09:00:00Z")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        run(args.raw, args.out, args.as_of)
    except (OSError, ValueError, KeyError) as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
