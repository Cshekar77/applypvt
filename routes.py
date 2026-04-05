from flask import (Blueprint, render_template, redirect, url_for,
                   flash, request, session, current_app, abort, send_file)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone

from database import db
from models import User, FormSettings, StudentApplication, ApplicationCategory
from forms.application_form import ApplicationForm
from utils.helpers import save_pdf, calc_percent, generate_otp, send_otp_email
from utils.exports import export_excel, export_verified_excel

main = Blueprint('main', __name__)

# IST timezone helper
IST = timezone(timedelta(hours=5, minutes=30))

def now_ist():
    """Return current datetime in Indian Standard Time."""
    return datetime.now(IST)

def utc_to_ist(dt):
    """Convert a naive UTC datetime to IST datetime."""
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
            otp = generate_otp()
            ok, err = send_otp_email(email, name, otp)
            if ok:
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
    temp_data = session.get('temp_registration')
    if not temp_data:
        flash('No pending registration found. Please register again.', 'warning')
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
                flash('A new OTP has been sent to your email.', 'success')
            else:
                flash('Could not send email. Please try again.', 'warning')
            return redirect(url_for('main.verify_otp'))

        entered   = request.form.get('otp', '').strip()
        stored_otp = temp_data.get('otp')
        expiry    = datetime.fromisoformat(temp_data.get('otp_expiry')) if temp_data.get('otp_expiry') else None

        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif expiry and datetime.utcnow() > expiry:
            flash('OTP has expired. Please request a new one.', 'danger')
        elif entered != stored_otp:
            flash('Incorrect OTP. Please try again.', 'danger')
        else:
            user = User(
                full_name      = temp_data['name'],
                email          = email,
                password_hash  = generate_password_hash(temp_data['password']),
                is_verified    = True,
                otp_code       = None,
                otp_expires_at = None,
            )
            db.session.add(user)
            db.session.commit()
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
#  Forgot Password
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
                    'email': email,
                    'otp': otp,
                    'otp_expiry': (datetime.utcnow() + timedelta(minutes=10)).isoformat()
                }
                flash('A 6-digit OTP has been sent to your email.', 'success')
                return redirect(url_for('main.verify_reset_otp'))
            else:
                flash('Could not send OTP. Please try again.', 'danger')

    return render_template('forgot_password.html')


@main.route('/verify-reset-otp', methods=['GET', 'POST'])
def verify_reset_otp():
    reset_data = session.get('reset_password')
    if not reset_data:
        flash('No password reset request found. Please try again.', 'warning')
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
                flash('A new OTP has been sent to your email.', 'success')
            else:
                flash('Could not send OTP. Please try again.', 'warning')
            return redirect(url_for('main.verify_reset_otp'))

        entered   = request.form.get('otp', '').strip()
        stored_otp = reset_data.get('otp')
        expiry    = datetime.fromisoformat(reset_data.get('otp_expiry'))

        if not entered:
            flash('Please enter the OTP.', 'danger')
        elif datetime.utcnow() > expiry:
            flash('OTP has expired. Please request a new one.', 'danger')
        elif entered != stored_otp:
            flash('Incorrect OTP. Please try again.', 'danger')
        else:
            session['reset_verified'] = email
            session.pop('reset_password', None)
            return redirect(url_for('main.reset_password'))

    return render_template('verify_reset_otp.html', email=email)


@main.route('/reset-password', methods=['GET', 'POST'])
def reset_password():
    email = session.get('reset_verified')
    if not email:
        flash('Unauthorized access. Please start again.', 'warning')
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
            flash('Password reset successfully! Please log in.', 'success')
            return redirect(url_for('main.login'))

    return render_template('reset_password.html')


# ─────────────────────────────────────────
#  Student
# ─────────────────────────────────────────
@main.route('/dashboard')
@login_required
def student_dashboard():
    appl     = StudentApplication.query.filter_by(user_id=current_user.id).first()
    settings = FormSettings.get()
    return render_template('student_dashboard.html', application=appl, settings=settings)


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
            specialization       = 'Other'

        category_id = request.form.get('category_id')
        if not category_id:
            flash('Please select a category.', 'danger')
            return render_template('application_form.html', form=form, categories=categories)

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
            specialization       = specialization,
            specialization_other = specialization_other,
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
            flash('New password cannot be the same as the current password.', 'warning')
        else:
            current_user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            flash('Password updated successfully!', 'success')
            return redirect(url_for('main.student_dashboard'))

    return render_template(
        'change_password.html',
        role='student',
        username=current_user.full_name,
        back_url=url_for('main.student_dashboard')
    )


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
    total     = StudentApplication.query.count()
    verified  = StudentApplication.query.filter_by(is_verified=True).count()
    pending   = total - verified
    ranked    = StudentApplication.query.filter(StudentApplication.rank.isnot(None)).count()
    reg_users = User.query.count()
    settings  = FormSettings.get()
    return render_template('admin_dashboard.html',
                           total=total, verified=verified,
                           pending=pending, ranked=ranked,
                           reg_users=reg_users, settings=settings)


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

        current_pw = current_app.config.get('ADMIN_PASSWORD', '')

        if old_password != current_pw:
            flash('Current password is incorrect.', 'danger')
        elif len(new_password) < 8:
            flash('New password must be at least 8 characters.', 'warning')
        elif new_password != confirm_password:
            flash('New passwords do not match.', 'danger')
        elif new_password == current_pw:
            flash('New password cannot be the same as the current password.', 'warning')
        else:
            current_app.config['ADMIN_PASSWORD'] = new_password
            flash('Password updated successfully! Remember to update your config/.env file too.', 'success')
            return redirect(url_for('main.admin_dashboard'))

    return render_template(
        'change_password.html',
        role='admin',
        username=current_app.config.get('ADMIN_USERNAME', 'Admin'),
        back_url=url_for('main.admin_dashboard')
    )


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


# ─────────────────────────────────────────
#  Admin — Verify All Students (Bulk verification)
# ─────────────────────────────────────────
@main.route('/admin/verify-all', methods=['POST'])
@admin_required
def verify_all_students():
    # Get all pending students
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
                 .order_by(StudentApplication.rank.asc().nullslast(),
                           StudentApplication.verified_at).all()
    return render_template('admin_verified.html', students=students)


# ─────────────────────────────────────────
#  Admin — Rank Management
#  Shows unsorted when no ranks exist, sorted by rank after ranks are saved
# ─────────────────────────────────────────
@main.route('/admin/rank')
@admin_required
def admin_rank():
    # Check if any verified student has a rank assigned
    has_ranks = StudentApplication.query.filter_by(is_verified=True)\
                 .filter(StudentApplication.rank.isnot(None)).first()
    
    if has_ranks:
        # If ranks exist, show sorted by rank
        students = StudentApplication.query.filter_by(is_verified=True)\
                     .order_by(StudentApplication.rank.asc()).all()
    else:
        # If no ranks exist, show unsorted (by submission order)
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
#  Category Management (Admin)
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
    existing = ApplicationCategory.query.filter_by(name=name).first()
    if existing:
        flash(f'Category "{name}" already exists.', 'danger')
        return redirect(url_for('main.admin_categories'))
    category = ApplicationCategory(name=name, is_active=True)
    db.session.add(category)
    db.session.commit()
    flash(f'Category "{name}" added successfully!', 'success')
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
    category = ApplicationCategory.query.get_or_404(cat_id)
    name     = category.name
    apps_count = StudentApplication.query.filter_by(category_id=cat_id).count()
    if apps_count > 0:
        flash(f'Cannot delete "{name}" - {apps_count} student(s) are using this category.', 'danger')
    else:
        db.session.delete(category)
        db.session.commit()
        flash(f'Category "{name}" deleted successfully!', 'success')
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