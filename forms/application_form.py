from flask_wtf import FlaskForm
from wtforms import (StringField, SelectField, TextAreaField, FloatField,
                     BooleanField, RadioField)
from wtforms.validators import DataRequired, Email, Optional, Length, NumberRange

BOARDS_10 = [
    ('', 'Select board'),
    ('CBSE', 'CBSE'),
    ('ICSE', 'ICSE'),
    ('NIOS', 'NIOS'),
    ('State Board (KSEAB)', 'State Board (KSEAB)'),
    ('IB', 'IB'),
    ('IGCSE', 'IGCSE'),
    ('Other', 'Other'),
]

BOARDS_12 = [
    ('', 'Select board'),
    ('CBSE', 'CBSE'),
    ('ICSE', 'ICSE'),
    ('NIOS', 'NIOS'),
    ('State Board (PUC)', 'State Board (PUC)'),
    ('IB', 'IB'),
    ('IGCSE', 'IGCSE'),
    ('Other', 'Other'),
]

GENDERS = [
    ('', 'Select'),
    ('Male', 'Male'),
    ('Female', 'Female'),
    ('Non-binary', 'Non-binary'),
    ('Prefer not to say', 'Prefer not to say'),
]

STREAMS = [
    ('', 'Select stream'),
    ('Science', 'Science'),
    ('Commerce', 'Commerce'),
    ('Arts / Humanities', 'Arts / Humanities'),
    ('Other', 'Other'),
]

COMBINATIONS_12 = [
    ('', 'Select combination'),
    ('PCMB', 'PCMB — Physics, Chemistry, Maths, Biology'),
    ('PCMC', 'PCMC — Physics, Chemistry, Maths, Computer Science'),
    ('PCME', 'PCME — Physics, Chemistry, Maths, Electronics'),
    ('CBSC', 'CBSC — Chemistry, Biology, Statistics, Computer Science'),
    ('CBSE', 'CBSE — Chemistry, Biology, Statistics, Electronics'),
    ('HEBA', 'HEBA — History, Economics, Business Studies, Accountancy'),
    ('SEBA', 'SEBA — Statistics, Economics, Business Studies, Accountancy'),
    ('Other', 'Other'),
]

YES_NO = [
    ('No', 'No'),
    ('Yes', 'Yes'),
]


class ApplicationForm(FlaskForm):
    # ── Personal Information ───────────────────────────────────────
    candidate_name  = StringField('Name of the Candidate',
                                  validators=[DataRequired(), Length(max=160)])
    mother_name     = StringField("Mother's Name",
                                  validators=[DataRequired(), Length(max=120)])
    father_name     = StringField("Father's Name",
                                  validators=[DataRequired(), Length(max=120)])
    dob             = StringField('Date of Birth',
                                  validators=[DataRequired()])
    gender          = SelectField('Gender', choices=GENDERS,
                                  validators=[DataRequired()])
    parent_mobile   = StringField('Mobile Number of Parent',
                                  validators=[DataRequired(), Length(max=20)])
    phone           = StringField('Mobile Number of Candidate',
                                  validators=[DataRequired(), Length(max=20)])
    email           = StringField('E-mail Id',
                                  validators=[DataRequired(), Email()])
    address         = TextAreaField('Address',
                                    validators=[DataRequired()])
    nationality     = StringField('Nationality',
                                  validators=[DataRequired(), Length(max=60)])
    religion        = StringField('Religion',
                                  validators=[DataRequired(), Length(max=60)])

    # ── Category / Background ─────────────────────────────────────
    # category_id populated dynamically from DB in route
    hk_region           = SelectField('Candidate Belongs to HK Region',
                                      choices=YES_NO, validators=[Optional()])
    kannada_medium      = SelectField('Kannada Medium',
                                      choices=YES_NO, validators=[Optional()])
    rural_background    = SelectField('Rural Background',
                                      choices=YES_NO, validators=[Optional()])
    caste_certificate_no  = StringField('Caste Certificate No.',
                                        validators=[Optional(), Length(max=100)])
    parent_annual_income  = StringField("Parent's Annual Income",
                                        validators=[Optional(), Length(max=100)])
    income_certificate_no = StringField('Income Certificate No.',
                                        validators=[Optional(), Length(max=100)])

    # ── 10th Standard ─────────────────────────────────────────────
    board_10    = SelectField('10th Standard Board',
                              choices=BOARDS_10, validators=[DataRequired()])
    percent_10  = FloatField('10th Standard Percentage',
                             validators=[DataRequired(), NumberRange(min=0, max=100)])

    # ── 12th Standard ─────────────────────────────────────────────
    board_12        = SelectField('12th Standard Board',
                                  choices=BOARDS_12, validators=[DataRequired()])
    stream_12       = SelectField('12th Standard Stream',
                                  choices=STREAMS, validators=[DataRequired()])
    combination_12  = SelectField('12th Standard Combination',
                                  choices=COMBINATIONS_12, validators=[DataRequired()])
    total_12        = FloatField('12th Standard Max. Marks',
                                 validators=[DataRequired(), NumberRange(min=1)])
    obtained_12     = FloatField('12th Standard Marks Scored',
                                 validators=[DataRequired(), NumberRange(min=0)])
    # percent_12 is auto-calculated

    # ── Declaration ───────────────────────────────────────────────
    declaration = BooleanField('Declaration', validators=[DataRequired()])