# Offline LAN Hackathon Judging System

Production-focused backend-first implementation for a fully offline LAN judging system.

## Folder Structure

```text
backend/
frontend/
templates/
static/
```

## Backend Highlights

- Flask + SQLite (WAL mode enabled for concurrent writes).
- Session-based authentication with role-based access control (`admin`, `judge`).
- Admin APIs:
  - Manage judges, teams, criteria.
  - Manage judge-team assignments.
  - View all scores, dashboard metrics, rankings.
  - Manual ranking override endpoint.
  - Submission deadline control.
  - CSV export.
- Judge APIs:
  - View assigned teams.
  - Fetch team evaluation packet (problem statement, expected solution, criteria).
  - Save draft scores and remarks.
  - Submit final scores (requires all criteria scored).
  - Editing locked after submission deadline.
- Concurrency safety:
  - SQLite `WAL`, `busy_timeout`, foreign keys.
  - `BEGIN IMMEDIATE` transactional writes.
  - UPSERT patterns to prevent duplicate submission rows.
- Test suite includes:
  - Draft persistence.
  - Duplicate submission prevention.
  - Concurrent multi-judge submission simulation.

## Setup

1. Create and activate a Python virtual environment.
2. Install dependencies:

```bash
pip install -r backend/requirements.txt
```

3. Initialize database:

```bash
python backend/init_db.py
```

4. Seed sample data:

```bash
python backend/seed_data.py
```

5. Run the server:

```bash
python backend/app.py
```

Server binds to `0.0.0.0:5000`.

## Access Over LAN

1. On the server machine, find local IP:
   - Windows: `ipconfig`
2. Other LAN devices access:
   - `http://<SERVER_LOCAL_IP>:5000`
   - Example: `http://192.168.1.20:5000`
3. Ensure local firewall allows inbound TCP port `5000`.

## Seed Credentials

- Admin: `admin / admin123`
- Judge: `judge_1 / judge123`

## API Quick Map

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `GET|POST /api/admin/judges`
- `PUT|DELETE /api/admin/judges/<judge_id>`
- `GET|POST /api/admin/teams`
- `PUT|DELETE /api/admin/teams/<team_id>`
- `GET|POST /api/admin/criteria`
- `PUT|DELETE /api/admin/criteria/<criterion_id>`
- `GET /api/admin/assignments`
- `PUT /api/admin/assignments/<judge_id>`
- `GET /api/admin/scores`
- `GET /api/admin/dashboard`
- `GET /api/admin/rankings`
- `PUT /api/admin/rankings/override`
- `DELETE /api/admin/rankings/override/<team_id>`
- `GET /api/admin/settings/submission-deadline`
- `PUT /api/admin/settings/submission-deadline`
- `GET /api/admin/export/csv`
- `GET /api/judge/teams`
- `GET /api/judge/teams/<team_id>/evaluation`
- `PUT /api/judge/teams/<team_id>/draft`
- `POST /api/judge/teams/<team_id>/submit`
- `GET /api/health`

## Run Tests

```bash
python -m unittest discover -s backend/tests -p "test_*.py" -v
```

