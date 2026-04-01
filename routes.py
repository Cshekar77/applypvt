from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, current_app, abort)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

from database import db
from models import User, FormSettings, StudentApplication, ApplicationCategory  # Added ApplicationCategory
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
#  Registration + OTP (Account created ONLY after OTP verification)
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
            # Generate OTP but DON'T create account yet
            otp = generate_otp()
            
            # Send OTP first
            ok, err = send_otp_email(email, name, otp)
            
            if ok:
                # Store registration data in session temporarily
                session['temp_registration'] = {
                    'name': name,
                    'email': email,
                    'password': password,
                    'otp': otp,
                    'otp_expiry': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                }
                flash('A 6-digit OTP has been sent to your email. Enter it below to verify.', 'success')
                return redirect(url_for('main.verify_otp'))
            else:
                flash(f'Could not send OTP. Please try again. Error: {err}', 'danger')
                return redirect(url_for('main.register'))

    return render_template('register.html')


@main.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    # Get temp registration data from session
    temp_data = session.get('temp_registration')
    if not temp_data:
        flash('No pending registration found. Please register again.', 'warning')
        return redirect(url_for('main.register'))

    email = temp_data['email']
    
    # Check if user already exists (shouldn't happen with this flow)
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        session.pop('temp_registration', None)
        flash('Account already exists. Please login.', 'info')
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'resend':
            # Generate new OTP
            new_otp = generate_otp()
            ok, err = send_otp_email(email, temp_data['name'], new_otp)
            
            if ok:
                # Update session with new OTP
                temp_data['otp'] = new_otp
                temp_data['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                session['temp_registration'] = temp_data
                flash('A new OTP has been sent to your email.', 'success')
            else:
                flash(f'Could not send email. Please try again.', 'warning')
            return redirect(url_for('main.verify_otp'))

        entered = request.form.get('otp', '').strip()
        stored_otp = temp_data.get('otp')
        expiry = datetime.fromisoformat(temp_data.get('otp_expiry')) if temp_data.get('otp_expiry') else None
        
        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif expiry and datetime.utcnow() > expiry:
            flash('OTP has expired. Please request a new one.', 'danger')
        elif entered != stored_otp:
            flash('Incorrect OTP. Please try again.', 'danger')
        else:
            # OTP verified - NOW create the account
            user = User(
                full_name     = temp_data['name'],
                email         = email,
                password_hash = generate_password_hash(temp_data['password']),
                is_verified   = True,  # Already verified via OTP
                otp_code      = None,
                otp_expires_at = None,
            )
            db.session.add(user)
            db.session.commit()
            
            # Clear temp data
            session.pop('temp_registration', None)
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
    
    # Get categories for dropdown
    categories = ApplicationCategory.query.filter_by(is_active=True).all()
    
    if request.method == 'GET':
        # Pass categories to template
        return render_template('application_form.html', form=form, categories=categories)
    
    if form.validate_on_submit():
        p10  = calc_percent(form.total_10.data, form.obtained_10.data)
        p12  = calc_percent(form.total_12.data, form.obtained_12.data)
        
        # Handle specialization
        specialization = form.specialization.data
        specialization_other = None
        if specialization == 'Other':
            specialization_other = form.specialization_other.data
            specialization = 'Other'
        
        # Get category ID from form
        category_id = request.form.get('category_id')
        if not category_id:
            flash('Please select a category.', 'danger')
            return render_template('application_form.html', form=form, categories=categories)
        
        # PDF uploads disabled for testing
        ms10 = None
        ms12 = None
        
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
            # New fields
            specialization      = specialization,
            specialization_other = specialization_other,
            category_id         = int(category_id),
        )
        db.session.add(appl)
        db.session.commit()
        flash('Application submitted successfully!', 'success')
        return redirect(url_for('main.my_application'))

    # If form validation fails
    return render_template('application_form.html', form=form, categories=categories)


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