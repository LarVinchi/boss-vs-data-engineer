# Fibre network incident report

Counted up to **2026-11-01T09:00:00Z**. Ongoing outages are measured up to that moment.

## Headline

- **46 incidents** across **8 locations** (3 still ongoing)
- **192.5 hours** of total downtime (overlapping alarms are never counted twice)
- **795 different customers** lost service at least once (4,938 customer-incidents, because a customer hit twice counts twice)

## Where it hurt most

| Location | Incidents | Downtime (hours) | Different customers hit |
|---|---|---|---|
| Palm Estate | 10 | 44.1 | 136 |
| Central Business Park | 10 | 42.8 | 154 |
| North Ridge | 5 | 37.6 | 76 |
| Station Road | 5 | 26.0 | 58 |
| Harbour Point Phase 2 | 6 | 13.7 | 90 |
| Green Valley | 3 | 12.5 | 104 |
| Harbour Point Phase 1 | 5 | 9.5 | 116 |
| Riverside Close | 2 | 6.4 | 61 |

## Causes

| Cause | Incidents |
|---|---|
| Equipment fault | 11 |
| Power failure | 8 |
| Unknown (no ticket) | 8 |
| Fibre cut | 7 |
| Weather damage | 5 |
| Planned maintenance overrun | 4 |
| Vandalism or theft | 3 |

## How much we trust the inputs

- Tickets read: 53 | repeated exports removed: 4 | rejected: 8
- Alarms read: 93 | repeated records removed: 4 | rejected: 0 | short blips ignored: 40
- Customer rows read: 840 | rejected: 0
- Incidents seen by alarm and ticket: 30 | alarm only: 8 | ticket only: 8

Every rejected record and the reason is in `rejected_records.csv`.

## Rules this report follows

- Automated alarm times win over typed ticket times when both exist.
- Alarms shorter than 5 minutes (or severity 4) are blips, not outages.
- Customers affected comes from the customer list, not from the free-text number in tickets.
- Ticket times are local (UTC+1) and day-first; the report shows both UTC and local.
