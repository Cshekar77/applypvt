from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, current_app, abort)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

from database import db
from models import User, FormSettings, StudentApplication
from forms.application_form import ApplicationForm
from utils.helpers import save_pdf, calc_percent, generate_otp, send_otp_email
from utils.exports import export_excel

main = Blueprint('main', __name__)


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
    return render_template('index.html', settings=settings)


# ─────────────────────────────────────────
#  Registration + OTP
# ─────────────────────────────────────────
@main.route('/register', methods=['GET', 'POST'])
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
            otp  = generate_otp()
            exp  = datetime.utcnow() + timedelta(minutes=10)
            user = User(
                full_name     = name,
                email         = email,
                password_hash = generate_password_hash(password),
                is_verified   = False,
                otp_code      = otp,
                otp_expires_at= exp,
            )
            db.session.add(user)
            db.session.commit()

            ok, err = send_otp_email(email, name, otp)
            if ok:
                flash('Account created! A 6-digit OTP has been sent to your email. Enter it below to verify.', 'success')
            else:
                # still allow verification flow; show OTP in flash for dev/testing
                flash(f'Account created but email could not be sent ({err}). '
                      f'[DEV MODE] Your OTP is: {otp}', 'warning')

            session['pending_verify_email'] = email
            return redirect(url_for('main.verify_otp'))

    return render_template('register.html')


@main.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    email = session.get('pending_verify_email')
    if not email:
        return redirect(url_for('main.register'))

    user = User.query.filter_by(email=email).first()
    if not user:
        return redirect(url_for('main.register'))

    if user.is_verified:
        session.pop('pending_verify_email', None)
        flash('Email already verified. Please log in.', 'info')
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'resend':
            otp = generate_otp()
            user.otp_code       = otp
            user.otp_expires_at = datetime.utcnow() + timedelta(minutes=10)
            db.session.commit()
            ok, err = send_otp_email(email, user.full_name, otp)
            if ok:
                flash('A new OTP has been sent to your email.', 'success')
            else:
                flash(f'Could not send email. [DEV MODE] OTP: {otp}', 'warning')
            return redirect(url_for('main.verify_otp'))

        entered = request.form.get('otp', '').strip()
        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif datetime.utcnow() > user.otp_expires_at:
            flash('OTP has expired. Please request a new one.', 'danger')
        elif entered != user.otp_code:
            flash('Incorrect OTP. Please try again.', 'danger')
        else:
            user.is_verified   = True
            user.otp_code      = None
            user.otp_expires_at = None
            db.session.commit()
            session.pop('pending_verify_email', None)
            flash('Email verified successfully! You can now log in.', 'success')
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
            session['pending_verify_email'] = email
            flash('Please verify your email first.', 'warning')
            return redirect(url_for('main.verify_otp'))
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
#  Student
# ─────────────────────────────────────────
@main.route('/dashboard')
@login_required
def student_dashboard():
    appl = StudentApplication.query.filter_by(user_id=current_user.id).first()
    settings = FormSettings.get()
    return render_template('student_dashboard.html', application=appl, settings=settings)


@main.route('/apply', methods=['GET', 'POST'])
@login_required
def apply():
    if StudentApplication.query.filter_by(user_id=current_user.id).first():
        flash('You have already submitted your application.', 'info')
        return redirect(url_for('main.my_application'))

    settings = FormSettings.get()
    now      = datetime.utcnow()
    form_open = settings.is_open
    if settings.open_from  and now < settings.open_from:  form_open = False
    if settings.open_until and now > settings.open_until: form_open = False
    if not form_open:
        return render_template('form_closed.html', settings=settings)

    form = ApplicationForm()
    if form.validate_on_submit():
        p10  = calc_percent(form.total_10.data, form.obtained_10.data)
        p12  = calc_percent(form.total_12.data, form.obtained_12.data)
        ms10 = save_pdf(form.marksheet_10.data)
        ms12 = save_pdf(form.marksheet_12.data)

        if not ms10 or not ms12:
            flash('PDF upload failed. Ensure files are valid PDFs under 5 MB.', 'danger')
            return render_template('application_form.html', form=form)

        appl = StudentApplication(
            user_id        = current_user.id,
            first_name     = form.first_name.data,
            last_name      = form.last_name.data,
            dob            = form.dob.data,
            gender         = form.gender.data,
            nationality    = form.nationality.data,
            email          = form.email.data,
            phone          = form.phone.data,
            address        = form.address.data,
            school_10      = form.school_10.data,
            board_10       = form.board_10.data,
            board_10_other = form.board_10_other.data,
            year_10        = form.year_10.data,
            total_10       = form.total_10.data,
            obtained_10    = form.obtained_10.data,
            percent_10     = p10,
            marksheet_10   = ms10,
            school_12      = form.school_12.data,
            board_12       = form.board_12.data,
            board_12_other = form.board_12_other.data,
            stream_12      = form.stream_12.data,
            year_12        = form.year_12.data,
            total_12       = form.total_12.data,
            obtained_12    = form.obtained_12.data,
            percent_12     = p12,
            marksheet_12   = ms12,
            why_bca        = form.why_bca.data,
        )
        db.session.add(appl)
        db.session.commit()
        flash('Application submitted successfully!', 'success')
        return redirect(url_for('main.my_application'))

    return render_template('application_form.html', form=form)


@main.route('/my-application')
@login_required
def my_application():
    appl = StudentApplication.query.filter_by(user_id=current_user.id).first()
    if not appl:
        return redirect(url_for('main.apply'))
    return render_template('my_application.html', appl=appl)


# ─────────────────────────────────────────
#  Admin
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
            return redirect(url_for('main.admin_dashboard'))
        flash('Invalid admin credentials.', 'danger')
    return render_template('admin_login.html')


@main.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('main.admin_login'))


@main.route('/admin')
@admin_required
def admin_dashboard():
    total    = StudentApplication.query.count()
    verified = StudentApplication.query.filter_by(is_verified=True).count()
    pending  = total - verified
    ranked   = StudentApplication.query.filter(StudentApplication.rank.isnot(None)).count()
    reg_users = User.query.count()
    settings = FormSettings.get()
    return render_template('admin_dashboard.html',
                           total=total, verified=verified,
                           pending=pending, ranked=ranked,
                           reg_users=reg_users, settings=settings)


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
    appl.is_verified = True
    appl.verified_at = datetime.utcnow()
    appl.admin_notes = request.form.get('notes', appl.admin_notes)
    db.session.commit()
    flash(f'{appl.full_name} verified.', 'success')
    return redirect(url_for('main.admin_verification'))


@main.route('/admin/unverify/<int:app_id>', methods=['POST'])
@admin_required
def unverify_student(app_id):
    appl = StudentApplication.query.get_or_404(app_id)
    appl.is_verified = False
    appl.verified_at = None
    db.session.commit()
    flash(f'{appl.full_name} moved back to pending.', 'info')
    return redirect(url_for('main.admin_verified'))


@main.route('/admin/verified')
@admin_required
def admin_verified():
    students = StudentApplication.query.filter_by(is_verified=True)\
                 .order_by(StudentApplication.rank.nullslast(),
                           StudentApplication.verified_at).all()
    return render_template('admin_verified.html', students=students)


@main.route('/admin/rank')
@admin_required
def admin_rank():
    students = StudentApplication.query.filter_by(is_verified=True)\
                 .order_by(StudentApplication.percent_12.desc()).all()
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


@main.route('/admin/export/all')
@admin_required
def export_all():
    apps = StudentApplication.query.order_by(StudentApplication.submitted_at).all()
    return export_excel(apps, 'all_applications.xlsx')


@main.route('/admin/export/verified')
@admin_required
def export_verified():
    apps = StudentApplication.query.filter_by(is_verified=True)\
             .order_by(StudentApplication.rank.nullslast()).all()
    return export_excel(apps, 'verified_students.xlsx')