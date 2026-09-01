"""
Proxy engine — generates suggestions using ScoringContext (bulk pre-loaded data).

Performance:
  OLD: 8,400+ DB queries for 5 absent teachers
  NEW: ~15 DB queries total, regardless of absent teacher count
"""

from database.models import (
    db, Teacher, Timetable, ProxyAssignment,
    TeacherLoadSummary
)
from services.availability_service import get_free_teachers, get_or_create_backup_teachers
from services.scoring_engine import ScoringContext, score_teacher

# Auto mode shows the TOP_N best-scored suggestions (restores original behavior).
# Manual mode shows EVERY eligible teacher, unranked, no cap — true free hand.
TOP_N = 5


def _day_from_date(d):
    return d.strftime('%A')


# ── Build ONE context per page request ────────────────────────────────────

def build_context(date_obj):
    """Create a ScoringContext. Call ONCE per request, reuse everywhere."""
    return ScoringContext(date_obj, _day_from_date(date_obj))


# ── Suggestions for a single period slot ──────────────────────────────────

def generate_suggestions_for_period(tt, date_obj, ctx, mode='auto'):
    """
    ctx  = ScoringContext (pre-loaded, no DB hits inside scoring).
    mode = 'auto'   → top-N best-scored suggestions, score shown, merge-freed first
           'manual' → ALL eligible teachers, no score/ranking, merge-freed first
                      then alphabetical — true free hand, no artificial cap.
    Returns (suggestions_list, needs_merge_bool)
    """
    day = _day_from_date(date_obj)

    # Free teachers for this slot (bulk-aware via availability_service)
    free = get_free_teachers(
        day, tt.period_no, date_obj,
        exclude_ids=[tt.teacher_id],
        preloaded={
            'absent_ids':      ctx.absent_ids,
            'blocked_ids':     ctx.blocked_ids,
            'all_teachers':    ctx.all_teachers,
            'merged_away':     ctx.merged_away,
            'proxy_in_period': ctx.proxy_in_period,
        }
    )

    def count_freed_periods(teacher_id):
        return sum(
            1 for cid in ctx.teacher_periods.get(teacher_id, {}).values()
            if cid in ctx.merged_away
        )

    scored = []
    for t in free:
        # Eligibility now depends on mode for the DAILY-limit / period-cap
        # checks specifically: auto stays conservative (buffer + 7-cap),
        # manual allows full freedom up to 8 periods/day. All other hard
        # rejects (blocked, absent, already-assigned, weekly limit) are
        # unaffected by mode and stay identical either way.
        s, reasons, eligible = score_teacher(
            t, tt.class_, date_obj, tt.period_no, ctx, mode=mode
        )
        if not eligible:
            continue

        freed_by_merge = ctx.freed_by_merge(t.id)
        free_p         = ctx.free_periods(t.id)
        eff_max        = ctx.eff_daily_limit(t, mode=mode)
        today_done     = ctx.today_count(t.id)
        remaining      = ctx.remaining_today(t, mode=mode)

        if freed_by_merge:
            reasons = ['+ Freed by class merge'] + reasons

        scored.append({
            'teacher':         t,
            'score':           s,
            'reasons':         reasons,
            'free_periods':    free_p,
            'freed_by_merge':  freed_by_merge,
            'freed_count':     count_freed_periods(t.id),
            # [M] tag count — how many of this teacher's merge-freed periods
            # today are still unused (2 -> 1 -> 0 as they get assigned).
            'merge_remaining': ctx.merge_remaining(t.id),
            'merge_periods':   ctx.merge_free_period_nos(t.id),
            'today_done':      today_done,
            'eff_max':         eff_max,
            'remaining':       remaining,
        })

    # Merge-freed teachers always shown first, regardless of mode
    freed_group = sorted(
        [s for s in scored if s['freed_by_merge']],
        key=lambda x: (x['freed_count'], x['score']),
        reverse=True
    )

    if mode == 'manual':
        # Free hand — no score-based ranking, no cap; alphabetical so the
        # list doesn't silently nudge the TT IC toward any particular pick.
        normal_group = sorted(
            [s for s in scored if not s['freed_by_merge']],
            key=lambda x: x['teacher'].display_name
        )
        suggestions = freed_group + normal_group   # ALL eligible — no cap
    else:
        # Auto — best-scored suggestions only, capped to TOP_N
        normal_group = sorted(
            [s for s in scored if not s['freed_by_merge']],
            key=lambda x: x['score'],
            reverse=True
        )
        suggestions = (freed_group + normal_group)[:TOP_N]

    # Backup teachers (BV1/BV2) — always offered for P6/P7/P8, in addition
    # to (never instead of) the normal suggestions, always listed last.
    # Not in the master timetable, so they're excluded from scoring/free
    # calculation above and appended here as plain selectable options.
    if tt.period_no in (6, 7, 8):
        for bt in get_or_create_backup_teachers():
            if ctx.already_proxy_in_period(bt.id, tt.period_no):
                continue
            if ctx.is_absent(bt.id) or ctx.is_blocked(bt.id):
                continue
            today_done = ctx.today_count(bt.id)
            eff_max    = bt.max_daily_proxy
            suggestions.append({
                'teacher':         bt,
                'score':           0,
                'reasons':         ['Backup teacher (pre-primary, default for P6-P8)'],
                'free_periods':    max(0, eff_max - today_done),
                'freed_by_merge':  False,
                'freed_count':     0,
                'merge_remaining': 0,
                'merge_periods':   [],
                'today_done':      today_done,
                'eff_max':         eff_max,
                'remaining':       max(0, eff_max - today_done),
                'is_backup':       True,
            })

    needs_merge = len(suggestions) == 0

    return suggestions, needs_merge


# ── All periods for one absent teacher ─────────────────────────────────────

def get_affected_periods(absent_teacher_id, date_obj):
    day = _day_from_date(date_obj)
    return Timetable.query.filter_by(
        teacher_id=absent_teacher_id, day=day
    ).order_by(Timetable.period_no).all()


def generate_all_suggestions(absent_teacher_id, date_obj, ctx=None, mode='auto'):
    """
    Generate suggestions for all periods of the absent teacher.

    Bug 1 Fix: Skip periods where the teacher's class is merged away
    (students already supervised by host teacher).

    ctx:  pass in if available (reuse across multiple absent teachers).
          If None, a new context is created (slightly less efficient).
    mode: 'auto' or 'manual' — see generate_suggestions_for_period().
    """
    if ctx is None:
        ctx = build_context(date_obj)

    merged_away = ctx.merged_away
    affected    = get_affected_periods(absent_teacher_id, date_obj)
    result      = []

    for tt in affected:
        # Bug 1: skip merged-away class — no proxy needed
        if merged_away and tt.class_id in merged_away:
            continue

        # Secondary classes (above 5 A/B/C/D, e.g. 6, 7, 8) are outside
        # this primary-teacher proxy system — their own secondary staff
        # arrange coverage separately, so no proxy is generated here.
        try:
            cls_num = int(tt.class_.class_name) if tt.class_ else 0
        except Exception:
            cls_num = 0
        if cls_num > 5:
            continue

        existing = ProxyAssignment.query.filter_by(
            date=date_obj,
            period_no=tt.period_no,
            class_id=tt.class_id
        ).first()

        suggestions, needs_merge = generate_suggestions_for_period(tt, date_obj, ctx, mode=mode)

        result.append({
            'tt':          tt,
            'suggestions': suggestions,
            'needs_merge': needs_merge,
            'existing':    existing,
        })

    return result


# ── Confirm proxy (upsert) ─────────────────────────────────────────────────

def confirm_proxy(date_obj, period_no, class_id, original_teacher_id,
                  proxy_teacher_id, score, reasons):
    """Safe upsert — updates existing record if it exists."""
    reason_text = (' | '.join(reasons) if reasons else 'TT IC selection')[:990]

    existing = ProxyAssignment.query.filter_by(
        date=date_obj, period_no=period_no, class_id=class_id
    ).first()

    # Remember who (if anyone) was already assigned to this exact slot
    # BEFORE this upsert — needed below to avoid double-counting a slot
    # that's simply being re-confirmed (e.g. TT IC clicks "Confirm All"
    # again after adding one more period) instead of newly assigned.
    prev_proxy_teacher_id = existing.proxy_teacher_id if existing else None

    if existing:
        existing.proxy_teacher_id    = proxy_teacher_id
        existing.original_teacher_id = original_teacher_id
        existing.score               = score
        existing.status              = 'confirmed'
        existing.reason              = reason_text
        pa = existing
    else:
        pa = ProxyAssignment(
            date=date_obj,
            day=_day_from_date(date_obj),
            period_no=period_no,
            class_id=class_id,
            original_teacher_id=original_teacher_id,
            proxy_teacher_id=proxy_teacher_id,
            score=score,
            status='confirmed',
            reason=reason_text,
        )
        db.session.add(pa)

    db.session.flush()

    # Only touch the persisted totals when the teacher ASSIGNED to this
    # slot actually changed (a brand-new confirmation, or a reassignment
    # to a different teacher). Re-confirming the SAME slot with the SAME
    # teacher — which happens every time "Confirm All" is submitted again
    # later in the day — must NOT increment their total a second time.
    if proxy_teacher_id != prev_proxy_teacher_id:
        # Reverse the previous teacher's count if this slot is being
        # handed to someone else, so their total doesn't stay inflated.
        if prev_proxy_teacher_id:
            _reverse_load_summary(prev_proxy_teacher_id)

        # Don't inflate the teacher's persisted arrangement/proxy total
        # when this assignment used a period that was already freed by a
        # class merge (their own period, not extra duty) — school policy.
        from services.availability_service import is_merge_covered_assignment
        if not is_merge_covered_assignment(proxy_teacher_id, date_obj):
            _update_load_summary(proxy_teacher_id, date_obj)

    db.session.commit()
    return pa


def _update_load_summary(proxy_teacher_id, date_obj):
    if not proxy_teacher_id:
        return
    load = TeacherLoadSummary.query.filter_by(
        teacher_id=proxy_teacher_id).first()
    if load:
        load.total_proxy_count += 1
        load.last_proxy_date    = date_obj
    else:
        db.session.add(TeacherLoadSummary(
            teacher_id=proxy_teacher_id,
            total_proxy_count=1,
            last_proxy_date=date_obj,
        ))


def _reverse_load_summary(proxy_teacher_id):
    """Best-effort decrement when a slot is reassigned away from this
    teacher, so their total doesn't stay inflated by a duty they no
    longer have. Floors at 0 — never goes negative."""
    if not proxy_teacher_id:
        return
    load = TeacherLoadSummary.query.filter_by(
        teacher_id=proxy_teacher_id).first()
    if load and load.total_proxy_count > 0:
        load.total_proxy_count -= 1
