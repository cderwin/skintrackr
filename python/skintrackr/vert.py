"""Year-to-date vert: elevation gain from Jan 1 to today's date, this year and in previous years.

Totals are kept in a small in-memory cache (lost when the server restarts, which is fine). An athlete's
first request of the day fetches every activity for all the years shown. Later requests that day fetch only
recent activities, and merge them by id into the current year.
"""

from collections import OrderedDict
from datetime import UTC, date, datetime, time, timedelta

from attrs import define, field, frozen

from skintrackr.clients.strava import StravaClient
from skintrackr.models import ActivitySummary

# previous years to compare against
PREVIOUS_YEARS = 5
# athletes whose totals are cached; the least recently used is dropped beyond this
CACHE_SIZE = 256
# how far before the last sync to fetch again, so activities uploaded late (or edited) are still counted
SYNC_OVERLAP = timedelta(days=14)
# Strava's `after`/`before` are in UTC but years are by local start date, so fetch this much either side
TIMEZONE_MARGIN = timedelta(days=1)

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
    """Year-to-date vert for the current year and the previous ones, newest first."""

    as_of: date
    years: list[YearVert]


@define
class _AthleteVert:
    as_of: date
    # previous years' totals, fixed for the day
    previous: dict[int, float]
    # this year's counted activities: id -> vert
    current: dict[int, float]
    synced_at: datetime

    def stats(self) -> VertStats:
        years = [YearVert(self.as_of.year, sum(self.current.values()))]
        years += [YearVert(year, vert) for year, vert in sorted(self.previous.items(), reverse=True)]
        return VertStats(as_of=self.as_of, years=years)


def _local_date(activity: ActivitySummary) -> date:
    return date.fromisoformat(activity.start_date_local[:10])


def _counts(activity: ActivitySummary, as_of: date) -> bool:
    """Whether an activity is on foot and falls in its year's Jan 1 to `as_of` window."""
    day = _local_date(activity)
    return activity.sport_type not in EXCLUDED_SPORT_TYPES and (day.month, day.day) <= (as_of.month, as_of.day)


@define
class VertCache:
    """Athletes' year-to-date vert, cached in memory for up to `size` athletes."""

    size: int = CACHE_SIZE
    _entries: OrderedDict[int, _AthleteVert] = field(factory=OrderedDict, init=False)

    async def year_to_date(self, strava: StravaClient, athlete_id: int, as_of: date) -> VertStats:
        """An athlete's vert from Jan 1 to `as_of`'s month and day, this year and the previous years.

        Args:
            strava: A client acting for the athlete.
            athlete_id: The athlete, as the cache key.
            as_of: Today, in the athlete's timezone.

        Raises:
            StravaError: Listing activities failed.
        """
        entry = self._entries.get(athlete_id)
        now = datetime.now(UTC)
        if entry is None or entry.as_of != as_of:
            entry = await self._fetch_all(strava, as_of, now)
        else:
            for activity in await strava.list_activities(entry.synced_at - SYNC_OVERLAP):
                if _local_date(activity).year == as_of.year:
                    if _counts(activity, as_of):
                        entry.current[activity.id] = activity.total_elevation_gain
                    else:
                        entry.current.pop(activity.id, None)
            entry.synced_at = now
        self._entries[athlete_id] = entry
        self._entries.move_to_end(athlete_id)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)
        return entry.stats()

    @staticmethod
    async def _fetch_all(strava: StravaClient, as_of: date, now: datetime) -> _AthleteVert:
        first_year = as_of.year - PREVIOUS_YEARS
        after = datetime.combine(date(first_year, 1, 1), time(), UTC) - TIMEZONE_MARGIN
        previous = dict.fromkeys(range(first_year, as_of.year), 0.0)
        current: dict[int, float] = {}
        for activity in await strava.list_activities(after):
            year = _local_date(activity).year
            if not _counts(activity, as_of):
                continue
            if year == as_of.year:
                current[activity.id] = activity.total_elevation_gain
            elif year in previous:
                previous[year] += activity.total_elevation_gain
        return _AthleteVert(as_of=as_of, previous=previous, current=current, synced_at=now)
