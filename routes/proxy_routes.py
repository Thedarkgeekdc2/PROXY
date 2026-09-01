from datetime import date, datetime, timedelta
from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify
from database.models import (db, Teacher, Class, Timetable, Absence,
                              ProxyAssignment, ClassDayMerge)
from services.proxy_engine import generate_all_suggestions, confirm_proxy, build_context
from sqlalchemy import func

proxy_bp = Blueprint('proxy', __name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _today(): return date.today()

def _parse_date(s): return datetime.strptime(s, '%Y-%m-%d').date()


def _parse_absence_reason(reason):
    """
    Parse an encoded absence reason string into (mode, partial_periods, display_reason).

    Format: [MODE:auto|manual][P:1,2,3] Actual reason text
    Both tags are optional and order-independent at the start of the string.
    Missing MODE defaults to 'auto' (backward compatible with older absences).
    Missing P means "all periods need a proxy" (partial_periods=None).
    """
    import re
    mode    = 'auto'
    partial = None
    text    = reason or ''

    m = re.match(r'^\[MODE:(auto|manual)\](.*)$', text, re.DOTALL)
    if m:
        mode = m.group(1)
        text = m.group(2)

    m2 = re.match(r'^\[P:([\d,]+)\](.*)$', text, re.DOTALL)
    if m2:
        try:
            partial = {int(x) for x in m2.group(1).split(',') if x.strip().isdigit()}
        except Exception:
            partial = None
        text = m2.group(2)

    return mode, partial, text.strip()


def _build_absent_rows(absences, date_obj):
    if not absences:
        return []

    # Build ONE ScoringContext — reused across ALL absent teachers
    # This is the key performance fix: ~15 DB queries total instead of 8000+
    from services.proxy_engine import build_context, generate_all_suggestions
    ctx = build_context(date_obj)

    rows = []
    for ab in absences:
        mode, allowed, display_reason = _parse_absence_reason(ab.reason)
        periods_data = generate_all_suggestions(ab.teacher_id, date_obj, ctx=ctx, mode=mode)
        by_period    = {i: None for i in range(1, 9)}
        for item in periods_data:
            pno = item['tt'].period_no
            if allowed is None or pno in allowed:
                by_period[pno] = item
        rows.append({
            'absence':         ab,
            'teacher':         ab.teacher,
            'by_period':       by_period,
            'partial_periods': allowed,
            'proxy_mode':      mode,
            'display_reason':  display_reason,
        })
    return rows


def _bulk_proxy_counts(date_obj):
    """Returns (today_counts, weekly_counts, monthly_counts) as dicts
    {teacher_id: count}. Merge-freed periods are a daily budget per
    teacher (not tied to a specific period) — see availability_service.

    Week/month bounds match the Reports page exactly (Mon–Sat week,
    full calendar month) so the "Week"/"Month" pills here never
    disagree with the Weekly/Monthly report tabs."""
    from services.availability_service import get_merge_excluded_counts
    import calendar as _calendar

    week_start  = date_obj - timedelta(days=date_obj.weekday())
    # KVS: Mon–Sat week
    week_end    = week_start + timedelta(days=5)
    month_start = date_obj.replace(day=1)
    _, last_day = _calendar.monthrange(date_obj.year, date_obj.month)
    month_end   = date_obj.replace(day=last_day)

    def _count(start, end):
        rows = ProxyAssignment.query.filter(
            ProxyAssignment.date >= start,
            ProxyAssignment.date <= end,
            ProxyAssignment.status == 'confirmed',
            ProxyAssignment.proxy_teacher_id.isnot(None)
        ).all()
        counts = {}
        for r in rows:
            counts[r.proxy_teacher_id] = counts.get(r.proxy_teacher_id, 0) + 1
        excluded = get_merge_excluded_counts(rows)   # {(tid,date): n}
        for (tid, _d), n in excluded.items():
            if tid in counts:
                counts[tid] = max(0, counts[tid] - n)
        return counts

    return (
        _count(date_obj, date_obj),
        _count(week_start, week_end),
        _count(month_start, month_end),
    )


def _live_all_time_counts():
    """All-time confirmed proxy count per teacher, with each teacher's
    merge-freed daily budget excluded — computed FRESH from
    ProxyAssignment every time (same method as Week/Month above), so it
    can never drift out of sync with them the way a persisted running
    counter can (double-confirms, reassignments, cancelled absences,
    edits — none of that can leave this stale, since nothing is stored;
    it's recomputed from the actual rows each time)."""
    from services.availability_service import get_merge_excluded_counts

    rows = ProxyAssignment.query.filter(
        ProxyAssignment.status == 'confirmed',
        ProxyAssignment.proxy_teacher_id.isnot(None)
    ).all()
    counts = {}
    for r in rows:
        counts[r.proxy_teacher_id] = counts.get(r.proxy_teacher_id, 0) + 1
    excluded = get_merge_excluded_counts(rows)
    for (tid, _d), n in excluded.items():
        if tid in counts:
            counts[tid] = max(0, counts[tid] - n)
    return counts


def _teacher_load_list(date_obj=None):
    if date_obj is None:
        date_obj = _today()

    from services.scoring_engine import (get_teacher_free_periods,
                                         get_effective_daily_limit)
    from services.availability_service import get_or_create_backup_teachers

    today_counts, weekly_counts, monthly_counts = _bulk_proxy_counts(date_obj)
    total_counts = _live_all_time_counts()   # fixes: Total used to drift
    teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()
    backups  = get_or_create_backup_teachers()
    day_str  = date_obj.strftime('%A')

    def _row(t):
        load       = t.load_summary
        free_today = get_teacher_free_periods(t.id, day_str, date_obj)
        today_done = today_counts.get(t.id, 0)

        # Effective daily limit (flexible, merge-aware)
        eff_max, _, remaining = get_effective_daily_limit(t, day_str, date_obj)

        return {
            'teacher':   t,
            'today':     today_done,
            'weekly':    weekly_counts.get(t.id, 0),   # live — never stale
            'monthly':   monthly_counts.get(t.id, 0),  # live — never stale
            'total':     total_counts.get(t.id, 0),    # live — never stale
            'last':      load.last_proxy_date if load else None,
            'free_today': free_today,
            'eff_max':    eff_max,
            'remaining':  remaining,
        }

    result = [_row(t) for t in teachers]
    result += [_row(t) for t in backups]   # BV1/BV2 — always last
    return result


# ── API: free teacher count per period (Improvement 2) ────────────────────

@proxy_bp.route('/api/period_free_counts')
def api_period_free_counts():
    """For each period P1-P8, return count and names of free teachers."""
    date_str = request.args.get('date', _today().strftime('%Y-%m-%d'))
    try:
        date_obj = _parse_date(date_str)
    except Exception:
        date_obj = _today()

    day = date_obj.strftime('%A')

    # Build ScoringContext for fast bulk lookups (no per-period queries)
    from services.proxy_engine import build_context
    ctx = build_context(date_obj)

    all_teachers = Teacher.query.filter_by(status='active').all()

    result = {}
    for pno in range(1, 9):
        # Build unavailable set using pre-loaded context data
        busy_ids = {
            tid for tid, periods in ctx.teacher_periods.items()
            if pno in periods and periods[pno] not in ctx.merged_away
        }
        already = {
            tid for (tid, p) in ctx.proxy_in_period if p == pno
        }
        unavail = busy_ids | ctx.absent_ids | ctx.blocked_ids | already

        free = [t for t in all_teachers if t.id not in unavail]
        names = [t.display_name for t in free]
        count = len(free)

        # Backup pre-primary teachers — always available for P6/P7/P8 by
        # default. They're not in the master timetable (so they never show
        # up via the normal free-teacher query above); kept as a fallback
        # for when no one else is free, and must still appear in this data.
        if pno in (6, 7, 8):
            from services.availability_service import get_or_create_backup_teachers
            backups = get_or_create_backup_teachers()
            names = names + [b.display_name for b in backups]
            count = count + len(backups)

        result[pno] = {
            'count': count,
            'names': names,
        }

    return jsonify(result)


# ── API: free periods for a specific teacher today (Improvement 4) ─────────

@proxy_bp.route('/api/teacher_free_periods')
def api_teacher_free_periods():
    """Return which periods (P1-P8) a teacher is free today."""
    teacher_id = request.args.get('teacher_id', type=int)
    date_str   = request.args.get('date', _today().strftime('%Y-%m-%d'))
    try:
        date_obj = _parse_date(date_str)
    except Exception:
        date_obj = _today()

    if not teacher_id:
        return jsonify({'free_periods': [], 'busy_periods': []})

    day = date_obj.strftime('%A')
    from services.availability_service import get_merged_away_class_ids

    merged_away   = get_merged_away_class_ids(date_obj)
    scheduled_tts = Timetable.query.filter_by(teacher_id=teacher_id, day=day).all()

    # Already proxy-assigned periods
    proxy_periods = {p.period_no for p in
                     ProxyAssignment.query.filter_by(
                         date=date_obj, proxy_teacher_id=teacher_id,
                         status='confirmed').all()}

    busy_periods  = []
    freed_periods = []
    free_periods  = []

    for tt in scheduled_tts:
        if tt.class_id in merged_away:
            freed_periods.append({'period': tt.period_no,
                                  'class':  tt.class_.label if tt.class_ else '?',
                                  'type':   'merge_freed'})
        else:
            busy_periods.append({'period': tt.period_no,
                                 'class':  tt.class_.label if tt.class_ else '?',
                                 'type':   'own_class'})

    scheduled_pnos = {tt.period_no for tt in scheduled_tts}
    for p in range(1, 9):
        if p not in scheduled_pnos:
            p_type = 'proxy' if p in proxy_periods else 'free'
            free_periods.append({'period': p, 'type': p_type})

    return jsonify({
        'free_periods':  free_periods,
        'busy_periods':  busy_periods,
        'freed_periods': freed_periods,
    })


# ── Main proxy page ────────────────────────────────────────────────────────

@proxy_bp.route('/proxy', methods=['GET', 'POST'])
def proxy_home():
    today_str = _today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        teacher_id  = request.form.get('teacher_id', type=int)
        absent_date = request.form.get('absent_date', today_str)
        reason      = request.form.get('reason', '').strip()
        proxy_mode  = request.form.get('proxy_mode', 'auto')
        if proxy_mode not in ('auto', 'manual'):
            proxy_mode = 'auto'
        if not teacher_id:
            flash('Please select a teacher.', 'error')
            return redirect(url_for('proxy.proxy_home'))
        try:
            date_obj = _parse_date(absent_date)
        except ValueError:
            flash('Invalid date.', 'error')
            return redirect(url_for('proxy.proxy_home'))

        # Encode mode (+ optional partial-day periods) as a prefix on reason
        tag = f'[MODE:{proxy_mode}]'
        partial_raw = request.form.get('partial_periods', '').strip()
        if partial_raw:
            nums = sorted(set(int(x) for x in partial_raw.split(',')
                              if x.strip().isdigit()))
            tag += '[P:' + ','.join(str(n) for n in nums) + ']'
        reason = tag + (' ' + reason if reason else '')

        existing = Absence.query.filter_by(
            teacher_id=teacher_id, absent_date=date_obj).first()
        if existing:
            flash('Already marked absent.', 'warning')
        else:
            db.session.add(Absence(teacher_id=teacher_id,
                                   absent_date=date_obj, reason=reason))
            db.session.commit()
            mode_label = 'Auto' if proxy_mode == 'auto' else 'Manual'
            flash(f'Marked absent ({mode_label} proxy). Suggestions loaded below.', 'success')
        return redirect(url_for('proxy.proxy_home') + f'?date={absent_date}')

    # GET
    date_str = request.args.get('date', today_str)
    try:
        date_obj = _parse_date(date_str)
    except Exception:
        date_obj = _today()
        date_str = today_str

    teachers       = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()
    absences_today = Absence.query.filter_by(absent_date=date_obj).all()
    absent_rows    = _build_absent_rows(absences_today, date_obj)
    teacher_loads  = _teacher_load_list(date_obj)
    day_merges     = ClassDayMerge.query.filter_by(date=date_obj).all()
    all_classes    = Class.query.order_by(Class.class_name, Class.section).all()

    return render_template('proxy_view.html',
                           teachers=teachers,
                           date_obj=date_obj,
                           date_str=date_str,
                           absent_rows=absent_rows,
                           teacher_loads=teacher_loads,
                           day_merges=day_merges,
                           all_classes=all_classes,
                           page='main')


# ── Confirm all selected slots ─────────────────────────────────────────────

@proxy_bp.route('/proxy/confirm_all', methods=['POST'])
def confirm_all():
    absent_date         = request.form.get('absent_date')
    original_teacher_id = request.form.get('original_teacher_id', type=int)
    try:
        date_obj = _parse_date(absent_date)
    except Exception:
        return redirect(url_for('proxy.proxy_home'))

    confirmed = skipped = 0
    for key, value in request.form.items():
        if not key.startswith('slot_'):
            continue
        parts = key.split('_')
        if len(parts) != 3:
            continue
        try:
            period_no        = int(parts[1])
            class_id         = int(parts[2])
            proxy_teacher_id = int(value) if value else 0
        except Exception:
            continue

        if not proxy_teacher_id:
            skipped += 1
            continue

        # No skip for already-confirmed — confirm_proxy does safe upsert
        score       = request.form.get(f'score_{period_no}_{class_id}',
                                       type=float, default=0)
        reasons_raw = request.form.get(f'reasons_{period_no}_{class_id}', '')
        reasons     = [r.strip() for r in reasons_raw.split('|') if r.strip()]
        confirm_proxy(date_obj, period_no, class_id, original_teacher_id,
                      proxy_teacher_id, score, reasons)
        confirmed += 1

    if confirmed:
        flash(f'✅ {confirmed} period(s) confirmed.', 'success')
    if skipped:
        flash(f'{skipped} period(s) had no selection.', 'info')
    return redirect(url_for('proxy.proxy_home') + f'?date={absent_date}')


# ── Cancel absence ─────────────────────────────────────────────────────────

@proxy_bp.route('/proxy/cancel_absence/<int:absence_id>', methods=['POST'])
def cancel_absence(absence_id):
    from services.proxy_engine import _reverse_load_summary

    absence   = Absence.query.get_or_404(absence_id)
    date_back = absence.absent_date.strftime('%Y-%m-%d')

    # Delete ALL proxy records (not just pending) — Bug B2 fix.
    # Reverse each deleted proxy's persisted total too — otherwise a
    # cancelled absence leaves the proxy teacher's Total permanently
    # inflated for duty they no longer actually did.
    to_delete = ProxyAssignment.query.filter_by(
        date=absence.absent_date,
        original_teacher_id=absence.teacher_id).all()
    for pa in to_delete:
        if pa.status == 'confirmed' and pa.proxy_teacher_id:
            _reverse_load_summary(pa.proxy_teacher_id)
        db.session.delete(pa)

    db.session.delete(absence)
    db.session.commit()
    flash('Absence cancelled.', 'info')
    return redirect(url_for('proxy.proxy_home') + f'?date={date_back}')


# ── Delete a whole month's data (proxy + merge + absence) ──────────────────

@proxy_bp.route('/proxy/delete_month/<int:year>/<int:month>', methods=['POST'])
def delete_month(year, month):
    """Permanently deletes EVERYTHING tied to the given calendar month:
    all ProxyAssignment records (any status), all ClassDayMerge records,
    and all Absence records. For every confirmed proxy row that had a
    proxy teacher, their persisted Total is reversed first, so no one's
    load stays inflated after the wipe. Timetable (the master weekly
    schedule) is untouched — it isn't date-specific data."""
    import calendar as _calendar
    from services.proxy_engine import _reverse_load_summary

    try:
        _, last_day = _calendar.monthrange(year, month)
        m_start = date(year, month, 1)
        m_end   = date(year, month, last_day)
    except Exception:
        flash('Invalid month.', 'error')
        return redirect(url_for('dashboard.reports') + '?tab=monthly')

    proxy_rows = ProxyAssignment.query.filter(
        ProxyAssignment.date >= m_start,
        ProxyAssignment.date <= m_end
    ).all()
    deleted_proxies = 0
    for pa in proxy_rows:
        if pa.status == 'confirmed' and pa.proxy_teacher_id:
            _reverse_load_summary(pa.proxy_teacher_id)
        db.session.delete(pa)
        deleted_proxies += 1

    merge_rows = ClassDayMerge.query.filter(
        ClassDayMerge.date >= m_start,
        ClassDayMerge.date <= m_end
    ).all()
    deleted_merges = len(merge_rows)
    for m in merge_rows:
        db.session.delete(m)

    absence_rows = Absence.query.filter(
        Absence.absent_date >= m_start,
        Absence.absent_date <= m_end
    ).all()
    deleted_absences = len(absence_rows)
    for a in absence_rows:
        db.session.delete(a)

    db.session.commit()

    total_deleted = deleted_proxies + deleted_merges + deleted_absences
    if total_deleted:
        flash(f'🗑️ Deleted {deleted_proxies} proxy record(s), '
              f'{deleted_merges} merge(s) and {deleted_absences} absence(s) '
              f'for {m_start.strftime("%B %Y")}.', 'info')
    else:
        flash(f'No data found for {m_start.strftime("%B %Y")}.', 'info')

    return redirect(url_for('dashboard.reports') +
                     f'?tab=monthly&date={m_start.strftime("%Y-%m-%d")}')

