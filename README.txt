======================================================
  BCA Admissions Portal — 2026-2027
  Flask Web Application with OTP Email Verification
======================================================

REQUIREMENTS
------------
Python 3.9 or higher

SETUP INSTRUCTIONS
------------------
1. Navigate to the project folder:
   cd applybca

2. Create a virtual environment (recommended):
   python -m venv venv
   source venv/bin/activate        # Mac/Linux
   venv\Scripts\activate           # Windows

3. Install dependencies:
   pip install -r requirements.txt

4. Configure Email (for OTP to work):
   Set these environment variables before running:

   Linux/Mac:
     export MAIL_USERNAME="yourgmail@gmail.com"
     export MAIL_PASSWORD="your_app_password"

   Windows (Command Prompt):
     set MAIL_USERNAME=yourgmail@gmail.com
     set MAIL_PASSWORD=your_app_password

   Gmail setup:
     - Enable 2-Step Verification on your Google account
     - Go to myaccount.google.com → Security → App Passwords
     - Generate an app password for "Mail"
     - Use that 16-character password as MAIL_PASSWORD

   DEV MODE (no email configured):
     If MAIL_USERNAME is not set, the OTP is shown in a flash
     message on screen — useful for local testing without email.

5. Run the app:
   python app.py

6. Open in browser:
   http://127.0.0.1:5000

ADMIN ACCESS
------------
  URL:      http://127.0.0.1:5000/admin/login
  Username: admin
  Password: admin@bca2026

  To change: set environment variables:
    ADMIN_USERNAME=yourusername
    ADMIN_PASSWORD=yourpassword

CUSTOMISATION
-------------
- Replace "[University Name]" in templates/application_form.html

OTP VERIFICATION FLOW
---------------------
1. Student registers with email + password
2. OTP (6-digit) sent to registered email (valid 10 min)
3. Student enters OTP on /verify-otp page
4. On success → account activated → login allowed
5. Resend OTP available if expired or not received

FEATURES
--------
Student side:
  ✓ Register with OTP email verification
  ✓ Login (blocked until email verified)
  ✓ Submit BCA application with PDF marksheet upload
  ✓ Auto-calculated percentage from marks
  ✓ View submitted application with verification/rank status

Admin side:
  ✓ Dashboard with stats (registered users, applications, etc.)
  ✓ View/search all applications
  ✓ Verify / unverify individual students
  ✓ View marksheet PDFs directly
  ✓ Assign ranks to verified students
  ✓ Open/close application form with optional timing
  ✓ Export all or verified students to Excel (.xlsx)

======================================================
