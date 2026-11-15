"""Year-to-date vert: elevation gain from Jan 1 to a date, that year and in previous years.

Computed on each request from an athlete's activity index (see `Store.activities`).
"""

from collections.abc import Iterable
from datetime import date

from attrs import frozen

from skintrackr.models import ActivitySummary

# previous years to compare against
PREVIOUS_YEARS = 5

# Sport types that don't count: rides, and lift-served skiing. Everything else is on foot (or near enough).
EXCLUDED_SPORT_TYPES = frozenset(
    {
        "Ride",
        "MountainBikeRide",
        "GravelRide",
        "EBikeRide",
        "EMountainBikeRide",
        "VirtualRide",
        "Velomobile",
        "Handcycle",
        "AlpineSki",
        "Snowboard",
    }
)


@frozen
class YearVert:
    """One year's vert from Jan 1 to the `as_of` month and day, in meters."""

    year: int
    vert_m: float


@frozen
class VertStats:
    """Year-to-date vert for the current year and the previous ones, newest first.

    Attributes:
        as_of: The date the totals run to, in each year.
        years: This year first, then the previous ones.
        activities: Activities in the athlete's index (0: nothing imported yet).
    """

    as_of: date
    years: list[YearVert]
    activities: int


def year_to_date(activities: Iterable[ActivitySummary], as_of: date) -> VertStats:
    """Sum on-foot vert by year, from Jan 1 to `as_of`'s month and day, by each activity's local start date."""
    totals = dict.fromkeys(range(as_of.year, as_of.year - PREVIOUS_YEARS - 1, -1), 0.0)
    count = 0
    for activity in activities:
        count += 1
        day = date.fromisoformat(activity.start_date_local[:10])
        if (
            day.year in totals
            and activity.sport_type not in EXCLUDED_SPORT_TYPES
            and (day.month, day.day) <= (as_of.month, as_of.day)
        ):
            totals[day.year] += activity.total_elevation_gain
    return VertStats(as_of=as_of, years=[YearVert(year, vert) for year, vert in totals.items()], activities=count)
