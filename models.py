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
    is_active   = db.Column(db.Boolean, default=False)
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
    status          = db.Column(db.String(20), default='stopped')
    current_rank    = db.Column(db.Integer, nullable=True)
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
#  Category Seats
# ─────────────────────────────────────────
class CategorySeats(db.Model):
    __tablename__ = 'category_seats'
    id              = db.Column(db.Integer, primary_key=True)
    category_id     = db.Column(db.Integer, db.ForeignKey('application_category.id'), unique=True)
    govt_total      = db.Column(db.Integer, default=0)
    govt_filled     = db.Column(db.Integer, default=0)
    mgmt_total      = db.Column(db.Integer, default=0)
    mgmt_filled     = db.Column(db.Integer, default=0)
    category        = db.relationship('ApplicationCategory', backref='seats')

    @property
    def govt_remaining(self):
        return max(0, self.govt_total - self.govt_filled)

    @property
    def mgmt_remaining(self):
        return max(0, self.mgmt_total - self.mgmt_filled)

    @property
    def total_seats(self):
        return self.govt_total + self.mgmt_total

    @property
    def filled_seats(self):
        return self.govt_filled + self.mgmt_filled

    @property
    def remaining_seats(self):
        return self.govt_remaining + self.mgmt_remaining

    @staticmethod
    def get_for_category(category_id):
        s = CategorySeats.query.filter_by(category_id=category_id).first()
        if not s:
            s = CategorySeats(category_id=category_id,
                              govt_total=0, govt_filled=0,
                              mgmt_total=0, mgmt_filled=0)
            db.session.add(s)
            db.session.commit()
        return s


# ─────────────────────────────────────────
#  Admit Category
# ─────────────────────────────────────────
class AdmitCategory(db.Model):
    __tablename__ = 'admit_category'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    total_seats = db.Column(db.Integer, default=0)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def seats_used(self):
        return SeatAllotment.query.filter_by(admit_category_id=self.id).count()

    @property
    def seats_remaining(self):
        return max(0, self.total_seats - self.seats_used)

    def __repr__(self):
        return f'<AdmitCategory {self.name}>'


# ─────────────────────────────────────────
#  Seat Allotment  (one per student)
# ─────────────────────────────────────────
class SeatAllotment(db.Model):
    __tablename__ = 'seat_allotment'
    id                  = db.Column(db.Integer, primary_key=True)
    application_id      = db.Column(db.Integer, db.ForeignKey('student_applications.id'), unique=True)
    category_id         = db.Column(db.Integer, db.ForeignKey('application_category.id'))
    quota               = db.Column(db.String(20), nullable=False)
    admit_category_id   = db.Column(db.Integer, db.ForeignKey('admit_category.id'), nullable=True)
    admit_seat_number   = db.Column(db.Integer, nullable=True)
    allotted_at         = db.Column(db.DateTime, default=datetime.utcnow)
    application         = db.relationship('StudentApplication', backref='allotment')
    category            = db.relationship('ApplicationCategory', backref='allotments')
    admit_category      = db.relationship('AdmitCategory', backref='allotments')

    @property
    def admit_seat_label(self):
        if self.admit_category and self.admit_seat_number:
            return f"{self.admit_category.name}({self.admit_seat_number})"
        return None


# ─────────────────────────────────────────
#  Document Category
# ─────────────────────────────────────────
class DocumentCategory(db.Model):
    __tablename__ = 'document_category'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_required = db.Column(db.Boolean, default=True)
    is_active   = db.Column(db.Boolean, default=True)
    sort_order  = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<DocumentCategory {self.name}>'


# ─────────────────────────────────────────
#  Student Document
# ─────────────────────────────────────────
class StudentDocument(db.Model):
    __tablename__ = 'student_document'
    id              = db.Column(db.Integer, primary_key=True)
    application_id  = db.Column(db.Integer, db.ForeignKey('student_applications.id'), nullable=False)
    doc_category_id = db.Column(db.Integer, db.ForeignKey('document_category.id'), nullable=False)
    status          = db.Column(db.String(30), default='not_given')
    is_approved     = db.Column(db.Boolean, default=False)
    approved_at     = db.Column(db.DateTime, nullable=True)
    approved_by_faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)
    approved_by_role       = db.Column(db.String(20), nullable=True)
    approved_by_name       = db.Column(db.String(120), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    application         = db.relationship('StudentApplication', backref='documents')
    doc_category        = db.relationship('DocumentCategory', backref='student_docs')
    approved_by_faculty = db.relationship('Faculty', backref='verified_docs')

    __table_args__ = (
        db.UniqueConstraint('application_id', 'doc_category_id', name='uq_student_doc'),
    )

    STATUS_LABELS = {
        'original':  'Original',
        'xerox':     'Xerox',
        'attested':  'Attested',
        'not_given': 'Not Given',
    }

    @property
    def status_label(self):
        return self.STATUS_LABELS.get(self.status, self.status)

    @property
    def status_color(self):
        return {
            'original':  'green',
            'xerox':     'blue',
            'attested':  'orange',
            'not_given': 'gray',
        }.get(self.status, 'gray')

    def __repr__(self):
        return f'<StudentDocument app={self.application_id} doc={self.doc_category_id} status={self.status}>'


# ─────────────────────────────────────────
#  Fee Category
# ─────────────────────────────────────────
class FeeCategory(db.Model):
    __tablename__ = 'fee_category'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False, unique=True)
    amount     = db.Column(db.Float, nullable=False, default=0.0)
    is_active  = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<FeeCategory {self.name} ${self.amount}>'


# ─────────────────────────────────────────
#  Student Fee
# ─────────────────────────────────────────
class StudentFee(db.Model):
    __tablename__ = 'student_fee'
    id               = db.Column(db.Integer, primary_key=True)
    application_id   = db.Column(db.Integer, db.ForeignKey('student_applications.id'), unique=True, nullable=False)
    fee_category_id  = db.Column(db.Integer, db.ForeignKey('fee_category.id'), nullable=True)
    additional_fee   = db.Column(db.Float, default=0.0, nullable=False)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    application  = db.relationship('StudentApplication', backref='student_fee')
    fee_category = db.relationship('FeeCategory', backref='student_fees')

    @property
    def base_amount(self):
        return self.fee_category.amount if self.fee_category else 0.0

    @property
    def total_amount(self):
        return self.base_amount + (self.additional_fee or 0.0)

    def __repr__(self):
        return f'<StudentFee app={self.application_id} total={self.total_amount}>'


# ─────────────────────────────────────────
#  Faculty
# ─────────────────────────────────────────
class Faculty(db.Model):
    __tablename__ = 'faculty'
    id            = db.Column(db.Integer, primary_key=True)
    full_name     = db.Column(db.String(120), nullable=False)
    username      = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    receipts      = db.relationship('PaymentReceipt', backref='faculty', lazy=True)

    def __repr__(self):
        return f'<Faculty {self.username}>'


# ─────────────────────────────────────────
#  Student Fees  (payment tracking)
# ─────────────────────────────────────────
class StudentFees(db.Model):
    __tablename__ = 'student_fees'
    id             = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey('student_applications.id'), unique=True)
    total_fees     = db.Column(db.Float, nullable=False, default=0.0)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    application    = db.relationship('StudentApplication', backref='fees')
    receipts       = db.relationship('PaymentReceipt', backref='student_fees', lazy=True)

    @property
    def total_paid(self):
        return sum(r.amount_paid for r in self.receipts)

    @property
    def balance_remaining(self):
        return max(0.0, self.total_fees - self.total_paid)

    def __repr__(self):
        return f'<StudentFees app_id={self.application_id} total={self.total_fees}>'


# ─────────────────────────────────────────
#  Payment Receipt
# ─────────────────────────────────────────
class PaymentReceipt(db.Model):
    __tablename__ = 'payment_receipt'
    id             = db.Column(db.Integer, primary_key=True)
    fees_id        = db.Column(db.Integer, db.ForeignKey('student_fees.id'), nullable=False)
    application_id = db.Column(db.Integer, db.ForeignKey('student_applications.id'), nullable=False)
    faculty_id     = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)
    receipt_number = db.Column(db.String(100), nullable=False)
    amount_paid    = db.Column(db.Float, nullable=False, default=0.0)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    application    = db.relationship('StudentApplication', backref='receipts')

    def __repr__(self):
        return f'<PaymentReceipt {self.receipt_number} amt={self.amount_paid}>'


# ─────────────────────────────────────────
#  Student Application
# ─────────────────────────────────────────
class StudentApplication(db.Model):
    __tablename__ = 'student_applications'
    id              = db.Column(db.Integer, primary_key=True)
    user_id         = db.Column(db.Integer, db.ForeignKey('users.id'), unique=True)
    submitted_at    = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Personal details ──────────────────
    candidate_name      = db.Column(db.String(160))          # Name of the Candidate
    mother_name         = db.Column(db.String(120))           # Mother's Name
    father_name         = db.Column(db.String(120))           # Father's Name
    dob                 = db.Column(db.String(20))            # Date of Birth
    gender              = db.Column(db.String(30))
    parent_mobile       = db.Column(db.String(20))            # Mobile Number of Parent
    phone               = db.Column(db.String(20))            # Mobile Number of Candidate
    email               = db.Column(db.String(120))           # E-mail Id
    address             = db.Column(db.Text)                  # Address
    nationality         = db.Column(db.String(60))            # Nationality
    religion            = db.Column(db.String(60))            # Religion

    # ── Category / background ─────────────
    category_id         = db.Column(db.Integer, db.ForeignKey('application_category.id'))
    category            = db.relationship('ApplicationCategory', backref='applications')
    hk_region           = db.Column(db.Boolean, default=False)   # Candidate Belongs to HK Region
    kannada_medium      = db.Column(db.Boolean, default=False)   # Kannada Medium
    rural_background    = db.Column(db.Boolean, default=False)   # Rural Background
    caste_certificate_no    = db.Column(db.String(100))          # Caste Certificate No.
    parent_annual_income    = db.Column(db.String(100))          # Parent's Annual Income
    income_certificate_no   = db.Column(db.String(100))          # Income Certificate No.

    # ── 10th Standard ─────────────────────
    board_10        = db.Column(db.String(80))                # 10th Standard Board
    percent_10      = db.Column(db.Float)                     # 10th Standard Percentage

    # ── 12th Standard ─────────────────────
    board_12        = db.Column(db.String(80))                # 12th Standard Board
    stream_12       = db.Column(db.String(60))                # 12th Standard Stream
    combination_12  = db.Column(db.String(100))               # 12th Standard Combination
    total_12        = db.Column(db.Float)                     # 12th Standard Max. Marks
    obtained_12     = db.Column(db.Float)                     # 12th Standard Marks Scored
    percent_12      = db.Column(db.Float)                     # 12th Standard Percentage

    # ── Declaration (student-only) ────────
    declaration     = db.Column(db.Boolean, default=False)

    # ── Admin fields ──────────────────────
    is_verified     = db.Column(db.Boolean, default=False)
    verified_at     = db.Column(db.DateTime, nullable=True)
    rank            = db.Column(db.Integer, nullable=True)

    # ── Application number (auto from id) ─
    @property
    def application_number(self):
        return f'BCA{str(self.id).zfill(5)}'

    @property
    def full_name(self):
        return self.candidate_name or ''

    @property
    def board_10_display(self):
        return self.board_10 or ''

    @property
    def board_12_display(self):
        return self.board_12 or ''

    @property
    def category_name(self):
        return self.category.name if self.category else None