"""Computation of the fiction-of-delivery deadline (fikce doručení).

Under § 17 odst. 4 zákona č. 300/2008 Sb., a message addressed to someone whose
box enables fiction of delivery is deemed delivered on the 10th day after it
was made available in the box (dodání), if no authorized person logs in
earlier. Per the amendment effective 1 Jan 2022 (zákon č. 261/2021 Sb., DEPO)
combined with § 33 odst. 4 správního řádu interpretation used by ISDS, when the
10th day falls on a Saturday, Sunday or a Czech public holiday, the fiction
occurs on the nearest following working day.
"""

from __future__ import annotations

from datetime import date, timedelta

import holidays

_FICTION_DAYS = 10

_cz_holidays = holidays.country_holidays("CZ")


def is_czech_working_day(day: date) -> bool:
    """True if the given date is a working day in the Czech Republic."""
    return day.weekday() < 5 and day not in _cz_holidays


def fiction_delivery_date(delivered_to_box: date) -> date:
    """Return the date the fiction of delivery occurs for a message dodaná on the given date.

    The base deadline is delivered_to_box + 10 days; if that day is not a
    working day, the fiction shifts to the nearest following working day.
    """
    deadline = delivered_to_box + timedelta(days=_FICTION_DAYS)
    while not is_czech_working_day(deadline):
        deadline += timedelta(days=1)
    return deadline


def describe_deadline(delivered_to_box: date, today: date) -> dict[str, object]:
    """Structured description of the fiction deadline, for the MCP tool."""
    deadline = fiction_delivery_date(delivered_to_box)
    days_left = (deadline - today).days
    return {
        "delivered_to_box": delivered_to_box.isoformat(),
        "fiction_delivery_date": deadline.isoformat(),
        "days_remaining": days_left,
        "already_passed": days_left < 0,
        "note": (
            "Fikce doručení dle § 17 odst. 4 zák. č. 300/2008 Sb.: zpráva se považuje "
            "za doručenou 10. dnem od dodání; připadne-li tento den na sobotu, neděli "
            "nebo svátek, je doručena nejbližší následující pracovní den. Skutečné "
            "doručení může nastat dříve přihlášením (včetně přístupu přes API)."
        ),
    }
