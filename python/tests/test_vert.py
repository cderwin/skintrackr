import asyncio
from datetime import UTC, date, datetime, timedelta

from skintrackr.vert import SYNC_OVERLAP, VertCache, VertStats
from tests.conftest import FakeStrava, summary

AS_OF = date(2026, 11, 14)


def year_to_date(cache: VertCache, strava: FakeStrava, as_of: date = AS_OF, athlete_id: int = 1) -> VertStats:
    return asyncio.run(cache.year_to_date(strava.client("token"), athlete_id, as_of))


def totals(stats: VertStats) -> dict[int, float]:
    return {year.year: year.vert_m for year in stats.years}


def afters(strava: FakeStrava) -> list[datetime]:
    return [datetime.fromtimestamp(int(r.url.params["after"]), UTC) for r in strava.requests]


def test_sums_on_foot_vert_from_jan_1_to_the_same_date() -> None:
    strava = FakeStrava()
    strava.add_activities(
        [
            summary(1, "2026-03-01", 1000.0),
            summary(2, "2026-11-14", 200.0, "Hike"),  # today counts
            summary(3, "2025-11-14", 300.0, "Run"),
            summary(4, "2025-11-15", 500.0, "Hike"),  # after the date
            summary(5, "2025-02-01", 900.0, "Ride"),  # cycling
            summary(6, "2025-01-10", 700.0, "AlpineSki"),  # lift-served
            summary(7, "2021-06-01", 50.0, "Walk"),  # oldest year shown
            summary(8, "2020-06-01", 70.0, "Walk"),  # too old
        ]
    )
    stats = year_to_date(VertCache(), strava)

    assert stats.as_of == AS_OF
    assert totals(stats) == {2026: 1200.0, 2025: 300.0, 2024: 0.0, 2023: 0.0, 2022: 0.0, 2021: 50.0}
    assert [y.year for y in stats.years] == [2026, 2025, 2024, 2023, 2022, 2021]
    # fetched once, from Jan 1 of the oldest year (less a day for timezones)
    assert afters(strava) == [datetime(2020, 12, 31, tzinfo=UTC)]


def test_leap_day_counts_only_once_its_date_has_passed() -> None:
    strava = FakeStrava()
    strava.add_activities([summary(1, "2024-02-28", 10.0), summary(2, "2024-02-29", 20.0)])
    assert totals(year_to_date(VertCache(), strava, date(2025, 2, 28)))[2024] == 10.0
    assert totals(year_to_date(VertCache(), strava, date(2025, 3, 1)))[2024] == 30.0


def test_later_requests_the_same_day_fetch_only_recent_activities() -> None:
    strava = FakeStrava()
    activities = [summary(1, "2026-11-13", 100.0), summary(2, "2025-03-01", 40.0)]
    strava.add_activities(activities)
    cache = VertCache()
    year_to_date(cache, strava)

    activities.append(summary(3, "2026-11-14", 25.0))  # a new activity
    activities[0]["total_elevation_gain"] = 110.0  # an edited one
    stats = year_to_date(cache, strava)

    assert totals(stats)[2026] == 135.0  # not double counted
    assert totals(stats)[2025] == 40.0
    sync_after = afters(strava)[-1]
    assert abs(sync_after - (datetime.now(UTC) - SYNC_OVERLAP)) < timedelta(seconds=5)


def test_a_new_day_fetches_everything_again() -> None:
    strava = FakeStrava()
    strava.add_activities([summary(1, "2025-11-15", 60.0)])
    cache = VertCache()
    assert totals(year_to_date(cache, strava))[2025] == 0.0
    assert totals(year_to_date(cache, strava, date(2026, 11, 15)))[2025] == 60.0
    assert len(afters(strava)) == 2 and afters(strava)[1] == datetime(2020, 12, 31, tzinfo=UTC)


def test_least_recently_used_athlete_is_dropped() -> None:
    strava = FakeStrava()
    strava.add_activities([])
    cache = VertCache(size=1)
    year_to_date(cache, strava, athlete_id=1)
    year_to_date(cache, strava, athlete_id=2)
    year_to_date(cache, strava, athlete_id=1)  # was dropped: fetches everything again
    assert all(after.year == 2020 for after in afters(strava))
