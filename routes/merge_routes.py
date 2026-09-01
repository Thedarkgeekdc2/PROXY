from datetime import datetime, date
from flask import Blueprint, request, render_template, redirect, url_for, flash, jsonify
from database.models import db, Teacher, Class, Timetable, ClassDayMerge

merge_bp = Blueprint('merge', __name__)


def _parse_date(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def _get_freed_teachers(going_class_id, date_obj):
    """Teachers of going_class who become free — excludes already-absent teachers."""
    from database.models import Absence
    day = date_obj.strftime('%A')
    tts = Timetable.query.filter_by(class_id=going_class_id, day=day).all()
    absent_ids = {a.teacher_id for a in Absence.query.filter_by(absent_date=date_obj).all()}
    seen = {}
    for tt in tts:
        if not tt.teacher:
            continue
        if tt.teacher_id in absent_ids:
            continue   # Bug fix: skip already-absent teachers
        if tt.teacher_id not in seen:
            seen[tt.teacher_id] = {'teacher': tt.teacher, 'periods': []}
        seen[tt.teacher_id]['periods'].append(tt.period_no)
    return list(seen.values())


@merge_bp.route('/merge')
def merge_view():
    date_str = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    try:
        date_obj = _parse_date(date_str)
    except Exception:
        date_obj = date.today()
        date_str = date_obj.strftime('%Y-%m-%d')

    all_classes    = Class.query.order_by(Class.class_name, Class.section).all()
    existing_merges = ClassDayMerge.query.filter_by(date=date_obj).all()

    return render_template('merge_view.html',
                           page='merge',
                           date_obj=date_obj,
                           date_str=date_str,
                           all_classes=all_classes,
                           existing_merges=existing_merges)


@merge_bp.route('/api/merge/freed_teachers')
def api_freed_teachers():
    """Return teachers freed if going_class is merged on given date."""
    going_class_id = request.args.get('going_class_id', type=int)
    date_str       = request.args.get('date', date.today().strftime('%Y-%m-%d'))
    try:
        date_obj = _parse_date(date_str)
    except Exception:
        date_obj = date.today()

    if not going_class_id:
        return jsonify([])

    freed = _get_freed_teachers(going_class_id, date_obj)
    return jsonify([{
        'name':    f['teacher'].display_name,
        'periods': sorted(f['periods']),
    } for f in freed])


@merge_bp.route('/merge/add', methods=['POST'])
def add_merge():
    date_str       = request.form.get('date', date.today().strftime('%Y-%m-%d'))
    going_class_id = request.form.get('going_class_id', type=int)
    host_class_id  = request.form.get('host_class_id',  type=int)
    note           = request.form.get('note', '').strip()

    if not going_class_id or not host_class_id:
        flash('Please select both classes.', 'error')
        return redirect(url_for('merge.merge_view') + f'?date={date_str}')

    if going_class_id == host_class_id:
        flash('Going class and host class cannot be the same.', 'error')
        return redirect(url_for('merge.merge_view') + f'?date={date_str}')

    try:
        date_obj = _parse_date(date_str)
    except Exception:
        flash('Invalid date.', 'error')
        return redirect(url_for('merge.merge_view'))

    existing = ClassDayMerge.query.filter_by(
        date=date_obj, going_class_id=going_class_id
    ).first()

    if existing:
        # Update host
        existing.host_class_id = host_class_id
        existing.note          = note
    else:
        m = ClassDayMerge(
            date=date_obj,
            going_class_id=going_class_id,
            host_class_id=host_class_id,
            note=note,
        )
        db.session.add(m)

    db.session.commit()

    going = Class.query.get(going_class_id)
    host  = Class.query.get(host_class_id)
    flash(f'✅ {going.label} → {host.label} merge added for {date_obj.strftime("%d %b %Y")}.', 'success')
    return redirect(url_for('merge.merge_view') + f'?date={date_str}')


@merge_bp.route('/merge/remove/<int:merge_id>', methods=['POST'])
def remove_merge(merge_id):
    m = ClassDayMerge.query.get_or_404(merge_id)
    date_str = m.date.strftime('%Y-%m-%d')
    db.session.delete(m)
    db.session.commit()
    flash('Merge removed.', 'info')
    return redirect(url_for('merge.merge_view') + f'?date={date_str}')


@merge_bp.route('/merge/history')
def merge_history():
    merges = ClassDayMerge.query.order_by(ClassDayMerge.date.desc()).limit(100).all()
    return render_template('merge_view.html', merges=merges, page='history')
