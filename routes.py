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
                    Faculty, StudentFees, PaymentReceipt)
from forms.application_form import ApplicationForm
from utils.helpers import save_pdf, calc_percent, generate_otp, send_otp_email
from utils.exports import export_excel, export_verified_excel

main = Blueprint('main', __name__)

IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    return datetime.now(IST)

def utc_to_ist(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


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

def _dob_to_password(dob_raw):
    if not dob_raw or str(dob_raw).strip() in ('', 'nan', 'NaT'):
        return None
    try:
        ts = pd.Timestamp(dob_raw)
        if not pd.isna(ts):
            return ts.strftime('%d%m%Y')
    except Exception:
        pass
    digits = re.sub(r'[^0-9]', '', str(dob_raw).strip())
    return digits if digits else None


# ─────────────────────────────────────────
#  Welcome Email via Apps Script
# ─────────────────────────────────────────
def send_welcome_email(email, full_name, password_plain, dob_display):
    APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbwExa3gOK26Vm4y4nZjHl87Oo3IZPV_Zr3TFFbsuGYpV2w_FiVp_wPNXeEfUZwamLSY/exec"
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


# ─────────────────────────────────────────
#  Public — Live Counselling Tracker
#  No login required — anyone can view
# ─────────────────────────────────────────
@main.route('/live')
def counselling_live():
    cs = CounsellingSettings.get()

    student       = None
    allotment     = None
    total_ranked  = StudentApplication.query.filter(
                        StudentApplication.rank.isnot(None)).count()
    total_allotted = SeatAllotment.query.count()

    if cs.current_rank:
        student = StudentApplication.query.filter_by(
            rank=cs.current_rank,
            is_verified=True
        ).first()

        if student:
            a = SeatAllotment.query.filter_by(application_id=student.id).first()
            if a:
                allotted_ist = utc_to_ist(a.allotted_at)
                allotment = {
                    'quota':       a.quota,
                    'allotted_at': allotted_ist,
                }

    return render_template('counselling_live.html',
                           cs=cs,
                           student=student,
                           allotment=allotment,
                           total_ranked=total_ranked,
                           total_allotted=total_allotted)

# ─────────────────────────────────────────
#  POST API — Register Student
# ─────────────────────────────────────────
@main.route('/api/d2faa6fb-745b-454d-8852-92ed0bb482d8', methods=['POST'])
def api_register_student():
    api_key = request.headers.get('x-api-key')
    if api_key != current_app.config.get('API_SECRET_KEY'):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
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

        parts      = full_name.split(' ', 1)
        first_name = parts[0]
        last_name  = parts[1] if len(parts) > 1 else ''

        cat_id  = None
        cat_raw = _str(data.get('category')).lower()
        if cat_raw:
            cat    = ApplicationCategory.query.filter(
                db.func.lower(ApplicationCategory.name) == cat_raw
            ).first()
            cat_id = cat.id if cat else None

        total_10    = _flt(data.get('total_10'))
        obtained_10 = _flt(data.get('obtained_10'))
        percent_10  = _flt(data.get('percent_10'))
        if total_10 and obtained_10 and not percent_10:
            percent_10 = round((obtained_10 / total_10) * 100, 2)

        total_12    = _flt(data.get('total_12'))
        obtained_12 = _flt(data.get('obtained_12'))
        percent_12  = _flt(data.get('percent_12'))
        if total_12 and obtained_12 and not percent_12:
            percent_12 = round((obtained_12 / total_12) * 100, 2)

        appl = StudentApplication(
            user_id        = user.id,
            first_name     = first_name,
            last_name      = last_name,
            dob            = dob_raw,
            email          = email,
            phone          = _str(data.get('student_mobile')),
            gender         = _str(data.get('gender')),
            address        = _str(data.get('address')),
            nationality    = _str(data.get('nationality')) or 'Indian',
            category_id    = cat_id,
            school_10      = _str(data.get('school_10')),
            board_10       = _str(data.get('board_10')),
            year_10        = _str(data.get('year_10')),
            total_10       = total_10,
            obtained_10    = obtained_10,
            percent_10     = percent_10,
            school_12      = _str(data.get('school_12')),
            board_12       = _str(data.get('board_12')),
            stream_12      = _str(data.get('stream_12')),
            year_12        = _str(data.get('year_12')),
            total_12       = total_12,
            obtained_12    = obtained_12,
            percent_12     = percent_12,
            specialization = _str(data.get('combination_12')),
            marksheet_10   = None,
            marksheet_12   = None,
        )
        db.session.add(appl)
        db.session.commit()

        send_welcome_email(
            email          = email,
            full_name      = full_name,
            password_plain = password_plain,
            dob_display    = dob_raw,
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
    cat_seats   = None
    if appl and appl.category_id:
        cat_seats = CategorySeats.get_for_category(appl.category_id)
    allotment = None
    if appl:
        a = SeatAllotment.query.filter_by(application_id=appl.id).first()
        if a:
            allotted_ist = utc_to_ist(a.allotted_at)
            allotment = {
                'quota':       a.quota,
                'allotted_at': allotted_ist
            }
    return render_template('student_counselling.html',
                           appl=appl, counselling=counselling,
                           cat_seats=cat_seats, allotment=allotment)


@main.route('/counselling/status')
@login_required
def counselling_status_api():
    appl        = StudentApplication.query.filter_by(user_id=current_user.id).first()
    counselling = CounsellingSettings.get()

    all_seats  = []
    categories = ApplicationCategory.query.filter_by(is_active=True).all()
    for cat in categories:
        cs = CategorySeats.get_for_category(cat.id)
        all_seats.append({
            'category':       cat.name,
            'govt_total':     cs.govt_total,
            'govt_filled':    cs.govt_filled,
            'govt_remaining': cs.govt_remaining,
            'mgmt_total':     cs.mgmt_total,
            'mgmt_filled':    cs.mgmt_filled,
            'mgmt_remaining': cs.mgmt_remaining,
        })

    cat_seats = None
    if appl and appl.category_id:
        cs  = CategorySeats.get_for_category(appl.category_id)
        cat = ApplicationCategory.query.get(appl.category_id)
        cat_seats = {
            'category':       cat.name if cat else '',
            'govt_total':     cs.govt_total,
            'govt_filled':    cs.govt_filled,
            'govt_remaining': cs.govt_remaining,
            'mgmt_total':     cs.mgmt_total,
            'mgmt_filled':    cs.mgmt_filled,
            'mgmt_remaining': cs.mgmt_remaining,
        }

    allotment = None
    if appl:
        a = SeatAllotment.query.filter_by(application_id=appl.id).first()
        if a:
            allotted_ist = utc_to_ist(a.allotted_at)
            allotment = {
                'quota':       a.quota,
                'allotted_at': allotted_ist.strftime('%d %b %Y, %I:%M %p') if allotted_ist else None
            }

    return jsonify({
        'status':       counselling.status,
        'current_rank': counselling.current_rank,
        'message':      counselling.message,
        'my_rank':      appl.rank if appl else None,
        'all_seats':    all_seats,
        'cat_seats':    cat_seats,
        'allotment':    allotment,
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
        p10 = calc_percent(form.total_10.data, form.obtained_10.data)
        p12 = calc_percent(form.total_12.data, form.obtained_12.data)
        specialization       = form.specialization.data
        specialization_other = None
        if specialization == 'Other':
            specialization_other = form.specialization_other.data
        category_id = request.form.get('category_id')
        if not category_id:
            flash('Please select a category.', 'danger')
            return render_template('application_form.html', form=form, categories=categories)
        appl = StudentApplication(
            user_id=current_user.id,
            first_name=form.first_name.data, last_name=form.last_name.data,
            dob=form.dob.data, gender=form.gender.data,
            nationality=form.nationality.data, email=form.email.data,
            phone=form.phone.data, address=form.address.data,
            school_10=form.school_10.data, board_10=form.board_10.data,
            board_10_other=form.board_10_other.data, year_10=form.year_10.data,
            total_10=form.total_10.data, obtained_10=form.obtained_10.data, percent_10=p10,
            marksheet_10=None,
            school_12=form.school_12.data, board_12=form.board_12.data,
            board_12_other=form.board_12_other.data, stream_12=form.stream_12.data,
            year_12=form.year_12.data, total_12=form.total_12.data,
            obtained_12=form.obtained_12.data, percent_12=p12,
            marksheet_12=None,
            specialization=specialization, specialization_other=specialization_other,
            category_id=int(category_id),
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
            cs.started_at   = datetime.utcnow()
            cs.current_rank = None
            cs.updated_at   = datetime.utcnow()
            db.session.commit()
            flash('Counselling started!', 'success')

        elif action == 'stop':
            cs.status     = 'stopped'
            cs.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Counselling stopped.', 'info')

        elif action == 'resume':
            cs.status     = 'running'
            cs.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Counselling resumed!', 'success')

        elif action == 'pause':
            cs.status     = 'paused'
            cs.updated_at = datetime.utcnow()
            db.session.commit()
            flash('Counselling paused.', 'warning')

        elif action == 'update_rank':
            rank_val        = request.form.get('current_rank', '').strip()
            msg_val         = request.form.get('message', '').strip()
            cs.current_rank = int(rank_val) if rank_val.isdigit() else None
            cs.message      = msg_val or None
            cs.updated_at   = datetime.utcnow()
            db.session.commit()
            flash('Live rank updated.', 'success')

        elif action == 'next_rank':
            cs.current_rank = (cs.current_rank or 0) + 1
            cs.updated_at   = datetime.utcnow()
            db.session.commit()
            flash(f'Now calling Rank #{cs.current_rank}', 'success')

        elif action == 'set_seats':
            cat_id    = request.form.get('cat_id')
            quota     = request.form.get('quota')
            total_str = request.form.get('total_seats', '0').strip()
            if cat_id and quota:
                seats = CategorySeats.get_for_category(int(cat_id))
                val   = int(total_str) if total_str.isdigit() else 0
                if quota == 'government':
                    seats.govt_total = val
                else:
                    seats.mgmt_total = val
                db.session.commit()
                flash('Seats updated.', 'success')

        elif action == 'allot_seat':
            app_id = request.form.get('app_id')
            quota  = request.form.get('quota')
            try:
                app_id = int(app_id)
            except (TypeError, ValueError):
                flash('Invalid student ID.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            appl = StudentApplication.query.get(app_id)
            if not appl:
                flash('Student not found.', 'danger')
            elif not quota or quota not in ('government', 'management'):
                flash('Please select a quota.', 'danger')
            elif SeatAllotment.query.filter_by(application_id=appl.id).first():
                flash(f'Seat already allotted to {appl.full_name}.', 'warning')
            else:
                seats = CategorySeats.get_for_category(appl.category_id)
                if quota == 'government' and seats.govt_remaining <= 0:
                    flash(f'No Government Quota seats remaining in {appl.category_name}!', 'danger')
                elif quota == 'management' and seats.mgmt_remaining <= 0:
                    flash(f'No Management Quota seats remaining in {appl.category_name}!', 'danger')
                else:
                    db.session.add(SeatAllotment(
                        application_id=appl.id,
                        category_id=appl.category_id,
                        quota=quota,
                    ))
                    if quota == 'government':
                        seats.govt_filled += 1
                    else:
                        seats.mgmt_filled += 1
                    db.session.commit()
                    flash(f'Seat allotted to {appl.full_name} ({quota.title()} Quota)!', 'success')

        elif action == 'revoke_seat':
            app_id = request.form.get('app_id')
            try:
                app_id = int(app_id)
            except (TypeError, ValueError):
                flash('Invalid student ID.', 'danger')
                return redirect(url_for('main.admin_counselling'))

            allotment = SeatAllotment.query.filter_by(application_id=app_id).first()
            if allotment:
                seats = CategorySeats.get_for_category(allotment.category_id)
                if allotment.quota == 'government':
                    seats.govt_filled = max(0, seats.govt_filled - 1)
                else:
                    seats.mgmt_filled = max(0, seats.mgmt_filled - 1)
                db.session.delete(allotment)
                db.session.commit()
                flash('Seat allotment revoked.', 'info')

        return redirect(url_for('main.admin_counselling'))

    cs         = CounsellingSettings.get()
    categories = ApplicationCategory.query.filter_by(is_active=True).all()
    cat_seats  = {cat.id: CategorySeats.get_for_category(cat.id) for cat in categories}
    students   = (StudentApplication.query
                  .filter_by(is_verified=True)
                  .filter(StudentApplication.rank.isnot(None))
                  .order_by(StudentApplication.rank.asc())
                  .all())
    allotments = {a.application_id: a for a in SeatAllotment.query.all()}

    return render_template('admin_counselling.html',
                           cs=cs, categories=categories,
                           cat_seats=cat_seats, students=students,
                           allotments=allotments)


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
            wa.updated_at  = datetime.utcnow()
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
            wa.updated_at  = datetime.utcnow()
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
# ─────────────────────────────────────────
@main.route('/admin/students')
@admin_required
def admin_students():
    q     = request.args.get('q', '').strip()
    query = StudentApplication.query
    if q:
        like  = f'%{q}%'
        query = query.filter(db.or_(
            StudentApplication.first_name.ilike(like),
            StudentApplication.last_name.ilike(like),
            StudentApplication.email.ilike(like),
        ))
    students = query.order_by(StudentApplication.submitted_at.desc()).all()
    return render_template('admin_students.html', students=students, q=q)


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
    appl.verified_at = datetime.utcnow()
    appl.admin_notes = request.form.get('notes', appl.admin_notes)
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
        student.verified_at = datetime.utcnow()
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
                     .order_by(StudentApplication.submitted_at).all()
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
#  Category Management
# ─────────────────────────────────────────
@main.route('/admin/categories')
@admin_required
def admin_categories():
    categories = ApplicationCategory.query.order_by(ApplicationCategory.id).all()
    return render_template('admin_categories.html', categories=categories)


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
    db.session.add(ApplicationCategory(name=name, is_active=True))
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


@main.route('/admin/categories/delete/<int:cat_id>', methods=['POST'])
@admin_required
def admin_category_delete(cat_id):
    category   = ApplicationCategory.query.get_or_404(cat_id)
    name       = category.name
    apps_count = StudentApplication.query.filter_by(category_id=cat_id).count()
    if apps_count > 0:
        flash(f'Cannot delete "{name}" — {apps_count} student(s) are using this category.', 'danger')
    else:
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
#  Admin — Import Students from Excel
# ─────────────────────────────────────────
IMPORT_TMP_DIR = '/tmp/bca_imports'

def _ensure_tmp():
    os.makedirs(IMPORT_TMP_DIR, exist_ok=True)

def _auto_map(columns):
    mapping = {}
    HINTS = {
        'email':          ['email', 'mail', 'e-mail', 'email address', 'emailid', 'email id'],
        'full_name':      ['full name', 'fullname', 'name', 'student name', 'student_name'],
        'first_name':     ['first name', 'firstname', 'fname', 'first'],
        'last_name':      ['last name', 'lastname', 'lname', 'last', 'surname'],
        'dob':            ['dob', 'date of birth', 'dateofbirth', 'birth date',
                           'birthdate', 'date_of_birth', 'birth_date'],
        'phone':          ['phone', 'mobile', 'contact', 'phone number', 'mobile number',
                           'contact number', 'student mobile', 'student_mobile',
                           'mob', 'mob no', 'mobile no', 'phone no'],
        'gender':         ['gender', 'sex'],
        'address':        ['address', 'residential address', 'addr', 'full address'],
        'nationality':    ['nationality', 'nation'],
        'category':       ['category', 'caste', 'cat', 'reservation', 'category name'],
        'school_10':      ['school 10', 'school10', '10th school', 'class 10 school',
                           'ssc school', '10 school'],
        'board_10':       ['board 10', 'board10', '10th board', 'class 10 board',
                           'ssc board', '10 board'],
        'year_10':        ['year 10', 'year10', '10th year', 'class 10 year',
                           'passing year 10', '10 year', 'pass year 10'],
        'total_10':       ['total 10', 'total10', '10th total', 'class 10 total',
                           'max marks 10', '10 total', 'total marks 10'],
        'obtained_10':    ['obtained 10', 'obtained10', '10th obtained',
                           'marks obtained 10', '10 obtained', 'obtained marks 10'],
        'percent_10':     ['percent 10', 'percentage 10', '10th percent',
                           'class 10 percentage', '%10', '10 percent', '10 percentage'],
        'school_12':      ['school 12', 'school12', '12th school', 'class 12 school',
                           'hsc school', '12 school'],
        'board_12':       ['board 12', 'board12', '12th board', 'class 12 board',
                           'hsc board', '12 board'],
        'stream_12':      ['stream', 'stream 12', '12th stream', 'class 12 stream',
                           '12 stream'],
        'year_12':        ['year 12', 'year12', '12th year', 'class 12 year',
                           'passing year 12', '12 year', 'pass year 12'],
        'total_12':       ['total 12', 'total12', '12th total', 'class 12 total',
                           'max marks 12', '12 total', 'total marks 12'],
        'obtained_12':    ['obtained 12', 'obtained12', '12th obtained',
                           'marks obtained 12', '12 obtained', 'obtained marks 12'],
        'percent_12':     ['percent 12', 'percentage 12', '12th percent',
                           'class 12 percentage', '%12', '12 percent', '12 percentage'],
        'specialization': ['specialization', 'specialisation', 'spec',
                           'programme', 'program', 'combination', 'combination_12',
                           'combination 12'],
    }
    col_lower = {c.lower().strip(): c for c in columns}
    for field, hints in HINTS.items():
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

            required = ['email', 'dob']
            missing  = [r for r in required if r not in field_map]
            if 'full_name' not in field_map and 'first_name' not in field_map:
                missing.append('full_name or first_name')

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

                if 'full_name' in field_map:
                    fn_col     = field_map['full_name']
                    full_name  = _str(row[fn_col]) if fn_col in df.columns else ''
                    parts      = full_name.split(' ', 1)
                    first_name = parts[0]
                    last_name  = parts[1] if len(parts) > 1 else ''
                else:
                    fn_col     = field_map.get('first_name', '')
                    ln_col     = field_map.get('last_name', '')
                    first_name = _str(row[fn_col]) if fn_col and fn_col in df.columns else ''
                    last_name  = _str(row[ln_col]) if ln_col and ln_col in df.columns else ''
                    full_name  = f'{first_name} {last_name}'.strip()

                if not full_name:
                    errors.append({'row': row_num, 'msg': 'Name is empty'})
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
                            cat_raw = _str(row[cat_col]).lower()
                            cat_id  = cat_name_map.get(cat_raw)

                    total_10    = _flt(_get_col('total_10'))
                    obtained_10 = _flt(_get_col('obtained_10'))
                    percent_10  = _flt(_get_col('percent_10'))
                    if total_10 and obtained_10 and not percent_10:
                        percent_10 = round((obtained_10 / total_10) * 100, 2)

                    total_12    = _flt(_get_col('total_12'))
                    obtained_12 = _flt(_get_col('obtained_12'))
                    percent_12  = _flt(_get_col('percent_12'))
                    if total_12 and obtained_12 and not percent_12:
                        percent_12 = round((obtained_12 / total_12) * 100, 2)

                    appl = StudentApplication(
                        user_id        = user.id,
                        first_name     = first_name,
                        last_name      = last_name,
                        dob            = dob_str,
                        email          = email,
                        phone          = _str(_get_col('phone')),
                        gender         = _str(_get_col('gender')),
                        address        = _str(_get_col('address')),
                        nationality    = _str(_get_col('nationality')) or 'Indian',
                        category_id    = cat_id,
                        school_10      = _str(_get_col('school_10')),
                        board_10       = _str(_get_col('board_10')),
                        year_10        = _str(_get_col('year_10')),
                        total_10       = total_10,
                        obtained_10    = obtained_10,
                        percent_10     = percent_10,
                        school_12      = _str(_get_col('school_12')),
                        board_12       = _str(_get_col('board_12')),
                        stream_12      = _str(_get_col('stream_12')),
                        year_12        = _str(_get_col('year_12')),
                        total_12       = total_12,
                        obtained_12    = obtained_12,
                        percent_12     = percent_12,
                        specialization = _str(_get_col('specialization')),
                        marksheet_10   = None,
                        marksheet_12   = None,
                    )
                    db.session.add(appl)
                    db.session.commit()
                    created += 1

                except Exception as e:
                    db.session.rollback()
                    errors.append({'row': row_num, 'msg': str(e)[:80]})

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
        fees.updated_at = datetime.utcnow()
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
    receipt.updated_at     = datetime.utcnow()
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