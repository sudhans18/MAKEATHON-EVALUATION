import csv
import io
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, request, session, send_file, render_template
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from db import close_db, get_db, init_connection_pool, init_db, transaction


def _ensure_parent(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _configure_logging(app):
    os.makedirs(app.config["LOG_DIR"], exist_ok=True)
    log_path = os.path.join(app.config["LOG_DIR"], "server.log")
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    if not any(isinstance(h, RotatingFileHandler) for h in app.logger.handlers):
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False


def _backup_database_file(db_path, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"backup_{timestamp}.db")
    src = sqlite3.connect(db_path, timeout=30.0)
    dest = sqlite3.connect(backup_path, timeout=30.0)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return backup_path


def _start_backup_worker(app):
    interval = max(30, int(app.config["DB_BACKUP_INTERVAL_SECONDS"]))
    db_path = app.config["DATABASE_PATH"]
    backup_dir = app.config["DB_BACKUP_DIR"]

    def _loop():
        while True:
            time.sleep(interval)
            try:
                with app.app_context():
                    path = _backup_database_file(db_path, backup_dir)
                    app.logger.info("Automatic DB backup created: %s", path)
            except Exception:
                app.logger.exception("Automatic DB backup failed")

    worker = threading.Thread(target=_loop, name="db-backup-worker", daemon=True)
    worker.start()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)
    _ensure_parent(app.config["DATABASE_PATH"])
    _configure_logging(app)
    init_connection_pool(app)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()

    if os.environ.get("DISABLE_DB_BACKUP_WORKER", "0") != "1":
        _start_backup_worker(app)
    register_routes(app)
    return app


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _get_submission_deadline(db):
    row = db.execute(
        "SELECT value FROM settings WHERE key = 'submission_deadline' LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return _parse_iso_datetime(row["value"])


def _is_editable(db):
    deadline = _get_submission_deadline(db)
    if deadline is None:
        return True
    return datetime.now() <= deadline


def _current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    db = get_db()
    return db.execute(
        "SELECT id, name, role FROM users WHERE id = ? LIMIT 1", (user_id,)
    ).fetchone()


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        user = _current_user()
        if not user:
            return jsonify({"error": "Authentication required"}), 401
        return handler(user, *args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(handler):
        @wraps(handler)
        @login_required
        def wrapped(user, *args, **kwargs):
            if user["role"] not in roles:
                return jsonify({"error": "Forbidden"}), 403
            return handler(user, *args, **kwargs)

        return wrapped

    return decorator


def _require_assignment(db, judge_id, team_id):
    row = db.execute(
        """
        SELECT 1
        FROM assignments
        WHERE judge_id = ? AND team_id = ?
        LIMIT 1
        """,
        (judge_id, team_id),
    ).fetchone()
    return bool(row)


def _criteria_map(db):
    criteria = db.execute("SELECT id, name, max_score FROM criteria ORDER BY id").fetchall()
    return {str(c["id"]): c for c in criteria}, criteria


def _validate_score_payload(scores_payload, criteria_lookup):
    errors = []
    cleaned = {}
    for criterion_id, score_value in (scores_payload or {}).items():
        criterion = criteria_lookup.get(str(criterion_id))
        if criterion is None:
            errors.append(f"Unknown criterion id: {criterion_id}")
            continue
        if score_value is None:
            cleaned[str(criterion["id"])] = None
            continue
        try:
            numeric_score = float(score_value)
        except (TypeError, ValueError):
            errors.append(f"Invalid score for criterion {criterion['name']}")
            continue
        if numeric_score < 0 or numeric_score > float(criterion["max_score"]):
            errors.append(
                f"Score for {criterion['name']} must be between 0 and {criterion['max_score']}"
            )
            continue
        cleaned[str(criterion["id"])] = numeric_score
    return errors, cleaned


def _save_draft_evaluation(db, judge_id, team_id, scores_payload, remarks_text):
    criteria_lookup, _ = _criteria_map(db)
    errors, cleaned_scores = _validate_score_payload(scores_payload, criteria_lookup)
    if errors:
        return False, errors

    try:
        with transaction():
            already_submitted = db.execute(
                """
                SELECT id
                FROM final_submissions
                WHERE judge_id = ? AND team_id = ?
                LIMIT 1
                """,
                (judge_id, team_id),
            ).fetchone()
            if already_submitted:
                raise ValueError("Final submission already locked and immutable")

            for criterion_id, score_value in cleaned_scores.items():
                if score_value is None:
                    db.execute(
                        """
                        DELETE FROM draft_scores
                        WHERE judge_id = ? AND team_id = ? AND criterion_id = ?
                        """,
                        (judge_id, team_id, int(criterion_id)),
                    )
                    continue
                db.execute(
                    """
                    INSERT INTO draft_scores (judge_id, team_id, criterion_id, score, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, criterion_id)
                    DO UPDATE SET score = excluded.score, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, int(criterion_id), score_value),
                )

            if remarks_text is not None:
                db.execute(
                    """
                    INSERT INTO draft_remarks (judge_id, team_id, text, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id)
                    DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, remarks_text),
                )
    except ValueError as exc:
        return False, [str(exc)]

    return True, []


def _load_draft_snapshot(db, judge_id, team_id):
    draft_rows = db.execute(
        """
        SELECT criterion_id, score
        FROM draft_scores
        WHERE judge_id = ? AND team_id = ?
        """,
        (judge_id, team_id),
    ).fetchall()
    draft_scores = {int(row["criterion_id"]): float(row["score"]) for row in draft_rows}
    draft_remarks = db.execute(
        """
        SELECT text
        FROM draft_remarks
        WHERE judge_id = ? AND team_id = ?
        LIMIT 1
        """,
        (judge_id, team_id),
    ).fetchone()
    return draft_scores, (draft_remarks["text"] if draft_remarks else "")


def _submit_final_evaluation(db, judge_id, team_id, scores_payload, remarks_text):
    criteria_lookup, criteria_list = _criteria_map(db)
    errors, cleaned_scores = _validate_score_payload(scores_payload, criteria_lookup)
    if errors:
        return False, errors, False

    try:
        with transaction():
            existing_final = db.execute(
                """
                SELECT id, submitted_at
                FROM final_submissions
                WHERE judge_id = ? AND team_id = ?
                LIMIT 1
                """,
                (judge_id, team_id),
            ).fetchone()
            if existing_final:
                return True, [], True

            # Merge inbound payload into draft first so "submit" is idempotent and atomic.
            for criterion_id, score_value in cleaned_scores.items():
                if score_value is None:
                    db.execute(
                        """
                        DELETE FROM draft_scores
                        WHERE judge_id = ? AND team_id = ? AND criterion_id = ?
                        """,
                        (judge_id, team_id, int(criterion_id)),
                    )
                    continue
                db.execute(
                    """
                    INSERT INTO draft_scores (judge_id, team_id, criterion_id, score, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, criterion_id)
                    DO UPDATE SET score = excluded.score, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, int(criterion_id), score_value),
                )

            if remarks_text is not None:
                db.execute(
                    """
                    INSERT INTO draft_remarks (judge_id, team_id, text, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id)
                    DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, remarks_text),
                )

            final_scores_map, final_remarks_text = _load_draft_snapshot(db, judge_id, team_id)
            if not criteria_list:
                raise ValueError("No criteria defined by admin yet")
            if len(final_scores_map) < len(criteria_list):
                raise ValueError("All criteria must be scored before final submission")

            db.execute(
                """
                INSERT INTO final_submissions (judge_id, team_id, submitted_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id) DO NOTHING
                """,
                (judge_id, team_id),
            )
            for criterion in criteria_list:
                criterion_id = int(criterion["id"])
                db.execute(
                    """
                    INSERT INTO final_scores (judge_id, team_id, criterion_id, score, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, criterion_id) DO NOTHING
                    """,
                    (judge_id, team_id, criterion_id, final_scores_map[criterion_id]),
                )
            db.execute(
                """
                INSERT INTO final_remarks (judge_id, team_id, text, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id) DO NOTHING
                """,
                (judge_id, team_id, final_remarks_text or ""),
            )

            # Backward-compatible mirrors for existing admin data consumers.
            for criterion in criteria_list:
                criterion_id = int(criterion["id"])
                db.execute(
                    """
                    INSERT INTO scores (judge_id, team_id, criterion_id, score, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, criterion_id)
                    DO UPDATE SET score = excluded.score, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, criterion_id, final_scores_map[criterion_id]),
                )
            db.execute(
                """
                INSERT INTO remarks (judge_id, team_id, text, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id)
                DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP
                """,
                (judge_id, team_id, final_remarks_text or ""),
            )
            db.execute(
                """
                INSERT INTO submissions (judge_id, team_id, is_submitted, submitted_at, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id)
                DO UPDATE SET
                    is_submitted = 1,
                    submitted_at = COALESCE(submitted_at, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (judge_id, team_id),
            )
    except ValueError as exc:
        return False, [str(exc)], False

    return True, [], False


def _build_rankings(db):
    first_criterion_row = db.execute(
        "SELECT id FROM criteria ORDER BY id LIMIT 1"
    ).fetchone()
    first_criterion_id = first_criterion_row["id"] if first_criterion_row else None

    params = []
    secondary_expr = "0"
    if first_criterion_id is not None:
        secondary_expr = """
            (
                SELECT AVG(s2.score)
                FROM scores s2
                JOIN submissions sub2
                    ON sub2.judge_id = s2.judge_id
                    AND sub2.team_id = s2.team_id
                    AND sub2.is_submitted = 1
                WHERE s2.team_id = t.id AND s2.criterion_id = ?
            )
        """
        params.append(first_criterion_id)

    rows = db.execute(
        f"""
        WITH judge_totals AS (
            SELECT
                s.team_id,
                s.judge_id,
                SUM(s.score) AS total_score
            FROM scores s
            JOIN submissions sub
                ON sub.judge_id = s.judge_id
                AND sub.team_id = s.team_id
                AND sub.is_submitted = 1
            GROUP BY s.team_id, s.judge_id
        )
        SELECT
            t.id AS team_id,
            t.name AS team_name,
            ROUND(COALESCE(AVG(jt.total_score), 0), 4) AS avg_total_score,
            ROUND(COALESCE({secondary_expr}, 0), 4) AS secondary_score,
            COUNT(DISTINCT jt.judge_id) AS submitted_judges,
            ro.override_rank,
            ro.reason AS override_reason
        FROM teams t
        LEFT JOIN judge_totals jt ON jt.team_id = t.id
        LEFT JOIN ranking_overrides ro ON ro.team_id = t.id
        GROUP BY t.id, t.name, ro.override_rank, ro.reason
        ORDER BY avg_total_score DESC, secondary_score DESC, team_name ASC
        """,
        tuple(params),
    ).fetchall()

    auto_sorted = sorted(
        rows,
        key=lambda r: (-float(r["avg_total_score"]), -float(r["secondary_score"]), r["team_name"]),
    )
    overrides = {int(r["override_rank"]): r["team_id"] for r in rows if r["override_rank"] is not None}
    used_teams = set(overrides.values())
    final_ranked = []
    next_auto = [r for r in auto_sorted if r["team_id"] not in used_teams]
    auto_idx = 0
    rank = 1
    total_count = len(rows)
    while rank <= total_count:
        if rank in overrides:
            row = next(r for r in rows if r["team_id"] == overrides[rank])
        else:
            row = next_auto[auto_idx]
            auto_idx += 1
        out = dict(row)
        out["rank"] = rank
        final_ranked.append(out)
        rank += 1
    return final_ranked


def register_routes(app):
    @app.after_request
    def log_failed_requests(response):
        if response.status_code >= 400:
            app.logger.warning(
                "HTTP %s %s -> %s",
                request.method,
                request.path,
                response.status_code,
            )
        return response

    @app.errorhandler(sqlite3.Error)
    def handle_sqlite_error(error):
        app.logger.exception("SQLite error on %s %s", request.method, request.path)
        return jsonify({"error": "Database operation failed"}), 500

    @app.errorhandler(sqlite3.IntegrityError)
    def handle_integrity_error(_error):
        return jsonify({"error": "Database constraint violation"}), 409

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.exception("Unhandled server error on %s %s", request.method, request.path)
        return jsonify({"error": "Internal server error"}), 500

    @app.errorhandler(404)
    def handle_not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("index.html"), 404

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "server_time": datetime.now().isoformat()})

    @app.post("/api/auth/login")
    def login():
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        password = payload.get("password") or ""
        if not name or not password:
            return jsonify({"error": "Name and password are required"}), 400

        db = get_db()
        user = db.execute(
            "SELECT id, name, role, password_hash FROM users WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Invalid credentials"}), 401

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["role"] = user["role"]
        session["name"] = user["name"]
        return jsonify({"id": user["id"], "name": user["name"], "role": user["role"]})

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify({"success": True})

    @app.get("/api/auth/me")
    @login_required
    def me(user):
        return jsonify(user)

    @app.get("/api/admin/judges")
    @role_required("admin")
    def list_judges(_user):
        db = get_db()
        judges = db.execute(
            """
            SELECT u.id, u.name, u.role,
                COUNT(DISTINCT a.team_id) AS assigned_teams
            FROM users u
            LEFT JOIN assignments a ON a.judge_id = u.id
            WHERE u.role = 'judge'
            GROUP BY u.id, u.name, u.role
            ORDER BY u.name
            """
        ).fetchall()
        return jsonify(judges)

    @app.post("/api/admin/judges")
    @role_required("admin")
    def create_judge(_user):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        password = payload.get("password") or ""
        if not name or not password:
            return jsonify({"error": "Judge name and password are required"}), 400
        with transaction():
            db = get_db()
            existing = db.execute("SELECT id FROM users WHERE name = ? LIMIT 1", (name,)).fetchone()
            if existing:
                return jsonify({"error": "User name already exists"}), 409
            db.execute(
                "INSERT INTO users (name, role, password_hash) VALUES (?, 'judge', ?)",
                (name, generate_password_hash(password)),
            )
        return jsonify({"success": True}), 201

    @app.put("/api/admin/judges/<int:judge_id>")
    @role_required("admin")
    def update_judge(_user, judge_id):
        payload = request.get_json(silent=True) or {}
        name = payload.get("name")
        password = payload.get("password")
        db = get_db()
        judge = db.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'judge'", (judge_id,)
        ).fetchone()
        if not judge:
            return jsonify({"error": "Judge not found"}), 404

        fields = []
        values = []
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                return jsonify({"error": "Name cannot be empty"}), 400
            fields.append("name = ?")
            values.append(clean_name)
        if password:
            fields.append("password_hash = ?")
            values.append(generate_password_hash(password))
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(judge_id)
        with transaction():
            db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", tuple(values))
        return jsonify({"success": True})

    @app.delete("/api/admin/judges/<int:judge_id>")
    @role_required("admin")
    def delete_judge(user, judge_id):
        if user["id"] == judge_id:
            return jsonify({"error": "Admin cannot delete self via judge endpoint"}), 400
        with transaction():
            db = get_db()
            deleted = db.execute(
                "DELETE FROM users WHERE id = ? AND role = 'judge'",
                (judge_id,),
            ).rowcount
        if not deleted:
            return jsonify({"error": "Judge not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/admin/teams")
    @role_required("admin")
    def list_teams(_user):
        db = get_db()
        teams = db.execute(
            """
            SELECT t.id, t.name, t.problem_statement, t.expected_solution,
                COUNT(DISTINCT a.judge_id) AS assigned_judges
            FROM teams t
            LEFT JOIN assignments a ON a.team_id = t.id
            GROUP BY t.id, t.name, t.problem_statement, t.expected_solution
            ORDER BY t.name
            """
        ).fetchall()
        return jsonify(teams)

    @app.post("/api/admin/teams")
    @role_required("admin")
    def create_team(_user):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Team name is required"}), 400
        problem_statement = payload.get("problem_statement") or ""
        expected_solution = payload.get("expected_solution") or ""
        with transaction():
            db = get_db()
            db.execute(
                """
                INSERT INTO teams (name, problem_statement, expected_solution)
                VALUES (?, ?, ?)
                """,
                (name, problem_statement, expected_solution),
            )
        return jsonify({"success": True}), 201

    @app.put("/api/admin/teams/<int:team_id>")
    @role_required("admin")
    def update_team(_user, team_id):
        payload = request.get_json(silent=True) or {}
        fields = []
        values = []
        if "name" in payload:
            name = (payload.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Team name cannot be empty"}), 400
            fields.append("name = ?")
            values.append(name)
        if "problem_statement" in payload:
            fields.append("problem_statement = ?")
            values.append(payload.get("problem_statement") or "")
        if "expected_solution" in payload:
            fields.append("expected_solution = ?")
            values.append(payload.get("expected_solution") or "")
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(team_id)
        with transaction():
            db = get_db()
            updated = db.execute(
                f"UPDATE teams SET {', '.join(fields)} WHERE id = ?", tuple(values)
            ).rowcount
        if not updated:
            return jsonify({"error": "Team not found"}), 404
        return jsonify({"success": True})

    @app.delete("/api/admin/teams/<int:team_id>")
    @role_required("admin")
    def delete_team(_user, team_id):
        with transaction():
            db = get_db()
            deleted = db.execute("DELETE FROM teams WHERE id = ?", (team_id,)).rowcount
        if not deleted:
            return jsonify({"error": "Team not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/admin/criteria")
    @role_required("admin")
    def list_criteria(_user):
        db = get_db()
        return jsonify(
            db.execute("SELECT id, name, max_score FROM criteria ORDER BY id").fetchall()
        )

    @app.post("/api/admin/criteria")
    @role_required("admin")
    def create_criteria(_user):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        max_score = payload.get("max_score")
        if not name:
            return jsonify({"error": "Criterion name is required"}), 400
        try:
            max_score = float(max_score)
            if max_score <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "max_score must be > 0"}), 400
        with transaction():
            db = get_db()
            db.execute(
                "INSERT INTO criteria (name, max_score) VALUES (?, ?)",
                (name, max_score),
            )
        return jsonify({"success": True}), 201

    @app.put("/api/admin/criteria/<int:criterion_id>")
    @role_required("admin")
    def update_criteria(_user, criterion_id):
        payload = request.get_json(silent=True) or {}
        fields = []
        values = []
        if "name" in payload:
            name = (payload.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Name cannot be empty"}), 400
            fields.append("name = ?")
            values.append(name)
        if "max_score" in payload:
            try:
                max_score = float(payload.get("max_score"))
                if max_score <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({"error": "max_score must be > 0"}), 400
            fields.append("max_score = ?")
            values.append(max_score)
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(criterion_id)
        with transaction():
            db = get_db()
            updated = db.execute(
                f"UPDATE criteria SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
            ).rowcount
        if not updated:
            return jsonify({"error": "Criterion not found"}), 404
        return jsonify({"success": True})

    @app.delete("/api/admin/criteria/<int:criterion_id>")
    @role_required("admin")
    def delete_criteria(_user, criterion_id):
        with transaction():
            db = get_db()
            deleted = db.execute("DELETE FROM criteria WHERE id = ?", (criterion_id,)).rowcount
        if not deleted:
            return jsonify({"error": "Criterion not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/admin/assignments")
    @role_required("admin")
    def list_assignments(_user):
        db = get_db()
        rows = db.execute(
            """
            SELECT a.judge_id, u.name AS judge_name, a.team_id, t.name AS team_name
            FROM assignments a
            JOIN users u ON u.id = a.judge_id
            JOIN teams t ON t.id = a.team_id
            ORDER BY u.name, t.name
            """
        ).fetchall()
        return jsonify(rows)

    @app.put("/api/admin/assignments/<int:judge_id>")
    @role_required("admin")
    def set_assignments(_user, judge_id):
        payload = request.get_json(silent=True) or {}
        team_ids = payload.get("team_ids")
        if not isinstance(team_ids, list):
            return jsonify({"error": "team_ids must be a list"}), 400

        clean_ids = []
        try:
            clean_ids = [int(team_id) for team_id in team_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "team_ids must be integers"}), 400
        clean_ids = sorted(set(clean_ids))

        with transaction():
            db = get_db()
            judge = db.execute(
                "SELECT id FROM users WHERE id = ? AND role = 'judge'", (judge_id,)
            ).fetchone()
            if not judge:
                return jsonify({"error": "Judge not found"}), 404

            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                existing_rows = db.execute(
                    f"SELECT id FROM teams WHERE id IN ({placeholders})",
                    tuple(clean_ids),
                ).fetchall()
                existing_ids = {row["id"] for row in existing_rows}
                invalid_ids = [team_id for team_id in clean_ids if team_id not in existing_ids]
                if invalid_ids:
                    return (
                        jsonify(
                            {
                                "error": "Some team_ids are invalid",
                                "invalid_team_ids": invalid_ids,
                            }
                        ),
                        400,
                    )

            db.execute("DELETE FROM assignments WHERE judge_id = ?", (judge_id,))
            for team_id in clean_ids:
                db.execute(
                    "INSERT INTO assignments (judge_id, team_id) VALUES (?, ?)",
                    (judge_id, team_id),
                )
        return jsonify({"success": True})

    @app.get("/api/admin/scores")
    @role_required("admin")
    def admin_scores(_user):
        db = get_db()
        team_id = request.args.get("team_id", type=int)
        judge_id = request.args.get("judge_id", type=int)
        submitted_only = request.args.get("submitted_only", default=0, type=int)

        query = """
            SELECT
                s.judge_id,
                u.name AS judge_name,
                s.team_id,
                t.name AS team_name,
                s.criterion_id,
                c.name AS criterion_name,
                c.max_score,
                s.score,
                s.updated_at,
                COALESCE(sub.is_submitted, 0) AS is_submitted,
                sub.submitted_at,
                COALESCE(r.text, '') AS remarks
            FROM scores s
            JOIN users u ON u.id = s.judge_id
            JOIN teams t ON t.id = s.team_id
            JOIN criteria c ON c.id = s.criterion_id
            LEFT JOIN submissions sub
                ON sub.judge_id = s.judge_id AND sub.team_id = s.team_id
            LEFT JOIN remarks r
                ON r.judge_id = s.judge_id AND r.team_id = s.team_id
            WHERE 1=1
        """
        params = []
        if team_id is not None:
            query += " AND s.team_id = ?"
            params.append(team_id)
        if judge_id is not None:
            query += " AND s.judge_id = ?"
            params.append(judge_id)
        if submitted_only:
            query += " AND COALESCE(sub.is_submitted, 0) = 1"
        query += " ORDER BY t.name, u.name, c.id"

        rows = db.execute(query, tuple(params)).fetchall()
        return jsonify(rows)

    @app.get("/api/admin/rankings")
    @role_required("admin")
    def rankings(_user):
        return jsonify(_build_rankings(get_db()))

    @app.put("/api/admin/rankings/override")
    @role_required("admin")
    def upsert_override(user):
        payload = request.get_json(silent=True) or {}
        team_id = payload.get("team_id")
        override_rank = payload.get("override_rank")
        reason = payload.get("reason") or ""
        try:
            team_id = int(team_id)
            override_rank = int(override_rank)
            if override_rank <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "team_id and override_rank must be valid positive integers"}), 400
        with transaction():
            db = get_db()
            db.execute(
                """
                INSERT INTO ranking_overrides (team_id, override_rank, reason, updated_by, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(team_id)
                DO UPDATE SET
                    override_rank = excluded.override_rank,
                    reason = excluded.reason,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (team_id, override_rank, reason, user["id"]),
            )
        return jsonify({"success": True})

    @app.delete("/api/admin/rankings/override/<int:team_id>")
    @role_required("admin")
    def delete_override(_user, team_id):
        with transaction():
            db = get_db()
            db.execute("DELETE FROM ranking_overrides WHERE team_id = ?", (team_id,))
        return jsonify({"success": True})

    @app.get("/api/admin/dashboard")
    @role_required("admin")
    def dashboard(_user):
        db = get_db()
        counts = {
            "judges": db.execute("SELECT COUNT(*) AS c FROM users WHERE role='judge'").fetchone()["c"],
            "teams": db.execute("SELECT COUNT(*) AS c FROM teams").fetchone()["c"],
            "criteria": db.execute("SELECT COUNT(*) AS c FROM criteria").fetchone()["c"],
            "assignments": db.execute("SELECT COUNT(*) AS c FROM assignments").fetchone()["c"],
        }
        submission_stats = db.execute(
            """
            SELECT
                SUM(CASE WHEN is_submitted = 1 THEN 1 ELSE 0 END) AS submitted,
                COUNT(*) AS total
            FROM submissions
            """
        ).fetchone()
        counts["submitted"] = submission_stats["submitted"] or 0
        counts["submission_total_records"] = submission_stats["total"] or 0
        return jsonify(
            {
                "counts": counts,
                "rankings": _build_rankings(db),
                "server_time": datetime.now().isoformat(),
            }
        )

    @app.get("/api/admin/export/csv")
    @role_required("admin")
    def export_csv(_user):
        db = get_db()
        rankings = _build_rankings(db)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Rank",
                "Team ID",
                "Team Name",
                "Average Total Score",
                "Secondary Score",
                "Submitted Judges",
                "Override Rank",
                "Override Reason",
            ]
        )
        for row in rankings:
            writer.writerow(
                [
                    row["rank"],
                    row["team_id"],
                    row["team_name"],
                    row["avg_total_score"],
                    row["secondary_score"],
                    row["submitted_judges"],
                    row["override_rank"] if row["override_rank"] is not None else "",
                    row["override_reason"] or "",
                ]
            )
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name="hackathon_rankings.csv",
        )

    @app.get("/api/admin/export")
    @app.get("/admin/export")
    @role_required("admin")
    def export_database(_user):
        backup_path = _backup_database_file(
            app.config["DATABASE_PATH"],
            app.config["DB_BACKUP_DIR"],
        )
        app.logger.info("Manual DB export generated: %s", backup_path)
        return send_file(
            backup_path,
            as_attachment=True,
            download_name=os.path.basename(backup_path),
            mimetype="application/octet-stream",
        )

    @app.get("/api/admin/settings/submission-deadline")
    @role_required("admin")
    def get_deadline(_user):
        db = get_db()
        row = db.execute(
            "SELECT value FROM settings WHERE key = 'submission_deadline' LIMIT 1"
        ).fetchone()
        return jsonify({"submission_deadline": row["value"] if row else None})

    @app.put("/api/admin/settings/submission-deadline")
    @role_required("admin")
    def update_deadline(_user):
        payload = request.get_json(silent=True) or {}
        value = payload.get("submission_deadline")
        if value is not None and _parse_iso_datetime(value) is None:
            return jsonify(
                {"error": "submission_deadline must be ISO datetime format (YYYY-MM-DDTHH:MM:SS)"}
            ), 400
        with transaction():
            db = get_db()
            if value:
                db.execute(
                    """
                    INSERT INTO settings (key, value) VALUES ('submission_deadline', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (value,),
                )
            else:
                db.execute("DELETE FROM settings WHERE key='submission_deadline'")
        return jsonify({"success": True, "submission_deadline": value})

    @app.get("/api/judge/teams")
    @role_required("judge")
    def judge_teams(user):
        db = get_db()
        rows = db.execute(
            """
            SELECT
                t.id,
                t.name,
                CASE WHEN fs.id IS NULL THEN 0 ELSE 1 END AS is_submitted,
                fs.submitted_at,
                COALESCE(draft_counts.score_count, 0) AS draft_score_count,
                COALESCE(dr.text, '') AS draft_remarks
            FROM assignments a
            JOIN teams t ON t.id = a.team_id
            LEFT JOIN final_submissions fs
                ON fs.judge_id = a.judge_id AND fs.team_id = a.team_id
            LEFT JOIN (
                SELECT judge_id, team_id, COUNT(*) AS score_count
                FROM draft_scores
                GROUP BY judge_id, team_id
            ) draft_counts
                ON draft_counts.judge_id = a.judge_id AND draft_counts.team_id = a.team_id
            LEFT JOIN draft_remarks dr
                ON dr.judge_id = a.judge_id AND dr.team_id = a.team_id
            WHERE a.judge_id = ?
            ORDER BY t.name
            """,
            (user["id"],),
        ).fetchall()
        items = []
        for row in rows:
            status = "not_started"
            if row["is_submitted"] == 1:
                status = "submitted"
            elif row["draft_score_count"] > 0 or (row["draft_remarks"] or "").strip():
                status = "in_progress"
            item = dict(row)
            item["status"] = status
            items.append(item)
        return jsonify(items)

    @app.get("/api/judge/teams/<int:team_id>/evaluation")
    @role_required("judge")
    def get_evaluation(user, team_id):
        db = get_db()
        if not _require_assignment(db, user["id"], team_id):
            return jsonify({"error": "Team not assigned to this judge"}), 403

        team = db.execute(
            """
            SELECT id, name, problem_statement, expected_solution
            FROM teams
            WHERE id = ?
            LIMIT 1
            """,
            (team_id,),
        ).fetchone()
        if not team:
            return jsonify({"error": "Team not found"}), 404

        criteria = db.execute("SELECT id, name, max_score FROM criteria ORDER BY id").fetchall()
        final_submission = db.execute(
            """
            SELECT submitted_at
            FROM final_submissions
            WHERE judge_id = ? AND team_id = ?
            LIMIT 1
            """,
            (user["id"], team_id),
        ).fetchone()

        score_source_table = "draft_scores"
        remarks_source_table = "draft_remarks"
        if final_submission:
            score_source_table = "final_scores"
            remarks_source_table = "final_remarks"

        score_rows = db.execute(
            f"""
            SELECT criterion_id, score
            FROM {score_source_table}
            WHERE judge_id = ? AND team_id = ?
            """,
            (user["id"], team_id),
        ).fetchall()
        scores = {str(row["criterion_id"]): row["score"] for row in score_rows}
        remarks = db.execute(
            f"SELECT text FROM {remarks_source_table} WHERE judge_id = ? AND team_id = ? LIMIT 1",
            (user["id"], team_id),
        ).fetchone()

        deadline = _get_submission_deadline(db)
        is_submitted = 1 if final_submission else 0
        editable = _is_editable(db) and not final_submission
        return jsonify(
            {
                "team": team,
                "criteria": criteria,
                "scores": scores,
                "remarks": remarks["text"] if remarks else "",
                "submission": {
                    "is_submitted": is_submitted,
                    "submitted_at": final_submission["submitted_at"] if final_submission else None,
                    "updated_at": final_submission["submitted_at"] if final_submission else None,
                },
                "editable": editable,
                "submission_deadline": deadline.isoformat() if deadline else None,
            }
        )

    @app.put("/api/judge/teams/<int:team_id>/draft")
    @role_required("judge")
    def save_draft(user, team_id):
        db = get_db()
        if not _require_assignment(db, user["id"], team_id):
            return jsonify({"error": "Team not assigned to this judge"}), 403
        if not _is_editable(db):
            return jsonify({"error": "Submission deadline passed. Editing is locked."}), 403
        submitted = db.execute(
            """
            SELECT id
            FROM final_submissions
            WHERE judge_id = ? AND team_id = ?
            LIMIT 1
            """,
            (user["id"], team_id),
        ).fetchone()
        if submitted:
            return jsonify({"error": "Final submission is immutable."}), 409

        payload = request.get_json(silent=True) or {}
        ok, errors = _save_draft_evaluation(
            db,
            judge_id=user["id"],
            team_id=team_id,
            scores_payload=payload.get("scores") or {},
            remarks_text=payload.get("remarks"),
        )
        if not ok:
            return jsonify({"error": "Invalid draft payload", "details": errors}), 400
        return jsonify({"success": True, "status": "in_progress", "synced": True})

    @app.post("/api/judge/teams/<int:team_id>/submit")
    @role_required("judge")
    def submit_scores(user, team_id):
        db = get_db()
        if not _require_assignment(db, user["id"], team_id):
            return jsonify({"error": "Team not assigned to this judge"}), 403
        if not _is_editable(db):
            return jsonify({"error": "Submission deadline passed. Editing is locked."}), 403

        payload = request.get_json(silent=True) or {}
        ok, errors, already_submitted = _submit_final_evaluation(
            db,
            judge_id=user["id"],
            team_id=team_id,
            scores_payload=payload.get("scores") or {},
            remarks_text=payload.get("remarks"),
        )
        if not ok:
            return jsonify({"error": "Could not submit evaluation", "details": errors}), 400
        return jsonify(
            {
                "success": True,
                "status": "submitted",
                "already_submitted": already_submitted,
            }
        )


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
