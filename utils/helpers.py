import os
import uuid
import random
import string
import requests
from datetime import datetime, timedelta
from flask import current_app
from werkzeug.utils import secure_filename

# Your Google Apps Script URL - REPLACE WITH YOUR ACTUAL URL
APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbwckpJunT6CZt0CNpLEL68cfnQK8EZ3n-9rbMzDOSMkNljpyuoCye0uYqptTt07kiYm/exec"  # <-- PUT YOUR REAL URL HERE


def allowed_file(filename):
    return (
        '.' in filename and
        filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']
    )


def save_pdf(file_storage):
    if not file_storage or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename):
        return None
    ext      = secure_filename(file_storage.filename).rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    path     = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file_storage.save(path)
    return filename


def calc_percent(total, obtained):
    try:
        t = float(total)
        o = float(obtained)
        if t > 0 and 0 <= o <= t:
            return round(o / t * 100, 2)
    except (TypeError, ValueError):
        pass
    return None


def generate_otp():
    """Generate a 6-digit numeric OTP."""
    return ''.join(random.choices(string.digits, k=6))


def send_otp_email(user_email, user_name, otp_code):
    """
    Send OTP verification email using Google Apps Script.
    Returns (success: bool, error: str|None).
    """
    try:
        # Call Google Apps Script
        response = requests.post(
            APPS_SCRIPT_URL,
            data={
                'action': 'sendOTP',
                'email': user_email,
                'name': user_name,
                'otp': otp_code
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            if result.get('success'):
                return True, None
            else:
                return False, result.get('message', 'Unknown error')
        else:
            return False, f"HTTP {response.status_code}"
            
    except Exception as e:
        return False, str(e)


def verify_otp_via_apps_script(email, otp):
    """
    Verify OTP using Google Apps Script.
    Returns (success: bool, message: str)
    """
    try:
        response = requests.post(
            APPS_SCRIPT_URL,
            data={
                'action': 'verifyOTP',
                'email': email,
                'otp': otp
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            return result.get('success', False), result.get('message', '')
        else:
            return False, f"HTTP {response.status_code}"
            
    except Exception as e:
        return False, str(e)