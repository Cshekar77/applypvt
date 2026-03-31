import os
from flask import Flask
from flask_login import LoginManager
from flask_mail import Mail
from database import init_db, db
from models import User

mail = Mail()

def create_app():
    app = Flask(__name__)

    app.config['SECRET_KEY']                     = os.environ.get('SECRET_KEY', 'bca-admissions-secret-2026')
    app.config['SQLALCHEMY_DATABASE_URI']        = 'sqlite:///admissions.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['UPLOAD_FOLDER']                  = os.path.join(app.root_path, 'static', 'uploads')
    app.config['MAX_CONTENT_LENGTH']             = 5 * 1024 * 1024
    app.config['ALLOWED_EXTENSIONS']             = {'pdf'}

    # Admin credentials
    app.config['ADMIN_USERNAME'] = os.environ.get('ADMIN_USERNAME', 'admin')
    app.config['ADMIN_PASSWORD'] = os.environ.get('ADMIN_PASSWORD', 'admin@bca2026')

    # Flask-Mail (Gmail SMTP — set env vars before deployment)
    app.config['MAIL_SERVER']   = os.environ.get('MAIL_SERVER',   'smtp.gmail.com')
    app.config['MAIL_PORT']     = int(os.environ.get('MAIL_PORT', 587))
    app.config['MAIL_USE_TLS']  = True
    app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')   # your Gmail
    app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')   # app password
    app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'noreply@bcaadmissions.com')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    init_db(app)
    mail.init_app(app)

    login_manager = LoginManager(app)
    login_manager.login_view         = 'main.login'
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
