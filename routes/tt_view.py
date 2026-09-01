"""
View Time Table — read-only timetable viewer.

Four sections, all sourced from the same master `Timetable` table so every
view always agrees with the others:

  1. Class-wise Full Week        — one class, Mon–Sat, P1–P8 (subject + teacher)
  2. Period-wise Per Day         — one date, P1–P8, all occupied teachers + class
     2.1 All Teachers Free Period on that same date, P1–P8
  3. Teacher-wise Full Week      — one teacher, Mon–Sat, P1–P8 (class + subject)
  4. Free Period — Full Week     — one teacher, Free/Busy matrix + totals

This module is STRICTLY VIEW ONLY. It never creates, updates, or deletes
anything — no teacher/class/subject/period editing happens here.
"""
from datetime import datetime, date
from flask import Blueprint, render_template, request, jsonify
from database.models import Class, Teacher, Timetable

tt_view_bp = Blueprint('tt_view', __name__)

# School's working days only (KVS: Mon–Sat, 6-day week)
WORKING_DAYS   = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
PERIODS        = list(range(1, 9))                    # P1 .. P8
SLOTS_PER_WEEK = len(WORKING_DAYS) * len(PERIODS)      # 48


def _parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return date.today()


# ── Page ─────────────────────────────────────────────────────────────────
@tt_view_bp.route('/timetable')
@tt_view_bp.route('/view-timetable')
def view_timetable():
    all_classes  = Class.query.order_by(Class.class_name, Class.section).all()
    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()
    return render_template(
        'tt_view.html',
        all_classes=all_classes,
        all_teachers=all_teachers,
        working_days=WORKING_DAYS,
        today_str=date.today().strftime('%Y-%m-%d'),
    )


# ── 1. Class-wise Full Week ─────────────────────────────────────────────

@tt_view_bp.route('/api/tt_view/class_week')
def api_class_week():
    class_id = request.args.get('class_id', type=int)
    if not class_id:
        return jsonify({'error': 'Please select a class.'})

    cls = Class.query.get(class_id)
    if not cls:
        return jsonify({'error': 'Class not found.'})

    rows = Timetable.query.filter_by(class_id=class_id).all()

    grid = {d: {p: None for p in PERIODS} for d in WORKING_DAYS}
    for tt in rows:
        if tt.day in grid and tt.period_no in grid[tt.day]:
            grid[tt.day][tt.period_no] = {
                'subject': tt.subject or '—',
                'teacher': tt.teacher.display_name if tt.teacher else '—',
            }

    return jsonify({
        'class_label': cls.label,
        'days':        WORKING_DAYS,
        'periods':     PERIODS,
        'grid':        grid,
        'has_data':    bool(rows),
    })


# ── 2. Period-wise Per Day  (+ 2.1 All Teachers Free Period) ───────────

@tt_view_bp.route('/api/tt_view/period_day')
def api_period_day():
    date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    date_obj = _parse_date(date_str)
    day      = date_obj.strftime('%A')

    if day not in WORKING_DAYS:
        return jsonify({
            'day': day, 'date': date_str, 'is_working_day': False,
            'periods': {}, 'has_data': False, 'total_teachers': 0,
        })

    rows         = Timetable.query.filter_by(day=day).all()
    all_teachers = Teacher.query.filter_by(status='active').order_by(Teacher.name).all()

    periods = {}
    for p in PERIODS:
        occupied_rows = [r for r in rows if r.period_no == p]
        busy_ids = {r.teacher_id for r in occupied_rows}
        occupied = [{
            'teacher': r.teacher.display_name if r.teacher else '—',
            'class':   r.class_.label if r.class_ else '—',
        } for r in occupied_rows]
        free = [t.display_name for t in all_teachers if t.id not in busy_ids]
        periods[p] = {'occupied': occupied, 'free': free}

    return jsonify({
        'day': day, 'date': date_str, 'is_working_day': True,
        'periods':        periods,
        'total_teachers': len(all_teachers),
        'has_data':       bool(rows),
    })


# ── 3. Teacher-wise Full Week ───────────────────────────────────────────

@tt_view_bp.route('/api/tt_view/teacher_week')
def api_teacher_week():
    teacher_id = request.args.get('teacher_id', type=int)
    if not teacher_id:
        return jsonify({'error': 'Please select a teacher.'})

    teacher = Teacher.query.get(teacher_id)
    if not teacher:
        return jsonify({'error': 'Teacher not found.'})

    rows = Timetable.query.filter_by(teacher_id=teacher_id).all()

    grid = {d: {p: None for p in PERIODS} for d in WORKING_DAYS}
    for tt in rows:
        if tt.day in grid and tt.period_no in grid[tt.day]:
            grid[tt.day][tt.period_no] = {
                'class_label': tt.class_.label if tt.class_ else '—',
                'subject':     tt.subject or '—',
            }

    return jsonify({
        'teacher_name': teacher.display_name,
        'teacher_code': teacher.name,
        'days':         WORKING_DAYS,
        'periods':      PERIODS,
        'grid':         grid,
        'has_data':     bool(rows),
    })


# ── 4. Free Period — Full Week Teacher-wise ─────────────────────────────

@tt_view_bp.route('/api/tt_view/teacher_free_week')
def api_teacher_free_week():
    teacher_id = request.args.get('teacher_id', type=int)
    if not teacher_id:
        return jsonify({'error': 'Please select a teacher.'})

    teacher = Teacher.query.get(teacher_id)
    if not teacher:
        return jsonify({'error': 'Teacher not found.'})

    rows = Timetable.query.filter_by(teacher_id=teacher_id).all()
    # A teacher is Free only when no class/period assignment exists for
    # that teacher in that period — so "busy" is simply every (day, period)
    # slot that has a Timetable row for this teacher.
    busy = {(tt.day, tt.period_no) for tt in rows}

    grid = {
        d: {p: ('busy' if (d, p) in busy else 'free') for p in PERIODS}
        for d in WORKING_DAYS
    }

    total_periods = len(busy)
    free_periods  = SLOTS_PER_WEEK - total_periods

    return jsonify({
        'teacher_name':   teacher.display_name,
        'teacher_code':   teacher.name,
        'days':           WORKING_DAYS,
        'periods':        PERIODS,
        'grid':           grid,
        'total_periods':  total_periods,
        'free_periods':   free_periods,
        'has_data':       bool(rows),
    })
