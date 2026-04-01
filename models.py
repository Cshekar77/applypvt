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


# NEW MODEL: Application Categories (Admin manages these)
class ApplicationCategory(db.Model):
    __tablename__ = 'application_category'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(100), unique=True, nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<Category {self.name}>'


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

    # Programme (UPDATED: removed why_bca, added specialization and category)
    # why_bca         = db.Column(db.Text)  # REMOVED - no longer needed
    specialization      = db.Column(db.String(100))  # AI/ML, Data Science, Full Stack, Other
    specialization_other = db.Column(db.String(200))  # If Other selected
    category_id         = db.Column(db.Integer, db.ForeignKey('application_category.id'))
    category            = db.relationship('ApplicationCategory', backref='applications')

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
        """Return the specialization with 'Other' text if applicable"""
        if self.specialization == 'Other' and self.specialization_other:
            return self.specialization_other
        return self.specialization
    
    @property
    def category_name(self):
        """Return category name"""
        return self.category.name if self.category else None