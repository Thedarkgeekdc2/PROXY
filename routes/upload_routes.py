import os
from werkzeug.utils import secure_filename
from flask import Blueprint, request, render_template, redirect, url_for, flash, current_app
from services.excel_service import upload_mastertt_excel
from database.models import db, Teacher, Class, Timetable, Absence, ProxyAssignment, ClassDayMerge, TeacherLoadSummary

upload_bp = Blueprint('upload', __name__)

ALLOWED = {'xlsx', 'xls'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED


@upload_bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        # ✅ ANDROID SAFE PATH
        folder = os.environ.get("UPLOAD_FOLDER") or current_app.config.get('UPLOAD_FOLDER')

        f = request.files.get('mastertt_file')
        if not f or not f.filename or not allowed_file(f.filename):
            flash('No valid file selected (.xlsx only)', 'error')
            return redirect(url_for('upload.upload'))

        # ✅ SAFE FILE NAME
        filename = secure_filename(f.filename)
        path = os.path.join(folder, filename)

        # Save file
        f.save(path)

        try:
            # 🔥 PROCESS EXCEL
            teacher_count, tt_count, errors = upload_mastertt_excel(path)

            for e in errors:
                flash(e, 'warning')

            flash(
                f'✅ Master TT imported: {teacher_count} teacher codes, {tt_count} timetable entries.',
                'success'
            )

        except Exception as e:
            flash(f'❌ Error while processing file: {str(e)}', 'error')

        finally:
            # 🔥 TEMP FILE DELETE (VERY IMPORTANT)
            try:
                os.remove(path)
            except:
                pass

        return redirect(url_for('upload.upload'))

    teachers    = Teacher.query.order_by(Teacher.name).all()
    tt_count    = Timetable.query.count()
    class_count = Class.query.count()
    return render_template('upload.html',
                           teachers=teachers,
                           tt_count=tt_count,
                           class_count=class_count)


@upload_bp.route('/teachers/toggle_block/<int:tid>', methods=['POST'])
def toggle_block(tid):
    t = Teacher.query.get_or_404(tid)
    t.is_blocked = not t.is_blocked
    db.session.commit()
    flash(f'{"🔒 Blocked" if t.is_blocked else "✅ Unblocked"}: {t.display_name}', 'info')
    return redirect(url_for('upload.upload'))


@upload_bp.route('/reset_timetable', methods=['POST'])
def reset_timetable():
    # Delete in dependency order
    ProxyAssignment.query.delete()
    ClassDayMerge.query.delete()
    Absence.query.delete()
    TeacherLoadSummary.query.delete()
    Timetable.query.delete()
    Class.query.delete()
    Teacher.query.delete()
    db.session.commit()
    flash('🗑️ All data cleared. Upload a fresh Master_TT.xlsx to begin.', 'warning')
    return redirect(url_for('upload.upload'))


# ── Timetable View / Edit API ──────────────────────────────────────────────

@upload_bp.route('/api/timetable/<int:teacher_id>')
def api_get_timetable(teacher_id):
    from flask import jsonify
    teacher = Teacher.query.get_or_404(teacher_id)
    days = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    
    grid = {}
    for d in days:
        grid[d] = {}
        for p in range(1, 9):
            grid[d][p] = {'class_label': '0', 'subject': '0', 'tt_id': None, 'class_id': None}
    
    tts = Timetable.query.filter_by(teacher_id=teacher_id).all()
    for tt in tts:
        d = tt.day
        p = tt.period_no
        if d in grid and 1 <= p <= 8:
            grid[d][p] = {
                'class_label': tt.class_.label if tt.class_ else '0',
                'subject':     tt.subject or '0',
                'tt_id':       tt.id,
                'class_id':    tt.class_id,
            }
    
    return jsonify({
        'teacher_id':   teacher.id,
        'teacher_code': teacher.name,
        'teacher_name': teacher.display_name,
        'days':         days,
        'grid':         grid,
    })


@upload_bp.route('/api/timetable/update', methods=['POST'])
def api_update_timetable():
    from flask import jsonify, request as req
    data        = req.get_json(force=True)
    teacher_id  = data.get('teacher_id')
    day         = data.get('day')
    period_no   = data.get('period_no')
    class_label = (data.get('class_label') or '').strip()
    subject     = (data.get('subject') or '').strip()

    if not all([teacher_id, day, period_no]):
        return jsonify({'success': False, 'error': 'Missing params'}), 400

    if not class_label or class_label == '0':
        Timetable.query.filter_by(
            teacher_id=teacher_id, day=day, period_no=period_no
        ).delete()
        db.session.commit()
        return jsonify({'success': True, 'action': 'cleared'})

    cls = None
    import re
    m = re.match(r'^([IVX]+)([A-Z]+)$', class_label.upper())
    if m:
        cls_name = m.group(1)
        section  = m.group(2)
        roman_map = {'I':'1','II':'2','III':'3','IV':'4','V':'5'}
        cls_num  = roman_map.get(cls_name, cls_name)
        cls = Class.query.filter_by(class_name=cls_num, section=section).first()
        if not cls:
            cls = Class(class_name=cls_num, section=section)
            db.session.add(cls)
            db.session.flush()
    else:
        m2 = re.match(r'^(\d+)([A-Z]+)$', class_label.upper())
        if m2:
            cls = Class.query.filter_by(class_name=m2.group(1), section=m2.group(2)).first()
            if not cls:
                cls = Class(class_name=m2.group(1), section=m2.group(2))
                db.session.add(cls)
                db.session.flush()

    if not cls:
        return jsonify({'success': False, 'error': f'Class "{class_label}" not recognised'}), 400

    existing = Timetable.query.filter_by(
        day=day, period_no=period_no, class_id=cls.id
    ).first()

    if existing:
        existing.teacher_id = teacher_id
        existing.subject    = subject or None
    else:
        Timetable.query.filter_by(
            teacher_id=teacher_id, day=day, period_no=period_no
        ).delete()
        tt = Timetable(
            day=day, period_no=period_no,
            class_id=cls.id, teacher_id=teacher_id,
            subject=subject or None,
        )
        db.session.add(tt)

    db.session.commit()
    return jsonify({'success': True, 'action': 'saved',
                    'class_label': cls.label, 'subject': subject})


@upload_bp.route('/teacher_timetable/<int:tid>')
def teacher_timetable(tid):
    from database.models import Teacher, Timetable, Class
    teacher = Teacher.query.get_or_404(tid)
    days    = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']
    classes = Class.query.order_by(Class.class_name, Class.section).all()

    # Build day → {period: timetable_entry} map
    tt_map = {}
    for day in days:
        tt_map[day] = {}
        for tt in Timetable.query.filter_by(teacher_id=tid, day=day).all():
            tt_map[day][tt.period_no] = tt

    return render_template('teacher_tt_edit.html',
                           teacher=teacher, days=days,
                           classes=classes, tt_map=tt_map)


@upload_bp.route('/teacher_timetable/<int:tid>/update', methods=['POST'])
def update_teacher_timetable(tid):
    from database.models import Teacher, Timetable, Class
    teacher = Teacher.query.get_or_404(tid)
    days    = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

    for day in days:
        for pno in range(1, 9):
            key      = f"{day}_{pno}"
            class_id = request.form.get(key, '').strip()
            existing = Timetable.query.filter_by(
                teacher_id=tid, day=day, period_no=pno).first()

            if class_id:
                class_id = int(class_id)
                if existing:
                    existing.class_id = class_id
                else:
                    db.session.add(Timetable(
                        teacher_id=tid, day=day,
                        period_no=pno, class_id=class_id))
            else:
                if existing:
                    db.session.delete(existing)

    db.session.commit()
    flash(f'{teacher.display_name}\'s timetable updated.', 'success')
    return redirect(url_for('upload.teacher_timetable', tid=tid))
