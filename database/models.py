from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Teacher(db.Model):
    __tablename__ = 'teacher'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(100), unique=True, nullable=False)   # short code e.g. 'MS'
    full_name       = db.Column(db.String(150))                                # editable later
    subject         = db.Column(db.String(50))
    level           = db.Column(db.String(20), default='both')                 # lower_primary | upper_primary | both
    status          = db.Column(db.String(20), default='active')               # active | inactive
    max_daily_proxy = db.Column(db.Integer, default=2)
    max_weekly_proxy= db.Column(db.Integer, default=8)
    is_blocked      = db.Column(db.Boolean, default=False)

    absences        = db.relationship('Absence', backref='teacher', lazy='dynamic')
    load_summary    = db.relationship('TeacherLoadSummary', backref='teacher', uselist=False)

    @property
    def display_name(self):
        return self.full_name or self.name

    def __repr__(self):
        return f'<Teacher {self.name}>'


class Class(db.Model):
    __tablename__ = 'class'
    id          = db.Column(db.Integer, primary_key=True)
    class_name  = db.Column(db.String(10), nullable=False)   # '1'..'5'
    section     = db.Column(db.String(5),  nullable=False)   # 'A','B','C','D'

    @property
    def label(self):
        return f"{self.class_name}{self.section}"

    @property
    def level(self):
        try:
            n = int(self.class_name)
            return 'lower_primary' if n <= 2 else 'upper_primary'
        except Exception:
            return 'both'

    def __repr__(self):
        return f'<Class {self.label}>'


class Timetable(db.Model):
    __tablename__ = 'timetable'
    id          = db.Column(db.Integer, primary_key=True)
    day         = db.Column(db.String(10), nullable=False)
    period_no   = db.Column(db.Integer,   nullable=False)
    class_id    = db.Column(db.Integer, db.ForeignKey('class.id'),   nullable=False)
    teacher_id  = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    subject     = db.Column(db.String(50))

    class_   = db.relationship('Class',   foreign_keys=[class_id],   backref='timetables')
    teacher  = db.relationship('Teacher', foreign_keys=[teacher_id],  backref='timetables')

    __table_args__ = (
        db.UniqueConstraint('day', 'period_no', 'class_id', name='uq_timetable_slot'),
    )


class Absence(db.Model):
    __tablename__ = 'absence'
    id          = db.Column(db.Integer, primary_key=True)
    teacher_id  = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    absent_date = db.Column(db.Date, nullable=False)
    reason      = db.Column(db.String(200))

    __table_args__ = (
        db.UniqueConstraint('teacher_id', 'absent_date', name='uq_absence'),
    )


class ProxyAssignment(db.Model):
    __tablename__ = 'proxy_assignment'
    id                  = db.Column(db.Integer, primary_key=True)
    date                = db.Column(db.Date,    nullable=False)
    day                 = db.Column(db.String(10), nullable=False)
    period_no           = db.Column(db.Integer, nullable=False)
    class_id            = db.Column(db.Integer, db.ForeignKey('class.id'),   nullable=False)
    original_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    proxy_teacher_id    = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=True)
    score               = db.Column(db.Float,   default=0)
    status              = db.Column(db.String(20), default='confirmed')  # always 'confirmed' once saved
    reason              = db.Column(db.String(1000))

    class_           = db.relationship('Class',   foreign_keys=[class_id])
    original_teacher = db.relationship('Teacher', foreign_keys=[original_teacher_id])
    proxy_teacher    = db.relationship('Teacher', foreign_keys=[proxy_teacher_id])

    __table_args__ = (
        db.UniqueConstraint('date', 'period_no', 'class_id', name='uq_proxy_slot'),
    )


class ClassDayMerge(db.Model):
    """Day-level class merge: going_class attends host_class for the whole day.
    All teachers of going_class become free for proxy on that date."""
    __tablename__ = 'class_day_merge'
    id              = db.Column(db.Integer, primary_key=True)
    date            = db.Column(db.Date, nullable=False)
    going_class_id  = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    host_class_id   = db.Column(db.Integer, db.ForeignKey('class.id'), nullable=False)
    note            = db.Column(db.String(300))

    going_class = db.relationship('Class', foreign_keys=[going_class_id])
    host_class  = db.relationship('Class', foreign_keys=[host_class_id])

    __table_args__ = (
        db.UniqueConstraint('date', 'going_class_id', name='uq_day_merge'),
    )

    def __repr__(self):
        return f'<DayMerge {self.going_class_id}→{self.host_class_id} on {self.date}>'


class TeacherLoadSummary(db.Model):
    __tablename__ = 'teacher_load_summary'
    id                  = db.Column(db.Integer, primary_key=True)
    teacher_id          = db.Column(db.Integer, db.ForeignKey('teacher.id'), unique=True, nullable=False)
    total_proxy_count   = db.Column(db.Integer, default=0)
    last_proxy_date     = db.Column(db.Date)
