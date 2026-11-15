from datetime import date

from skintrackr.models import ActivitySummary
from skintrackr.vert import year_to_date

AS_OF = date(2026, 11, 14)


def activity(activity_id: int, day: str, vert: float, sport_type: str = "BackcountrySki") -> ActivitySummary:
    return ActivitySummary(
        id=activity_id, sport_type=sport_type, start_date_local=f"{day}T09:00:00Z", total_elevation_gain=vert
    )


def totals(activities: list[ActivitySummary], as_of: date = AS_OF) -> dict[int, float]:
    return {year.year: year.vert_m for year in year_to_date(activities, as_of).years}


def test_sums_on_foot_vert_from_jan_1_to_the_same_date() -> None:
    activities = [
        activity(1, "2026-03-01", 1000.0),
        activity(2, "2026-11-14", 200.0, "Hike"),  # today counts
        activity(3, "2025-11-14", 300.0, "Run"),
        activity(4, "2025-11-15", 500.0, "Hike"),  # after the date
        activity(5, "2025-02-01", 900.0, "Ride"),  # cycling
        activity(6, "2025-01-10", 700.0, "AlpineSki"),  # lift-served
        activity(7, "2021-06-01", 50.0, "Walk"),  # oldest year shown
        activity(8, "2020-06-01", 70.0, "Walk"),  # too old
    ]
    stats = year_to_date(activities, AS_OF)
    assert stats.as_of == AS_OF
    assert stats.activities == 8
    assert [(y.year, y.vert_m) for y in stats.years] == [
        (2026, 1200.0),
        (2025, 300.0),
        (2024, 0.0),
        (2023, 0.0),
        (2022, 0.0),
        (2021, 50.0),
    ]


def test_leap_day_counts_only_once_its_date_has_passed() -> None:
    activities = [activity(1, "2024-02-28", 10.0), activity(2, "2024-02-29", 20.0)]
    assert totals(activities, date(2025, 2, 28))[2024] == 10.0
    assert totals(activities, date(2025, 3, 1))[2024] == 30.0


def test_nothing_imported() -> None:
    stats = year_to_date([], AS_OF)
    assert stats.activities == 0
    assert all(year.vert_m == 0 for year in stats.years)
