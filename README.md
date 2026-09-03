# Traffic Intelligence

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Framework-Flask-000000.svg)](https://flask.palletsprojects.com/)

Traffic Intelligence is a smart traffic-monitoring system for analysing road video, detecting vehicles, estimating speed, flagging violations, and generating evidence-based reports. The project combines computer-vision inference, a lightweight Flask backend, and a static dashboard for operational review.

This repository is structured for real-world use and is intentionally configured so runtime secrets, generated media, and local environment files stay out of GitHub.

## Why this project

Traffic monitoring teams need a practical workflow to:

- ingest video from roads or checkpoints
- detect moving vehicles in frames
- track vehicles across time
- estimate speed and density
- identify violations and risky events
- export evidence snapshots and reports

## Features

- Video upload and processing pipeline
- YOLO-based vehicle detection and tracking
- Speed, density, and violation analytics
- Snapshot and processed-video evidence generation
- SQLite-backed session history and reporting
- Flask REST API for dashboard and report access
- HTML/CSS/JavaScript frontend served by the backend

## Architecture

The project separates runtime concerns into a backend, frontend, data storage, and model assets.

- `backend/` contains the Flask app, processing pipeline, analytics, reports, and database helpers
- `frontend/` contains static dashboard and report pages
- `models/` stores local YOLO weights when needed
- `database/` is for local runtime database files
- `scripts/` contains operational utilities

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

- Python 3.12+
- Flask
- OpenCV
- Ultralytics YOLO
- NumPy, Pandas, SciPy, Matplotlib
- SQLite
- ReportLab, OpenPyXL
- HTML, CSS, JavaScript

## Quick start

1. Clone the repository
2. Create a virtual environment
3. Install dependencies
4. Copy `.env.example` to `.env`
5. Start the app

```bash
git clone https://github.com/mshayanahmed/Traffic-Intelligence.git
cd Traffic-Intelligence
python -m venv .venv

# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
copy .env.example .env
python backend/app.py
```

Then open:

- http://localhost:5000/
- http://localhost:5000/login.html
- http://localhost:5000/report.html

## Environment configuration

Copy the example env file and edit it with your local settings:

```bash
cp .env.example .env
```

The project expects values such as:

- `FLASK_ENV`
- `BACKEND_HOST`
- `BACKEND_PORT`
- `MODEL_PATH`
- `DATABASE_PATH`
- `UPLOAD_DIR`
- `EVIDENCE_DIR`
- `CONFIDENCE_THRESHOLD`
- `NMS_THRESHOLD`
- `ALERT_SPEED_THRESHOLD`

Do not commit secrets or local runtime files. `.env` and generated output are intentionally ignored by Git.

## API overview

Key endpoints include:

- `GET /api/health`
- `POST /api/upload`
- `GET /api/jobs/<id>/status`
- `GET /api/sessions`
- `GET /api/sessions/<id>/summary`
- `GET /api/sessions/<id>/violations`
- `GET /api/sessions/<id>/processed-video`
- `GET /api/sessions/<id>/evidence`
- `GET /api/sessions/<id>/report.csv`
- `GET /api/sessions/<id>/report.pdf`
- `GET /api/sessions/<id>/report.xlsx`

## Traffic detection workflow

1. Upload source video
2. Preprocess frames and metadata
3. Run YOLO detection on each frame
4. Match detections to tracked vehicle IDs
5. Estimate vehicle speed and movement
6. Overlay detections and trajectories
7. Save evidence and processed output
8. Generate analytics and downloadable reports

## Development

Install dependencies and run the project checks:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
```

## Contributing

We welcome improvements, bug reports, and feature ideas. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes.

## Security

If you discover a vulnerability or security issue, please follow the guidance in [SECURITY.md](SECURITY.md).

## License

This project is licensed under the [MIT License](LICENSE).

```bash
python scripts/validate_pdf.py
python -m unittest discover -s tests -v
```

Also test the live backend health endpoint and a small upload/processing cycle with a sample video.

## Troubleshooting

- If YOLO weights cannot be found, confirm `MODEL_PATH` points to the correct file in `models/`.
- If the database is missing, confirm the runtime path under `database/`.
- If uploads fail, verify backend upload directories exist and the size limit matches your environment.
- If static frontend assets do not load, confirm Flask is serving the project from the backend app context.

## Deployment Notes

The application can run behind a production WSGI server such as Gunicorn. Set
all paths and secrets through the deployment platform's environment settings;
do not upload a real `.env` file. Persistent storage is required for SQLite,
uploaded videos, processed videos, reports, and evidence. A hosted database and
object storage should be considered before a multi-instance deployment.

No deployment is performed by this repository setup.

## License

Released under the MIT License. See [LICENSE](LICENSE).
