from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed, FileRequired
from wtforms import StringField, SelectField, TextAreaField, FloatField, HiddenField
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

GENDERS = [('', 'Select'), ('Male', 'Male'), ('Female', 'Female'),
           ('Non-binary', 'Non-binary'), ('Prefer not to say', 'Prefer not to say')]

STREAMS = [('', 'Select stream'), ('Science', 'Science'),
           ('Commerce', 'Commerce'), ('Arts / Humanities', 'Arts / Humanities'), ('Other', 'Other')]


class ApplicationForm(FlaskForm):
    # Personal
    first_name  = StringField('First name',      validators=[DataRequired(), Length(max=80)])
    last_name   = StringField('Last name',        validators=[DataRequired(), Length(max=80)])
    dob         = StringField('Date of birth',    validators=[DataRequired()])
    gender      = SelectField('Gender',           choices=GENDERS, validators=[Optional()])
    nationality = StringField('Nationality',      validators=[DataRequired(), Length(max=60)])
    email       = StringField('Email address',    validators=[DataRequired(), Email()])
    phone       = StringField('Phone number',     validators=[DataRequired(), Length(max=20)])
    address     = TextAreaField('Address',        validators=[Optional()])

    # Grade 10
    school_10      = StringField('School name',     validators=[DataRequired()])
    board_10       = SelectField('Board',           choices=BOARDS_10, validators=[DataRequired()])
    board_10_other = StringField('Board (other)',   validators=[Optional(), Length(max=80)])
    year_10        = StringField('Year of passing', validators=[DataRequired()])
    total_10       = FloatField('Total marks',      validators=[DataRequired(), NumberRange(min=1)])
    obtained_10    = FloatField('Marks obtained',   validators=[DataRequired(), NumberRange(min=0)])
    marksheet_10   = FileField('Grade 10 marksheet',
                               validators=[FileRequired(), FileAllowed(['pdf'], 'PDF only')])

    # Grade 12
    school_12      = StringField('School/College name', validators=[DataRequired()])
    board_12       = SelectField('Board',               choices=BOARDS_12, validators=[DataRequired()])
    board_12_other = StringField('Board (other)',       validators=[Optional(), Length(max=80)])
    stream_12      = SelectField('Stream',              choices=STREAMS, validators=[DataRequired()])
    year_12        = StringField('Year of passing',     validators=[Optional()])
    total_12       = FloatField('Total marks',          validators=[DataRequired(), NumberRange(min=1)])
    obtained_12    = FloatField('Marks obtained',       validators=[DataRequired(), NumberRange(min=0)])
    marksheet_12   = FileField('Grade 12 marksheet',
                               validators=[FileRequired(), FileAllowed(['pdf'], 'PDF only')])

    # Programme
    why_bca = TextAreaField('Why BCA?', validators=[Optional(), Length(max=2000)])
