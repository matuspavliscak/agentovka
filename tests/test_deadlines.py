from datetime import date

from agentovka_mcp.deadlines import describe_deadline, fiction_delivery_date, is_czech_working_day


def test_plain_ten_days() -> None:
    # Mon 2026-06-01 + 10 days = Thu 2026-06-11 (working day)
    assert fiction_delivery_date(date(2026, 6, 1)) == date(2026, 6, 11)


def test_weekend_shifts_to_monday() -> None:
    # Wed 2026-06-03 + 10 days = Sat 2026-06-13 -> Mon 2026-06-15
    assert fiction_delivery_date(date(2026, 6, 3)) == date(2026, 6, 15)


def test_holiday_shifts_further() -> None:
    # 2026-06-25 + 10 = Sun 2026-07-05 (also Cyril & Methodius day),
    # Mon 2026-07-06 is Jan Hus day -> first working day is Tue 2026-07-07.
    assert fiction_delivery_date(date(2026, 6, 25)) == date(2026, 7, 7)


def test_is_working_day() -> None:
    assert is_czech_working_day(date(2026, 6, 1))  # Monday
    assert not is_czech_working_day(date(2026, 6, 6))  # Saturday
    assert not is_czech_working_day(date(2026, 12, 24))  # Štědrý den


def test_describe_deadline() -> None:
    info = describe_deadline(date(2026, 6, 1), today=date(2026, 6, 5))
    assert info["fiction_delivery_date"] == "2026-06-11"
    assert info["days_remaining"] == 6
    assert info["already_passed"] is False
