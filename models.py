from database import db
from flask_login import UserMixin
from datetime import datetime


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id              = db.Column(db.Integer, primary_key=True)
    full_name       = db.Column(db.String(120), nullable=False)
    email           = db.Column(db.String(120), unique=True, nullable=False)
    password_hash   = db.Column(db.String(256), nullable=False)
    is_verified     = db.Column(db.Boolean, default=False)
    otp_code        = db.Column(db.String(6), nullable=True)
    otp_expires_at  = db.Column(db.DateTime, nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    application     = db.relationship('StudentApplication', backref='user', uselist=False)


class FormSettings(db.Model):
    __tablename__ = 'form_settings'
    id          = db.Column(db.Integer, primary_key=True)
    is_open     = db.Column(db.Boolean, default=True)
    open_from   = db.Column(db.DateTime, nullable=True)
    open_until  = db.Column(db.DateTime, nullable=True)
    message     = db.Column(db.Text, default='')

    @staticmethod
    def get():
        s = FormSettings.query.first()
        if not s:
            s = FormSettings()
            db.session.add(s)
            db.session.commit()
        return s


class WhatsAppSettings(db.Model):
    __tablename__ = 'whatsapp_settings'
    id          = db.Column(db.Integer, primary_key=True)
    link        = db.Column(db.String(500), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        s = WhatsAppSettings.query.first()
        if not s:
            s = WhatsAppSettings(link=None, description=None, is_active=False)
            db.session.add(s)
            db.session.commit()
        return s


# ─────────────────────────────────────────
#  Application Categories
# ─────────────────────────────────────────
class ApplicationCategory(db.Model):
    __tablename__ = 'application_category'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Category {self.name}>'


# ─────────────────────────────────────────
#  Counselling Settings  (singleton)
# ─────────────────────────────────────────
class CounsellingSettings(db.Model):
    __tablename__ = 'counselling_settings'
    id              = db.Column(db.Integer, primary_key=True)

    # Status: 'stopped' | 'running' | 'paused'
    status          = db.Column(db.String(20), default='stopped')

    # Current rank being called live
    current_rank    = db.Column(db.Integer, nullable=True)

    # Optional message admin can broadcast to students
    message         = db.Column(db.Text, nullable=True)

    started_at      = db.Column(db.DateTime, nullable=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @staticmethod
    def get():
        s = CounsellingSettings.query.first()
        if not s:
            s = CounsellingSettings(status='stopped', current_rank=None, message=None)
            db.session.add(s)
            db.session.commit()
        return s

    @property
    def is_running(self):
        return self.status == 'running'

    @property
    def is_paused(self):
        return self.status == 'paused'

    @property
    def is_stopped(self):
        return self.status == 'stopped'


# ─────────────────────────────────────────
#  Category Seats  (total + filled per category)
# ─────────────────────────────────────────
class CategorySeats(db.Model):
    __tablename__ = 'category_seats'
    id              = db.Column(db.Integer, primary_key=True)
    category_id     = db.Column(db.Integer, db.ForeignKey('application_category.id'), unique=True)
    total_seats     = db.Column(db.Integer, default=0)
    filled_seats    = db.Column(db.Integer, default=0)
    category        = db.relationship('ApplicationCategory', backref='seats')

    @property
    def remaining_seats(self):
        return max(0, self.total_seats - self.filled_seats)

    @staticmethod
    def get_for_category(category_id):
        s = CategorySeats.query.filter_by(category_id=category_id).first()
        if not s:
            s = CategorySeats(category_id=category_id, total_seats=0, filled_seats=0)
            db.session.add(s)
            db.session.commit()
        return s


# ─────────────────────────────────────────
#  Seat Allotment  (one per student)
# ─────────────────────────────────────────
class SeatAllotment(db.Model):
    __tablename__ = 'seat_allotment'
    id              = db.Column(db.Integer, primary_key=True)
    application_id  = db.Column(db.Integer, db.ForeignKey('student_applications.id'), unique=True)
    category_id     = db.Column(db.Integer, db.ForeignKey('application_category.id'))

    # 'government' or 'management'
    quota           = db.Column(db.String(20), nullable=False)

    allotted_at     = db.Column(db.DateTime, default=datetime.utcnow)

    application     = db.relationship('StudentApplication', backref='allotment')
    category        = db.relationship('ApplicationCategory', backref='allotments')


# ─────────────────────────────────────────
#  Student Application
# ─────────────────────────────────────────
class StudentApplication(db.Model):
    __tablename__ = 'student_applications'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # Personal
    first_name      = db.Column(db.String(80))
    last_name       = db.Column(db.String(80))
    dob             = db.Column(db.String(20))
    gender          = db.Column(db.String(30))
    nationality     = db.Column(db.String(60))
    email           = db.Column(db.String(120))
    phone           = db.Column(db.String(20))
    address         = db.Column(db.Text)

    # Grade 10
    school_10       = db.Column(db.String(150))
    board_10        = db.Column(db.String(80))
    board_10_other  = db.Column(db.String(80))
    year_10         = db.Column(db.String(10))
    total_10        = db.Column(db.Float)
    obtained_10     = db.Column(db.Float)
    percent_10      = db.Column(db.Float)
    marksheet_10    = db.Column(db.String(200))

    # Grade 12
    school_12       = db.Column(db.String(150))
    board_12        = db.Column(db.String(80))
    board_12_other  = db.Column(db.String(80))
    stream_12       = db.Column(db.String(60))
    year_12         = db.Column(db.String(10))
    total_12        = db.Column(db.Float)
    obtained_12     = db.Column(db.Float)
    percent_12      = db.Column(db.Float)
    marksheet_12    = db.Column(db.String(200))

    # Programme
    specialization       = db.Column(db.String(100))
    specialization_other = db.Column(db.String(200))
    category_id          = db.Column(db.Integer, db.ForeignKey('application_category.id'))
    category             = db.relationship('ApplicationCategory', backref='applications')

    # Admin fields
    is_verified     = db.Column(db.Boolean, default=False)
    verified_at     = db.Column(db.DateTime, nullable=True)
    rank            = db.Column(db.Integer, nullable=True)
    admin_notes     = db.Column(db.Text, default='')

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def board_10_display(self):
        return self.board_10_other if self.board_10 == 'Other' else self.board_10

    @property
    def board_12_display(self):
        return self.board_12_other if self.board_12 == 'Other' else self.board_12

    @property
    def specialization_display(self):
        if self.specialization == 'Other' and self.specialization_other:
            return self.specialization_other
        return self.specialization

    @property
    def category_name(self):
        return self.category.name if self.category else None