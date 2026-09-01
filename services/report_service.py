"""
Report service — aggregates proxy data for dashboard and reports.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from database.models import (
    Teacher, Class, ProxyAssignment, ClassDayMerge,
    Absence, TeacherLoadSummary, Timetable
)
from services.availability_service import (
    get_merge_excluded_counts, total_merge_excluded_count, get_or_create_backup_teachers
)


def _apply_merge_exclusion(counts, proxies):
    """Subtract each teacher's merge-freed daily budget from their
    per-date counts in place. `counts` is {teacher_id: {date: n}} as
    already accumulated from `proxies`. See availability_service —
    the exclusion is a per-day budget (however many of the teacher's
    periods were merge-freed that day), not tied to a specific period."""
    excluded = get_merge_excluded_counts(proxies)
    for (tid, d), n in excluded.items():
        if tid in counts and d in counts[tid]:
            counts[tid][d] = max(0, counts[tid][d] - n)


def _backup_rows(counts, dates):
    """Row(s) for BV1/BV2 — always appended LAST (after the normal
    total-sorted rows), with their real proxy counts if any were assigned
    in this date range."""
    rows = []
    for t in get_or_create_backup_teachers():
        daily = {d: counts[t.id].get(d, 0) for d in dates}
        total = sum(daily.values())
        rows.append({
            'teacher':      t,
            'daily_counts': daily,
            'dates':        dates,
            'total':        total,
        })
    return rows


def _week_start(d=None):
    d = d or date.today()
    return d - timedelta(days=d.weekday())   # Monday


def _week_end(d=None):
    """KVS week ends on Saturday (Mon–Sat, 6-day week)."""
    return _week_start(d) + timedelta(days=5)


def _month_start(d=None):
    d = d or date.today()
    return d.replace(day=1)


# ── Daily report ───────────────────────────────────────────────────────────

def get_daily_report(target_date=None):
    target_date = target_date or date.today()
    absences = Absence.query.filter_by(absent_date=target_date).all()
    proxies  = ProxyAssignment.query.filter_by(date=target_date).order_by(
        ProxyAssignment.period_no).all()

    # ClassDayMerge = actual class merges (going_class → host_class for full day)
    # ProxyAssignment.status='merge' is NOT used — merges live in ClassDayMerge only
    day_merges = ClassDayMerge.query.filter_by(date=target_date).all()

    confirmed = [p for p in proxies if p.status == 'confirmed']
    pending   = [p for p in proxies if p.status == 'pending']

    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()
    absent_ids   = {ab.teacher_id for ab in absences}

    return {
        'date':                   target_date,
        'absences':               absences,
        'proxies':                proxies,
        'day_merges':             day_merges,
        'confirmed_count':        len(confirmed),
        'pending_count':          len(pending),
        'merge_count':            len(day_merges),   # FIX: use ClassDayMerge count
        'total_periods_affected': len(proxies),
        'all_teachers':           all_teachers,
        'absent_ids':             absent_ids,
    }


# ── Custom date range report ───────────────────────────────────────────────

def get_custom_report(from_date=None, to_date=None):
    today = date.today()
    from_date = from_date or today
    to_date   = to_date   or today
    if to_date < from_date:
        to_date = from_date

    delta = (to_date - from_date).days + 1
    dates = [from_date + timedelta(days=i) for i in range(delta)]

    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()

    proxies = ProxyAssignment.query.filter(
        ProxyAssignment.date >= from_date,
        ProxyAssignment.date <= to_date,
        ProxyAssignment.status == 'confirmed'
    ).all()

    counts = defaultdict(lambda: defaultdict(int))
    for p in proxies:
        if p.proxy_teacher_id:
            counts[p.proxy_teacher_id][p.date] += 1
    _apply_merge_exclusion(counts, proxies)

    rows = []
    for t in all_teachers:
        daily = {d: counts[t.id].get(d, 0) for d in dates}
        total = sum(daily.values())
        rows.append({
            'teacher':      t,
            'daily_counts': daily,
            'dates':        dates,
            'total':        total,
        })

    rows.sort(key=lambda x: x['total'], reverse=True)
    rows += _backup_rows(counts, dates)   # BV1/BV2 — always last

    return {
        'from_date': from_date,
        'to_date':   to_date,
        'dates':     dates,
        'rows':      rows,
        'total':     sum(r['total'] for r in rows),
    }


# ── Monthly report ─────────────────────────────────────────────────────────

def get_weekly_report(ref_date=None):
    """Proxy count per teacher for the current Mon-Sat week."""
    ref_date = ref_date or date.today()
    start = _week_start(ref_date)
    end   = _week_end(ref_date)
    dates = [start + timedelta(days=i) for i in range(6)]   # Mon-Sat

    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()

    proxies = ProxyAssignment.query.filter(
        ProxyAssignment.date >= start,
        ProxyAssignment.date <= end,
        ProxyAssignment.status == 'confirmed'
    ).all()

    counts = defaultdict(lambda: defaultdict(int))
    for p in proxies:
        if p.proxy_teacher_id:
            counts[p.proxy_teacher_id][p.date] += 1
    _apply_merge_exclusion(counts, proxies)

    rows = []
    for t in all_teachers:
        daily = {d: counts[t.id].get(d, 0) for d in dates}
        total = sum(daily.values())
        rows.append({
            'teacher':      t,
            'daily_counts': daily,
            'dates':        dates,
            'total':        total,
        })

    rows.sort(key=lambda x: x['total'], reverse=True)
    rows += _backup_rows(counts, dates)   # BV1/BV2 — always last

    return {
        'week_start': start,
        'week_end':   end,
        'dates':      dates,
        'rows':       rows,
        'total':      sum(r['total'] for r in rows),
    }


def get_monthly_report(ref_date=None):
    ref_date = ref_date or date.today()
    start = _month_start(ref_date)
    _, ld = calendar.monthrange(ref_date.year, ref_date.month)
    end   = date(ref_date.year, ref_date.month, ld)
    dates = [start + timedelta(days=i) for i in range(ld)]

    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()

    proxies = ProxyAssignment.query.filter(
        ProxyAssignment.date >= start,
        ProxyAssignment.date <= end,
        ProxyAssignment.status == 'confirmed'
    ).all()

    counts = defaultdict(lambda: defaultdict(int))
    for p in proxies:
        if p.proxy_teacher_id:
            counts[p.proxy_teacher_id][p.date] += 1
    _apply_merge_exclusion(counts, proxies)

    rows = []
    for t in all_teachers:
        daily = {d: counts[t.id].get(d, 0) for d in dates}
        total = sum(daily.values())
        rows.append({
            'teacher':      t,
            'daily_counts': daily,
            'dates':        dates,
            'total':        total,
        })

    rows.sort(key=lambda x: x['total'], reverse=True)
    rows += _backup_rows(counts, dates)   # BV1/BV2 — always last

    return {
        'month_start': start,
        'month_end':   end,
        'dates':       dates,
        'rows':        rows,
        'total':       sum(r['total'] for r in rows),
    }


# ── Teacher-wise load ──────────────────────────────────────────────────────

def get_teacher_load_report():
    active_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()
    backup_teachers = get_or_create_backup_teachers()
    today    = date.today()
    week_s   = _week_start(today)
    week_e   = _week_end(today)
    month_s  = _month_start(today)
    _, ld    = calendar.monthrange(today.year, today.month)
    month_e  = date(today.year, today.month, ld)

    # All-time Total — computed LIVE from ProxyAssignment (same method as
    # Weekly/Monthly below), not from the persisted TeacherLoadSummary
    # counter. The persisted counter can drift out of sync over time
    # (double-confirms, reassignments, cancelled absences) — recomputing
    # fresh here means Total can never disagree with Weekly/Monthly again.
    all_rows = ProxyAssignment.query.filter(
        ProxyAssignment.status == 'confirmed',
        ProxyAssignment.proxy_teacher_id.isnot(None)
    ).all()
    total_counts = {}
    for r in all_rows:
        total_counts[r.proxy_teacher_id] = total_counts.get(r.proxy_teacher_id, 0) + 1
    for (tid, _d), n in get_merge_excluded_counts(all_rows).items():
        if tid in total_counts:
            total_counts[tid] = max(0, total_counts[tid] - n)

    def _row(t):
        load = t.load_summary
        # Always read weekly/monthly LIVE from ProxyAssignment to avoid stale counts.
        #
        # Bug fix: both ends of the date range are now bounded. Previously
        # these filters only had a ">=" lower bound with NO upper bound, so
        # a proxy confirmed for a date outside the current week/month (e.g.
        # pre-planned for a future date) could wrongly inflate "This Week"
        # / "This Month" here even though no such proxy applied to that
        # period — which is why these numbers could disagree with the
        # correctly-bounded Weekly/Monthly report tabs. The bounds below
        # now exactly match _week_start/_week_end and the Monthly report's
        # month_start/month_end, so the totals always agree.
        weekly_rows = ProxyAssignment.query.filter(
            ProxyAssignment.proxy_teacher_id == t.id,
            ProxyAssignment.date >= week_s,
            ProxyAssignment.date <= week_e,
            ProxyAssignment.status == 'confirmed'
        ).all()
        monthly_rows = ProxyAssignment.query.filter(
            ProxyAssignment.proxy_teacher_id == t.id,
            ProxyAssignment.date >= month_s,
            ProxyAssignment.date <= month_e,
            ProxyAssignment.status == 'confirmed'
        ).all()
        weekly  = len(weekly_rows)  - total_merge_excluded_count(weekly_rows)
        monthly = len(monthly_rows) - total_merge_excluded_count(monthly_rows)
        return {
            'teacher':   t,
            'total':     total_counts.get(t.id, 0),
            'weekly':    weekly,
            'monthly':   monthly,
            'last_date': load.last_proxy_date   if load else None,
        }

    result = [_row(t) for t in active_teachers]
    result.sort(key=lambda x: x['total'], reverse=True)
    result += [_row(t) for t in backup_teachers]   # BV1/BV2 — always last
    return result


# ── Leave (absence) summary — periods missed per teacher ───────────────────

def get_leave_summary(start=None, end=None):
    """
    For every active teacher, total periods missed due to leave: for each
    day the teacher was marked absent, however many periods they had on
    the master timetable that day (by day-of-week), summed up.

    Example: absent Monday with 6 periods that day, absent again another
    day with 7 periods that day -> leave_periods = 13.

    start/end (optional, inclusive dates) restrict this to absences within
    that window — used by the monthly Excel export's "Leave Summary"
    column. With no start/end, this is the all-time running total used by
    the dashboard widget; it is computed live from Absence + Timetable, so
    it is always up to date the moment a new absence is recorded — no
    separate counter to keep in sync.

    Sorted descending by leave_periods (heaviest leave-takers first).
    """
    teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()

    q = Absence.query
    if start is not None:
        q = q.filter(Absence.absent_date >= start)
    if end is not None:
        q = q.filter(Absence.absent_date <= end)
    absences = q.all()

    # Periods-per-teacher-per-weekday, loaded once (avoids N+1 queries).
    periods_by_teacher_day = defaultdict(int)
    for tt in Timetable.query.all():
        periods_by_teacher_day[(tt.teacher_id, tt.day)] += 1

    leave_periods = defaultdict(int)
    for ab in absences:
        day_name = ab.absent_date.strftime('%A')
        leave_periods[ab.teacher_id] += periods_by_teacher_day.get((ab.teacher_id, day_name), 0)

    rows = [{
        'teacher':       t,
        'leave_periods': leave_periods.get(t.id, 0),
    } for t in teachers]

    rows.sort(key=lambda x: x['leave_periods'], reverse=True)
    return rows


# ── Merge report ──────────────────────────────────────────────────────────

def get_merge_report():
    """Returns ClassDayMerge records (the real merge model)."""
    return ClassDayMerge.query.order_by(ClassDayMerge.date.desc()).limit(50).all()


# ── Dashboard summary ──────────────────────────────────────────────────────

def get_dashboard_summary():
    today = date.today()
    daily = get_daily_report(today)

    total_teachers   = Teacher.query.filter_by(status='active').count()
    total_classes    = Class.query.count()
    total_tt_entries = Timetable.query.count()

    loads = TeacherLoadSummary.query.order_by(
        TeacherLoadSummary.total_proxy_count.desc()
    ).limit(5).all()

    # All-time leave summary for every active teacher (dashboard widget).
    # Computed live from Absence + Timetable, so it's always current —
    # updates the moment a new absence/proxy is recorded, nothing to cache.
    leave_summary = get_leave_summary()

    return {
        'today':              today,
        'daily':              daily,
        'total_teachers':     total_teachers,
        'total_classes':      total_classes,
        'total_tt_entries':   total_tt_entries,
        'top_proxy_teachers': loads,
        'leave_summary':      leave_summary,
    }
