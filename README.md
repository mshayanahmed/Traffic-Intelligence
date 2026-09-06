# Traffic Intelligence

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Framework-Flask-000000.svg)](https://flask.palletsprojects.com/)

Traffic Intelligence is a smart traffic-monitoring system for analysing road video, detecting vehicles, estimating speed, tracking traffic movement, identifying potential violations, and generating evidence-based reports. The project combines computer-vision inference, a Flask backend, database-backed session management, secure authentication, email verification, role-based access control, and a web-based operational dashboard.

This repository is structured for real-world use and is intentionally configured so runtime secrets, generated media, private user data, authentication credentials, local databases, and local environment files stay out of GitHub.

## Why this project

Traffic monitoring teams need a practical workflow to:

* ingest video from roads, checkpoints, or traffic cameras
* detect moving vehicles in video frames
* track vehicles across time
* estimate vehicle speed and traffic density
* analyse traffic movement and trajectories
* identify violations and risky events
* generate evidence snapshots
* review processed video and session analytics
* export evidence and operational reports
* provide controlled access through secure authentication and user roles

## Features

* Video upload and processing pipeline
* YOLO-based vehicle detection and tracking
* Vehicle ID tracking across video frames
* Speed estimation
* Traffic-density analysis
* Vehicle trajectories
* Traffic heatmap
* Violation detection and analysis
* Snapshot and processed-video evidence generation
* SQLite-backed session history and reporting
* Flask REST API for dashboard and report access
* HTML/CSS/JavaScript frontend
* Secure user registration and login
* Email verification using one-time passwords (OTP)
* Verification-code resend flow
* Secure logout
* Password change
* Password reset and account recovery
* Protected application routes
* Role-based access control
* Normal user access
* Administrator access
* Admin dashboard
* User management
* Account status management
* Email verification status management
* System and AI/model status monitoring
* Authentication and security settings
* PDF, CSV, and Excel reporting

## Authentication

Traffic Intelligence provides a secure authentication system for registered users and authorized administrators.

The authentication system supports:

* User registration
* Email verification
* OTP verification
* Secure login
* Secure logout
* Protected routes
* Password change
* Password recovery
* Account status checks
* Role-based authorization
* Administrator-only functionality

Public registration creates a normal user account. Administrative privileges cannot be self-assigned through the public signup flow.

## User roles

### Normal user

Normal users can access the core Traffic Intelligence functionality.

Depending on the current application configuration, normal users may access:

* Live Feed
* Camera
* Overview
* Heatmap
* Violations
* Evidence
* Reports
* Sessions
* supported settings
* video upload and processing
* traffic analytics

Normal users cannot access:

* Admin dashboard
* User management
* Administrative security controls
* Developer-only configuration
* Other users' private account information
* Authentication administration
* System secrets
* Privileged configuration
* Administrative audit/security information

### Administrator

Authorized administrators have additional access to management and security functionality.

Administrator capabilities may include:

* Admin dashboard
* User list
* User details
* User role information
* Email verification status
* Account status
* Registration date
* Last login information
* User activity information where appropriate
* User account management
* Security settings
* Authentication management
* Password management
* System health/status
* AI/model status
* Administrative configuration
* Audit/security information
* Account enable/disable controls where implemented

Administrators must never be able to view plaintext user passwords.

Passwords are stored securely and are never returned through administrative interfaces.

## Administrator account

The initial administrator account is provisioned through the application's secure bootstrap or environment configuration mechanism.

The administrator password must never be hard-coded into source code.

Administrator credentials must be configured using secure environment variables or another controlled provisioning mechanism.

Public users cannot register themselves as administrators.

Role assignment is enforced server-side.

Where administrative email-domain restrictions are configured, privileged accounts must use the approved administrative domain.

## Email verification

New users must verify their email address before normal authentication is completed.

The registration flow is:

```text
Create Account
      ↓
Enter Name, Email, Password
      ↓
Create Account
      ↓
Verification OTP Sent
      ↓
Enter OTP
      ↓
Email Verified
      ↓
Login Page
      ↓
Sign In
      ↓
Traffic Intelligence Dashboard
```

New users are not automatically logged into the dashboard immediately after signup.

## OTP verification

The email verification system uses a one-time verification code.

Verification codes are designed to be:

* cryptographically generated
* time-limited
* single-use
* protected against brute-force attempts
* invalidated after successful verification
* replaced when a new code is generated
* protected by resend limits

Typical states include:

* Verification code sent
* Invalid verification code
* Verification code expired
* Verification successful
* Verification code resent

The application should not expose verification secrets through browser responses or logs.

## Login

The login process validates the user's credentials, account status, email verification status, and authorization requirements.

The typical flow is:

```text
Login Page
      ↓
Enter Email / Username
      ↓
Enter Password
      ↓
Validate Credentials
      ↓
Check Account Status
      ↓
Check Email Verification
      ↓
Create Authenticated Session
      ↓
Traffic Intelligence Dashboard
```

If the credentials are correct but the user's email has not been verified, normal dashboard access is blocked until the verification process is completed.

## Login greeting

After a successful login, the application may send a professional login notification or greeting email to the user's verified email address.

The greeting may contain:

* User's name
* Successful sign-in notification
* Traffic Intelligence branding
* General account-security information

The email must never contain:

* Passwords
* OTP values
* Authentication tokens
* JWT/session secrets
* API keys
* Database credentials

An optional email-delivery failure should not unnecessarily prevent an otherwise valid login unless the security architecture explicitly requires it.

## Logout

The logout process is:

```text
Dashboard
      ↓
Sign Out
      ↓
Authentication State Cleared
      ↓
Login Page
```

Logout should:

* clear the authenticated client state
* invalidate server-side authentication where applicable
* remove stale authentication state
* prevent access to protected pages after logout
* allow the same valid account to log in again

## Password management

Authenticated users can change their password through the supported security settings.

Password changes should:

* require appropriate authentication
* verify the current password where applicable
* securely hash the new password
* invalidate old credentials where appropriate
* never expose the previous password
* never store passwords in plaintext

Administrator passwords follow the same security principles.

## Password recovery

The password recovery flow is:

```text
Forgot Password
      ↓
Enter Email
      ↓
Verification / Reset Code
      ↓
Set New Password
      ↓
Return to Login
      ↓
Sign In
```

Password-reset tokens or codes should be:

* time-limited
* single-use
* protected against brute-force attempts
* invalidated after successful use

Public recovery responses should avoid unnecessarily revealing whether a specific account exists.

## Protected routes

Protected application routes require authentication.

Examples include:

```text
/#/live
/#/camera
/#/overview
/#/heatmap
/#/violations
/#/evidence
/#/reports
/#/sessions
/#/settings
```

Administrative routes additionally require administrator authorization.

Examples include:

```text
/#/admin
/#/admin/users
/#/admin/security
/#/admin/settings
```

Unauthenticated users attempting to directly open protected routes should be redirected to the login flow.

Normal users attempting to open administrative routes should receive an access-denied response or be redirected to an appropriate normal-user page.

Authorized administrators may access administrative routes.

## Account data

A user account may contain appropriate profile and authentication metadata such as:

* Name
* Email
* Role
* Account status
* Email verification status
* Registration timestamp
* Last login timestamp

Sensitive authentication information must not be exposed to ordinary users.

Administrators may view appropriate account-management information but must never receive plaintext passwords.

## Admin user management

The administrator interface may provide a user-management table containing:

* Name
* Email
* Role
* Email verification status
* Account status
* Registration date
* Last login
* Available administrative actions

Depending on the implementation, administrators may be able to:

* View user details
* Enable or disable an account
* Review verification status
* Trigger verification workflows
* Manage permitted roles
* Review authentication/security activity

Sensitive credentials are never displayed.

## Architecture

The project separates runtime concerns into a backend, frontend, authentication layer, database, model assets, generated data, and operational tooling.

* `backend/` contains the Flask app, authentication, authorization, processing pipeline, analytics, reports, and database helpers
* `frontend/` contains authentication pages, dashboard pages, report pages, and operational interfaces
* `models/` stores local YOLO weights when needed
* `database/` is for local runtime database files
* `scripts/` contains operational utilities
* `tests/` contains automated testing and validation

## Project structure

```text
Traffic-Intelligence/
├── backend/
│   ├── analytics.py
│   ├── api.py
│   ├── app.py
│   ├── auth.py
│   ├── config.py
│   ├── database.py
│   ├── generate_report.py
│   ├── job_manager.py
│   ├── live_feed.py
│   ├── process_video.py
│   ├── session_store.py
│   └── requirements.txt
├── frontend/
│   ├── css/
│   ├── images/
│   ├── js/
│   ├── index.html
│   ├── login.html
│   ├── report.html
│   └── results.html
├── models/
├── scripts/
├── tests/
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── requirements.txt
└── .github/
```

## Tech stack

* Python 3.12+
* Flask
* Flask-CORS
* Gunicorn
* OpenCV
* Ultralytics YOLO
* NumPy, Pandas, SciPy, Scikit-learn
* SQLite
* ReportLab
* OpenPyXL
* HTML, CSS, JavaScript
* ECharts
* Lucide

## Quick start

1. Clone the repository
2. Create a virtual environment
3. Activate the environment
4. Install dependencies
5. Copy `.env.example` to `.env`
6. Configure the environment
7. Start the application

```bash
git clone https://github.com/mshayanahmed/Traffic-Intelligence.git
cd Traffic-Intelligence
python -m venv .venv
```

### Windows PowerShell

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### macOS/Linux

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure the `.env` file with the appropriate local values.

Start the application using the backend startup command:

```bash
python backend/app.py
```

Then open:

* http://localhost:5000/
* http://localhost:5000/login.html
* http://localhost:5000/report.html

The exact routes may depend on the current frontend routing implementation.

## Environment configuration

Copy the example environment file and edit it with your local settings.

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

### macOS/Linux

```bash
cp .env.example .env
```

The project may use configuration values such as:

* `FLASK_ENV`
* `BACKEND_HOST`
* `BACKEND_PORT`
* `MODEL_PATH`
* `DATABASE_PATH`
* `UPLOAD_DIR`
* `EVIDENCE_DIR`
* `CONFIDENCE_THRESHOLD`
* `NMS_THRESHOLD`
* `ALERT_SPEED_THRESHOLD`
* `FRONTEND_URL`
* `APP_URL`
* authentication/session secret
* JWT secret where applicable
* SMTP host
* SMTP port
* SMTP username
* SMTP password
* sender email configuration
* OTP configuration

Use the exact environment-variable names defined by `.env.example` and the current implementation.

Never place real production secrets in the repository.

## API overview

Key application endpoints include:

### Health and configuration

* `GET /api/health`
* `GET /api/config`

The configuration endpoint must expose only information that is safe for the frontend to receive.

Sensitive secrets must never be returned through `/api/config`.

### Authentication

The authentication API provides functionality for:

* Registration
* Login
* Logout
* Email verification
* OTP resend
* Password recovery
* Password reset
* Password change
* Authentication/session validation

The exact endpoint names depend on the current implementation.

Authentication endpoints should enforce appropriate validation, rate limiting, and authorization.

### Video processing

* `POST /api/upload`
* `GET /api/jobs/<id>/status`

### Sessions

* `GET /api/sessions`
* `GET /api/sessions/<id>/summary`
* `GET /api/sessions/<id>/violations`
* `GET /api/sessions/<id>/processed-video`
* `GET /api/sessions/<id>/evidence`

### Reports

* `GET /api/sessions/<id>/report.csv`
* `GET /api/sessions/<id>/report.pdf`
* `GET /api/sessions/<id>/report.xlsx`

### Administrative APIs

Administrative endpoints, where implemented, require administrator authorization.

Sensitive user-management APIs must never be available to ordinary users.

## Traffic detection workflow

1. Upload source video
2. Preprocess frames and metadata
3. Run YOLO detection on each frame
4. Match detections to tracked vehicle IDs
5. Estimate vehicle speed and movement
6. Analyse traffic density
7. Analyse potential violations
8. Generate trajectories and heatmap information
9. Save evidence and processed output
10. Generate session analytics
11. Generate downloadable reports

## Dashboard sections

Traffic Intelligence provides a web dashboard for operational traffic analysis.

### Live Feed

Provides the live or processed detection interface, including available vehicle counts, confidence values, speed estimates, violations, trajectories, and AI analysis.

### Camera

Provides supported camera and source workflows.

### Overview

Displays high-level traffic statistics and operational analysis.

### Heatmap

Displays traffic-density and movement visualization based on available processed data.

### Violations

Displays detected or classified traffic violations associated with processing sessions.

### Evidence

Provides generated evidence images and related artifacts.

### Reports

Provides generated PDF, CSV, and Excel reports where available.

### Sessions

Provides processing history, session summaries, violations, evidence, and generated outputs.

### Settings

Provides supported user and application settings.

Administrative settings are restricted to authorized administrators.

## Development

Install dependencies and run the project checks:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
```

Additional project validation may include:

```bash
python scripts/validate_pdf.py
python -m unittest discover -s tests -v
```

Run only the commands supported by the current repository.

## Testing

The test suite should verify the following areas.

### Authentication

* User registration
* Email verification
* OTP verification
* OTP expiration
* OTP resend protection
* Login
* Logout
* Login after logout
* Protected route access
* Password change
* Password recovery
* Password reset

### Authorization

* Public signup creates a normal user
* Public signup cannot create an administrator
* Normal users cannot access administrative APIs
* Normal users cannot access administrator pages
* Administrators can access authorized administrator pages
* Administrator permissions are checked server-side
* User passwords are never returned

### Application

* Dashboard loads
* Live Feed loads
* Camera interface loads
* Overview loads
* Heatmap loads
* Violations load
* Evidence loads
* Reports load
* Sessions load
* Settings load

### Computer vision

* Video upload works
* Processing job is created
* Job status is available
* YOLO model initializes correctly
* Vehicle detection works
* Vehicle tracking works
* Speed/density analysis works
* Violation analysis works
* Evidence is generated
* Processed video is generated

### Reports

* CSV report generation
* PDF report generation
* Excel report generation

### Production verification

Also test:

* Backend health endpoint
* Authentication flow
* Email verification
* Password recovery
* Direct protected routes
* Administrator authorization
* Small upload/processing cycle
* Frontend API connectivity

## Contributing

We welcome improvements, bug reports, and feature ideas. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

Before submitting changes:

* do not commit secrets
* do not commit `.env`
* run relevant tests
* update documentation when behavior changes
* preserve server-side authorization
* avoid exposing private user information
* avoid unrelated changes to the computer-vision pipeline

## Security

Traffic Intelligence is designed so authentication credentials, runtime secrets, and private runtime data remain outside source control.

Never commit:

* passwords
* OTP values
* SMTP passwords
* API keys
* JWT/session secrets
* database credentials
* authentication tokens
* production `.env` files
* private certificates
* private user data

Passwords must be securely hashed.

Administrator roles must be assigned and validated server-side.

Public registration must never allow a user to select an administrator role.

Administrative APIs must independently verify the authenticated user's permissions.

Sensitive configuration must never be returned to ordinary users.

If you discover a vulnerability or security issue, please follow the guidance in [SECURITY.md](SECURITY.md).

## License

This project is licensed under the [MIT License](LICENSE).

## Troubleshooting

* If login fails, verify the backend, database, credentials, account status, and authentication/session configuration.
* If signup does not complete, verify database connectivity and required registration fields.
* If email verification fails, verify SMTP configuration, sender configuration, email delivery, and OTP settings.
* If OTP verification fails, verify the code, expiration time, and resend/attempt limits.
* If password reset fails, verify email delivery, reset-token configuration, and database connectivity.
* If logout does not work correctly, verify that client authentication state and server-side session state are both being cleared.
* If logging in again after logout fails, inspect token/session handling, cookies, credentials, and logout behavior.
* If a protected direct route opens without authentication, verify the frontend route guard and backend authorization.
* If a normal user can access an administrator page, verify server-side role checks.
* If `/api/config` returns `403`, verify its intended access policy, authentication middleware, CORS/preflight behavior, and frontend credentials.
* If YOLO weights cannot be found, confirm `MODEL_PATH` points to the correct file in `models/`.
* If the AI model fails to initialize, check model availability, dependencies, RAM/CPU capacity, storage, and backend logs.
* If the database is missing, confirm the configured database path and runtime storage.
* If uploads fail, verify upload directories, permissions, file-size limits, and available storage.
* If reports fail, verify ReportLab, OpenPyXL, output directories, and session data.
* If static frontend assets do not load, confirm that the frontend assets are being served correctly.
* If dashboard JavaScript errors appear, check the browser console and verify that all required scripts/functions are loaded.

## Deployment Notes

The application can run behind a production WSGI server such as Gunicorn.

Set all paths, authentication configuration, email settings, database configuration, and secrets through the deployment platform's environment settings.

Do not upload a real `.env` file.

Production deployments should configure appropriate values for:

* Application URL
* Frontend URL
* Authentication/session secrets
* Database
* SMTP/email delivery
* Model paths
* Upload paths
* Evidence paths
* Runtime storage
* Application-specific configuration

## Persistent storage

Traffic Intelligence may require persistent storage for:

* SQLite database files
* Uploaded videos
* Processed videos
* Evidence images
* Generated reports
* Runtime-generated data
* Model files where applicable

For multi-instance production deployments, a managed database and object/object-file storage should be considered.

## Production security

Production deployments should:

* use HTTPS
* protect authentication secrets
* securely hash passwords
* restrict administrator access
* enforce server-side authorization
* configure appropriate CORS rules
* configure secure session/cookie settings where applicable
* rate-limit login and OTP operations
* protect password-reset functionality
* prevent sensitive configuration from being returned by APIs
* keep secrets outside Git
* monitor relevant authentication/security events
* keep dependencies maintained

## Deployment verification

Before considering a production deployment ready, verify:

* application loads
* login works
* signup works
* email verification works
* OTP expiration works
* OTP resend works
* verified user can log in
* unverified user cannot bypass verification
* logout works
* user can log in again after logout
* direct protected routes redirect correctly
* normal users cannot access admin routes
* administrators can access authorized admin routes
* administrator password change works
* password recovery works
* `/api/config` follows the intended authorization policy
* dashboard has no uncaught application JavaScript errors
* heatmap works
* video upload works
* video processing works
* evidence generation works
* report generation works
* AI/model status is correct
* production secrets are configured outside Git

## Deployment workflow

A typical deployment workflow is:

```text
Local Development
      ↓
Run Tests
      ↓
Review Git Changes
      ↓
Commit Changes
      ↓
Push to GitHub
      ↓
Deploy Frontend
      ↓
Deploy Backend
      ↓
Configure Production Environment Variables
      ↓
Verify Authentication
      ↓
Verify Dashboard
      ↓
Verify Video Processing
      ↓
Verify Reports and Evidence
```

No production secrets are stored in this repository.

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
