"""Slot engine, pure part (no database): DST, cap per local day, min gap, horizon."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.slots import day_bounds, first_free, slot_instants

LON, NY = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
H = timedelta(hours=1)
M = timedelta(minutes=1)


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=UTC)


def free(times, tz, after, taken=(), cap=10, gap=0, days=30):
    return first_free(times, tz, cap, timedelta(minutes=gap), list(taken), after, after + timedelta(days=days))


def test_london_spring_gap_shifts_forward():
    # 2026-03-29: 01:00 GMT -> 02:00 BST, so 01:30 doesn't exist
    got = slot_instants(["01:30"], LON, utc(2026, 3, 28), utc(2026, 3, 30, 12))
    assert got == [utc(2026, 3, 28, 1, 30), utc(2026, 3, 29, 1, 30), utc(2026, 3, 30, 0, 30)]
    assert got[1].astimezone(LON).strftime("%H:%M %Z") == "02:30 BST"  # shifted forward by the gap


def test_london_fall_overlap_takes_first():
    # 2026-10-25: 02:00 BST -> 01:00 GMT, 01:30 happens twice; fold=0 is the BST one, and only once
    got = slot_instants(["01:30"], LON, utc(2026, 10, 24, 12), utc(2026, 10, 26, 12))
    assert got == [utc(2026, 10, 25, 0, 30), utc(2026, 10, 26, 1, 30)]
    assert got[0].astimezone(LON).strftime("%H:%M %Z") == "01:30 BST"


def test_new_york_dst():
    spring = slot_instants(["02:30", "09:00"], NY, utc(2026, 3, 8, 5), utc(2026, 3, 8, 23))  # 02:00 EST -> 03:00 EDT
    assert spring == [utc(2026, 3, 8, 7, 30), utc(2026, 3, 8, 13)]
    assert spring[0].astimezone(NY).strftime("%H:%M %Z") == "03:30 EDT"
    fall = slot_instants(["01:30"], NY, utc(2026, 11, 1, 0), utc(2026, 11, 1, 23))  # 02:00 EDT -> 01:00 EST
    assert fall == [utc(2026, 11, 1, 5, 30)]
    assert fall[0].astimezone(NY).strftime("%H:%M %Z") == "01:30 EDT"


def test_gap_times_collapse_to_one_slot():
    # 01:30 (in the gap) and 02:30 BST are the same instant on the spring day
    assert slot_instants(["01:30", "02:30"], LON, utc(2026, 3, 29), utc(2026, 3, 29, 12)) == [utc(2026, 3, 29, 1, 30)]


def test_day_bounds_dst_days():
    s, e = day_bounds(date(2026, 3, 29), LON)
    assert (s, e - s) == (utc(2026, 3, 29), 23 * H)
    s, e = day_bounds(date(2026, 10, 25), LON)
    assert (s, e - s) == (utc(2026, 10, 24, 23), 25 * H)


def test_cap_counts_the_local_day_across_utc_midnight():
    # New York 19:00/20:00/21:00 EDT = 23:00, 00:00 (+1), 01:00 (+1) UTC: one local day, two UTC days
    times, after = ["19:00", "20:00", "21:00"], utc(2026, 6, 1, 12)
    d19, d20, d21 = utc(2026, 6, 1, 23), utc(2026, 6, 2, 0), utc(2026, 6, 2, 1)
    assert free(times, NY, after, [d19], cap=2) == d20
    assert free(times, NY, after, [d19, d20], cap=2) == d19 + 24 * H  # 21:00 is the same local day: full
    assert free(times, NY, after, [d19, d20], cap=3) == d21
    # a post counts on its own local day: 00:30 EDT is 06-02, 22:30 EDT is still 06-01
    assert free(["19:00"], NY, after, [utc(2026, 6, 2, 4, 30)], cap=1) == d19
    assert free(["19:00"], NY, after, [utc(2026, 6, 2, 2, 30)], cap=1) == d19 + 24 * H


def test_min_gap_boundaries():
    after, nine = utc(2026, 6, 1, 6), utc(2026, 6, 1, 9)
    times = ["09:00", "09:30", "09:45"]
    tz = ZoneInfo("UTC")
    assert free(times, tz, after, [nine], gap=30) == nine + 30 * M  # exactly the gap apart: fine
    assert free(times, tz, after, [nine + M], gap=30) == nine + 45 * M  # 29 min: too close
    assert free(times, tz, after, [nine + 29 * M], gap=30) == nine + 24 * H  # blocks slots on both sides
    assert free(times, tz, after, [nine], gap=0) == nine + 30 * M  # the same instant is never free
    assert free(times, tz, after, [nine + M], gap=0) == nine


def test_after_is_inclusive_and_skips_earlier_slots():
    tz = ZoneInfo("UTC")
    assert free(["09:00", "09:05"], tz, utc(2026, 6, 1, 9)) == utc(2026, 6, 1, 9)
    assert free(["09:00", "09:05"], tz, utc(2026, 6, 1, 9, 0, 1)) == utc(2026, 6, 1, 9, 5)


def test_horizon():
    tz, after = ZoneInfo("UTC"), utc(2026, 6, 1, 10)
    assert free([], tz, after) is None
    days = [utc(2026, 6, 1, 9) + i * 24 * H for i in range(1, 31)]  # 06-02 .. 07-01
    assert free(["09:00"], tz, after, days[:-1], cap=1) == days[-1]  # exactly 30 days is inside
    assert free(["09:00"], tz, after, days, cap=1) is None
