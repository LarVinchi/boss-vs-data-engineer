"""
Tests for the Week 1 fibre incident pipeline.   Run:  python -m pytest -v

Every test name states one rule. Small hand-built data checks each rule on its own;
the last test generates the full synthetic data set and checks the pipeline against the
hidden answer key, number by number.
"""
import csv
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import generate_data
import pipeline as p

UTC = timezone.utc
AS_OF = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def at(h, m=0, day=10):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


REF = [{"site_code": "HP1", "site_name": "Harbour Point Phase 1", "zone": "Harbour", "aliases": "Harbour Pt Phase 1|HP1"},
       {"site_code": "PE", "site_name": "Palm Estate", "zone": "Gardens", "aliases": "Palm Est"}]
LOOKUP, SITES = p.build_site_lookup(REF)


# ---- names, times, severity, cause --------------------------------------------------
@pytest.mark.parametrize("text", ["Harbour Point Phase 1", "  HARBOUR POINT PHASE 1  ", "harbour pt. phase 1",
                                  "Harbour Pt Phase 1.", "hp1"])
def test_every_known_spelling_maps_to_the_same_site(text):
    assert LOOKUP[p.normalize_text(text)] == "HP1"


def test_unknown_spelling_is_not_guessed():
    assert p.normalize_text("Harbor Point Phase 1") not in LOOKUP


@pytest.mark.parametrize("text, expected", [("2026-10-13 08:27", at(7, 27, 13)),
                                            ("13/10/2026 08:27", at(7, 27, 13)),
                                            ("13 Oct 2026 8:27 AM", at(7, 27, 13)),
                                            ("13 Oct 2026 8:27 PM", at(19, 27, 13))])
def test_local_ticket_times_become_utc(text, expected):
    assert p.parse_local_time(text) == expected          # local is UTC+1, so 08:27 local = 07:27 UTC


def test_slash_dates_are_read_day_first():
    assert p.parse_local_time("03/10/2026 14:05") == datetime(2026, 10, 3, 13, 5, tzinfo=UTC)


@pytest.mark.parametrize("text", ["TBC", "", None, "2026-13-45 99:99"])
def test_unreadable_times_give_none(text):
    assert p.parse_local_time(text) is None


def test_monitoring_times_are_utc():
    assert p.parse_utc("2026-10-03T14:05:00Z") == datetime(2026, 10, 3, 14, 5, tzinfo=UTC)
    assert p.parse_utc("nonsense") is None


@pytest.mark.parametrize("text, expected", [("P1", "P1"), ("Critical", "P1"), ("sev 2", "P2"), ("SEV1", "P1"),
                                            ("minor", "P3"), ("whatever", None), (None, None)])
def test_severity_words_map_to_one_scale(text, expected):
    assert p.normalize_severity(text) == expected


@pytest.mark.parametrize("text, expected", [("FIBRE CUT - third party excavation", "Fibre cut"),
                                            ("power failure at cabinet", "Power failure"),
                                            ("Customers report no service", "Customer-reported (cause unknown)"),
                                            ("???", "Other / unknown")])
def test_causes_are_grouped(text, expected):
    assert p.normalize_cause(text) == expected


# ---- standardize + validate ---------------------------------------------------------
def ticket(**kw):
    row = {"ticket_id": "T1", "opened_at": "2026-10-10 10:00", "closed_at": "2026-10-10 12:00",
           "location": "Palm Estate", "severity": "Major", "cause": "Power failure", "customers_affected": "50"}
    row.update(kw)
    return row


def test_good_ticket_is_standardized():
    t, why = p.standardize_ticket(ticket(), LOOKUP)
    assert why is None and t["site"] == "PE" and t["severity"] == "P2" and t["opened"] == at(9)
    assert t["closed"] == at(11) and t["bad_close"] is False


def test_ticket_without_close_time_is_still_open():
    t, _ = p.standardize_ticket(ticket(closed_at=""), LOOKUP)
    assert t["closed"] is None


@pytest.mark.parametrize("change, fragment", [({"ticket_id": ""}, "ticket_id"), ({"location": ""}, "missing location"),
                                              ({"location": "Atlantis"}, "unmapped"),
                                              ({"opened_at": "TBC"}, "opened_at"), ({"closed_at": "soon"}, "closed_at")])
def test_bad_tickets_are_rejected_with_a_reason(change, fragment):
    t, why = p.standardize_ticket(ticket(**change), LOOKUP)
    assert t is None and fragment in why


def test_ticket_closed_before_opened_is_flagged_not_trusted():
    t, _ = p.standardize_ticket(ticket(closed_at="2026-10-10 08:00"), LOOKUP)
    assert t["bad_close"] is True


def alarm(**kw):
    row = {"alarm_id": "A1", "device_id": "PE-OLT-01", "site_code": "PE", "severity": 2,
           "raised_at": "2026-10-10T09:00:00Z", "cleared_at": "2026-10-10T10:00:00Z"}
    row.update(kw)
    return row


def test_good_alarm_is_standardized():
    a, why = p.standardize_alarm(alarm(), SITES)
    assert why is None and a["raised"] == at(9) and a["cleared"] == at(10)


def test_alarm_without_cleared_time_is_ongoing():
    a, _ = p.standardize_alarm(alarm(cleared_at=None), SITES)
    assert a["cleared"] is None


@pytest.mark.parametrize("change, fragment", [({"alarm_id": ""}, "alarm_id"), ({"site_code": "XX"}, "unknown site"),
                                              ({"raised_at": "x"}, "raised_at"),
                                              ({"cleared_at": "2026-10-10T08:00:00Z"}, "before raised"),
                                              ({"severity": "high"}, "severity")])
def test_bad_alarms_are_rejected_with_a_reason(change, fragment):
    a, why = p.standardize_alarm(alarm(**change), SITES)
    assert a is None and fragment in why


# ---- dedupe ---------------------------------------------------------------------------
def test_repeated_export_rows_count_once():
    rows = [{"id": "1", "x": "a"}, {"id": "1", "x": "a"}, {"id": "2", "x": "b"}]
    unique, dupes, conflicts = p.dedupe(rows, "id")
    assert [r["id"] for r in unique] == ["1", "2"] and dupes == 1 and conflicts == 0


def test_repeated_id_with_different_content_is_reported_as_conflict():
    unique, dupes, conflicts = p.dedupe([{"id": "1", "x": "a"}, {"id": "1", "x": "CHANGED"}], "id")
    assert len(unique) == 1 and dupes == 1 and conflicts == 1


def test_rows_without_an_id_are_kept_for_validation_to_reject():
    unique, dupes, _ = p.dedupe([{"id": ""}, {"id": ""}], "id")
    assert len(unique) == 2 and dupes == 0


# ---- reconcile -------------------------------------------------------------------------
def A(site="PE", raised=at(9), cleared=at(10), severity=2, aid="A"):
    return {"alarm_id": aid, "device": "d", "site": site, "severity": severity, "raised": raised, "cleared": cleared}


def test_blips_and_info_alarms_are_ignored():
    keep, ignored = p.drop_transient_alarms([A(cleared=at(9, 2)), A(severity=4), A(cleared=at(9, 10)), A(cleared=None)])
    assert len(keep) == 2 and ignored == 2


def test_two_devices_reporting_one_outage_make_one_cluster():
    clusters = p.cluster_alarms([A(raised=at(9), cleared=at(11), aid="1"), A(raised=at(9, 10), cleared=at(10, 50), aid="2")], AS_OF)
    assert len(clusters) == 1 and clusters[0]["start"] == at(9) and clusters[0]["until"] == at(11)


def test_alarms_at_different_sites_stay_separate():
    assert len(p.cluster_alarms([A(site="PE"), A(site="HP1")], AS_OF)) == 2


def test_alarms_far_apart_at_one_site_stay_separate():
    assert len(p.cluster_alarms([A(raised=at(9), cleared=at(10)), A(raised=at(13), cleared=at(14))], AS_OF)) == 2


def test_an_open_alarm_makes_the_cluster_open():
    c = p.cluster_alarms([A(cleared=None)], AS_OF)[0]
    assert c["open"] is True and c["until"] == AS_OF


def T(site="PE", opened=at(9, 10), closed=at(10, 30), tid="T", bad=False):
    return {"ticket_id": tid, "site": site, "opened": opened, "closed": closed, "bad_close": bad,
            "severity": "P2", "cause": "Power failure"}


def test_ticket_joins_the_outage_it_belongs_to():
    clusters = p.cluster_alarms([A()], AS_OF)
    loose = p.attach_tickets(clusters, [T()])
    assert loose == [] and len(clusters[0]["tickets"]) == 1


def test_ticket_for_another_site_or_another_day_stays_loose():
    clusters = p.cluster_alarms([A()], AS_OF)
    loose = p.attach_tickets(clusters, [T(site="HP1"), T(opened=at(9, 10, day=20), closed=at(11, day=20))])
    assert len(loose) == 2


def test_overlapping_tickets_without_alarms_merge_into_one_incident():
    inc = p.ticket_only_incidents([T(tid="1", opened=at(9), closed=at(11)), T(tid="2", opened=at(10), closed=at(12))], AS_OF)
    assert len(inc) == 1 and inc[0]["start"] == at(9) and inc[0]["until"] == at(12)


CUSTOMERS = {"PE": [{"customer_id": "old", "site": "PE", "activated": date(2025, 1, 1), "terminated": None},
                    {"customer_id": "new", "site": "PE", "activated": date(2026, 10, 20), "terminated": None},
                    {"customer_id": "gone", "site": "PE", "activated": date(2025, 1, 1), "terminated": date(2026, 9, 1)}]}


def test_only_customers_active_that_day_are_counted_as_impacted():
    c = p.cluster_alarms([A()], AS_OF)[0]
    inc = p.finish_incident(c, AS_OF, CUSTOMERS, SITES)
    assert inc["impacted_ids"] == ["old"]


def test_ongoing_incident_is_measured_up_to_the_cutoff():
    c = p.cluster_alarms([A(raised=AS_OF - timedelta(hours=3), cleared=None)], AS_OF)[0]
    inc = p.finish_incident(c, AS_OF, CUSTOMERS, SITES)
    assert inc["downtime_minutes"] == 180 and inc["end"] is None and "ongoing" in inc["flags"]


def test_overlapping_alarms_do_not_double_count_downtime():
    c = p.cluster_alarms([A(raised=at(9), cleared=at(11), aid="1"), A(raised=at(9, 30), cleared=at(10, 30), aid="2")], AS_OF)[0]
    assert p.finish_incident(c, AS_OF, CUSTOMERS, SITES)["downtime_minutes"] == 120     # not 180


def test_automated_alarm_times_win_over_ticket_times():
    c = p.cluster_alarms([A(raised=at(9), cleared=at(10))], AS_OF)[0]
    c["tickets"].append(T(opened=at(9, 20), closed=at(10, 40)))
    inc = p.finish_incident(c, AS_OF, CUSTOMERS, SITES)
    assert inc["start"] == at(9) and inc["end"] == at(10) and inc["cause"] == "Power failure"


# ---- whole pipeline on tiny data ---------------------------------------------------------
def write_raw(tmp, tickets, alarms, customers=None):
    raw = tmp / "raw"
    raw.mkdir()
    with (raw / "site_reference.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["site_code", "site_name", "zone", "aliases"])
        w.writeheader()
        w.writerows(REF)
    cols = ["ticket_id", "opened_at", "closed_at", "location", "severity", "cause", "customers_affected"]
    with (raw / "noc_tickets.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(tickets)
    (raw / "monitoring_alarms.json").write_text(json.dumps(alarms))
    customers = customers or [{"customer_id": f"C{i}", "site_code": "PE", "plan": "50", "activated_at": "2025-01-01",
                               "terminated_at": ""} for i in range(10)]
    with (raw / "customers.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["customer_id", "site_code", "plan", "activated_at", "terminated_at"])
        w.writeheader()
        w.writerows(customers)
    return raw


def read_report(out):
    with (out / "incident_report.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_end_to_end_one_outage_seen_three_ways_is_one_incident(tmp_path):
    raw = write_raw(tmp_path,
                    [ticket(ticket_id="T1", opened_at="2026-10-10 10:15", closed_at="2026-10-10 12:30"),
                     ticket(ticket_id="T1", opened_at="2026-10-10 10:15", closed_at="2026-10-10 12:30")],   # exported twice
                    [alarm(alarm_id="A1", raised_at="2026-10-10T09:00:00Z", cleared_at="2026-10-10T11:00:00Z"),
                     alarm(alarm_id="A2", raised_at="2026-10-10T09:10:00Z", cleared_at="2026-10-10T10:50:00Z")])
    summary = p.run(raw, tmp_path / "out")
    rows = read_report(tmp_path / "out")
    assert summary["incidents"] == 1 and len(rows) == 1
    assert rows[0]["downtime_minutes"] == "120" and rows[0]["impacted_customers"] == "10"
    assert rows[0]["sources"] == "alarm+ticket" and rows[0]["start_local"] == "2026-10-10 10:00"
    assert summary["data_quality"]["ticket_duplicates_removed"] == 1


def test_end_to_end_rejects_are_listed_with_reasons(tmp_path):
    raw = write_raw(tmp_path, [ticket(ticket_id="T9", location="Atlantis")], [])
    p.run(raw, tmp_path / "out")
    with (tmp_path / "out" / "rejected_records.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1 and rows[0]["record_id"] == "T9" and "unmapped" in rows[0]["reason"]
    assert read_report(tmp_path / "out") == []


def test_ticket_with_bad_close_and_no_alarm_is_rejected_not_guessed(tmp_path):
    raw = write_raw(tmp_path, [ticket(closed_at="2026-10-10 08:00")], [])
    p.run(raw, tmp_path / "out")
    assert read_report(tmp_path / "out") == []


def test_ticket_with_bad_close_but_an_alarm_keeps_the_incident(tmp_path):
    raw = write_raw(tmp_path, [ticket(opened_at="2026-10-10 10:10", closed_at="2026-10-10 08:00")],
                    [alarm(raised_at="2026-10-10T09:00:00Z", cleared_at="2026-10-10T10:00:00Z")])
    p.run(raw, tmp_path / "out")
    rows = read_report(tmp_path / "out")
    assert len(rows) == 1 and rows[0]["downtime_minutes"] == "60" and "ticket_close_time_ignored" in rows[0]["quality_flags"]


def test_running_twice_gives_identical_files(tmp_path):
    raw = write_raw(tmp_path, [ticket()], [alarm()])
    p.run(raw, tmp_path / "out")
    first = (tmp_path / "out" / "incident_report.csv").read_bytes()
    p.run(raw, tmp_path / "out")
    assert (tmp_path / "out" / "incident_report.csv").read_bytes() == first


def test_wrong_shape_alarm_file_stops_the_run(tmp_path):
    raw = write_raw(tmp_path, [], [])
    (raw / "monitoring_alarms.json").write_text('{"error": "rate limited"}')
    with pytest.raises(ValueError):
        p.run(raw, tmp_path / "out")


def test_bad_cutoff_is_refused(tmp_path):
    raw = write_raw(tmp_path, [], [])
    with pytest.raises(ValueError):
        p.run(raw, tmp_path / "out", "tomorrow morning")


# ---- the real test: the full synthetic data set against the hidden answer key ----------------
def test_full_synthetic_data_matches_the_answer_key(tmp_path):
    generate_data.main(str(tmp_path / "data"))
    key = json.loads((tmp_path / "data" / "answer_key.json").read_text())
    s = p.run(tmp_path / "data" / "raw", tmp_path / "out", key["as_of_utc"])
    assert s["incidents"] == key["incidents"]
    assert s["ongoing_incidents"] == key["ongoing_incidents"]
    assert s["total_downtime_minutes"] == key["total_downtime_minutes"]
    assert s["distinct_customers_impacted"] == key["distinct_customers_impacted"]
    assert s["customer_incident_impacts"] == key["customer_incident_impacts"]
    got = {(r["site_code"], r["start_utc"], r["end_utc"], int(r["downtime_minutes"]), int(r["impacted_customers"]))
           for r in read_report(tmp_path / "out")}
    want = {(i["site_code"], i["start_utc"], i["end_utc"] or "", i["downtime_minutes"], i["impacted_customers"])
            for i in key["incident_list"]}
    assert got == want
