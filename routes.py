import os
import re
import uuid
import requests
import pandas as pd
from werkzeug.utils import secure_filename

from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, current_app, abort, send_file, jsonify)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone

from database import db
from models import (User, FormSettings, StudentApplication, ApplicationCategory,
                    WhatsAppSettings, CounsellingSettings, CategorySeats, SeatAllotment,
                    Faculty, StudentFees, PaymentReceipt, AdmitCategory,
                    DocumentCategory, StudentDocument,
                    FeeCategory, StudentFee)
from forms.application_form import ApplicationForm
from utils.helpers import save_pdf, calc_percent, generate_otp, send_otp_email
from utils.exports import export_excel, export_verified_excel

main = Blueprint('main', __name__)

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def utc_to_ist(dt):
    """
    DB stores naive datetimes in UTC. Convert to IST (+5:30).
    """
    if dt is None:
        return None
    from datetime import timezone
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # treat as UTC
    return dt.astimezone(IST)

def ist_now_naive():
    """Return current UTC time as naive datetime (for DB storage)."""
    return datetime.utcnow()

def fmt_ist(dt, fmt='%d %b %Y, %I:%M %p'):
    """Format a DB datetime (naive IST) for display."""
    aware = utc_to_ist(dt)
    if aware is None:
        return '—'
    return aware.strftime(fmt) + ' IST'


# ─────────────────────────────────────────
#  Admin guard
# ─────────────────────────────────────────
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin login required.', 'warning')
            return redirect(url_for('main.admin_login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
#  Faculty guard
# ─────────────────────────────────────────
def faculty_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('faculty_logged_in'):
            flash('Faculty login required.', 'warning')
            return redirect(url_for('main.faculty_login'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────
#  Shared helpers (used by import + API)
# ─────────────────────────────────────────
def _str(val, default=''):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return str(val).strip()

def _flt(val):
    try:
        v = float(val)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None

def _bool_field(val):
    """Parse Yes/No/True/False/1/0 → bool"""
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ('yes', 'true', '1', 'y')

def _dob_to_password(dob_raw):
    """Convert DOB to password: 06/11/2006 → 06112006"""
    if not dob_raw or str(dob_raw).strip() in ('', 'nan', 'NaT'):
        return None
    try:
        ts = pd.to_datetime(dob_raw, dayfirst=True)
        if not pd.isna(ts):
            return ts.strftime('%d%m%Y')
    except Exception:
        pass
    digits = re.sub(r'[^0-9]', '', str(dob_raw).strip())
    return digits if digits else None


# ─────────────────────────────────────────
#  Helper: sync StudentFee total → StudentFees
# ─────────────────────────────────────────
def _sync_student_fees(application_id):
    sf = StudentFee.query.filter_by(application_id=application_id).first()
    if not sf:
        return
    total = sf.total_amount
    fees  = StudentFees.query.filter_by(application_id=application_id).first()
    if fees:
        fees.total_fees = total
        fees.updated_at = ist_now_naive()
    else:
        db.session.add(StudentFees(application_id=application_id, total_fees=total))


# ─────────────────────────────────────────
#  Welcome Email via Apps Script
# ─────────────────────────────────────────
def send_welcome_email(email, full_name, password_plain, dob_display):
    APPS_SCRIPT_URL = "exec"
    WEBSITE_URL     = "https://applybcabu.pythonanywhere.com/"
    payload = {
        "action":   "sendWelcome",
        "email":    email,
        "name":     full_name,
        "password": password_plain,
        "dob":      dob_display,
        "website":  WEBSITE_URL,
    }
    try:
        resp   = requests.post(APPS_SCRIPT_URL, data=payload, timeout=10)
        result = resp.json()
        return result.get("success", False), result.get("message", "")
    except Exception as ex:
        return False, str(ex)


# ─────────────────────────────────────────
#  Public
# ─────────────────────────────────────────
@main.route('/')
def index():
    settings = FormSettings.get()
    return render_template('index.html', settings=settings)

@main.route('/api/seat-matrix')
def api_seat_matrix():
    categories = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()
    return jsonify([{
        'name':      ac.name,
        'total':     ac.total_seats,
        'used':      ac.seats_used,
        'remaining': ac.seats_remaining,
        'is_mgmt':   ac.name.upper().endswith('-PY'),
    } for ac in categories])

# ─────────────────────────────────────────
#  Public — Live Counselling Tracker
# ─────────────────────────────────────────
@main.route('/live')
def counselling_live():
    cs = CounsellingSettings.get()

    student        = None
    allotment      = None
    total_ranked   = StudentApplication.query.filter(
                         StudentApplication.rank.isnot(None)).count()
    total_allotted = SeatAllotment.query.count()

    if cs.current_rank:
        student = StudentApplication.query.filter_by(
            rank=cs.current_rank,
            is_verified=True
        ).first()
        if student:
            pass  # Currently called student is always shown as pending on the live page

    admit_categories = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()
    govt_seats_list  = []
    mgmt_seats_list  = []
    for ac in admit_categories:
        entry = {
            'category':  ac.name,
            'total':     ac.total_seats,
            'filled':    ac.seats_used,
            'remaining': ac.seats_remaining,
        }
        if ac.name.upper().endswith('-PY'):
            mgmt_seats_list.append(entry)
        else:
            govt_seats_list.append(entry)

    all_allotments    = SeatAllotment.query.order_by(SeatAllotment.allotted_at.asc()).all()
    allotment_history = []
    for a in all_allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue
        allotment_history.append({
            'rank':        appl.rank or '—',
            'name':        appl.full_name,
            'category':    appl.category_name or '—',
            'quota':       a.quota,
            'allotted_at': fmt_ist(a.allotted_at),
        })
    allotment_history.sort(key=lambda x: (x['rank'] if isinstance(x['rank'], int) else 9999))

    return render_template('counselling_live.html',
                           cs=cs,
                           student=student,
                           allotment=allotment,
                           total_ranked=total_ranked,
                           total_allotted=total_allotted,
                           govt_seats_list=govt_seats_list,
                           mgmt_seats_list=mgmt_seats_list,
                           allotment_history=allotment_history)


@main.route('/live/status')
def counselling_live_status_api():
    cs = CounsellingSettings.get()

    total_ranked   = StudentApplication.query.filter(
        StudentApplication.rank.isnot(None)).count()
    total_allotted = SeatAllotment.query.count()

    student   = None
    allotment = None
    if cs.current_rank:
        student = StudentApplication.query.filter_by(
            rank=cs.current_rank,
            is_verified=True
        ).first()
        if student:
            a = SeatAllotment.query.filter_by(application_id=student.id).first()
            if a:
                allotment = {
                    'quota': a.quota,
                    'allotted_at': fmt_ist(a.allotted_at, '%d %b %Y at %I:%M %p'),
                }

    admit_categories = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()

    govt_seats_list = []
    mgmt_seats_list = []
    for ac in admit_categories:
        entry = {
            'category': ac.name,
            'total': ac.total_seats,
            'filled': ac.seats_used,
            'remaining': ac.seats_remaining,
        }
        if ac.name.upper().endswith('-PY'):
            mgmt_seats_list.append(entry)
        else:
            govt_seats_list.append(entry)

    return jsonify({
        'status': cs.status,
        'current_rank': cs.current_rank,
        'message': cs.message,
        'total_ranked': total_ranked,
        'total_allotted': total_allotted,
        'student': {
            'full_name': student.full_name if student else None,
            'rank': student.rank if student else None,
            'percent_12': student.percent_12 if student else None,
            'category_name': student.category_name if student else None,
            'quota': allotment['quota'] if allotment else None,
        },
        'allotment': allotment,
        'govt_seats_list': govt_seats_list,
        'mgmt_seats_list': mgmt_seats_list,
    })


# ─────────────────────────────────────────
#  POST API — Register Student
# ─────────────────────────────────────────
@main.route('/api/d2faa6fb-745b-454d-8852-92ed0bb482d8', methods=['POST'])
def api_register_student():
    api_key = request.headers.get('x-api-key')
    if api_key != current_app.config.get('API_SECRET_KEY'):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    print(data)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    try:
        email     = _str(data.get('email')).lower()
        full_name = _str(data.get('full_name'))
        dob_raw   = _str(data.get('dob'))

        if not email or not full_name or not dob_raw:
            return jsonify({"error": "Missing required fields: email, full_name, dob"}), 400

        if User.query.filter_by(email=email).first():
            return jsonify({"error": "User already exists"}), 409

        password_plain = _dob_to_password(dob_raw)
        if not password_plain:
            return jsonify({"error": "Invalid DOB — could not parse into password"}), 400

        user = User(
            full_name      = full_name,
            email          = email,
            password_hash  = generate_password_hash(password_plain),
            is_verified    = True,
            otp_code       = None,
            otp_expires_at = None,
        )
        db.session.add(user)
        db.session.flush()

        cat_id  = None
        cat_raw = _str(data.get('category')).lower()
        if cat_raw:
            cat    = ApplicationCategory.query.filter(
                db.func.lower(ApplicationCategory.name) == cat_raw
            ).first()
            cat_id = cat.id if cat else None

        total_12    = _flt(data.get('total_12'))
        obtained_12 = _flt(data.get('obtained_12'))
        percent_12  = _flt(data.get('percent_12'))
        if total_12 and obtained_12 and not percent_12:
            percent_12 = round((obtained_12 / total_12) * 100, 2)

        appl = StudentApplication(
            user_id              = user.id,
            candidate_name       = full_name,
            dob                  = dob_raw,
            email                = email,
            phone                = _str(data.get('student_mobile')),
            parent_mobile        = _str(data.get('parent_mobile')),
            gender               = _str(data.get('gender')),
            address              = _str(data.get('address')),
            nationality          = _str(data.get('nationality')) or 'Indian',
            religion             = _str(data.get('religion')),
            mother_name          = _str(data.get('mother_name')),
            father_name          = _str(data.get('father_name')),
            hk_region            = bool(data.get('hk_region', False)),
            kannada_medium       = bool(data.get('kannada_medium', False)),
            rural_background     = bool(data.get('rural_background', False)),
            caste_certificate_no = _str(data.get('caste_certificate_no')),
            parent_annual_income = _str(data.get('parent_annual_income')),
            income_certificate_no= _str(data.get('income_certificate_no')),
            category_id          = cat_id,
            board_10             = _str(data.get('board_10')),
            percent_10           = _flt(data.get('percent_10')),
            board_12             = _str(data.get('board_12')),
            stream_12            = _str(data.get('stream_12')),
            combination_12       = _str(data.get('combination_12')),
            total_12             = total_12,
            obtained_12          = obtained_12,
            percent_12           = percent_12,
            declaration          = True,
        )
        db.session.add(appl)
        db.session.commit()

        try:
            dob_display = pd.to_datetime(dob_raw, dayfirst=True).strftime('%d/%m/%Y')
        except Exception:
            dob_display = dob_raw

        send_welcome_email(
            email          = email,
            full_name      = full_name,
            password_plain = password_plain,
            dob_display    = dob_display,
        )

        return jsonify({
            "message": "Student registered successfully",
            "user_id": user.id,
            "email":   email,
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────
#  Registration + OTP — hidden URL
# ─────────────────────────────────────────
@main.route('/register28497234827', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('main.student_dashboard'))
    if request.method == 'POST':
        name     = request.form.get('full_name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')
        if not name or not email or not password:
            flash('All fields are required.', 'danger')
        elif len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        elif User.query.filter_by(email=email).first():
            flash('An account with that email already exists.', 'danger')
        else:
            otp = generate_otp()
            ok, err = send_otp_email(email, name, otp)
            if ok:
                session['temp_registration'] = {
                    'name': name, 'email': email, 'password': password,
                    'otp': otp,
                    'otp_expiry': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                }
                flash('A 6-digit OTP has been sent to your email.', 'success')
                return redirect(url_for('main.verify_otp'))
            else:
                flash(f'Could not send OTP. Error: {err}', 'danger')
                return redirect(url_for('main.register'))
    return render_template('register.html')


@main.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    temp_data = session.get('temp_registration')
    if not temp_data:
        flash('No pending registration found.', 'warning')
        return redirect(url_for('main.register'))
    email = temp_data['email']
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        session.pop('temp_registration', None)
        flash('Account already exists. Please login.', 'info')
        return redirect(url_for('main.login'))
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'resend':
            new_otp = generate_otp()
            ok, err = send_otp_email(email, temp_data['name'], new_otp)
            if ok:
                temp_data['otp'] = new_otp
                temp_data['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                session['temp_registration'] = temp_data
                flash('A new OTP has been sent.', 'success')
            else:
                flash('Could not send email.', 'warning')
            return redirect(url_for('main.verify_otp'))
        entered    = request.form.get('otp', '').strip()
        stored_otp = temp_data.get('otp')
        expiry     = datetime.fromisoformat(temp_data.get('otp_expiry')) if temp_data.get('otp_expiry') else None
        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif expiry and datetime.utcnow() > expiry:
            flash('OTP has expired.', 'danger')
        elif entered != stored_otp:
            flash('Incorrect OTP.', 'danger')
        else:
            user = User(
                full_name=temp_data['name'], email=email,
                password_hash=generate_password_hash(temp_data['password']),
                is_verified=True, otp_code=None, otp_expires_at=None,
            )
            db.session.add(user)
            db.session.commit()
            session.pop('temp_registration', None)
            flash('Email verified! You can now log in.', 'success')
            return redirect(url_for('main.login'))
    return render_template('verify_otp.html', email=email)


# ─────────────────────────────────────────
#  Login / Logout
# ─────────────────────────────────────────
@main.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('main.student_dashboard'))
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user     = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('Invalid email or password.', 'danger')
        elif not user.is_verified:
            flash('Your account is not verified yet. Please contact the admissions office.', 'warning')
        else:
            login_user(user)
            return redirect(url_for('main.student_dashboard'))
    return render_template('login.html')


@main.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))


# ─────────────────────────────────────────
#  Forgot / Reset Password
# ─────────────────────────────────────────
@main.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user  = User.query.filter_by(email=email).first()
        if not user:
            flash('No account found with that email.', 'danger')
        else:
            otp = generate_otp()
            ok, err = send_otp_email(email, user.full_name, otp)
            if ok:
                session['reset_password'] = {
                    'email': email, 'otp': otp,
                    'otp_expiry': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                }
                flash('OTP sent to your email.', 'success')
                return redirect(url_for('main.verify_reset_otp'))
            else:
                flash('Could not send OTP.', 'danger')
    return render_template('forgot_password.html')


@main.route('/verify-reset-otp', methods=['GET', 'POST'])
def verify_reset_otp():
    reset_data = session.get('reset_password')
    if not reset_data:
        flash('No password reset request found.', 'warning')
        return redirect(url_for('main.forgot_password'))
    email = reset_data['email']
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'resend':
            user = User.query.filter_by(email=email).first()
            new_otp = generate_otp()
            ok, err = send_otp_email(email, user.full_name, new_otp)
            if ok:
                reset_data['otp'] = new_otp
                reset_data['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                session['reset_password'] = reset_data
                flash('New OTP sent.', 'success')
            else:
                flash('Could not send OTP.', 'warning')
            return redirect(url_for('main.verify_reset_otp'))
        entered    = request.form.get('otp', '').strip()
        stored_otp = reset_data.get('otp')
        expiry     = datetime.fromisoformat(reset_data.get('otp_expiry'))
        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif datetime.utcnow() > expiry:
            flash('OTP expired.', 'danger')
        elif entered != stored_otp:
            flash('Incorrect OTP.', 'danger')
        else:
            session['reset_verified'] = email
            session.pop('reset_password', None)
            return redirect(url_for('main.reset_password'))
    return render_template('verify_reset_otp.html', email=email)


@main.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('reset_verified')
    if not email:
        flash('Unauthorized. Please start again.', 'warning')
        return redirect(url_for('main.forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm_password', '')
        if len(password) < 8:
            flash('Password must be at least 8 characters.', 'danger')
        elif password != confirm:
            flash('Passwords do not match.', 'danger')
        else:
            user = User.query.filter_by(email=email).first()
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            session.pop('reset_verified', None)
            flash('Password reset! Please log in.', 'success')
            return redirect(url_for('main.login'))
    return render_template('reset_password.html')


# ─────────────────────────────────────────
#  Student Dashboard
# ─────────────────────────────────────────
@main.route('/dashboard')
@login_required
def student_dashboard():
    appl        = StudentApplication.query.filter_by(user_id=current_user.id).first()
    if appl:
        appl.fee_details = appl.student_fee[0] if appl.student_fee else None
    settings    = FormSettings.get()
    whatsapp    = WhatsAppSettings.get()
    counselling = CounsellingSettings.get()
    return render_template('student_dashboard.html',
                           application=appl, settings=settings,
                           whatsapp=whatsapp, counselling=counselling)


@main.route('/whatsapp-group')
@login_required
def student_whatsapp():
    whatsapp = WhatsAppSettings.get()
    return render_template('student_whatsapp.html', whatsapp=whatsapp)


# ─────────────────────────────────────────
#  Student — Live Counselling Tracker
# ─────────────────────────────────────────
@main.route('/counselling')
@login_required
def student_counselling():
    appl        = StudentApplication.query.filter_by(user_id=current_user.id).first()
    counselling = CounsellingSettings.get()
    allotment   = None
    if appl:
        a = SeatAllotment.query.filter_by(application_id=appl.id).first()
        if a:
            allotment = {
                'quota':       a.quota,
                'allotted_at': utc_to_ist(a.allotted_at)
            }
    return render_template('student_counselling.html',
                           appl=appl, counselling=counselling,
                           allotment=allotment)


@main.route('/counselling/status')
@login_required
def counselling_status_api():
    appl        = StudentApplication.query.filter_by(user_id=current_user.id).first()
    counselling = CounsellingSettings.get()

    admit_categories = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()
    govt_seats = []
    mgmt_seats = []
    for ac in admit_categories:
        entry = {
            'category':  ac.name,
            'total':     ac.total_seats,
            'filled':    ac.seats_used,
            'remaining': ac.seats_remaining,
        }
        if ac.name.upper().endswith('-PY'):
            mgmt_seats.append(entry)
        else:
            govt_seats.append(entry)

    allotment = None
    if appl:
        a = SeatAllotment.query.filter_by(application_id=appl.id).first()
        if a:
            allotment = {
                'quota':       a.quota,
                'allotted_at': fmt_ist(a.allotted_at)
            }

    # FIXED: current_student info for live display
    current_student = None
    if counselling.current_rank:
        cs_appl = StudentApplication.query.filter_by(
            rank=counselling.current_rank, is_verified=True
        ).first()
        if cs_appl:
            cs_allot = SeatAllotment.query.filter_by(application_id=cs_appl.id).first()
            current_student = {
                'name':        cs_appl.full_name,
                'percent_12':  cs_appl.percent_12,
                'category':    cs_appl.category_name,
                'allotted':    cs_allot is not None,
                'allot_quota': cs_allot.quota if cs_allot else None,
            }

    return jsonify({
        'status':           counselling.status,
        'current_rank':     counselling.current_rank,
        'message':          counselling.message,
        'my_rank':          appl.rank if appl else None,
        'govt_seats':       govt_seats,
        'mgmt_seats':       mgmt_seats,
        'allotment':        allotment,
        'current_student':  current_student,
    })


# ─────────────────────────────────────────
#  Student — Apply
# ─────────────────────────────────────────
@main.route('/apply', methods=['GET', 'POST'])
@login_required
def apply():
    if StudentApplication.query.filter_by(user_id=current_user.id).first():
        flash('You have already submitted your application.', 'info')
        return redirect(url_for('main.my_application'))
    settings  = FormSettings.get()
    now       = datetime.utcnow()
    form_open = settings.is_open
    if settings.open_from  and now < settings.open_from:  form_open = False
    if settings.open_until and now > settings.open_until: form_open = False
    if not form_open:
        return render_template('form_closed.html', settings=settings)
    form       = ApplicationForm()
    categories = ApplicationCategory.query.filter_by(is_active=True).all()

    if form.validate_on_submit():
        p12 = calc_percent(form.total_12.data, form.obtained_12.data)

        category_id = request.form.get('category_id')
        if not category_id:
            flash('Please select a category.', 'danger')
            return render_template('application_form.html', form=form, categories=categories)

        appl = StudentApplication(
            user_id              = current_user.id,
            candidate_name       = form.candidate_name.data,
            mother_name          = form.mother_name.data,
            father_name          = form.father_name.data,
            dob                  = form.dob.data,
            gender               = form.gender.data,
            parent_mobile        = form.parent_mobile.data,
            phone                = form.phone.data,
            email                = form.email.data,
            address              = form.address.data,
            nationality          = form.nationality.data,
            religion             = form.religion.data,
            hk_region            = (form.hk_region.data == 'Yes'),
            kannada_medium       = (form.kannada_medium.data == 'Yes'),
            rural_background     = (form.rural_background.data == 'Yes'),
            caste_certificate_no = form.caste_certificate_no.data,
            parent_annual_income = form.parent_annual_income.data,
            income_certificate_no= form.income_certificate_no.data,
            board_10             = form.board_10.data,
            percent_10           = form.percent_10.data,
            board_12             = form.board_12.data,
            stream_12            = form.stream_12.data,
            combination_12       = form.combination_12.data,
            total_12             = form.total_12.data,
            obtained_12          = form.obtained_12.data,
            percent_12           = p12,
            declaration          = form.declaration.data,
            category_id          = int(category_id),
        )
        db.session.add(appl)
        db.session.commit()
        flash('Application submitted successfully!', 'success')
        return redirect(url_for('main.my_application'))
    return render_template('application_form.html', form=form, categories=categories)


@main.route('/my-application')
@login_required
def my_application():
    appl = StudentApplication.query.filter_by(user_id=current_user.id).first()
    if not appl:
        return redirect(url_for('main.apply'))
    return render_template('my_application.html', appl=appl)


# ─────────────────────────────────────────
#  Student — Change Password
# ─────────────────────────────────────────
@main.route('/change-password', methods=['GET', 'POST'])
@login_required
def student_change_password():
    if request.method == 'POST':
        old_password     = request.form.get('old_password', '').strip()
        new_password     = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        if not check_password_hash(current_user.password_hash, old_password):
            flash('Current password is incorrect.', 'danger')
        elif len(new_password) < 8:
            flash('New password must be at least 8 characters.', 'warning')
        elif new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
        elif check_password_hash(current_user.password_hash, new_password):
            flash('New password cannot be same as current.', 'warning')
        else:
            current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('Password updated!', 'success')
            return redirect(url_for('main.student_dashboard'))
    return render_template('change_password.html', role='student',
                           username=current_user.full_name,
                           back_url=url_for('main.student_dashboard'))


# ─────────────────────────────────────────
#  Student — Fee Structure
# ─────────────────────────────────────────
@main.route('/my-fees')
@login_required
def student_fee():
    appl = StudentApplication.query.filter_by(user_id=current_user.id).first()
    if not appl:
        flash('No application found.', 'warning')
        return redirect(url_for('main.student_dashboard'))

    allotment   = SeatAllotment.query.filter_by(application_id=appl.id).first()
    student_fee = StudentFee.query.filter_by(application_id=appl.id).first()

    return render_template('student_fee.html',
                           appl=appl,
                           allotment=allotment,
                           student_fee=student_fee)


# ─────────────────────────────────────────
#  Admin Login / Logout
# ─────────────────────────────────────────
@main.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('main.admin_dashboard'))
    if request.method == 'POST':
        u = request.form.get('username', '')
        p = request.form.get('password', '')
        if (u == current_app.config['ADMIN_USERNAME'] and
                p == current_app.config['ADMIN_PASSWORD']):
            session['admin_logged_in'] = True
            session['admin_username']  = u
            return redirect(url_for('main.admin_dashboard'))
        flash('Invalid admin credentials.', 'danger')
    return render_template('admin_login.html')


@main.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('main.admin_login'))


# ─────────────────────────────────────────
#  Admin Dashboard
# ─────────────────────────────────────────
@main.route('/admin')
@admin_required
def admin_dashboard():
    total       = StudentApplication.query.count()
    verified    = StudentApplication.query.filter_by(is_verified=True).count()
    pending     = total - verified
    ranked      = StudentApplication.query.filter(StudentApplication.rank.isnot(None)).count()
    reg_users   = User.query.count()
    settings    = FormSettings.get()
    whatsapp    = WhatsAppSettings.get()
    counselling = CounsellingSettings.get()
    return render_template('admin_dashboard.html',
                           total=total, verified=verified, pending=pending,
                           ranked=ranked, reg_users=reg_users,
                           settings=settings, whatsapp=whatsapp,
                           counselling=counselling)


# ─────────────────────────────────────────
#  Admin — Fee Categories CRUD
# ─────────────────────────────────────────
@main.route('/admin/fee-categories', methods=['GET', 'POST'])
@admin_required
def admin_fee_categories():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name   = request.form.get('name', '').strip()
            amount = request.form.get('amount', '0').strip()
            if not name:
                flash('Fee category name is required.', 'danger')
            else:
                try:
                    amt = float(amount)
                    if amt < 0:
                        raise ValueError
                except ValueError:
                    flash('Please enter a valid amount.', 'danger')
                    return redirect(url_for('main.admin_fee_categories'))
                if FeeCategory.query.filter_by(name=name).first():
                    flash(f'Fee category "{name}" already exists.', 'danger')
                else:
                    db.session.add(FeeCategory(name=name, amount=amt))
                    db.session.commit()
                    flash(f'Fee category "{name}" added.', 'success')

        elif action == 'edit':
            cat_id = request.form.get('cat_id')
            cat    = FeeCategory.query.get_or_404(int(cat_id))
            name   = request.form.get('name', '').strip()
            amount = request.form.get('amount', '0').strip()
            if not name:
                flash('Name is required.', 'danger')
            else:
                try:
                    amt = float(amount)
                    if amt < 0:
                        raise ValueError
                except ValueError:
                    flash('Please enter a valid amount.', 'danger')
                    return redirect(url_for('main.admin_fee_categories'))
                existing = FeeCategory.query.filter_by(name=name).first()
                if existing and existing.id != cat.id:
                    flash(f'Name "{name}" already in use.', 'danger')
                else:
                    cat.name       = name
                    cat.amount     = amt
                    cat.updated_at = ist_now_naive()
                    db.session.commit()
                    flash('Fee category updated.', 'success')

        elif action == 'delete':
            cat_id = request.form.get('cat_id')
            cat    = FeeCategory.query.get_or_404(int(cat_id))
            in_use = StudentFee.query.filter_by(fee_category_id=cat.id).count()
            if in_use > 0:
                flash(f'Cannot delete "{cat.name}" — assigned to {in_use} student(s).', 'danger')
            else:
                db.session.delete(cat)
                db.session.commit()
                flash(f'Fee category "{cat.name}" deleted.', 'success')

        elif action == 'toggle':
            cat_id        = request.form.get('cat_id')
            cat           = FeeCategory.query.get_or_404(int(cat_id))
            cat.is_active = not cat.is_active
            db.session.commit()
            flash(f'Fee category {"activated" if cat.is_active else "deactivated"}.', 'success')

        return redirect(url_for('main.admin_fee_categories'))

    categories = FeeCategory.query.order_by(FeeCategory.created_at.desc()).all()
    return render_template('admin_fee_categories.html', categories=categories)


# ─────────────────────────────────────────
#  Admin — AJAX fee total preview
# ─────────────────────────────────────────
@main.route('/admin/counselling/fee-total')
@admin_required
def admin_counselling_fee_total():
    fee_cat_id     = request.args.get('fee_cat_id', type=int)
    additional_fee = request.args.get('additional', 0.0, type=float)
    base           = 0.0
    if fee_cat_id:
        fc   = FeeCategory.query.get(fee_cat_id)
        base = fc.amount if fc else 0.0
    return jsonify({'base': base, 'additional': additional_fee, 'total': base + additional_fee})


# ─────────────────────────────────────────
#  Admin — Faculty Credentials Management
# ─────────────────────────────────────────
@main.route('/admin/faculty', methods=['GET', 'POST'])
@admin_required
def admin_faculty():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            full_name = request.form.get('full_name', '').strip()
            username  = request.form.get('username', '').strip()
            password  = request.form.get('password', '').strip()
            if not full_name or not username or not password:
                flash('All fields are required.', 'danger')
            elif Faculty.query.filter_by(username=username).first():
                flash(f'Username "{username}" already exists.', 'danger')
            elif len(password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
            else:
                db.session.add(Faculty(
                    full_name     = full_name,
                    username      = username,
                    password_hash = generate_password_hash(password),
                    is_active     = True,
                ))
                db.session.commit()
                flash(f'Faculty "{full_name}" added successfully!', 'success')

        elif action == 'toggle':
            fac_id  = request.form.get('fac_id')
            faculty = Faculty.query.get_or_404(int(fac_id))
            faculty.is_active = not faculty.is_active
            db.session.commit()
            status = 'activated' if faculty.is_active else 'deactivated'
            flash(f'Faculty "{faculty.full_name}" {status}.', 'info')

        elif action == 'reset_password':
            fac_id       = request.form.get('fac_id')
            new_password = request.form.get('new_password', '').strip()
            faculty      = Faculty.query.get_or_404(int(fac_id))
            if len(new_password) < 6:
                flash('Password must be at least 6 characters.', 'danger')
            else:
                faculty.password_hash = generate_password_hash(new_password)
                db.session.commit()
                flash(f'Password reset for "{faculty.full_name}".', 'success')

        elif action == 'delete':
            fac_id  = request.form.get('fac_id')
            faculty = Faculty.query.get_or_404(int(fac_id))
            name    = faculty.full_name
            db.session.delete(faculty)
            db.session.commit()
            flash(f'Faculty "{name}" deleted.', 'info')

        return redirect(url_for('main.admin_faculty'))

    faculties = Faculty.query.order_by(Faculty.created_at.desc()).all()
    return render_template('admin_faculty.html', faculties=faculties)


# ─────────────────────────────────────────
#  Admin — Payment Overview
# ─────────────────────────────────────────
@main.route('/admin/payments')
@admin_required
def admin_payments():
    allotments    = SeatAllotment.query.all()
    students_data = []
    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue
        fees     = StudentFees.query.filter_by(application_id=appl.id).first()
        receipts = PaymentReceipt.query.filter_by(application_id=appl.id)\
                     .order_by(PaymentReceipt.created_at.asc()).all()
        students_data.append({
            'appl':      appl,
            'allotment': a,
            'fees':      fees,
            'receipts':  receipts,
        })
    students_data.sort(key=lambda x: (x['appl'].rank or 9999))
    return render_template('admin_payments.html', students_data=students_data)


# ─────────────────────────────────────────
#  Admin — Set / Update Total Fees
# ─────────────────────────────────────────
@main.route('/admin/payments/<int:app_id>/set-fees', methods=['POST'])
@admin_required
def admin_set_fees(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    if not SeatAllotment.query.filter_by(application_id=appl.id).first():
        flash('This student does not have a seat allotment.', 'danger')
        return redirect(url_for('main.admin_payments'))

    total_fees_str = request.form.get('total_fees', '').strip()
    try:
        total_fees = float(total_fees_str)
        if total_fees < 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid fee amount.', 'danger')
        return redirect(url_for('main.admin_payments'))

    fees = StudentFees.query.filter_by(application_id=appl.id).first()
    if fees:
        fees.total_fees = total_fees
        fees.updated_at = ist_now_naive()
    else:
        fees = StudentFees(application_id=appl.id, total_fees=total_fees)
        db.session.add(fees)
    db.session.commit()
    flash(f'Total fees updated for {appl.full_name}.', 'success')
    return redirect(url_for('main.admin_payments'))


# ─────────────────────────────────────────
#  Admin — Add Payment Receipt
# ─────────────────────────────────────────
@main.route('/admin/payments/<int:app_id>/add-receipt', methods=['POST'])
@admin_required
def admin_add_receipt(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    fees = StudentFees.query.filter_by(application_id=appl.id).first()
    if not fees:
        flash('Please set total fees for this student first.', 'warning')
        return redirect(url_for('main.admin_payments'))

    receipt_number = request.form.get('receipt_number', '').strip()
    amount_str     = request.form.get('amount_paid', '').strip()

    if not receipt_number:
        flash('Receipt number is required.', 'danger')
        return redirect(url_for('main.admin_payments'))

    try:
        amount_paid = float(amount_str)
        if amount_paid <= 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid amount.', 'danger')
        return redirect(url_for('main.admin_payments'))

    db.session.add(PaymentReceipt(
        fees_id        = fees.id,
        application_id = appl.id,
        faculty_id     = None,
        receipt_number = receipt_number,
        amount_paid    = amount_paid,
    ))
    db.session.commit()
    flash(f'Receipt #{receipt_number} added for {appl.full_name}.', 'success')
    return redirect(url_for('main.admin_payments'))


# ─────────────────────────────────────────
#  Admin — Edit Payment Receipt
# ─────────────────────────────────────────
@main.route('/admin/payments/receipt/<int:receipt_id>/edit', methods=['POST'])
@admin_required
def admin_edit_receipt(receipt_id):
    receipt        = PaymentReceipt.query.get_or_404(receipt_id)
    receipt_number = request.form.get('receipt_number', '').strip()
    amount_str     = request.form.get('amount_paid', '').strip()

    if not receipt_number:
        flash('Receipt number is required.', 'danger')
        return redirect(url_for('main.admin_payments'))

    try:
        amount_paid = float(amount_str)
        if amount_paid <= 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid amount.', 'danger')
        return redirect(url_for('main.admin_payments'))

    receipt.receipt_number = receipt_number
    receipt.amount_paid    = amount_paid
    receipt.updated_at     = ist_now_naive()
    db.session.commit()
    flash(f'Receipt #{receipt_number} updated.', 'success')
    return redirect(url_for('main.admin_payments'))


# ─────────────────────────────────────────
#  Admin — Delete Payment Receipt
# ─────────────────────────────────────────
@main.route('/admin/payments/receipt/<int:receipt_id>/delete', methods=['POST'])
@admin_required
def admin_delete_receipt(receipt_id):
    receipt        = PaymentReceipt.query.get_or_404(receipt_id)
    receipt_number = receipt.receipt_number
    db.session.delete(receipt)
    db.session.commit()
    flash(f'Receipt #{receipt_number} deleted.', 'info')
    return redirect(url_for('main.admin_payments'))


# ─────────────────────────────────────────
#  Admin — Export: Payment Overview Excel
# ─────────────────────────────────────────
@main.route('/admin/export/payments')
@admin_required
def export_payments():
    from utils.exports import export_payments_excel
    allotments    = SeatAllotment.query.all()
    students_data = []
    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue
        fees     = StudentFees.query.filter_by(application_id=appl.id).first()
        receipts = PaymentReceipt.query.filter_by(application_id=appl.id)\
                     .order_by(PaymentReceipt.created_at.asc()).all()
        students_data.append({
            'appl':      appl,
            'allotment': a,
            'fees':      fees,
            'receipts':  receipts,
        })
    return export_payments_excel(students_data, 'payment_overview.xlsx')


# ─────────────────────────────────────────
#  Admin — Export: Payment Receipts Detail Excel
#  FIXED: IST display using fmt_ist()
# ─────────────────────────────────────────
@main.route('/admin/export/payment-receipts')
@admin_required
def export_payment_receipts():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    allotments = SeatAllotment.query.all()
    rows = []

    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue
        fees     = StudentFees.query.filter_by(application_id=appl.id).first()
        receipts = PaymentReceipt.query.filter_by(application_id=appl.id)\
                     .order_by(PaymentReceipt.created_at.asc()).all()

        total_paid = sum(r.amount_paid for r in receipts) if receipts else 0
        total_fees = float(fees.total_fees) if fees and fees.total_fees else 0
        remaining  = total_fees - total_paid if fees and fees.total_fees else ''

        if receipts:
            for r in receipts:
                # FIXED: use utc_to_ist (which now just labels IST correctly)
                receipt_ist = utc_to_ist(r.created_at)
                rows.append({
                    'Rank':               appl.rank or '',
                    'Student Name':       appl.full_name,
                    'Email':              appl.email,
                    'Phone':              appl.phone or '',
                    'Quota':              a.quota.title() if a.quota else '',
                    'Category':           appl.category_name or '',
                    'Total Fees (₹)':     total_fees if fees and fees.total_fees else '',
                    'Receipt No':         r.receipt_number,
                    'Amount Paid (₹)':    r.amount_paid,
                    'Receipt Date (IST)': receipt_ist.strftime('%d %b %Y') if receipt_ist else '',
                    'Receipt Time (IST)': receipt_ist.strftime('%I:%M %p') if receipt_ist else '',
                    'Total Paid So Far (₹)': total_paid,
                    'Remaining (₹)':      remaining if remaining != '' else '',
                })
        else:
            rows.append({
                'Rank':               appl.rank or '',
                'Student Name':       appl.full_name,
                'Email':              appl.email,
                'Phone':              appl.phone or '',
                'Quota':              a.quota.title() if a.quota else '',
                'Category':           appl.category_name or '',
                'Total Fees (₹)':     total_fees if fees and fees.total_fees else '',
                'Receipt No':         '—',
                'Amount Paid (₹)':    0,
                'Receipt Date (IST)': '—',
                'Receipt Time (IST)': '—',
                'Total Paid So Far (₹)': 0,
                'Remaining (₹)':      remaining if remaining != '' else '',
            })

    rows.sort(key=lambda x: (x['Rank'] if isinstance(x['Rank'], int) else 9999))

    wb  = Workbook()
    ws  = wb.active
    ws.title = 'Payment Receipts'

    header_fill = PatternFill('solid', fgColor='0F4C75')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'),  bottom=Side(style='thin')
    )

    headers = list(rows[0].keys()) if rows else [
        'Rank', 'Student Name', 'Email', 'Phone', 'Quota', 'Category',
        'Total Fees (₹)', 'Receipt No', 'Amount Paid (₹)',
        'Receipt Date (IST)', 'Receipt Time (IST)', 'Total Paid So Far (₹)', 'Remaining (₹)'
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.border    = thin_border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 30

    alt_fill     = PatternFill('solid', fgColor='E8F4FD')
    cleared_fill = PatternFill('solid', fgColor='D1FAE5')
    for row_idx, row_data in enumerate(rows, 2):
        rem = row_data.get('Remaining (₹)', '')
        if rem != '' and isinstance(rem, (int, float)) and rem <= 0:
            fill = cleared_fill
        elif row_idx % 2 == 0:
            fill = alt_fill
        else:
            fill = None

        for col_idx, key in enumerate(headers, 1):
            cell        = ws.cell(row=row_idx, column=col_idx, value=row_data.get(key, ''))
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')
            if fill:
                cell.fill = fill

    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        max_len    = len(str(header))
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 38)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='payment_receipts_detail.xlsx')


# ─────────────────────────────────────────
#  Admin — Export: Document Overview Excel
#  FIXED: IST display using utc_to_ist (corrected)
# ─────────────────────────────────────────
@main.route('/admin/export/document-overview')
@admin_required
def export_document_overview():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    allotments = (SeatAllotment.query
                  .join(StudentApplication, SeatAllotment.application_id == StudentApplication.id)
                  .order_by(StudentApplication.rank)
                  .all())
    doc_categories = DocumentCategory.query.filter_by(is_active=True)\
                       .order_by(DocumentCategory.sort_order, DocumentCategory.id).all()

    all_docs = StudentDocument.query.all()
    doc_map  = {}
    for d in all_docs:
        doc_map.setdefault(d.application_id, {})[d.doc_category_id] = d

    STATUS_LABELS = {
        'not_given':  'Not Given',
        'submitted':  'Submitted',
        'original':   'Original',
        'xerox':      'Xerox',
        'attested':   'Attested',
        'photocopy':  'Photocopy',
    }

    rows = []
    for a in allotments:
        appl      = StudentApplication.query.get(a.application_id)
        appl_docs = doc_map.get(a.application_id, {})

        total     = len(doc_categories)
        submitted = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].status != 'not_given')
        approved  = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].is_approved)

        row = {
            'Rank':         appl.rank or '',
            'Student Name': appl.full_name,
            'Email':        appl.email,
            'Phone':        appl.phone or '',
            'Quota':        a.quota.title() if a.quota else '',
            'Category':     appl.category_name or '',
            'Total Docs':   total,
            'Submitted':    submitted,
            'Approved':     approved,
            'Pending':      total - approved,
        }

        for dc in doc_categories:
            doc = appl_docs.get(dc.id)
            if doc:
                status_label   = STATUS_LABELS.get(doc.status, doc.status or 'Not Given')
                approved_label = 'Yes' if doc.is_approved else 'No'
                approved_by    = doc.approved_by_name or ''
                # FIXED: correct IST label
                approved_at = fmt_ist(doc.approved_at) if doc.approved_at else ''
            else:
                status_label   = 'Not Given'
                approved_label = 'No'
                approved_by    = ''
                approved_at    = ''

            row[f'{dc.name} — Status']      = status_label
            row[f'{dc.name} — Approved']    = approved_label
            row[f'{dc.name} — Approved By'] = approved_by
            row[f'{dc.name} — Approved At'] = approved_at

        rows.append(row)

    wb  = Workbook()
    ws  = wb.active
    ws.title = 'Document Overview'

    header_fill     = PatternFill('solid', fgColor='145A32')
    sub_header_fill = PatternFill('solid', fgColor='1E8449')
    header_font     = Font(color='FFFFFF', bold=True, size=11)
    sub_font        = Font(color='FFFFFF', bold=True, size=10)
    thin_border     = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'),  bottom=Side(style='thin')
    )

    headers = list(rows[0].keys()) if rows else ['Rank', 'Student Name', 'Email', 'Phone',
                                                  'Quota', 'Category', 'Total Docs',
                                                  'Submitted', 'Approved', 'Pending']

    fixed_cols = ['Rank', 'Student Name', 'Email', 'Phone', 'Quota', 'Category',
                  'Total Docs', 'Submitted', 'Approved', 'Pending']

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        if header in fixed_cols:
            cell.font = header_font
            cell.fill = header_fill
        else:
            cell.font = sub_font
            cell.fill = sub_header_fill
        cell.border    = thin_border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 36

    alt_fill      = PatternFill('solid', fgColor='EAFAF1')
    complete_fill = PatternFill('solid', fgColor='D5F5E3')

    for row_idx, row_data in enumerate(rows, 2):
        is_complete = row_data.get('Pending', 1) == 0
        base_fill   = complete_fill if is_complete else (alt_fill if row_idx % 2 == 0 else None)

        for col_idx, key in enumerate(headers, 1):
            val  = row_data.get(key, '')
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border    = thin_border
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if base_fill:
                cell.fill = base_fill
            if key.endswith('— Approved'):
                if val == 'Yes':
                    cell.font = Font(color='145A32', bold=True)
                else:
                    cell.font = Font(color='922B21')

    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        max_len    = len(str(header))
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 30)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='document_overview.xlsx')


# ─────────────────────────────────────────
#  Admin — Export: Admitted Students Excel
#  FIXED: IST display + Govt first then Mgmt sort
# ─────────────────────────────────────────
@main.route('/admin/export/admitted-students')
@admin_required
def export_admitted_students():
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    allotments = (SeatAllotment.query
                  .join(AdmitCategory, SeatAllotment.admit_category_id == AdmitCategory.id)
                  .filter(
                      SeatAllotment.admit_category_id.isnot(None),
                      SeatAllotment.admit_seat_number.isnot(None)
                  )
                  .order_by(AdmitCategory.sort_order.asc(), SeatAllotment.admit_seat_number.asc())
                  .all())

    rows = []
    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue

        admit_cat_name = ''
        if a.admit_category_id:
            ac = AdmitCategory.query.get(a.admit_category_id)
            if ac:
                admit_cat_name = ac.name

        fee_cat_name    = ''
        fee_base_amount = ''
        additional_fee  = 0
        total_fee       = ''
        sf = StudentFee.query.filter_by(application_id=appl.id).first()
        if sf:
            if sf.fee_category_id:
                fc = FeeCategory.query.get(sf.fee_category_id)
                if fc:
                    fee_cat_name    = fc.name
                    fee_base_amount = fc.amount
            additional_fee = sf.additional_fee or 0
            base           = fee_base_amount if fee_base_amount != '' else 0
            total_fee      = base + additional_fee

        fees     = StudentFees.query.filter_by(application_id=appl.id).first()
        receipts = PaymentReceipt.query.filter_by(application_id=appl.id)\
                     .order_by(PaymentReceipt.created_at.asc()).all()
        total_paid = sum(r.amount_paid for r in receipts) if receipts else 0
        balance    = (float(fees.total_fees) - total_paid) if fees and fees.total_fees else ''

        doc_categories   = DocumentCategory.query.filter_by(is_active=True).all()
        all_student_docs = StudentDocument.query.filter_by(application_id=appl.id).all()
        doc_status_map   = {d.doc_category_id: d for d in all_student_docs}
        submitted_docs   = sum(1 for dc in doc_categories
                               if doc_status_map.get(dc.id) and
                               doc_status_map[dc.id].status != 'not_given')
        approved_docs    = sum(1 for dc in doc_categories
                               if doc_status_map.get(dc.id) and
                               doc_status_map[dc.id].is_approved)

        # FIXED: correct IST label
        allotted_str = fmt_ist(a.allotted_at)

        rows.append({
            'Admit Category':        admit_cat_name,
            'Seat No':               a.admit_seat_number or '',
            'Rank':                  appl.rank or '',
            'Full Name':             appl.full_name,
            'Email':                 appl.email,
            'Phone':                 appl.phone or '',
            'DOB':                   appl.dob or '',
            'Gender':                appl.gender or '',
            'Category':              appl.category_name or '',
            'Quota':                 a.quota.title() if a.quota else '',
            'Address':               appl.address or '',
            'Nationality':           appl.nationality or '',
            'Allotted At (IST)':     allotted_str,
            'Fee Structure':         fee_cat_name,
            'Base Fee (₹)':          fee_base_amount if fee_base_amount != '' else '',
            'Additional Fee (₹)':    additional_fee,
            'Total Fee (₹)':         total_fee if total_fee != '' else '',
            'Total Fees Set (₹)':    float(fees.total_fees) if fees and fees.total_fees else '',
            'Total Paid (₹)':        total_paid if receipts else '',
            'Balance Remaining (₹)': balance,
            'Docs Submitted':        submitted_docs,
            'Docs Approved':         approved_docs,
        })

    wb  = Workbook()
    ws  = wb.active
    ws.title = 'Admitted Students'

    header_fill = PatternFill('solid', fgColor='145A32')
    header_font = Font(color='FFFFFF', bold=True, size=11)
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'),  bottom=Side(style='thin')
    )

    headers = list(rows[0].keys()) if rows else [
        'Admit Category', 'Seat No', 'Rank', 'Full Name', 'Email', 'Phone',
        'DOB', 'Gender', 'Category', 'Quota', 'Address', 'Nationality',
        'Allotted At (IST)', 'Fee Structure',
        'Base Fee (₹)', 'Additional Fee (₹)', 'Total Fee (₹)',
        'Total Fees Set (₹)', 'Total Paid (₹)', 'Balance Remaining (₹)',
        'Docs Submitted', 'Docs Approved'
    ]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.border    = thin_border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 30

    alt_fill = PatternFill('solid', fgColor='EAFAF1')
    for row_idx, row_data in enumerate(rows, 2):
        fill = alt_fill if row_idx % 2 == 0 else None
        for col_idx, key in enumerate(headers, 1):
            cell        = ws.cell(row=row_idx, column=col_idx, value=row_data.get(key, ''))
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center')
            if fill:
                cell.fill = fill

    for col_idx, header in enumerate(headers, 1):
        col_letter = get_column_letter(col_idx)
        max_len    = len(str(header))
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 40)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='admitted_students.xlsx')


# ─────────────────────────────────────────
#  Admin — Counselling Management
# ─────────────────────────────────────────
@main.route('/admin/counselling', methods=['GET', 'POST'])
@admin_required
def admin_counselling():
    if request.method == 'POST':
        cs     = CounsellingSettings.get()
        action = request.form.get('action')

        if action == 'start':
            cs.status       = 'running'
            cs.started_at   = ist_now_naive()
            cs.current_rank = None
            cs.updated_at   = ist_now_naive()
            db.session.commit()
            flash('Counselling started!', 'success')

        elif action == 'stop':
            cs.status     = 'stopped'
            cs.updated_at = ist_now_naive()
            db.session.commit()
            flash('Counselling stopped.', 'info')

        elif action == 'resume':
            cs.status     = 'running'
            cs.updated_at = ist_now_naive()
            db.session.commit()
            flash('Counselling resumed!', 'success')

        elif action == 'pause':
            cs.status     = 'paused'
            cs.updated_at = ist_now_naive()
            db.session.commit()
            flash('Counselling paused.', 'warning')

        elif action == 'update_rank':
            rank_val        = request.form.get('current_rank', '').strip()
            msg_val         = request.form.get('message', '').strip()
            cs.current_rank = int(rank_val) if rank_val.isdigit() else None
            cs.message      = msg_val or None
            cs.updated_at   = ist_now_naive()
            db.session.commit()
            flash('Live rank updated.', 'success')

        elif action == 'next_rank':
            cs.current_rank = (cs.current_rank or 0) + 1
            cs.updated_at   = ist_now_naive()
            db.session.commit()
            flash(f'Now calling Rank #{cs.current_rank}', 'success')

        elif action == 'set_seats':
            cat_id   = request.form.get('cat_id')
            if cat_id:
                seats        = CategorySeats.get_for_category(int(cat_id))
                govt_str     = request.form.get('govt_seats', '').strip()
                mgmt_str     = request.form.get('mgmt_seats', '').strip()
                if govt_str.isdigit():
                    seats.govt_total = int(govt_str)
                if mgmt_str.isdigit():
                    seats.mgmt_total = int(mgmt_str)
                db.session.commit()
                flash('Seats updated for both quotas.', 'success')

        elif action == 'allot_seat':
            app_id            = request.form.get('app_id')
            quota             = request.form.get('quota')
            admit_category_id = request.form.get('admit_category_id')
            fee_category_id   = request.form.get('fee_category_id')
            additional_fee    = request.form.get('additional_fee', '0').strip()
            is_edit           = request.form.get('is_edit') == '1'

            try:
                app_id = int(app_id)
            except (TypeError, ValueError):
                flash('Invalid student ID.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            appl = StudentApplication.query.get(app_id)
            if not appl:
                flash('Student not found.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            if not quota or quota not in ('government', 'management'):
                flash('Please select a quota.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            existing_allotment = SeatAllotment.query.filter_by(application_id=appl.id).first()

            if not admit_category_id:
                flash('Please select an Admit Category.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            try:
                admit_category_id = int(admit_category_id)
            except (TypeError, ValueError):
                flash('Invalid Admit Category.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            admit_cat = AdmitCategory.query.get(admit_category_id)
            if not admit_cat:
                flash('Admit Category not found.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            if admit_cat.seats_remaining <= 0:
                flash(f'❌ No seats remaining in "{admit_cat.name}". Category is full.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            if is_edit and existing_allotment:
                existing_allotment.quota             = quota
                existing_allotment.admit_category_id = admit_cat.id
                existing_allotment.admit_seat_number = admit_cat.seats_used + 1

                try:
                    add_fee = float(additional_fee) if additional_fee else 0.0
                except ValueError:
                    add_fee = 0.0

                fee_cat = None
                if fee_category_id:
                    try:
                        fee_cat = FeeCategory.query.get(int(fee_category_id))
                    except (TypeError, ValueError):
                        pass

                existing_sf = StudentFee.query.filter_by(application_id=appl.id).first()
                if existing_sf:
                    existing_sf.fee_category_id = fee_cat.id if fee_cat else existing_sf.fee_category_id
                    existing_sf.additional_fee  = add_fee
                    existing_sf.updated_at      = ist_now_naive()
                elif fee_cat or add_fee > 0:
                    db.session.add(StudentFee(
                        application_id  = appl.id,
                        fee_category_id = fee_cat.id if fee_cat else None,
                        additional_fee  = add_fee,
                    ))

                db.session.flush()
                _sync_student_fees(appl.id)
                db.session.commit()
                flash(f'✅ Allotment updated for {appl.full_name}.', 'success')

            elif not is_edit and existing_allotment:
                flash(f'⚠️ Seat already allotted to {appl.full_name}. Use the Edit button to change.', 'warning')

            else:
                seat_number = admit_cat.seats_used + 1

                db.session.add(SeatAllotment(
                    application_id    = appl.id,
                    category_id       = appl.category_id,
                    quota             = quota,
                    admit_category_id = admit_cat.id,
                    admit_seat_number = seat_number,
                ))

                try:
                    add_fee = float(additional_fee) if additional_fee else 0.0
                except ValueError:
                    add_fee = 0.0

                fee_cat = None
                if fee_category_id:
                    try:
                        fee_cat = FeeCategory.query.get(int(fee_category_id))
                    except (TypeError, ValueError):
                        pass

                if fee_cat or add_fee > 0:
                    existing_sf = StudentFee.query.filter_by(application_id=appl.id).first()
                    if existing_sf:
                        existing_sf.fee_category_id = fee_cat.id if fee_cat else None
                        existing_sf.additional_fee  = add_fee
                        existing_sf.updated_at      = ist_now_naive()
                    else:
                        db.session.add(StudentFee(
                            application_id  = appl.id,
                            fee_category_id = fee_cat.id if fee_cat else None,
                            additional_fee  = add_fee,
                        ))

                db.session.flush()
                _sync_student_fees(appl.id)
                db.session.commit()
                flash(f'✅ Seat allotted to {appl.full_name} ({quota.title()} Quota) — {admit_cat.name}({seat_number})!', 'success')
                if request.form.get('print_after') == '1':
                    return redirect(url_for('main.allotment_acknowledgement', app_id=appl.id))
        elif action == 'revoke_seat':
            app_id = request.form.get('app_id')
            try:
                app_id = int(app_id)
            except (TypeError, ValueError):
                flash('Invalid student ID.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            allotment = SeatAllotment.query.filter_by(application_id=app_id).first()
            if allotment:
                db.session.delete(allotment)

            sf = StudentFee.query.filter_by(application_id=app_id).first()
            if sf:
                db.session.delete(sf)

            fees = StudentFees.query.filter_by(application_id=app_id).first()
            if fees:
                PaymentReceipt.query.filter_by(fees_id=fees.id).delete()
                db.session.delete(fees)

            PaymentReceipt.query.filter_by(application_id=app_id).delete()

            db.session.commit()
            flash('✅ Seat allotment and fees revoked.', 'info')

        return redirect(url_for('main.admin_counselling'))

    # ── GET ──
    from sqlalchemy import func as sqlfunc

    cs             = CounsellingSettings.get()
    categories     = ApplicationCategory.query.filter_by(is_active=True).all()
    cat_seats      = {cat.id: CategorySeats.get_for_category(cat.id) for cat in categories}
    fee_categories = FeeCategory.query.filter_by(is_active=True).order_by(FeeCategory.name).all()

    search_q  = request.args.get('q', '')

    # Ranked students (optionally filtered by search)
    stu_q = StudentApplication.query.filter(StudentApplication.rank.isnot(None))
    if search_q:
<<<<<<< HEAD
        stu_q = stu_q.filter(
            db.or_(
                StudentApplication.full_name.ilike(f'%{search_q}%'),
                db.cast(StudentApplication.rank, db.String).ilike(f'%{search_q}%'),
=======
        if search_q.isdigit():
            query = query.filter(StudentApplication.rank == int(search_q))
        else:
            query = query.filter(
                StudentApplication.candidate_name.ilike(f'%{search_q}%')
>>>>>>> 89fc6f4477857f33f96f00b3dd8dd32717ec2ac0
            )
        )
    students = stu_q.order_by(StudentApplication.rank.asc()).all()

<<<<<<< HEAD
=======
    students = query.order_by(StudentApplication.rank.asc()).all()

>>>>>>> 89fc6f4477857f33f96f00b3dd8dd32717ec2ac0
    allotments = {a.application_id: a
                  for a in SeatAllotment.query.all()}

    admit_categories_raw = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()
    seat_counts = dict(
        db.session.query(SeatAllotment.admit_category_id,
                         db.func.count(SeatAllotment.id))
        .group_by(SeatAllotment.admit_category_id).all()
    )
    admit_categories = []
    for ac in admit_categories_raw:
        used      = seat_counts.get(ac.id, 0)
        remaining = max(0, ac.total_seats - used)
        admit_categories.append({
            'id': ac.id, 'name': ac.name,
            'total_seats': ac.total_seats,
            'seats_used': used,
            'seats_remaining': remaining,
            'is_active': ac.is_active,
        })

    doc_categories_all = DocumentCategory.query.filter_by(is_active=True)\
        .order_by(DocumentCategory.sort_order, DocumentCategory.id).all()

    student_ids = [s.id for s in students]
    counselling_doc_map = {}
    if student_ids:
        relevant_docs = StudentDocument.query.filter(
            StudentDocument.application_id.in_(student_ids)
        ).all()
        for d in relevant_docs:
            counselling_doc_map.setdefault(d.application_id, {})[d.doc_category_id] = {
                'status':      d.status or 'not_given',
                'is_approved': d.is_approved,
            }

    return render_template('admin_counselling.html',
                           cs=cs, categories=categories,
                           cat_seats=cat_seats, students=students,
                           allotments=allotments,
                           admit_categories=admit_categories,
                           fee_categories=fee_categories,
                           search_q=search_q,
                           doc_categories=doc_categories_all,
                           counselling_doc_map=counselling_doc_map)


# ─────────────────────────────────────────
#  Admin — Counselling Student Modal (AJAX)
# ─────────────────────────────────────────
@main.route('/admin/counselling/student-modal/<int:app_id>')
@admin_required
def counselling_student_modal(app_id):
    appl      = StudentApplication.query.get_or_404(app_id)
    allotment = SeatAllotment.query.filter_by(application_id=appl.id).first()
    sf        = appl.student_fee[0] if appl.student_fee else None

    doc_categories = DocumentCategory.query.filter_by(is_active=True)\
        .order_by(DocumentCategory.sort_order, DocumentCategory.id).all()
    student_docs   = StudentDocument.query.filter_by(application_id=appl.id).all()
    appl_docs      = {d.doc_category_id: {'status': d.status or 'not_given',
                                           'is_approved': d.is_approved} for d in student_docs}

    admit_categories_raw = AdmitCategory.query.filter_by(is_active=True)\
        .order_by(AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()).all()
    seat_counts = dict(
        db.session.query(SeatAllotment.admit_category_id,
                         db.func.count(SeatAllotment.id))
        .group_by(SeatAllotment.admit_category_id).all()
    )
    admit_categories = []
    for ac in admit_categories_raw:
        used      = seat_counts.get(ac.id, 0)
        remaining = max(0, ac.total_seats - used)
        admit_categories.append({
            'id': ac.id, 'name': ac.name,
            'total_seats': ac.total_seats,
            'seats_used': used,
            'seats_remaining': remaining,
            'is_active': ac.is_active,
        })

    fee_categories = FeeCategory.query.filter_by(is_active=True)\
        .order_by(FeeCategory.name).all()

    return render_template('_counselling_student_modal.html',
                           appl=appl,
                           allotment=allotment,
                           sf=sf,
                           doc_categories=doc_categories,
                           appl_docs=appl_docs,
                           admit_categories=admit_categories,
                           fee_categories=fee_categories)

# ─────────────────────────────────────────
#  Admin — Allotment Acknowledgement PDF
#  NEW: generates printable PDF split into
#       student copy (top) + college copy (bottom)
# ─────────────────────────────────────────
@main.route('/admin/counselling/acknowledgement/<int:app_id>')
@admin_required
def allotment_acknowledgement(app_id):
    """Render print-ready acknowledgement page in A4 landscape with two copies."""
    appl = StudentApplication.query.get_or_404(app_id)
    allotment = SeatAllotment.query.filter_by(application_id=appl.id).first()
    if not allotment:
        flash('No seat allotment found for this student.', 'danger')
        return redirect(url_for('main.admin_counselling'))

    admit_cat_name = '—'
    if allotment.admit_category_id:
        ac = AdmitCategory.query.get(allotment.admit_category_id)
        if ac:
            admit_cat_name = ac.name

    fees_obj = StudentFees.query.filter_by(application_id=appl.id).first()
    fee_to_be_paid = f"Rs {fees_obj.total_fees:,.2f}" if fees_obj and fees_obj.total_fees else '—'

    admission_date = fmt_ist(allotment.allotted_at, '%d/%m/%Y').replace(' IST', '')
    admission_type = (allotment.quota or '—').title()
    app_number = appl.application_number or f'BCA{str(appl.id).zfill(5)}'

    university_name = current_app.config.get('UNIVERSITY_NAME', 'UNIVERSITY').upper()
    course_year = current_app.config.get('ACK_COURSE_YEAR', '2025 - 2026')
    ack = {
        'university_name': university_name,
        'course_year': course_year,
        'name': appl.full_name or '—',
        'category': appl.category_name or '—',
        'application_number': app_number,
        'rank_number': str(appl.rank or '—'),
        'admit_category': admit_cat_name,
        'admission_type': admission_type,
        'date_of_admission': admission_date,
        'fee_to_be_paid': fee_to_be_paid,
    }

    return render_template('admission_acknowledgement_print.html', ack=ack)


def _generate_document_acknowledgement_pdf(app_id):
    """
    Shared PDF generator for document acknowledgement.
    One A4 page: top half = Student Copy, bottom half = College Copy.
    Both halves: University logo, student name, rank, application number,
    document list with status, student + college signature boxes.
    """
    import io
    from datetime import datetime
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable, Image)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

    appl = StudentApplication.query.get_or_404(app_id)
    
    # Generate Application Number
    application_number = f"BCA/{datetime.now().year}/{str(appl.id).zfill(6)}"

    doc_categories = DocumentCategory.query.filter_by(is_active=True)\
        .order_by(DocumentCategory.sort_order, DocumentCategory.id).all()
    student_docs   = StudentDocument.query.filter_by(application_id=app_id).all()
    doc_status_map = {d.doc_category_id: d for d in student_docs}

    STATUS_DISPLAY = {
        'original':  ('Original',  '#008032'),
        'xerox':     ('Xerox',     '#1a4dcc'),
        'attested':  ('Attested',  '#996600'),
        'not_given': ('Not Given', '#808080'),
        'submitted': ('Submitted', '#006699'),
    }

    university_name = current_app.config.get('UNIVERSITY_NAME', 'University')
    logo_path       = current_app.config.get('UNIVERSITY_LOGO_PATH', None)

    buffer = io.BytesIO()
    page_w, page_h = A4
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        topMargin=0.8*cm,  bottomMargin=0.8*cm,
    )

    styles     = getSampleStyleSheet()
    title_sty  = ParagraphStyle('t',  fontSize=13, fontName='Helvetica-Bold',
                                alignment=TA_CENTER, spaceAfter=2)
    copy_sty   = ParagraphStyle('cp', fontSize=8,  fontName='Helvetica-Bold',
                                alignment=TA_CENTER, textColor=colors.grey)
    label_sty  = ParagraphStyle('lb', fontSize=8,  fontName='Helvetica-Bold',
                                alignment=TA_LEFT)
    value_sty  = ParagraphStyle('vl', fontSize=8,  fontName='Helvetica',
                                alignment=TA_LEFT)
    sign_sty   = ParagraphStyle('sg', fontSize=8,  fontName='Helvetica',
                                alignment=TA_CENTER)
    wm_sty     = ParagraphStyle('wm', fontSize=24, fontName='Helvetica-Bold',
                                textColor=colors.Color(0.88, 0.88, 0.88),
                                alignment=TA_CENTER)
    
    # Header style for table - made larger and bolder to fix blurry issue
    header_sty = ParagraphStyle('header', fontSize=9, fontName='Helvetica-Bold',
                                alignment=TA_CENTER, textColor=colors.white)

    def _half(copy_label):
        elems = []
        wm_text = current_app.config.get('UNIVERSITY_WATERMARK_TEXT',
                                         university_name.upper())
        elems.append(Paragraph(wm_text, wm_sty))

        if logo_path and os.path.exists(logo_path):
            hdr = [[Image(logo_path, width=1.8*cm, height=1.8*cm),
                    Paragraph(f'<b>{university_name}</b><br/>'
                              f'<font size=7>BCA Admissions — Document Receipt</font>',
                              title_sty),
                    '']]
        else:
            hdr = [['',
                    Paragraph(f'<b>{university_name}</b><br/>'
                              f'<font size=7>BCA Admissions — Document Receipt</font>',
                              title_sty),
                    '']]

        hdr_tbl = Table(hdr, colWidths=[2*cm, page_w - 6*cm, 2*cm])
        hdr_tbl.setStyle(TableStyle([
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('ALIGN',  (1,0), (1,0),   'CENTER'),
        ]))
        elems.append(hdr_tbl)
        elems.append(HRFlowable(width='100%', thickness=1.5, color=colors.darkblue))
        elems.append(Spacer(1, 0.15*cm))

        elems.append(Paragraph('DOCUMENT VERIFICATION ACKNOWLEDGEMENT', title_sty))
        elems.append(Paragraph(copy_label, copy_sty))
        elems.append(Spacer(1, 0.2*cm))

        # Updated info table - removed Admit Category, Allotted At, Seat No
        # Added Application Number
        info = [
            ['Student Name', appl.full_name,   'Rank',           str(appl.rank or '—')],
            ['Application No.', application_number, 'Caste Category', appl.category_name or '—'],
        ]
        info_tbl = Table(
            [[Paragraph(str(r[0]), label_sty), Paragraph(str(r[1]), value_sty),
              Paragraph(str(r[2]), label_sty), Paragraph(str(r[3]), value_sty)]
             for r in info],
            colWidths=[3.5*cm, 5.5*cm, 3.5*cm, 4.5*cm]
        )
        info_tbl.setStyle(TableStyle([
            ('GRID',       (0,0), (-1,-1), 0.4, colors.lightgrey),
            ('BACKGROUND', (0,0), (0,-1),  colors.Color(0.93, 0.96, 1.0)),
            ('BACKGROUND', (2,0), (2,-1),  colors.Color(0.93, 0.96, 1.0)),
            ('PADDING',    (0,0), (-1,-1), 5),
        ]))
        elems.append(info_tbl)
        elems.append(Spacer(1, 0.2*cm))

        # Documents table - fixed header to be clearly visible
        doc_rows = [[
            Paragraph('#', header_sty),
            Paragraph('Document Name', header_sty),
            Paragraph('Status', header_sty),
            Paragraph('Approved', header_sty),
            Paragraph('Approved By', header_sty),
        ]]
        for idx, dc in enumerate(doc_categories, 1):
            d            = doc_status_map.get(dc.id)
            status       = d.status if d else 'not_given'
            disp, col    = STATUS_DISPLAY.get(status, ('—', colors.grey))
            approved     = '✓ Yes' if (d and d.is_approved) else '✗ No'
            approved_by  = (d.approved_by_name or '—') if (d and d.is_approved) else '—'
            doc_rows.append([
                Paragraph(str(idx), value_sty),
                Paragraph(dc.name, value_sty),
                Paragraph(f'<font color="{col}">{disp}</font>', value_sty),
                Paragraph(approved, value_sty),
                Paragraph(approved_by, value_sty),
            ])

        doc_tbl = Table(doc_rows, colWidths=[0.7*cm, 5.8*cm, 2.8*cm, 2.2*cm, 3.5*cm])
        doc_tbl.setStyle(TableStyle([
            ('BACKGROUND',   (0,0), (-1,0),  colors.Color(0.15, 0.25, 0.5)),
            ('TEXTCOLOR',    (0,0), (-1,0),  colors.white),
            ('FONTNAME',     (0,0), (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',     (0,0), (-1,0),  9),
            ('ALIGN',        (0,0), (-1,0),  'CENTER'),
            ('VALIGN',       (0,0), (-1,0),  'MIDDLE'),
            ('GRID',         (0,0), (-1,-1), 0.4, colors.lightgrey),
            ('PADDING',      (0,0), (-1,-1), 5),
            ('ROWBACKGROUNDS',(0,1), (-1,-1),
             [colors.white, colors.Color(0.97, 0.97, 1.0)]),
        ]))
        elems.append(doc_tbl)
        elems.append(Spacer(1, 0.3*cm))

        # Dual signature row: student left, college right
        sig_tbl = Table(
            [[Paragraph('Student Signature<br/><br/>___________________________<br/>'
                        f'<font size=7>{appl.full_name}</font>', sign_sty),
              Paragraph('Verified By (College)<br/><br/>___________________________<br/>'
                        '<font size=7>Name &amp; Designation</font>', sign_sty)]],
            colWidths=[9*cm, 8*cm]
        )
        sig_tbl.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'BOTTOM')]))
        elems.append(sig_tbl)
        return elems

    story = []
    story += _half('— STUDENT COPY —')
    story.append(Spacer(1, 0.25*cm))
    story.append(HRFlowable(width='100%', thickness=1, color=colors.grey, dash=[3, 3]))
    story.append(Spacer(1, 0.25*cm))
    story += _half('— COLLEGE COPY —')

    doc.build(story)
    buffer.seek(0)

    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', appl.full_name)
    filename  = f'document_acknowledgement_{safe_name}_rank{appl.rank}.pdf'
    return send_file(buffer, mimetype='application/pdf',
                     as_attachment=False, download_name=filename)
# ─────────────────────────────────────────
#  Admin — Document Acknowledgement PDF Route
# ─────────────────────────────────────────
@main.route('/admin/document-verification/acknowledgement/<int:app_id>')
@admin_required
def document_acknowledgement(app_id):
    return _generate_document_acknowledgement_pdf(app_id)

@main.route('/faculty/document-verification/acknowledgement/<int:app_id>')
@faculty_required
def faculty_document_acknowledgement(app_id):
    return _generate_document_acknowledgement_pdf(app_id)
# ─────────────────────────────────────────
#  Admin — Admit Categories Management
# ─────────────────────────────────────────
@main.route('/admin/admit-categories', methods=['GET', 'POST'])
@admin_required
def admin_admit_categories():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name        = request.form.get('name', '').strip().upper()
            total_seats = request.form.get('total_seats', '0').strip()
            sort_order  = request.form.get('sort_order', '0').strip()
            quota_type  = request.form.get('quota_type', 'government').strip()

            if not name:
                flash('Category name is required.', 'danger')
            elif AdmitCategory.query.filter_by(name=name).first():
                flash(f'Admit category "{name}" already exists.', 'danger')
            else:
                try:
                    total_seats_int = int(total_seats) if total_seats.isdigit() else 0
                    sort_order_int  = int(sort_order)  if sort_order.lstrip('-').isdigit() else 0
                except (ValueError, TypeError):
                    total_seats_int = 0
                    sort_order_int  = 0

                new_cat = AdmitCategory(
                    name        = name,
                    total_seats = total_seats_int,
                    sort_order  = sort_order_int,
                )
                if quota_type == 'management' and not name.endswith('-PY'):
                    new_cat.name = name + '-PY'
                db.session.add(new_cat)
                db.session.commit()
                flash(f'Admit category "{new_cat.name}" added.', 'success')

        elif action == 'edit':
            cat_id      = request.form.get('cat_id')
            cat         = AdmitCategory.query.get_or_404(int(cat_id))
            name        = request.form.get('name', '').strip().upper()
            total_seats = request.form.get('total_seats', '0').strip()
            sort_order  = request.form.get('sort_order', '0').strip()
            quota_type  = request.form.get('quota_type', 'government').strip()

            if not name:
                flash('Category name is required.', 'danger')
            else:
                existing = AdmitCategory.query.filter_by(name=name).first()
                if existing and existing.id != cat.id:
                    flash(f'Name "{name}" already in use.', 'danger')
                else:
                    try:
                        total_seats_int = int(total_seats) if total_seats.isdigit() else cat.total_seats
                        sort_order_int  = int(sort_order)  if sort_order.lstrip('-').isdigit() else cat.sort_order
                    except (ValueError, TypeError):
                        total_seats_int = cat.total_seats
                        sort_order_int  = cat.sort_order

                    if quota_type == 'management' and not name.endswith('-PY'):
                        name = name + '-PY'
                    elif quota_type == 'government' and name.endswith('-PY'):
                        name = name[:-3]

                    cat.name        = name
                    cat.total_seats = total_seats_int
                    cat.sort_order  = sort_order_int
                    db.session.commit()
                    flash('Admit category updated.', 'success')

        elif action == 'delete':
            cat_id = int(request.form.get('cat_id'))
            cat    = AdmitCategory.query.get_or_404(cat_id)
            if cat.seats_used > 0:
                flash(f'Cannot delete "{cat.name}" — {cat.seats_used} seat(s) already allotted under it.', 'danger')
            else:
                db.session.delete(cat)
                db.session.commit()
                flash('Admit category deleted.', 'success')

        elif action == 'toggle':
            cat_id        = int(request.form.get('cat_id'))
            cat           = AdmitCategory.query.get_or_404(cat_id)
            cat.is_active = not cat.is_active
            db.session.commit()
            flash(f'Admit category {"activated" if cat.is_active else "deactivated"}.', 'success')

        return redirect(url_for('main.admin_admit_categories'))

    categories = AdmitCategory.query.order_by(
        AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()
    ).all()
    return render_template('admin_admit_categories.html', categories=categories)


# ─────────────────────────────────────────
#  Admin — Application Categories + Seats
# ─────────────────────────────────────────
@main.route('/admin/categories')
@admin_required
def admin_categories():
    categories = ApplicationCategory.query.order_by(ApplicationCategory.id).all()
    cat_seats  = {cat.id: CategorySeats.get_for_category(cat.id) for cat in categories}
    return render_template('admin_categories.html', categories=categories, cat_seats=cat_seats)


@main.route('/admin/categories/add', methods=['POST'])
@admin_required
def admin_category_add():
    name = request.form.get('category_name', '').strip()
    if not name:
        flash('Category name is required.', 'danger')
        return redirect(url_for('main.admin_categories'))
    if ApplicationCategory.query.filter_by(name=name).first():
        flash(f'Category "{name}" already exists.', 'danger')
        return redirect(url_for('main.admin_categories'))
    new_cat = ApplicationCategory(name=name, is_active=True)
    db.session.add(new_cat)
    db.session.flush()
    db.session.add(CategorySeats(category_id=new_cat.id,
                                 govt_total=0, govt_filled=0,
                                 mgmt_total=0, mgmt_filled=0))
    db.session.commit()
    flash(f'Category "{name}" added!', 'success')
    return redirect(url_for('main.admin_categories'))


@main.route('/admin/categories/edit/<int:cat_id>', methods=['POST'])
@admin_required
def admin_category_edit(cat_id):
    category = ApplicationCategory.query.get_or_404(cat_id)
    new_name = request.form.get('category_name', '').strip()
    if not new_name:
        flash('Category name is required.', 'danger')
        return redirect(url_for('main.admin_categories'))
    existing = ApplicationCategory.query.filter_by(name=new_name).first()
    if existing and existing.id != cat_id:
        flash(f'Category "{new_name}" already exists.', 'danger')
        return redirect(url_for('main.admin_categories'))
    category.name = new_name
    db.session.commit()
    flash(f'Category updated to "{new_name}"!', 'success')
    return redirect(url_for('main.admin_categories'))


@main.route('/admin/categories/set-seats/<int:cat_id>', methods=['POST'])
@admin_required
def admin_category_set_seats(cat_id):
    ApplicationCategory.query.get_or_404(cat_id)
    seats    = CategorySeats.get_for_category(cat_id)
    govt_str = request.form.get('govt_seats', '').strip()
    mgmt_str = request.form.get('mgmt_seats', '').strip()
    if govt_str.isdigit():
        seats.govt_total = int(govt_str)
    if mgmt_str.isdigit():
        seats.mgmt_total = int(mgmt_str)
    db.session.commit()
    flash('Seat allocation updated successfully.', 'success')
    return redirect(url_for('main.admin_categories'))


@main.route('/admin/categories/delete/<int:cat_id>', methods=['POST'])
@admin_required
def admin_category_delete(cat_id):
    category   = ApplicationCategory.query.get_or_404(cat_id)
    name       = category.name
    apps_count = StudentApplication.query.filter_by(category_id=cat_id).count()
    if apps_count > 0:
        flash(f'Cannot delete "{name}" — {apps_count} student(s) are using this category.', 'danger')
    else:
        CategorySeats.query.filter_by(category_id=cat_id).delete()
        db.session.delete(category)
        db.session.commit()
        flash(f'Category "{name}" deleted!', 'success')
    return redirect(url_for('main.admin_categories'))


@main.route('/admin/categories/toggle/<int:cat_id>', methods=['POST'])
@admin_required
def admin_category_toggle(cat_id):
    category           = ApplicationCategory.query.get_or_404(cat_id)
    category.is_active = not category.is_active
    db.session.commit()
    status = "activated" if category.is_active else "deactivated"
    flash(f'Category "{category.name}" {status}!', 'success')
    return redirect(url_for('main.admin_categories'))


# ─────────────────────────────────────────
#  Admin — Admitted Students
# ─────────────────────────────────────────
@main.route('/admin/admitted-students')
@admin_required
def admin_admitted_students():
    admit_categories = AdmitCategory.query.order_by(
        AdmitCategory.sort_order.asc(), AdmitCategory.name.asc()
    ).all()

    all_allotments = SeatAllotment.query.filter(
        SeatAllotment.admit_category_id.isnot(None),
        SeatAllotment.admit_seat_number.isnot(None)
    ).all()

    seat_map = {}
    for a in all_allotments:
        appl     = a.application
        fees_obj = StudentFees.query.filter_by(application_id=appl.id).first()
        a._has_fees     = fees_obj is not None
        a._fees_total   = float(fees_obj.total_fees)        if fees_obj and fees_obj.total_fees  else 0.0
        a._fees_paid    = float(fees_obj.total_paid)        if fees_obj else 0.0
        a._fees_pending = float(fees_obj.balance_remaining) if fees_obj else 0.0
        key = (a.admit_category_id, a.admit_seat_number)
        seat_map[key] = a

    total_seats    = sum(ac.total_seats for ac in admit_categories)
    total_assigned = len(seat_map)
    govt_count     = sum(1 for a in seat_map.values() if a.quota == 'government')
    mgmt_count     = sum(1 for a in seat_map.values() if a.quota == 'management')

    return render_template('admin_admitted_students.html',
                           admit_categories=admit_categories,
                           seat_map=seat_map,
                           total_seats=total_seats,
                           total_assigned=total_assigned,
                           govt_count=govt_count,
                           mgmt_count=mgmt_count)


# ─────────────────────────────────────────
#  Admin — Document Categories (CRUD)
# ─────────────────────────────────────────
@main.route('/admin/document-categories', methods=['GET', 'POST'])
@admin_required
def admin_document_categories():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            name        = request.form.get('name', '').strip()
            desc        = request.form.get('description', '').strip()
            is_required = request.form.get('is_required') == '1'
            sort_order  = int(request.form.get('sort_order', 0))
            if name:
                if DocumentCategory.query.filter_by(name=name).first():
                    flash('A document with that name already exists.', 'warning')
                else:
                    db.session.add(DocumentCategory(name=name, description=desc,
                                                    is_required=is_required, sort_order=sort_order))
                    db.session.commit()
                    flash(f'Document "{name}" added.', 'success')
            else:
                flash('Name is required.', 'danger')

        elif action == 'edit':
            doc_id          = request.form.get('doc_id')
            doc             = DocumentCategory.query.get_or_404(doc_id)
            doc.name        = request.form.get('name', doc.name).strip()
            doc.description = request.form.get('description', '').strip()
            doc.is_required = request.form.get('is_required') == '1'
            doc.sort_order  = int(request.form.get('sort_order', 0))
            db.session.commit()
            flash('Document updated.', 'success')

        elif action == 'delete':
            doc_id = request.form.get('doc_id')
            doc    = DocumentCategory.query.get_or_404(doc_id)
            StudentDocument.query.filter_by(doc_category_id=doc.id).delete()
            db.session.delete(doc)
            db.session.commit()
            flash(f'Document "{doc.name}" deleted.', 'success')

        elif action == 'toggle':
            doc_id          = request.form.get('doc_id')
            doc             = DocumentCategory.query.get_or_404(doc_id)
            doc.is_active   = not doc.is_active
            db.session.commit()
            flash('Status updated.', 'success')

        return redirect(url_for('main.admin_document_categories'))

    docs = DocumentCategory.query.order_by(DocumentCategory.sort_order, DocumentCategory.created_at).all()
    return render_template('admin_document_categories.html', docs=docs)


# ─────────────────────────────────────────
#  Admin — Document Verification
#  FIXED: fetch ALL ranked students, not just allotted
# ─────────────────────────────────────────
@main.route('/admin/document-verification')
@admin_required
def admin_document_verification():
    # FIXED: all verified + ranked students (not just allotted)
    students = (StudentApplication.query
                .filter_by(is_verified=True)
                .filter(StudentApplication.rank.isnot(None))
                .order_by(StudentApplication.rank.asc())
                .all())

    doc_categories = DocumentCategory.query.filter_by(is_active=True).order_by(
        DocumentCategory.sort_order, DocumentCategory.id).all()

    all_docs = StudentDocument.query.all()
    doc_map  = {}
    for d in all_docs:
        doc_map.setdefault(d.application_id, {})[d.doc_category_id] = d

    # Build allotment map for display
    allotment_map = {a.application_id: a for a in SeatAllotment.query.all()}

    summaries = {}
    for appl in students:
        appl_docs = doc_map.get(appl.id, {})
        total     = len(doc_categories)
        submitted = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].status != 'not_given')
        approved  = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].is_approved)
        summaries[appl.id] = {
            'total': total, 'submitted': submitted, 'approved': approved,
        }

    return render_template('admin_document_verification.html',
                           students=students,
                           allotment_map=allotment_map,
                           doc_categories=doc_categories,
                           doc_map=doc_map,
                           summaries=summaries)


@main.route('/admin/document-verification/save', methods=['POST'])
@admin_required
def admin_save_document():
    application_id = request.form.get('application_id', type=int)
    approve_all    = request.form.get('approve_all') == '1'
    doc_categories = DocumentCategory.query.filter_by(is_active=True).all()

    for dc in doc_categories:
        status = request.form.get(f'status_{dc.id}', 'not_given')
        rec    = StudentDocument.query.filter_by(
            application_id=application_id, doc_category_id=dc.id).first()
        if not rec:
            rec = StudentDocument(application_id=application_id, doc_category_id=dc.id)
            db.session.add(rec)
        rec.status = status
        if approve_all:
            rec.is_approved            = True
            rec.approved_at            = ist_now_naive()
            rec.approved_by_role       = 'admin'
            rec.approved_by_name       = 'Admin'
            rec.approved_by_faculty_id = None

    db.session.commit()
    flash('Documents saved successfully.', 'success')

    print_after = request.form.get('print_after_save') == '1'
    app_id = request.form.get('application_id', type=int)
    if print_after and app_id:
        return redirect(url_for('main.admin_document_verification', print_app_id=app_id))
    return redirect(url_for('main.admin_document_verification'))


# ─────────────────────────────────────────
#  Admin — Document Overview
# ─────────────────────────────────────────
@main.route('/admin/document-overview')
@admin_required
def admin_document_overview():
    students = (StudentApplication.query
                .filter_by(is_verified=True)
                .filter(StudentApplication.rank.isnot(None))
                .order_by(StudentApplication.rank.asc())
                .all())

    doc_categories = DocumentCategory.query.filter_by(is_active=True).order_by(
        DocumentCategory.sort_order, DocumentCategory.id).all()

    all_docs = StudentDocument.query.all()
    doc_map  = {}
    for d in all_docs:
        doc_map.setdefault(d.application_id, {})[d.doc_category_id] = d

    allotment_map = {a.application_id: a for a in SeatAllotment.query.all()}

    summaries = {}
    for appl in students:
        appl_docs = doc_map.get(appl.id, {})
        total     = len(doc_categories)
        submitted = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].status != 'not_given')
        approved  = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].is_approved)
        summaries[appl.id] = {
            'total': total, 'submitted': submitted, 'approved': approved
        }

    return render_template('admin_document_overview.html',
                           students=students,
                           allotment_map=allotment_map,
                           doc_categories=doc_categories,
                           doc_map=doc_map,
                           summaries=summaries)

# ─────────────────────────────────────────
#  Faculty — Document Verification
#  FIXED: fetch ALL ranked students, not just allotted
# ─────────────────────────────────────────
@main.route('/faculty/document-verification')
@faculty_required
def faculty_document_verification():
    # FIXED: all verified + ranked students
    students = (StudentApplication.query
                .filter_by(is_verified=True)
                .filter(StudentApplication.rank.isnot(None))
                .order_by(StudentApplication.rank.asc())
                .all())

    doc_categories = DocumentCategory.query.filter_by(is_active=True).order_by(
        DocumentCategory.sort_order, DocumentCategory.id).all()

    all_docs = StudentDocument.query.all()
    doc_map  = {}
    for d in all_docs:
        doc_map.setdefault(d.application_id, {})[d.doc_category_id] = d

    allotment_map = {a.application_id: a for a in SeatAllotment.query.all()}

    summaries = {}
    for appl in students:
        appl_docs = doc_map.get(appl.id, {})
        total     = len(doc_categories)
        submitted = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].status != 'not_given')
        approved  = sum(1 for dc in doc_categories
                        if appl_docs.get(dc.id) and appl_docs[dc.id].is_approved)
        summaries[appl.id] = {
            'total': total, 'submitted': submitted, 'approved': approved,
        }

    return render_template('faculty_document_verification.html',
                           students=students,
                           allotment_map=allotment_map,
                           doc_categories=doc_categories,
                           doc_map=doc_map,
                           summaries=summaries)


@main.route('/faculty/document-verification/save', methods=['POST'])
@faculty_required
def faculty_save_document():
    application_id  = request.form.get('application_id', type=int)
    approve_all     = request.form.get('approve_all') == '1'
    faculty_id      = session.get('faculty_id')
    faculty_name    = session.get('faculty_name', 'Faculty')
    doc_categories  = DocumentCategory.query.filter_by(is_active=True).all()

    for dc in doc_categories:
        status = request.form.get(f'status_{dc.id}', 'not_given')
        rec    = StudentDocument.query.filter_by(
            application_id=application_id, doc_category_id=dc.id).first()
        if not rec:
            rec = StudentDocument(application_id=application_id, doc_category_id=dc.id)
            db.session.add(rec)
        rec.status = status
        if approve_all:
            rec.is_approved            = True
            rec.approved_at            = ist_now_naive()
            rec.approved_by_role       = 'faculty'
            rec.approved_by_name       = faculty_name
            rec.approved_by_faculty_id = faculty_id

    db.session.commit()
    flash('Documents saved successfully.', 'success')
    return redirect(url_for('main.faculty_document_verification'))


# ─────────────────────────────────────────
#  Admin — WhatsApp Group Management
# ─────────────────────────────────────────
@main.route('/admin/whatsapp', methods=['GET', 'POST'])
@admin_required
def admin_whatsapp():
    wa = WhatsAppSettings.get()
    if request.method == 'POST':
        action = request.form.get('action')
        if action in ('delete', 'clear_all'):
            wa.link        = None
            wa.description = None
            wa.is_active   = False
            wa.updated_at  = ist_now_naive()
            db.session.commit()
            flash('WhatsApp link cleared.', 'info')
        else:
            link = request.form.get('link', '').strip()
            desc = request.form.get('description', '').strip()
            if not link:
                flash('Please enter a WhatsApp group link.', 'danger')
                return render_template('admin_whatsapp.html', wa=wa)
            if not link.startswith('https://chat.whatsapp.com/'):
                flash('Please enter a valid WhatsApp invite link.', 'danger')
                return render_template('admin_whatsapp.html', wa=wa)
            wa.link        = link
            wa.description = desc
            wa.is_active   = True
            wa.updated_at  = ist_now_naive()
            db.session.commit()
            flash('WhatsApp group link updated!', 'success')
        return redirect(url_for('main.admin_whatsapp'))
    return render_template('admin_whatsapp.html', wa=wa)


# ─────────────────────────────────────────
#  Admin — Change Password
# ─────────────────────────────────────────
@main.route('/admin/change-password', methods=['GET', 'POST'])
@admin_required
def admin_change_password():
    if request.method == 'POST':
        old_password     = request.form.get('old_password', '').strip()
        new_password     = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        current_pw       = current_app.config.get('ADMIN_PASSWORD', '')
        if old_password != current_pw:
            flash('Current password is incorrect.', 'danger')
        elif len(new_password) < 8:
            flash('New password must be at least 8 characters.', 'warning')
        elif new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
        elif new_password == current_pw:
            flash('New password cannot be same as current.', 'warning')
        else:
            current_app.config['ADMIN_PASSWORD'] = new_password
            flash('Password updated! Remember to update your .env file.', 'success')
            return redirect(url_for('main.admin_dashboard'))
    return render_template('change_password.html', role='admin',
                           username=current_app.config.get('ADMIN_USERNAME', 'Admin'),
                           back_url=url_for('main.admin_dashboard'))


# ─────────────────────────────────────────
#  Admin — Students
#  NEW: edit student details, delete single, delete all
# ─────────────────────────────────────────
@main.route('/admin/students')
@admin_required
def admin_students():
    q     = request.args.get('q', '').strip()
    query = StudentApplication.query

    if q:
        like  = f'%{q}%'
        query = query.filter(db.or_(
            StudentApplication.candidate_name.ilike(like),
            StudentApplication.email.ilike(like),
        ))
    students = query.order_by(StudentApplication.submitted_at.desc()).all()
    return render_template('admin_students.html', students=students, q=q)


@main.route('/admin/students/<int:app_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_student(app_id):
    """Edit student application details."""
    appl       = StudentApplication.query.get_or_404(app_id)
    categories = ApplicationCategory.query.filter_by(is_active=True).all()

    if request.method == 'POST':
        appl.candidate_name       = request.form.get('candidate_name', appl.candidate_name).strip()
        appl.mother_name          = request.form.get('mother_name', '').strip()
        appl.father_name          = request.form.get('father_name', '').strip()
        appl.dob                  = request.form.get('dob', appl.dob).strip()
        new_dob = request.form.get('dob', '').strip()
        if new_dob and appl.user_id:
            new_password = _dob_to_password(new_dob)
            if new_password:
                user = User.query.get(appl.user_id)
                if user:
                    user.password_hash = generate_password_hash(new_password)
        appl.gender               = request.form.get('gender', appl.gender)
        appl.phone                = request.form.get('phone', '').strip()
        appl.parent_mobile        = request.form.get('parent_mobile', '').strip()
        appl.email                = request.form.get('email', appl.email).strip().lower()
        appl.address              = request.form.get('address', '').strip()
        appl.nationality          = request.form.get('nationality', 'Indian').strip()
        appl.religion             = request.form.get('religion', '').strip()
        appl.hk_region            = request.form.get('hk_region') == '1'
        appl.kannada_medium       = request.form.get('kannada_medium') == '1'
        appl.rural_background     = request.form.get('rural_background') == '1'
        appl.caste_certificate_no = request.form.get('caste_certificate_no', '').strip()
        appl.parent_annual_income = request.form.get('parent_annual_income', '').strip()
        appl.income_certificate_no= request.form.get('income_certificate_no', '').strip()
        appl.board_10             = request.form.get('board_10', '').strip()
        appl.board_12             = request.form.get('board_12', '').strip()
        appl.stream_12            = request.form.get('stream_12', '').strip()
        appl.combination_12       = request.form.get('combination_12', '').strip()

        cat_id = request.form.get('category_id')
        if cat_id:
            appl.category_id = int(cat_id)

        try:
            appl.percent_10  = float(request.form.get('percent_10', '') or 0)
        except ValueError:
            pass
        try:
            appl.total_12    = float(request.form.get('total_12', '') or 0)
            appl.obtained_12 = float(request.form.get('obtained_12', '') or 0)
            if appl.total_12 and appl.obtained_12:
                appl.percent_12 = round((appl.obtained_12 / appl.total_12) * 100, 2)
        except ValueError:
            pass

        db.session.commit()
        flash(f'Student "{appl.full_name}" updated successfully.', 'success')
        return redirect(url_for('main.admin_students'))

    return render_template('admin_edit_student.html',
                           appl=appl, categories=categories)


@main.route('/admin/students/<int:app_id>/delete', methods=['POST'])
@admin_required
def admin_delete_student(app_id):
    """
    Delete a single student — removes from ALL tables:
    SeatAllotment, StudentDocument, StudentFee, StudentFees,
    PaymentReceipt, StudentApplication, User.
    """
    appl = StudentApplication.query.get_or_404(app_id)
    name = appl.full_name

    # Delete from all child tables first
    SeatAllotment.query.filter_by(application_id=appl.id).delete()
    StudentDocument.query.filter_by(application_id=appl.id).delete()

    # Delete PaymentReceipts via StudentFees
    fees = StudentFees.query.filter_by(application_id=appl.id).first()
    if fees:
        PaymentReceipt.query.filter_by(fees_id=fees.id).delete()
        db.session.delete(fees)

    PaymentReceipt.query.filter_by(application_id=appl.id).delete()
    StudentFee.query.filter_by(application_id=appl.id).delete()

    user_id = appl.user_id
    db.session.delete(appl)
    db.session.flush()

    if user_id:
        user = User.query.get(user_id)
        if user:
            db.session.delete(user)

    db.session.commit()
    flash(f'Student "{name}" and all related data deleted.', 'success')
    return redirect(url_for('main.admin_students'))


@main.route('/admin/students/delete-all', methods=['POST'])
@admin_required
def admin_delete_all_students():
    """
    Delete ALL student applications and all related data.
    Clears: SeatAllotment, StudentDocument, StudentFee, StudentFees,
    PaymentReceipt, StudentApplication, User (student accounts only).
    """
    # Delete in dependency order
    StudentDocument.query.delete()
    SeatAllotment.query.delete()
    PaymentReceipt.query.delete()
    StudentFee.query.delete()
    StudentFees.query.delete()

    # Get user IDs before deleting applications
    user_ids = [a.user_id for a in StudentApplication.query.all() if a.user_id]
    StudentApplication.query.delete()
    db.session.flush()

    # Delete only student users (not admin/faculty accounts)
    for uid in user_ids:
        user = User.query.get(uid)
        if user:
            db.session.delete(user)

    db.session.commit()
    flash('All student applications and related data have been deleted.', 'success')
    return redirect(url_for('main.admin_students'))


# ─────────────────────────────────────────
#  Admin — Verification
# ─────────────────────────────────────────
@main.route('/admin/verification')
@admin_required
def admin_verification():
    pending = StudentApplication.query.filter_by(is_verified=False)\
                .order_by(StudentApplication.submitted_at).all()
    return render_template('admin_verification.html', students=pending)


@main.route('/admin/verify/<int:app_id>', methods=['POST'])
@admin_required
def verify_student(app_id):
    appl             = StudentApplication.query.get_or_404(app_id)
    appl.is_verified = True
    appl.verified_at = ist_now_naive()
    db.session.commit()
    flash(f'{appl.full_name} verified.', 'success')
    return redirect(url_for('main.admin_verification'))


@main.route('/admin/verify-all', methods=['POST'])
@admin_required
def verify_all_students():
    pending_students = StudentApplication.query.filter_by(is_verified=False).all()
    count = 0
    for student in pending_students:
        student.is_verified = True
        student.verified_at = ist_now_naive()
        count += 1
    db.session.commit()
    flash(f'Successfully verified {count} student(s)!', 'success')
    return redirect(url_for('main.admin_verification'))


@main.route('/admin/unverify/<int:app_id>', methods=['POST'])
@admin_required
def unverify_student(app_id):
    appl             = StudentApplication.query.get_or_404(app_id)
    appl.is_verified = False
    appl.verified_at = None
    db.session.commit()
    flash(f'{appl.full_name} moved back to pending.', 'info')
    return redirect(url_for('main.admin_verified'))


@main.route('/admin/verified')
@admin_required
def admin_verified():
    students = StudentApplication.query.filter_by(is_verified=True)\
                 .order_by(StudentApplication.rank.asc().nullslast(),
                           StudentApplication.verified_at).all()
    return render_template('admin_verified.html', students=students)


@main.route('/admin/rank')
@admin_required
def admin_rank():
    has_ranks = StudentApplication.query.filter_by(is_verified=True)\
                 .filter(StudentApplication.rank.isnot(None)).first()
    if has_ranks:
        students = StudentApplication.query.filter_by(is_verified=True)\
                     .order_by(StudentApplication.rank.asc()).all()
    else:
        students = StudentApplication.query.filter_by(is_verified=True)\
                     .order_by(
                         StudentApplication.percent_12.desc().nullslast(),
                         StudentApplication.percent_10.desc().nullslast(),
                         StudentApplication.submitted_at.asc()
                     ).all()
    return render_template('admin_rank.html', students=students)


@main.route('/admin/rank/save', methods=['POST'])
@admin_required
def save_ranks():
    for key, val in request.form.items():
        if key.startswith('rank_'):
            app_id = int(key.split('_')[1])
            appl   = StudentApplication.query.get(app_id)
            if appl:
                try:
                    appl.rank = int(val) if val.strip() else None
                except ValueError:
                    pass
    db.session.commit()
    flash('Ranks saved.', 'success')
    return redirect(url_for('main.admin_rank'))

@main.route('/admin/rank/auto-assign', methods=['POST'])
@admin_required
def auto_assign_ranks():
    students = StudentApplication.query.filter_by(is_verified=True)\
                 .order_by(
                     StudentApplication.percent_12.desc().nullslast(),
                     StudentApplication.percent_10.desc().nullslast(),
                     StudentApplication.submitted_at.asc()
                 ).all()
    for i, appl in enumerate(students, start=1):
        appl.rank = i
    db.session.commit()
    flash(f'Ranks auto-assigned to {len(students)} student(s) based on 12th % → 10th % tiebreaker.', 'success')
    return redirect(url_for('main.admin_rank'))

@main.route('/admin/form-control', methods=['GET', 'POST'])
@admin_required
def admin_form_control():
    settings = FormSettings.get()
    if request.method == 'POST':
        settings.is_open = 'is_open' in request.form
        settings.message = request.form.get('message', '')
        of = request.form.get('open_from', '').strip()
        ou = request.form.get('open_until', '').strip()
        settings.open_from  = datetime.strptime(of, '%Y-%m-%dT%H:%M') if of else None
        settings.open_until = datetime.strptime(ou, '%Y-%m-%dT%H:%M') if ou else None
        db.session.commit()
        flash('Form settings updated.', 'success')
        return redirect(url_for('main.admin_form_control'))
    return render_template('admin_form_control.html', settings=settings)


# ─────────────────────────────────────────
#  Exports
# ─────────────────────────────────────────
@main.route('/admin/export/all')
@admin_required
def export_all():
    apps = StudentApplication.query.order_by(StudentApplication.submitted_at).all()
    return export_excel(apps, 'all_applications.xlsx')


@main.route('/admin/export/verified')
@admin_required
def export_verified():
    apps = StudentApplication.query.filter_by(is_verified=True)\
             .order_by(StudentApplication.rank.asc().nullslast()).all()
    return export_verified_excel(apps, 'verified_students.xlsx')


# ─────────────────────────────────────────
#  Admin — Import Students from Excel/Google Form Export
# ─────────────────────────────────────────
IMPORT_TMP_DIR = '/tmp/bca_imports'

def _ensure_tmp():
    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)


def _auto_map(columns):
    mapping = {}

    EXACT = {
        'email address':                  'email',
        'e-mail id:':                     'email',
        'name of the candidate:':         'full_name',
        "mother's name:":                 'mother_name',
        "father's name:":                 'father_name',
        'date of birth:':                 'dob',
        'gender:':                        'gender',
        'mobile number of parent':        'parent_mobile',
        'mobile number of candidate':     'phone',
        'address :':                      'address',
        'nationality :':                  'nationality',
        'religion :':                     'religion',
        'category:':                      'category',
        'candidate belongs to hk region': 'hk_region',
        'kannada medium':                 'kannada_medium',
        'rural background':               'rural_background',
        'caste certificate no. :':        'caste_certificate_no',
        "parent's annual income :":       'parent_annual_income',
        'income certificate no. :':       'income_certificate_no',
        '10th standard board':            'board_10',
        '10th standard percentage':       'percent_10',
        '12th standard board':            'board_12',
        '12th standard stream':           'stream_12',
        '12th standard combination':      'combination_12',
        '12th standard max. marks':       'total_12',
        '12th standard marks scored':     'obtained_12',
        '12th standard percentage':       'percent_12',
        'declaration':                    'declaration',
    }

    HINTS = {
        'email':               ['email', 'mail', 'e-mail', 'emailid', 'email id'],
        'full_name':           ['full name', 'fullname', 'name', 'student name',
                                'student_name', 'candidate name'],
        'dob':                 ['dob', 'date of birth', 'dateofbirth', 'birth date',
                                'birthdate', 'date_of_birth', 'birth_date'],
        'phone':               ['phone', 'mobile', 'contact', 'phone number',
                                'mobile number', 'contact number', 'student mobile',
                                'mob', 'mob no', 'mobile no', 'phone no'],
        'parent_mobile':       ['parent mobile', 'parent_mobile', 'father mobile',
                                'guardian mobile', 'parent contact'],
        'gender':              ['gender', 'sex'],
        'address':             ['address', 'residential address', 'addr'],
        'nationality':         ['nationality', 'nation'],
        'religion':            ['religion'],
        'mother_name':         ['mother name', 'mother', 'mothers name'],
        'father_name':         ['father name', 'father', 'fathers name'],
        'category':            ['category', 'caste', 'cat', 'reservation'],
        'hk_region':           ['hk region', 'hk_region', 'hyderabad karnataka'],
        'kannada_medium':      ['kannada medium', 'kannada_medium'],
        'rural_background':    ['rural background', 'rural_background', 'rural'],
        'caste_certificate_no':['caste certificate', 'caste cert no', 'caste certificate no'],
        'parent_annual_income':['annual income', 'parent income', 'parent annual income'],
        'income_certificate_no':['income certificate', 'income cert no'],
        'board_10':            ['board 10', 'board10', '10th board', '10 board', 'ssc board'],
        'percent_10':          ['percent 10', 'percentage 10', '10th percent',
                                '10th percentage', '10 percent', '10 percentage'],
        'board_12':            ['board 12', 'board12', '12th board', '12 board', 'hsc board'],
        'stream_12':           ['stream', 'stream 12', '12th stream', '12 stream'],
        'combination_12':      ['combination', 'combination 12', '12th combination',
                                'specialization', 'specialisation'],
        'total_12':            ['total 12', 'total12', '12th total', 'max marks 12',
                                '12 total', 'total marks 12', 'max marks'],
        'obtained_12':         ['obtained 12', 'obtained12', '12th obtained',
                                'marks obtained 12', '12 obtained', 'marks scored'],
        'percent_12':          ['percent 12', 'percentage 12', '12th percent',
                                '12 percent', '12 percentage', '12th percentage'],
    }

    col_lower = {c.lower().strip(): c for c in columns}

    for col_l, original_col in col_lower.items():
        if col_l in EXACT:
            field = EXACT[col_l]
            if field not in mapping:
                mapping[field] = original_col

    for field, hints in HINTS.items():
        if field in mapping:
            continue
        for hint in hints:
            if hint in col_lower:
                mapping[field] = col_lower[hint]
                break

    return mapping


@main.route('/admin/import', methods=['GET', 'POST'])
@admin_required
def admin_import():
    _ensure_tmp()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'upload':
            f = request.files.get('excel_file')
            if not f or not f.filename:
                flash('Please select an Excel file.', 'danger')
                return redirect(url_for('main.admin_import'))
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in ('.xlsx', '.xls'):
                flash('Only .xlsx and .xls files are supported.', 'danger')
                return redirect(url_for('main.admin_import'))
            tmp_name = f'{uuid.uuid4().hex}{ext}'
            tmp_path = os.path.join(IMPORT_TMP_DIR, tmp_name)
            f.save(tmp_path)
            try:
                df       = pd.read_excel(tmp_path, nrows=5)
                df_full  = pd.read_excel(tmp_path)
                columns  = list(df.columns)
                if not columns:
                    flash('The Excel file appears to be empty.', 'danger')
                    return redirect(url_for('main.admin_import'))
                preview_rows = df.fillna('').astype(str).to_dict(orient='records')
                auto_map     = _auto_map(columns)
                total_rows   = len(df_full)
                return render_template('admin_import.html',
                                       columns=columns,
                                       preview_rows=preview_rows,
                                       auto_map=auto_map,
                                       total_rows=total_rows,
                                       session_file=tmp_name,
                                       result=None)
            except Exception as e:
                flash(f'Could not read Excel file: {e}', 'danger')
                return redirect(url_for('main.admin_import'))

        elif action == 'import':
            tmp_name = request.form.get('session_file', '')
            tmp_path = os.path.join(IMPORT_TMP_DIR, tmp_name)
            if not tmp_name or not os.path.exists(tmp_path):
                flash('Session expired. Please upload the file again.', 'warning')
                return redirect(url_for('main.admin_import'))

            field_map = {}
            for key in request.form:
                if key.startswith('map_') and request.form[key]:
                    field_map[key[4:]] = request.form[key]

            required = ['dob']
            missing  = [r for r in required if r not in field_map]
            if 'email' not in field_map:
                missing.append('email')
            if 'full_name' not in field_map:
                missing.append('full_name (or Name of the Candidate)')

            if missing:
                flash(f'Required fields not mapped: {", ".join(missing)}', 'danger')
                try:
                    df      = pd.read_excel(tmp_path, nrows=5)
                    df_full = pd.read_excel(tmp_path)
                    columns = list(df.columns)
                except Exception as e:
                    flash(f'Could not re-read file: {e}', 'danger')
                    return redirect(url_for('main.admin_import'))
                return render_template('admin_import.html',
                                       columns=columns,
                                       preview_rows=df.fillna('').astype(str).to_dict(orient='records'),
                                       auto_map=field_map,
                                       total_rows=len(df_full),
                                       session_file=tmp_name,
                                       result=None)

            try:
                df = pd.read_excel(tmp_path)
            except Exception as e:
                flash(f'Could not read file: {e}', 'danger')
                return redirect(url_for('main.admin_import'))

            categories   = ApplicationCategory.query.filter_by(is_active=True).all()
            cat_name_map = {c.name.lower().strip(): c.id for c in categories}

            created = 0
            skipped = 0
            errors  = []

            for idx, row in df.iterrows():
                row_num = idx + 2

                email_col = field_map.get('email', '')
                email_raw = _str(row[email_col]) if email_col and email_col in df.columns else ''
                if not email_raw:
                    errors.append({'row': row_num, 'msg': 'Email is empty'})
                    continue
                email = email_raw.lower()

                if User.query.filter_by(email=email).first():
                    skipped += 1
                    continue

                fn_col    = field_map.get('full_name', '')
                full_name = _str(row[fn_col]) if fn_col and fn_col in df.columns else ''
                if not full_name:
                    errors.append({'row': row_num, 'msg': f'Name is empty for {email}'})
                    continue

                dob_col        = field_map.get('dob', '')
                dob_raw        = row[dob_col] if dob_col and dob_col in df.columns else None
                password_plain = _dob_to_password(dob_raw)
                dob_str        = _str(dob_raw)

                if not password_plain:
                    errors.append({'row': row_num, 'msg': f'Could not parse DOB for {email}'})
                    continue

                def _get_col(field):
                    col = field_map.get(field, '')
                    if col and col in df.columns:
                        return row[col]
                    return None

                try:
                    user = User(
                        full_name      = full_name,
                        email          = email,
                        password_hash  = generate_password_hash(password_plain),
                        is_verified    = True,
                        otp_code       = None,
                        otp_expires_at = None,
                    )
                    db.session.add(user)
                    db.session.flush()

                    cat_id = None
                    if 'category' in field_map:
                        cat_col = field_map['category']
                        if cat_col in df.columns:
                            cat_raw = _str(row[cat_col]).lower().strip()
                            cat_id  = cat_name_map.get(cat_raw)

                    total_12    = _flt(_get_col('total_12'))
                    obtained_12 = _flt(_get_col('obtained_12'))
                    percent_12  = _flt(_get_col('percent_12'))
                    if total_12 and obtained_12 and not percent_12:
                        percent_12 = round((obtained_12 / total_12) * 100, 2)

                    percent_10 = _flt(_get_col('percent_10'))

                    hk_region         = _bool_field(_get_col('hk_region'))
                    kannada_medium    = _bool_field(_get_col('kannada_medium'))
                    rural_background  = _bool_field(_get_col('rural_background'))

                    appl = StudentApplication(
                        user_id              = user.id,
                        candidate_name       = full_name,
                        mother_name          = _str(_get_col('mother_name')),
                        father_name          = _str(_get_col('father_name')),
                        dob                  = dob_str,
                        gender               = _str(_get_col('gender')),
                        parent_mobile        = _str(_get_col('parent_mobile')),
                        phone                = _str(_get_col('phone')),
                        email                = email,
                        address              = _str(_get_col('address')),
                        nationality          = _str(_get_col('nationality')) or 'Indian',
                        religion             = _str(_get_col('religion')),
                        hk_region            = hk_region,
                        kannada_medium       = kannada_medium,
                        rural_background     = rural_background,
                        caste_certificate_no = _str(_get_col('caste_certificate_no')),
                        parent_annual_income = _str(_get_col('parent_annual_income')),
                        income_certificate_no= _str(_get_col('income_certificate_no')),
                        category_id          = cat_id,
                        board_10             = _str(_get_col('board_10')),
                        percent_10           = percent_10,
                        board_12             = _str(_get_col('board_12')),
                        stream_12            = _str(_get_col('stream_12')),
                        combination_12       = _str(_get_col('combination_12')),
                        total_12             = total_12,
                        obtained_12          = obtained_12,
                        percent_12           = percent_12,
                        declaration          = True,
                        is_verified          = False,
                    )
                    db.session.add(appl)
                    db.session.commit()
                    created += 1

                except Exception as e:
                    db.session.rollback()
                    errors.append({'row': row_num, 'msg': str(e)[:120]})

            try:
                os.remove(tmp_path)
            except Exception:
                pass

            result = {'created': created, 'skipped': skipped, 'errors': errors}
            return render_template('admin_import.html',
                                   result=result,
                                   columns=None,
                                   preview_rows=None,
                                   auto_map=None,
                                   total_rows=None,
                                   session_file=None)

    return render_template('admin_import.html',
                           columns=None,
                           preview_rows=None,
                           auto_map=None,
                           total_rows=None,
                           session_file=None,
                           result=None)


# ─────────────────────────────────────────
#  Faculty Login / Logout
# ─────────────────────────────────────────
@main.route('/faculty/login', methods=['GET', 'POST'])
def faculty_login():
    if session.get('faculty_logged_in'):
        return redirect(url_for('main.faculty_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        faculty  = Faculty.query.filter_by(username=username).first()
        if not faculty or not check_password_hash(faculty.password_hash, password):
            flash('Invalid username or password.', 'danger')
        elif not faculty.is_active:
            flash('Your account has been deactivated. Contact admin.', 'warning')
        else:
            session['faculty_logged_in'] = True
            session['faculty_id']        = faculty.id
            session['faculty_name']      = faculty.full_name
            return redirect(url_for('main.faculty_dashboard'))
    return render_template('faculty_login.html')


@main.route('/faculty/logout')
def faculty_logout():
    session.pop('faculty_logged_in', None)
    session.pop('faculty_id', None)
    session.pop('faculty_name', None)
    flash('Logged out successfully.', 'info')
    return redirect(url_for('main.faculty_login'))


# ─────────────────────────────────────────
#  Faculty Dashboard
# ─────────────────────────────────────────
@main.route('/faculty')
@faculty_required
def faculty_dashboard():
    total_allotted = SeatAllotment.query.count()
    fees_set       = StudentFees.query.count()
    paid_count     = db.session.query(PaymentReceipt.application_id.distinct()).count()
    return render_template('faculty_dashboard.html',
                           total_allotted=total_allotted,
                           fees_set=fees_set,
                           paid_count=paid_count,
                           faculty_name=session.get('faculty_name'))


# ─────────────────────────────────────────
#  Faculty — Allotted Seats View
# ─────────────────────────────────────────
@main.route('/faculty/allotted')
@faculty_required
def faculty_allotted():
    allotments = SeatAllotment.query.all()
    students   = []
    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if appl:
            students.append({'appl': appl, 'allotment': a})
    students.sort(key=lambda x: (x['appl'].rank or 9999))
    return render_template('faculty_allotted.html', students=students)


# ─────────────────────────────────────────
#  Faculty — Payment Receipts
# ─────────────────────────────────────────
@main.route('/faculty/payments')
@faculty_required
def faculty_payments():
    q          = request.args.get('q', '').strip()
    allotments = SeatAllotment.query.all()
    students   = []

    for a in allotments:
        appl = StudentApplication.query.get(a.application_id)
        if not appl:
            continue
        if q:
            rank_match = str(appl.rank or '') == q
            name_match = q.lower() in appl.full_name.lower()
            if not rank_match and not name_match:
                continue
        fees     = StudentFees.query.filter_by(application_id=appl.id).first()
        receipts = PaymentReceipt.query.filter_by(application_id=appl.id)\
                     .order_by(PaymentReceipt.created_at.asc()).all()
        students.append({
            'appl':      appl,
            'allotment': a,
            'fees':      fees,
            'receipts':  receipts,
        })

    students.sort(key=lambda x: (x['appl'].rank or 9999))
    return render_template('faculty_payments.html', students=students, q=q)


# ─────────────────────────────────────────
#  Faculty — Set / Update Total Fees
# ─────────────────────────────────────────
@main.route('/faculty/payments/<int:app_id>/set-fees', methods=['POST'])
@faculty_required
def faculty_set_fees(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    if not SeatAllotment.query.filter_by(application_id=appl.id).first():
        flash('This student does not have a seat allotment.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    total_fees_str = request.form.get('total_fees', '').strip()
    try:
        total_fees = float(total_fees_str)
        if total_fees < 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid fee amount.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    fees = StudentFees.query.filter_by(application_id=appl.id).first()
    if fees:
        fees.total_fees = total_fees
        fees.updated_at = ist_now_naive()
    else:
        fees = StudentFees(application_id=appl.id, total_fees=total_fees)
        db.session.add(fees)
    db.session.commit()
    flash(f'Total fees updated for {appl.full_name}.', 'success')
    return redirect(url_for('main.faculty_payments'))


# ─────────────────────────────────────────
#  Faculty — Add Payment Receipt
# ─────────────────────────────────────────
@main.route('/faculty/payments/<int:app_id>/add-receipt', methods=['POST'])
@faculty_required
def faculty_add_receipt(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    fees = StudentFees.query.filter_by(application_id=appl.id).first()
    if not fees:
        flash('Please set total fees for this student first.', 'warning')
        return redirect(url_for('main.faculty_payments'))

    receipt_number = request.form.get('receipt_number', '').strip()
    amount_str     = request.form.get('amount_paid', '').strip()

    if not receipt_number:
        flash('Receipt number is required.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    try:
        amount_paid = float(amount_str)
        if amount_paid <= 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid amount.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    db.session.add(PaymentReceipt(
        fees_id        = fees.id,
        application_id = appl.id,
        faculty_id     = session.get('faculty_id'),
        receipt_number = receipt_number,
        amount_paid    = amount_paid,
    ))
    db.session.commit()
    flash(f'Receipt #{receipt_number} added for {appl.full_name}.', 'success')
    return redirect(url_for('main.faculty_payments'))


# ─────────────────────────────────────────
#  Faculty — Edit Payment Receipt
# ─────────────────────────────────────────
@main.route('/faculty/payments/receipt/<int:receipt_id>/edit', methods=['POST'])
@faculty_required
def faculty_edit_receipt(receipt_id):
    receipt        = PaymentReceipt.query.get_or_404(receipt_id)
    receipt_number = request.form.get('receipt_number', '').strip()
    amount_str     = request.form.get('amount_paid', '').strip()

    if not receipt_number:
        flash('Receipt number is required.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    try:
        amount_paid = float(amount_str)
        if amount_paid <= 0:
            raise ValueError
    except ValueError:
        flash('Please enter a valid amount.', 'danger')
        return redirect(url_for('main.faculty_payments'))

    receipt.receipt_number = receipt_number
    receipt.amount_paid    = amount_paid
    receipt.updated_at     = ist_now_naive()
    db.session.commit()
    flash(f'Receipt #{receipt_number} updated.', 'success')
    return redirect(url_for('main.faculty_payments'))


# ─────────────────────────────────────────
#  Faculty — Delete Payment Receipt
# ─────────────────────────────────────────
@main.route('/faculty/payments/receipt/<int:receipt_id>/delete', methods=['POST'])
@faculty_required
def faculty_delete_receipt(receipt_id):
    receipt        = PaymentReceipt.query.get_or_404(receipt_id)
    receipt_number = receipt.receipt_number
    db.session.delete(receipt)
    db.session.commit()
    flash(f'Receipt #{receipt_number} deleted.', 'info')
    return redirect(url_for('main.faculty_payments'))