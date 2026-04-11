import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from datetime import timezone, timedelta
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from database import init_db, db
from models import User

csrf = CSRFProtect()

IST = timezone(timedelta(hours=5, minutes=30))

def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'bca-admissions-secret-2026')
    app.config['API_SECRET_KEY'] = os.environ.get('API_SECRET_KEY')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///admissions.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
    app.config['ALLOWED_EXTENSIONS'] = {'pdf'}

    app.config['WTF_CSRF_CHECK_DEFAULT'] = False

    # Admin credentials
    app.config['ADMIN_USERNAME'] = os.environ.get('ADMIN_USERNAME', 'admin')
    app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', 'admin@bca2026')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    init_db(app)
    csrf.init_app(app)

    # ── IST Jinja2 filter — use {{ some_datetime | ist }} in any template ──
    @app.template_filter('ist')
    def ist_filter(dt):
        if dt is None:
            return ''
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IST).strftime('%d %b %Y, %I:%M %p')

    login_manager = LoginManager(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from routes import main
    app.register_blueprint(main)

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True)