"""
Determines which teachers are free in a given period on a given day.
Accounts for day-level class merges (going_class teachers become free).
"""
from database.models import Teacher, Timetable, Absence, ProxyAssignment


def get_merged_away_class_ids(date_obj):
    """Classes that are merged into another class on this date — their teachers are free."""
    try:
        from database.models import ClassDayMerge
        merges = ClassDayMerge.query.filter_by(date=date_obj).all()
        return {m.going_class_id for m in merges}
    except Exception:
        return set()


def get_busy_teacher_ids(day, period_no, date_obj=None):
    """Teachers who have a timetable class in this period on this day.
    Excludes teachers whose class is merged away (they are free)."""
    rows = Timetable.query.filter_by(day=day, period_no=period_no).all()
    merged_away = get_merged_away_class_ids(date_obj) if date_obj else set()
    return {r.teacher_id for r in rows if r.class_id not in merged_away}


def get_absent_teacher_ids(date_obj):
    rows = Absence.query.filter_by(absent_date=date_obj).all()
    return {r.teacher_id for r in rows}


def get_proxy_busy_ids(date_obj, period_no):
    """Teachers already confirmed as proxy in this period today."""
    rows = ProxyAssignment.query.filter_by(
        date=date_obj, period_no=period_no, status='confirmed'
    ).all()
    return {r.proxy_teacher_id for r in rows if r.proxy_teacher_id}


def get_free_teachers(day, period_no, date_obj, exclude_ids=None,
                      preloaded=None):
    """
    Returns list of Teacher objects who are free in this period.

    preloaded: optional dict with pre-loaded sets (from ScoringContext)
      {
        'absent_ids':    set of absent teacher IDs,
        'blocked_ids':   set of blocked teacher IDs,
        'all_teachers':  list of Teacher objects,
        'merged_away':   set of merged class IDs,
        'proxy_in_period': set of (proxy_tid, period_no) tuples,
      }
    When provided, avoids per-period DB queries entirely.
    """
    exclude_ids = set(exclude_ids or [])

    if preloaded:
        absent  = preloaded.get('absent_ids', set())
        blocked = preloaded.get('blocked_ids', set())
        merged_away = preloaded.get('merged_away', set())
        all_teachers = preloaded.get('all_teachers', [])

        # Busy = has class in this period AND class is not merged away
        from database.models import Timetable as _TT
        busy_rows = _TT.query.filter_by(day=day, period_no=period_no).all()
        busy = {r.teacher_id for r in busy_rows if r.class_id not in merged_away}

        already = {tid for (tid, p) in preloaded.get('proxy_in_period', set())
                   if p == period_no}

        blocked_out = busy | absent | blocked | already | exclude_ids
        return [t for t in all_teachers if t.id not in blocked_out]

    else:
        # Fallback: individual queries (slower)
        busy    = get_busy_teacher_ids(day, period_no, date_obj)
        absent  = get_absent_teacher_ids(date_obj)
        proxy_b = get_proxy_busy_ids(date_obj, period_no)

        blocked_out = busy | absent | proxy_b | exclude_ids

        all_teachers = Teacher.query.filter_by(status='active', is_blocked=False).all()
        return [t for t in all_teachers if t.id not in blocked_out]


def get_proxy_count_today(teacher_id, date_obj):
    return ProxyAssignment.query.filter_by(
        proxy_teacher_id=teacher_id,
        date=date_obj,
        status='confirmed'
    ).count()


def get_free_period_count(teacher_id, day):
    """How many of 8 periods has this teacher NO timetable class."""
    scheduled = Timetable.query.filter_by(teacher_id=teacher_id, day=day).count()
    return 8 - scheduled


# ── Backup teachers for P6/P7/P8 (pre-primary, not on master timetable) ────
# BV1 / BV2 are always-available fallback teachers for periods 6, 7, 8.
# They are real Teacher rows (status='backup') so they can be picked and
# stored as a normal proxy_teacher_id — but status='backup' keeps them out
# of every normal query (status='active'), so they never show up as a
# regular candidate for periods 1-5 or get pulled into scoring.

BACKUP_TEACHER_NAMES = ['BV1', 'BV2']


def get_or_create_backup_teachers():
    """Idempotent — creates BV1/BV2 the first time, just fetches them after."""
    from database.models import db, Teacher
    existing = {
        t.name: t
        for t in Teacher.query.filter(Teacher.name.in_(BACKUP_TEACHER_NAMES)).all()
    }
    missing = [n for n in BACKUP_TEACHER_NAMES if n not in existing]
    if missing:
        for n in missing:
            db.session.add(Teacher(
                name=n, full_name=n, subject='Pre-Primary (Backup)',
                level='both', status='backup',
                max_daily_proxy=8, max_weekly_proxy=48, is_blocked=False,
            ))
        db.session.commit()
        existing = {
            t.name: t
            for t in Teacher.query.filter(Teacher.name.in_(BACKUP_TEACHER_NAMES)).all()
        }
    return [existing[n] for n in BACKUP_TEACHER_NAMES if n in existing]


# ── Merge-covered proxy detection (for report exclusion) ───────────────────
# A teacher's merge-freed periods are a DAILY BUDGET, not tied to any one
# period. If DKC has P3 freed by a merge (1 period) and P1 was already
# genuinely free, and the arrangement lands in P1 — it STILL doesn't count
# as extra duty, because DKC nets out to the same "one free period today"
# either way. So: whichever period(s) the proxy actually lands in, up to
# `merge_free_count` of the teacher's proxies THAT DAY are excluded from
# arrangement/proxy-count reports — not specifically the ones in the
# merge-freed period itself.

def count_teacher_merge_free_periods(teacher_id, date_obj):
    """How many of teacher_id's own periods were merged away on date_obj."""
    try:
        from database.models import ClassDayMerge
        merged_classes = {
            m.going_class_id
            for m in ClassDayMerge.query.filter_by(date=date_obj).all()
        }
    except Exception:
        return 0
    if not merged_classes:
        return 0
    day = date_obj.strftime('%A')
    return Timetable.query.filter(
        Timetable.day == day,
        Timetable.teacher_id == teacher_id,
        Timetable.class_id.in_(merged_classes)
    ).count()


def is_merge_covered_assignment(proxy_teacher_id, date_obj):
    """Called right after confirm_proxy upserts+flushes a row. True if
    this assignment falls within the teacher's merge-freed budget for the
    day — i.e. their TOTAL confirmed proxies today (including this one,
    counted in confirmation order) is still <= their merge-freed period
    count for the day. Which period this particular one landed in does
    not matter."""
    if not proxy_teacher_id:
        return False
    merge_free = count_teacher_merge_free_periods(proxy_teacher_id, date_obj)
    if merge_free == 0:
        return False
    total_today = ProxyAssignment.query.filter_by(
        proxy_teacher_id=proxy_teacher_id, date=date_obj, status='confirmed'
    ).count()
    return total_today <= merge_free


def get_merge_excluded_counts(proxies):
    """Bulk version for reports. Given confirmed ProxyAssignment rows,
    returns {(teacher_id, date): excluded_count} — how many of that
    teacher's assignments on that date should be dropped from the
    arrangement/proxy-count total (min of their daily proxy count and
    their merge-freed period count that day)."""
    from collections import defaultdict
    from database.models import ClassDayMerge

    raw_by_teacher_date = defaultdict(int)
    for p in proxies:
        if p.proxy_teacher_id:
            raw_by_teacher_date[(p.proxy_teacher_id, p.date)] += 1
    if not raw_by_teacher_date:
        return {}

    dates = {d for (_, d) in raw_by_teacher_date}
    teacher_ids = {tid for (tid, _) in raw_by_teacher_date}

    merges = ClassDayMerge.query.filter(ClassDayMerge.date.in_(dates)).all()
    merged_by_date = defaultdict(set)
    for m in merges:
        merged_by_date[m.date].add(m.going_class_id)
    if not merged_by_date:
        return {}

    day_names_needed = {d.strftime('%A') for d in merged_by_date}
    tts = Timetable.query.filter(
        Timetable.teacher_id.in_(teacher_ids),
        Timetable.day.in_(day_names_needed)
    ).all()
    tt_by_teacher_day = defaultdict(list)
    for t in tts:
        tt_by_teacher_day[(t.teacher_id, t.day)].append(t.class_id)

    excluded = {}
    for (tid, d), raw in raw_by_teacher_date.items():
        merged_classes = merged_by_date.get(d)
        if not merged_classes:
            continue
        day_name = d.strftime('%A')
        class_ids = tt_by_teacher_day.get((tid, day_name), [])
        merge_free_count = sum(1 for cid in class_ids if cid in merged_classes)
        if merge_free_count:
            excluded[(tid, d)] = min(raw, merge_free_count)
    return excluded


def total_merge_excluded_count(proxies):
    """Sum of get_merge_excluded_counts() across all teacher/date groups —
    handy when a report only needs one grand-total number to subtract
    (e.g. weekly/monthly panel pills)."""
    return sum(get_merge_excluded_counts(proxies).values())
