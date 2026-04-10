import os
import uuid
import pandas as pd
import re
from werkzeug.utils import secure_filename
from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, current_app, abort, send_file, jsonify)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
import requests
import uuid

from database import db
from models import (User, FormSettings, StudentApplication, ApplicationCategory,
                    WhatsAppSettings, CounsellingSettings, CategorySeats, SeatAllotment)
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
#  Helper functions for API
# ─────────────────────────────────────────
def _str(val):
    """Safely convert to string, return empty string if None or NaN"""
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except:
        pass
    return str(val).strip()

def _flt(val):
    """Safely convert to float, return None if invalid"""
    try:
        v = float(val)
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError):
        return None

# Your Apps Script URL - CHANGE THIS
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbwExa3gOK26Vm4y4nZjHl87Oo3IZPV_Zr3TFFbsuGYpV2w_FiVp_wPNXeEfUZwamLSY/exec"  # ← PUT YOUR ACTUAL URL HERE

# Non-guessable API endpoint path - CHANGE THIS to a random string
# Generate with: import secrets; print(secrets.token_urlsafe(16))
API_SECRET_PATH = "d2faa6fb-745b-454d-8852-92ed0bb482d8"  # ← CHANGE THIS to a random string


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
#  Public
# ─────────────────────────────────────────
@main.route('/')
def index():
    settings = FormSettings.get()
    whatsapp = WhatsAppSettings.get()
    return render_template('index.html', settings=settings, whatsapp_group_link=whatsapp.link if whatsapp and whatsapp.is_active else None)


# ─────────────────────────────────────────
# ─────────────────────────────────────────
#  Registration + OTP (Using API import - kept for admin use)
# ─────────────────────────────────────────
@main.route('/register_xy9k4m2p7q8w3r5t', methods=['GET', 'POST'])
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
            flash('Please contact admin to verify your account.', 'warning')
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
        allotment = SeatAllotment.query.filter_by(application_id=appl.id).first()
    return render_template('student_counselling.html',
                           appl=appl, counselling=counselling,
                           cat_seats=cat_seats, allotment=allotment)


@main.route('/counselling/status')
@login_required
def counselling_status_api():
    appl        = StudentApplication.query.filter_by(user_id=current_user.id).first()
    counselling = CounsellingSettings.get()
    
    # Get ALL seats for all categories
    all_seats = []
    categories = ApplicationCategory.query.filter_by(is_active=True).all()
    for cat in categories:
        cs = CategorySeats.get_for_category(cat.id)
        all_seats.append({
            'category': cat.name,
            'govt_total': cs.govt_total,
            'govt_filled': cs.govt_filled,
            'govt_remaining': cs.govt_remaining,
            'mgmt_total': cs.mgmt_total,
            'mgmt_filled': cs.mgmt_filled,
            'mgmt_remaining': cs.mgmt_remaining,
        })
    
    cat_seats = None
    if appl and appl.category_id:
        cs = CategorySeats.get_for_category(appl.category_id)
        cat_seats = {
            'category': appl.category.name if appl.category else '',
            'govt_total': cs.govt_total,
            'govt_filled': cs.govt_filled,
            'govt_remaining': cs.govt_remaining,
            'mgmt_total': cs.mgmt_total,
            'mgmt_filled': cs.mgmt_filled,
            'mgmt_remaining': cs.mgmt_remaining,
        }
    
    allotment = None
    if appl:
        a = SeatAllotment.query.filter_by(application_id=appl.id).first()
        if a:
            allotment = {'quota': a.quota, 'allotted_at': a.allotted_at.strftime('%d %b %Y, %I:%M %p')}
    
    return jsonify({
        'status': counselling.status,
        'current_rank': counselling.current_rank,
        'message': counselling.message,
        'my_rank': appl.rank if appl else None,
        'all_seats': all_seats,
        'cat_seats': cat_seats,
        'allotment': allotment,
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
#  API - Register Student (via POST) - NON-GUESSABLE URL
# ─────────────────────────────────────────
@main.route(f'/api/{API_SECRET_PATH}', methods=['POST'])
def api_register_student():
    # 🔐 API Key check
    api_key = request.headers.get('x-api-key')
    if current_app.config.get('API_SECRET_KEY') and api_key != current_app.config.get('API_SECRET_KEY'):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    try:
        # Required fields
        email     = _str(data.get('email')).lower()
        full_name = _str(data.get('full_name'))
        dob_raw   = _str(data.get('dob'))

        if not email or not full_name or not dob_raw:
            return jsonify({"error": "Missing required fields: email, full_name, dob"}), 400

        # Duplicate check
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "User already exists"}), 409

        # DOB → password (strip all non-digit characters) - FIXED!
        # Example: "15/06/2006" → "15062006"
        password_plain = re.sub(r'[^0-9]', '', dob_raw)
        if not password_plain or len(password_plain) != 8:
            return jsonify({"error": "Invalid DOB format. Use DD/MM/YYYY"}), 400

        # Create User
        user = User(
            full_name     = full_name,
            email         = email,
            password_hash = generate_password_hash(password_plain),
            is_verified   = True,
            otp_code      = None,
            otp_expires_at= None,
        )
        db.session.add(user)
        db.session.flush()

        # Split name
        parts      = full_name.split(' ', 1)
        first_name = parts[0]
        last_name  = parts[1] if len(parts) > 1 else ''

        # Category lookup
        cat_id = None
        cat_raw = _str(data.get('category')).lower()
        if cat_raw:
            cat = ApplicationCategory.query.filter(
                db.func.lower(ApplicationCategory.name) == cat_raw
            ).first()
            cat_id = cat.id if cat else None

        # Create Application
        appl = StudentApplication(
            user_id      = user.id,
            first_name   = first_name,
            last_name    = last_name,
            dob          = dob_raw,
            email        = email,
            phone        = _str(data.get('student_mobile')),
            gender       = _str(data.get('gender')),
            address      = _str(data.get('address')),
            nationality  = _str(data.get('nationality')) or 'Indian',
            category_id  = cat_id,
            school_10    = _str(data.get('school_10')),
            board_10     = _str(data.get('board_10')),
            year_10      = _str(data.get('year_10')),
            total_10     = _flt(data.get('total_10')),
            obtained_10  = _flt(data.get('obtained_10')),
            percent_10   = _flt(data.get('percent_10')),
            school_12    = _str(data.get('school_12')),
            board_12     = _str(data.get('board_12')),
            stream_12    = _str(data.get('stream_12')),
            year_12      = _str(data.get('year_12')),
            total_12     = _flt(data.get('total_12')),
            obtained_12  = _flt(data.get('obtained_12')),
            percent_12   = _flt(data.get('percent_12')),
            specialization = _str(data.get('combination_12')),
            marksheet_10 = None,
            marksheet_12 = None,
        )
        db.session.add(appl)
        db.session.commit()

        # Send welcome email via Apps Script
        website_url = request.host_url.rstrip('/')
        try:
            requests.post(
                APPS_SCRIPT_URL,
                data={
                    'action': 'sendWelcome',
                    'email': email,
                    'name': full_name,
                    'password': password_plain,
                    'website': website_url
                },
                timeout=30
            )
        except Exception as e:
            print(f"Welcome email error: {e}")

        return jsonify({
            "message": "Student registered successfully",
            "user_id": user.id,
            "email":   email,
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


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
            session['admin_username'] = u
            session['admin_password_hash'] = generate_password_hash(p)
            return redirect(url_for('main.admin_dashboard'))
        flash('Invalid admin credentials.', 'danger')
    return render_template('admin_login.html')


@main.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    session.pop('admin_password_hash', None)
    return redirect(url_for('main.admin_login'))


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
#  Admin — Counselling Management
# ─────────────────────────────────────────
@main.route('/admin/counselling', methods=['GET', 'POST'])
@admin_required
def admin_counselling():
    cs         = CounsellingSettings.get()
    categories = ApplicationCategory.query.filter_by(is_active=True).all()
    cat_seats  = {cat.id: CategorySeats.get_for_category(cat.id) for cat in categories}
    students   = StudentApplication.query.filter_by(is_verified=True)\
                   .filter(StudentApplication.rank.isnot(None))\
                   .order_by(StudentApplication.rank.asc()).all()
    allotments = {a.application_id: a for a in SeatAllotment.query.all()}

    if request.method == 'POST':
        action = request.form.get('action')
        # ... (keep all existing counselling management code)
        pass

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
            wa.link = None; wa.description = None; wa.is_active = False
            wa.updated_at = datetime.utcnow()
            db.session.commit(); flash('WhatsApp link cleared.', 'info')
        else:
            link = request.form.get('link', '').strip()
            desc = request.form.get('description', '').strip()
            if not link:
                flash('Please enter a WhatsApp group link.', 'danger')
                return render_template('admin_whatsapp.html', wa=wa)
            if not link.startswith('https://chat.whatsapp.com/'):
                flash('Please enter a valid WhatsApp invite link.', 'danger')
                return render_template('admin_whatsapp.html', wa=wa)
            wa.link = link; wa.description = desc; wa.is_active = True
            wa.updated_at = datetime.utcnow()
            db.session.commit(); flash('WhatsApp group link updated!', 'success')
        return redirect(url_for('main.admin_whatsapp'))
    return render_template('admin_whatsapp.html', wa=wa)


# ─────────────────────────────────────────
#  Admin — Import Students from Excel (Full Feature)
# ─────────────────────────────────────────
@main.route('/admin/import', methods=['GET', 'POST'])
@admin_required
def admin_import():
    import pandas as pd
    import re
    from werkzeug.security import generate_password_hash
    
    # Handle POST requests (file upload or import)
    if request.method == 'POST':
        action = request.form.get('action')
        
        # Step 1: Upload file
        if action == 'upload':
            if 'excel_file' not in request.files:
                flash('No file selected', 'danger')
                return redirect(url_for('main.admin_import'))
            
            file = request.files['excel_file']
            if file.filename == '':
                flash('No file selected', 'danger')
                return redirect(url_for('main.admin_import'))
            
            if not file.filename.endswith(('.xlsx', '.xls')):
                flash('Please upload an Excel file (.xlsx or .xls)', 'danger')
                return redirect(url_for('main.admin_import'))
            
            try:
                # Read the Excel file
                df = pd.read_excel(file)
                
                # Generate a unique session ID for this import
                import uuid
                session_id = str(uuid.uuid4())
                session[f'import_{session_id}'] = {
                    'columns': df.columns.tolist(),
                    'preview': df.head(5).to_dict('records'),
                    'total_rows': len(df),
                    'filename': file.filename
                }
                session['current_import'] = session_id
                
                # Auto-map common column names
                auto_map = {}
                col_lower = {col.lower(): col for col in df.columns}
                
                field_mappings = {
                    'email': ['email', 'mail', 'e-mail'],
                    'full_name': ['full_name', 'fullname', 'name', 'student_name'],
                    'first_name': ['first_name', 'firstname', 'fname'],
                    'last_name': ['last_name', 'lastname', 'lname'],
                    'dob': ['dob', 'date_of_birth', 'birth_date', 'birthdate'],
                    'phone': ['phone', 'mobile', 'contact', 'student_mobile'],
                    'gender': ['gender'],
                    'address': ['address'],
                    'nationality': ['nationality'],
                    'category': ['category'],
                    'school_10': ['school_10', '10th_school', 'class10_school'],
                    'board_10': ['board_10', '10th_board'],
                    'year_10': ['year_10', '10th_year'],
                    'total_10': ['total_10', '10th_total'],
                    'obtained_10': ['obtained_10', '10th_obtained'],
                    'percent_10': ['percent_10', '10th_percent'],
                    'school_12': ['school_12', '12th_school', 'class12_school'],
                    'board_12': ['board_12', '12th_board'],
                    'stream_12': ['stream_12', '12th_stream'],
                    'year_12': ['year_12', '12th_year'],
                    'total_12': ['total_12', '12th_total'],
                    'obtained_12': ['obtained_12', '12th_obtained'],
                    'percent_12': ['percent_12', '12th_percent'],
                    'specialization': ['specialization', 'combination_12', 'spec']
                }
                
                for field, possible_names in field_mappings.items():
                    for name in possible_names:
                        if name in col_lower:
                            auto_map[field] = col_lower[name]
                            break
                
                return render_template('admin_import.html', 
                                     columns=df.columns.tolist(),
                                     preview_rows=df.head(5).to_dict('records'),
                                     total_rows=len(df),
                                     auto_map=auto_map,
                                     session_file=session_id)
                
            except Exception as e:
                flash(f'Error reading file: {str(e)}', 'danger')
                return redirect(url_for('main.admin_import'))
        
        # Step 2: Import data
        elif action == 'import':
            session_id = request.form.get('session_file')
            import_data = session.get(f'import_{session_id}')
            
            if not import_data:
                flash('Import session expired. Please upload the file again.', 'danger')
                return redirect(url_for('main.admin_import'))
            
            # Get column mappings from form
            mappings = {}
            for key in request.form:
                if key.startswith('map_'):
                    field = key[4:]  # Remove 'map_' prefix
                    column = request.form.get(key)
                    if column:
                        mappings[field] = column
            
            # Read the Excel file again
            filepath = import_data.get('filename')
            # Need to get the actual file - for now, show error
            flash('Please upload the file again to complete import.', 'warning')
            return redirect(url_for('main.admin_import'))
    
    # GET request - show upload form
    return render_template('admin_import.html')

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
    appl = StudentApplication.query.get_or_404(app_id)
    appl.is_verified = True; appl.verified_at = datetime.utcnow()
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
        student.is_verified = True; student.verified_at = datetime.utcnow(); count += 1
    db.session.commit()
    flash(f'Successfully verified {count} student(s)!', 'success')
    return redirect(url_for('main.admin_verification'))


@main.route('/admin/unverify/<int:app_id>', methods=['POST'])
@admin_required
def unverify_student(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    appl.is_verified = False; appl.verified_at = None
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
    category = ApplicationCategory.query.get_or_404(cat_id)
    category.is_active = not category.is_active
    db.session.commit()
    status = "activated" if category.is_active else "deactivated"
    flash(f'Category "{category.name}" {status}!', 'success')
    return redirect(url_for('main.admin_categories'))


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