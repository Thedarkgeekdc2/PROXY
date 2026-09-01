"""
Scoring engine — transparent proxy teacher scoring.

PERFORMANCE: Use ScoringContext to pre-load all data in bulk
before the scoring loop.  This reduces ~8000 DB queries per page
load (with 5 absent teachers) down to ~15 total.
"""

from datetime import timedelta
from database.models import (
    db, Teacher, Class, Timetable, ProxyAssignment,
    Absence, TeacherLoadSummary
)
from sqlalchemy import func


# ────────────────────────────────────────────────────────────────────────────
#  ScoringContext — bulk-loaded once per page request
# ────────────────────────────────────────────────────────────────────────────

class ScoringContext:
    """
    Pre-loads ALL data needed for scoring in ~10 bulk queries.
    Pass one context to every score_teacher() call; no further DB hits.
    """

    def __init__(self, date_obj, day):
        self.date_obj    = date_obj
        self.day         = day

        week_start  = date_obj - timedelta(days=date_obj.weekday())
        week_end    = week_start + timedelta(days=5)          # Mon–Sat
        month_start = date_obj.replace(day=1)

        # ── Proxy counts ─────────────────────────────────────────────────
        def _bulk(start, end):
            rows = (db.session.query(
                        ProxyAssignment.proxy_teacher_id,
                        func.count(ProxyAssignment.id))
                    .filter(
                        ProxyAssignment.date >= start,
                        ProxyAssignment.date <= end,
                        ProxyAssignment.status == 'confirmed',
                        ProxyAssignment.proxy_teacher_id.isnot(None))
                    .group_by(ProxyAssignment.proxy_teacher_id).all())
            return {tid: cnt for tid, cnt in rows}

        self.today_counts  = _bulk(date_obj, date_obj)
        self.weekly_counts = _bulk(week_start, date_obj)

        # ── Proxy-in-period lookup {(proxy_tid, period_no)} ──────────────
        prx_today = ProxyAssignment.query.filter_by(
            date=date_obj, status='confirmed').all()
        self.proxy_in_period = {
            (p.proxy_teacher_id, p.period_no)
            for p in prx_today if p.proxy_teacher_id
        }

        # ── Absent teacher IDs ────────────────────────────────────────────
        self.absent_ids = {
            a.teacher_id
            for a in Absence.query.filter_by(absent_date=date_obj).all()
        }

        # ── Blocked teacher IDs ───────────────────────────────────────────
        self.blocked_ids = {
            t.id for t in Teacher.query.filter_by(is_blocked=True).all()
        }

        # ── All active teachers (pre-loaded once) ─────────────────────────
        self.all_teachers = Teacher.query.filter_by(
            status='active', is_blocked=False).all()

        # ── Merged-away class IDs ─────────────────────────────────────────
        from services.availability_service import get_merged_away_class_ids
        self.merged_away = get_merged_away_class_ids(date_obj)

        # ── Timetable for today: {teacher_id: {period_no: class_id}} ─────
        all_tts = Timetable.query.filter_by(day=day).all()
        self.teacher_periods = {}   # {tid: {period: class_id}}
        self.teacher_class_names = {}  # {tid: set of class_names}

        for tt in all_tts:
            tid = tt.teacher_id
            if tid not in self.teacher_periods:
                self.teacher_periods[tid] = {}
            self.teacher_periods[tid][tt.period_no] = tt.class_id

            cls_name = tt.class_.class_name if tt.class_ else None
            if cls_name:
                if tid not in self.teacher_class_names:
                    self.teacher_class_names[tid] = set()
                self.teacher_class_names[tid].add(cls_name)

        # ── Teacher load summaries {teacher_id: TeacherLoadSummary} ──────
        self.loads = {l.teacher_id: l
                      for l in TeacherLoadSummary.query.all()}

        # ── Last-proxy-date {teacher_id: date} ───────────────────────────
        # (already in TeacherLoadSummary.last_proxy_date)

    # ── Helper methods (no DB hits) ───────────────────────────────────────

    def today_count(self, tid):
        return self.today_counts.get(tid, 0)

    def weekly_count(self, tid):
        return self.weekly_counts.get(tid, 0)

    def is_absent(self, tid):
        return tid in self.absent_ids

    def is_blocked(self, tid):
        return tid in self.blocked_ids

    def already_proxy_in_period(self, tid, period_no):
        return (tid, period_no) in self.proxy_in_period

    def free_periods(self, tid):
        """Periods NOT occupied by own class (merge-aware)."""
        periods = self.teacher_periods.get(tid, {})
        real_busy = sum(
            1 for p, cid in periods.items()
            if cid not in self.merged_away
        )
        return 8 - real_busy

    def own_class_count(self, tid):
        """Own classes today (excluding merged-away)."""
        periods = self.teacher_periods.get(tid, {})
        return sum(
            1 for cid in periods.values()
            if cid not in self.merged_away
        )

    def max_weekly(self, tid):
        t = Teacher.query.get(tid)
        return t.max_weekly_proxy if t else 8

    def eff_daily_limit(self, teacher, mode='auto'):
        """
        Auto mode: conservative — leaves at least 1 free period as buffer
        whenever the teacher has 3+ free periods (existing fairness rule).
        Manual mode: full freedom — the TT IC can assign up to every
        genuinely free period, no automatic buffer held back.
        """
        free_p = self.free_periods(teacher.id)
        if mode == 'manual':
            return max(teacher.max_daily_proxy, free_p)
        if free_p >= 3:
            return max(teacher.max_daily_proxy, free_p - 1)
        return teacher.max_daily_proxy

    def remaining_today(self, teacher, mode='auto'):
        eff  = self.eff_daily_limit(teacher, mode=mode)
        done = self.today_count(teacher.id)
        own  = self.own_class_count(teacher.id)
        # Auto keeps the conservative 7-period/day wellbeing cap.
        # Manual allows the true physical ceiling of 8 periods/day.
        day_cap     = 8 if mode == 'manual' else 7
        hard        = max(0, day_cap - own - done)
        weekly_used = self.weekly_count(teacher.id)
        weekly_rem  = max(0, teacher.max_weekly_proxy - weekly_used)
        return max(0, min(eff - done, hard, weekly_rem))

    def freed_by_merge(self, tid):
        return any(
            cid in self.merged_away
            for cid in self.teacher_periods.get(tid, {}).values()
        )

    def merge_free_period_nos(self, tid):
        """List of this teacher's own period numbers today whose class was
        merged away (i.e. genuinely freed by the merge, not just any free
        period)."""
        periods = self.teacher_periods.get(tid, {})
        return [p for p, cid in periods.items() if cid in self.merged_away]

    def merge_remaining(self, tid):
        """How many of this teacher's merge-freed periods today are still
        UNUSED. This is a DAILY BUDGET, not tied to any specific period —
        if the teacher has 1 merge-freed period today and gets ANY proxy
        assignment today (in that period or any other free period), the
        budget is used: they still only "gained" one net free period today,
        so one of their proxies is absorbed by it. 2 -> 1 -> 0 as their
        TOTAL proxy count today increases, regardless of which period(s)
        those proxies actually landed in."""
        merge_periods = self.merge_free_period_nos(tid)
        return max(0, len(merge_periods) - self.today_count(tid))

    def load(self, tid):
        return self.loads.get(tid)


# ────────────────────────────────────────────────────────────────────────────
#  score_teacher  — pure in-memory, zero DB hits when context is provided
# ────────────────────────────────────────────────────────────────────────────

def score_teacher(teacher, cls, date_obj, period_no, ctx: ScoringContext, mode='auto'):
    """
    Score a proxy candidate.
    All data comes from ctx (pre-loaded) — NO DB queries.

    mode='auto'   — conservative daily limit (leaves 1 period buffer,
                    7-period/day wellbeing cap).
    mode='manual' — full freedom: up to every genuinely free period,
                    true 8-period/day physical ceiling, no held-back buffer.

    Returns (score, reasons, eligible)
    score = -999 means hard reject.
    """
    score   = 0.0
    reasons = []
    tid     = teacher.id

    # ── Hard rejects ──────────────────────────────────────────────────────
    if ctx.is_blocked(tid):
        return -999, ['REJECTED: Teacher is blocked'], False

    if teacher.status != 'active':
        return -999, ['REJECTED: Inactive'], False

    if ctx.is_absent(tid):
        return -999, ['REJECTED: Absent today'], False

    if ctx.already_proxy_in_period(tid, period_no):
        return -999, [f'REJECTED: Already proxy in P{period_no}'], False

    today_done  = ctx.today_count(tid)
    free_p      = ctx.free_periods(tid)
    own_cls     = ctx.own_class_count(tid)
    eff_max     = ctx.eff_daily_limit(teacher, mode=mode)
    weekly      = ctx.weekly_count(tid)
    load        = ctx.load(tid)
    total_done  = load.total_proxy_count if load else 0

    if today_done >= eff_max:
        return -999, [
            f'REJECTED: Daily limit ({today_done}/{eff_max}, free={free_p})'
        ], False

    day_cap = 8 if mode == 'manual' else 7
    if (own_cls + today_done) >= day_cap:
        return -999, [
            f'REJECTED: {day_cap}-period cap ({own_cls}+{today_done}={day_cap})'
        ], False

    if weekly >= teacher.max_weekly_proxy:
        return -999, [
            f'REJECTED: Weekly limit ({weekly}/{teacher.max_weekly_proxy})'
        ], False

    # ── Level match ────────────────────────────────────────────────────────
    try:
        class_num = int(cls.class_name)
    except Exception:
        class_num = 3
    c_level = 'lower_primary' if class_num <= 2 else 'upper_primary'
    t_level = teacher.level or 'both'

    if t_level == c_level:
        score += 20; reasons.append('+20  Level match')
    elif t_level == 'both':
        score += 10; reasons.append('+10  All-level teacher')
    else:
        score -= 5;  reasons.append('-5   Level mismatch')

    # ── Free period bonus ──────────────────────────────────────────────────
    if free_p >= 5:
        score += 15; reasons.append(f'+15  Very free ({free_p} free today)')
    elif free_p >= 3:
        score += 8;  reasons.append(f'+8   Free ({free_p} today)')
    elif free_p == 2:
        score += 3;  reasons.append('+3   Somewhat free (2)')
    else:
        score -= 5;  reasons.append(f'-5   Barely free ({free_p})')

    # ── Total load ─────────────────────────────────────────────────────────
    if total_done == 0:
        score += 25; reasons.append('+25  Never proxied')
    elif total_done <= 3:
        score += 20; reasons.append(f'+20  Very low total ({total_done})')
    elif total_done <= 7:
        score += 15; reasons.append(f'+15  Low total ({total_done})')
    elif total_done <= 15:
        score += 10; reasons.append(f'+10  Moderate ({total_done})')
    elif total_done <= 25:
        score += 5;  reasons.append(f'+5   Acceptable ({total_done})')
    else:
        score -= 5;  reasons.append(f'-5   High total ({total_done})')

    # ── Weekly load ────────────────────────────────────────────────────────
    if weekly == 0:
        score += 10; reasons.append('+10  No proxy this week')
    elif weekly <= 2:
        score += 5;  reasons.append(f'+5   Low weekly ({weekly})')
    elif weekly > 4:
        score -= 10; reasons.append(f'-10  High weekly ({weekly})')

    # ── Recency ────────────────────────────────────────────────────────────
    if load and load.last_proxy_date:
        days_since = (date_obj - load.last_proxy_date).days
        if days_since >= 7:
            score += 10; reasons.append(f'+10  Last proxy {days_since}d ago')
        elif days_since >= 3:
            score += 5;  reasons.append(f'+5   Last proxy {days_since}d ago')
        elif days_since == 0:
            score -= 5;  reasons.append('-5   Used today')
    else:
        score += 10; reasons.append('+10  Fresh (never proxied)')

    # ── Today penalty ──────────────────────────────────────────────────────
    if today_done > 0:
        pen = today_done * 8
        score -= pen
        reasons.append(f'-{pen}  {today_done} proxy already today')

    # ── Fairness (utilization ratio) ───────────────────────────────────────
    if eff_max > 0:
        util = today_done / eff_max
        pen  = round(util * 30)
        if pen > 0:
            score -= pen
            reasons.append(f'-{pen}  Fairness ({today_done}/{eff_max} used)')

    # ── Class familiarity ──────────────────────────────────────────────────
    if cls.class_name in ctx.teacher_class_names.get(tid, set()):
        score += 5; reasons.append(f'+5   Familiar with Class {cls.class_name}')

    return round(score, 2), reasons, True


# ────────────────────────────────────────────────────────────────────────────
#  Standalone helpers (used outside scoring loop — still cached)
# ────────────────────────────────────────────────────────────────────────────

def get_teacher_free_periods(teacher_id, day, date_obj=None):
    from services.availability_service import get_merged_away_class_ids
    tts = Timetable.query.filter_by(teacher_id=teacher_id, day=day).all()
    if date_obj:
        merged_away = get_merged_away_class_ids(date_obj)
        real_busy   = sum(1 for tt in tts if tt.class_id not in merged_away)
    else:
        real_busy = len(tts)
    return 8 - real_busy


def get_teacher_own_class_count(teacher_id, day, date_obj=None):
    tts = Timetable.query.filter_by(teacher_id=teacher_id, day=day).all()
    if date_obj:
        from services.availability_service import get_merged_away_class_ids
        merged_away = get_merged_away_class_ids(date_obj)
        return sum(1 for tt in tts if tt.class_id not in merged_away)
    return len(tts)


def get_effective_daily_limit(teacher, day, date_obj):
    free_p     = get_teacher_free_periods(teacher.id, day, date_obj)
    today_done = ProxyAssignment.query.filter_by(
        proxy_teacher_id=teacher.id, date=date_obj, status='confirmed').count()
    own_cls    = get_teacher_own_class_count(teacher.id, day, date_obj)
    if free_p >= 3:
        eff_max = max(teacher.max_daily_proxy, free_p - 1)
    else:
        eff_max = teacher.max_daily_proxy
    hard_rem = max(0, 7 - own_cls - today_done)

    # Weekly remaining cap — Rem: must never overstate what's left once the
    # weekly quota is close to used up, even if today's daily allowance
    # would otherwise permit more.
    week_start  = date_obj - timedelta(days=date_obj.weekday())
    weekly_used = ProxyAssignment.query.filter(
        ProxyAssignment.proxy_teacher_id == teacher.id,
        ProxyAssignment.date >= week_start,
        ProxyAssignment.date <= date_obj,
        ProxyAssignment.status == 'confirmed'
    ).count()
    weekly_rem = max(0, teacher.max_weekly_proxy - weekly_used)

    remaining = max(0, min(eff_max - today_done, hard_rem, weekly_rem))
    return eff_max, today_done, remaining
