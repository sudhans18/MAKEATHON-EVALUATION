import csv
import io
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config
from db import close_db, get_db, init_db, transaction


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    # Triggering reload for Spider-Verse Matrix v2.1
    app.config.from_object(Config)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        _ensure_default_round(get_db())

    register_routes(app)
    return app


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _list_rounds(db):
    return db.execute(
        "SELECT id, name, sequence FROM rounds ORDER BY sequence, id"
    ).fetchall()


def _ensure_default_round(db):
    existing = db.execute("SELECT id FROM rounds LIMIT 1").fetchone()
    if not existing:
        db.execute(
            "INSERT INTO rounds (name, sequence) VALUES ('Round 1', 1)"
        )
        db.commit()
    first_round = db.execute(
        "SELECT id FROM rounds ORDER BY sequence, id LIMIT 1"
    ).fetchone()
    if first_round:
        db.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('active_round_id', ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (str(first_round["id"]),),
        )
        db.commit()


def _resolve_round_id(db, requested=None, required=True):
    candidate = requested
    if candidate is None:
        candidate = request.args.get("round_id")
    if candidate is None and request.is_json:
        payload = request.get_json(silent=True) or {}
        candidate = payload.get("round_id")
    if candidate is None:
        row = db.execute(
            "SELECT value FROM settings WHERE key='active_round_id' LIMIT 1"
        ).fetchone()
        if row:
            candidate = row["value"]
    if candidate is None:
        row = db.execute(
            "SELECT id FROM rounds ORDER BY sequence, id LIMIT 1"
        ).fetchone()
        if row:
            candidate = row["id"]
    if candidate is None:
        if required:
            return None, jsonify({"error": "No rounds configured"}), 400
        return None, None, None
    try:
        round_id = int(candidate)
    except (TypeError, ValueError):
        return None, jsonify({"error": "Invalid round_id"}), 400
    round_row = db.execute(
        "SELECT id, name, sequence FROM rounds WHERE id = ? LIMIT 1",
        (round_id,),
    ).fetchone()
    if not round_row:
        return None, jsonify({"error": "Round not found"}), 404
    return round_id, round_row, None


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


def _require_round_assignment(db, judge_id, team_id, round_id):
    row = db.execute(
        """
        SELECT 1
        FROM round_assignments
        WHERE judge_id = ? AND team_id = ? AND round_id = ?
        LIMIT 1
        """,
        (judge_id, team_id, round_id),
    ).fetchone()
    return bool(row)


def _criteria_map(db, round_id):
    criteria = db.execute(
        """
        SELECT id, round_id, name, max_score
        FROM round_criteria
        WHERE round_id = ?
        ORDER BY id
        """,
        (round_id,),
    ).fetchall()
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


def _save_round_draft_evaluation(db, judge_id, team_id, round_id, scores_payload, remarks_text):
    criteria_lookup, _ = _criteria_map(db, round_id)
    errors, cleaned_scores = _validate_score_payload(scores_payload, criteria_lookup)
    if errors:
        return False, errors
    try:
        with transaction():
            already_submitted = db.execute(
                """
                SELECT id
                FROM round_final_submissions
                WHERE judge_id = ? AND team_id = ? AND round_id = ?
                LIMIT 1
                """,
                (judge_id, team_id, round_id),
            ).fetchone()
            if already_submitted:
                raise ValueError("Final submission already locked and immutable")

            for criterion_id, score_value in cleaned_scores.items():
                if score_value is None:
                    db.execute(
                        """
                        DELETE FROM round_draft_scores
                        WHERE judge_id = ? AND team_id = ? AND round_id = ? AND criterion_id = ?
                        """,
                        (judge_id, team_id, round_id, int(criterion_id)),
                    )
                    continue
                db.execute(
                    """
                    INSERT INTO round_draft_scores (
                        judge_id, team_id, round_id, criterion_id, score, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, round_id, criterion_id)
                    DO UPDATE SET score = excluded.score, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, round_id, int(criterion_id), score_value),
                )

            if remarks_text is not None:
                db.execute(
                    """
                    INSERT INTO round_draft_remarks (
                        judge_id, team_id, round_id, text, updated_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, round_id)
                    DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, round_id, remarks_text),
                )
    except ValueError as exc:
        return False, [str(exc)]

    return True, []


def _load_round_draft_snapshot(db, judge_id, team_id, round_id):
    draft_rows = db.execute(
        """
        SELECT criterion_id, score
        FROM round_draft_scores
        WHERE judge_id = ? AND team_id = ? AND round_id = ?
        """,
        (judge_id, team_id, round_id),
    ).fetchall()
    draft_scores = {int(row["criterion_id"]): float(row["score"]) for row in draft_rows}
    draft_remarks = db.execute(
        """
        SELECT text
        FROM round_draft_remarks
        WHERE judge_id = ? AND team_id = ? AND round_id = ?
        LIMIT 1
        """,
        (judge_id, team_id, round_id),
    ).fetchone()
    return draft_scores, (draft_remarks["text"] if draft_remarks else "")


def _submit_round_final_evaluation(db, judge_id, team_id, round_id, scores_payload, remarks_text):
    criteria_lookup, criteria_list = _criteria_map(db, round_id)
    errors, cleaned_scores = _validate_score_payload(scores_payload, criteria_lookup)
    if errors:
        return False, errors, False
    try:
        with transaction():
            existing_final = db.execute(
                """
                SELECT id
                FROM round_final_submissions
                WHERE judge_id = ? AND team_id = ? AND round_id = ?
                LIMIT 1
                """,
                (judge_id, team_id, round_id),
            ).fetchone()
            if existing_final:
                return True, [], True

            for criterion_id, score_value in cleaned_scores.items():
                if score_value is None:
                    db.execute(
                        """
                        DELETE FROM round_draft_scores
                        WHERE judge_id = ? AND team_id = ? AND round_id = ? AND criterion_id = ?
                        """,
                        (judge_id, team_id, round_id, int(criterion_id)),
                    )
                    continue
                db.execute(
                    """
                    INSERT INTO round_draft_scores (
                        judge_id, team_id, round_id, criterion_id, score, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, round_id, criterion_id)
                    DO UPDATE SET score = excluded.score, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, round_id, int(criterion_id), score_value),
                )

            if remarks_text is not None:
                db.execute(
                    """
                    INSERT INTO round_draft_remarks (
                        judge_id, team_id, round_id, text, updated_at
                    ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, round_id)
                    DO UPDATE SET text = excluded.text, updated_at = CURRENT_TIMESTAMP
                    """,
                    (judge_id, team_id, round_id, remarks_text),
                )

            final_scores_map, final_remarks_text = _load_round_draft_snapshot(
                db, judge_id, team_id, round_id
            )
            if not criteria_list:
                raise ValueError("No criteria defined for this round yet")
            if len(final_scores_map) < len(criteria_list):
                raise ValueError("All criteria in this round must be scored before final submission")

            db.execute(
                """
                INSERT INTO round_final_submissions (judge_id, team_id, round_id, submitted_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id, round_id) DO NOTHING
                """,
                (judge_id, team_id, round_id),
            )
            for criterion in criteria_list:
                criterion_id = int(criterion["id"])
                db.execute(
                    """
                    INSERT INTO round_final_scores (
                        judge_id, team_id, round_id, criterion_id, score, updated_at
                    ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(judge_id, team_id, round_id, criterion_id) DO NOTHING
                    """,
                    (judge_id, team_id, round_id, criterion_id, final_scores_map[criterion_id]),
                )
            db.execute(
                """
                INSERT INTO round_final_remarks (
                    judge_id, team_id, round_id, text, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(judge_id, team_id, round_id) DO NOTHING
                """,
                (judge_id, team_id, round_id, final_remarks_text or ""),
            )
    except ValueError as exc:
        return False, [str(exc)], False
    return True, [], False


def _build_rankings(db, round_id, category=None):
    # Base params for the query
    
    # Category filter where clause
    category_filter = ""
    if category in ["HW", "SW"]:
        category_filter = "AND t.category = ?"

    if round_id is not None:
        # Standard round rankings
        first_criterion_row = db.execute(
            "SELECT id FROM round_criteria WHERE round_id = ? ORDER BY id LIMIT 1",
            (round_id,),
        ).fetchone()
        first_criterion_id = first_criterion_row["id"] if first_criterion_row else None

        secondary_expr = "0"
        query_params = [round_id, round_id]
        
        if first_criterion_id is not None:
            secondary_expr = """
                (
                    SELECT AVG(fs2.score)
                    FROM round_final_scores fs2
                    JOIN round_final_submissions sub2
                        ON sub2.judge_id = fs2.judge_id
                        AND sub2.team_id = fs2.team_id
                        AND sub2.round_id = fs2.round_id
                    WHERE fs2.round_id = ? AND fs2.team_id = t.id AND fs2.criterion_id = ?
                )
            """
            query_params.extend([round_id, first_criterion_id])
        
        query_params.append(round_id) # for override join
        if category in ["HW", "SW"]:
            query_params.append(category)

        rows = db.execute(
            f"""
            WITH criteria_totals AS (
                SELECT SUM(max_score) AS max_total
                FROM round_criteria
                WHERE round_id = ?
            ),
            judge_totals AS (
                SELECT
                    s.team_id,
                    s.judge_id,
                    SUM(s.score) AS total_score
                FROM round_final_scores s
                JOIN round_final_submissions sub
                    ON sub.judge_id = s.judge_id
                    AND sub.team_id = s.team_id
                    AND sub.round_id = s.round_id
                WHERE s.round_id = ?
                GROUP BY s.team_id, s.judge_id
            )
            SELECT
                t.id AS team_id,
                t.name AS team_name,
                t.category,
                ROUND(COALESCE(AVG(jt.total_score), 0), 4) AS avg_total_score,
                ROUND(
                    CASE
                        WHEN (SELECT max_total FROM criteria_totals) > 0
                        THEN COALESCE(AVG(jt.total_score), 0) * 100.0 / (SELECT max_total FROM criteria_totals)
                        ELSE 0
                    END,
                    4
                ) AS avg_percentage,
                ROUND(COALESCE({secondary_expr}, 0), 4) AS secondary_score,
                COUNT(DISTINCT jt.judge_id) AS submitted_judges,
                ro.override_rank,
                ro.reason AS override_reason
            FROM teams t
            LEFT JOIN judge_totals jt ON jt.team_id = t.id
            LEFT JOIN round_ranking_overrides ro
                ON ro.team_id = t.id AND ro.round_id = ?
            WHERE 1=1 {category_filter}
            GROUP BY t.id, t.name, t.category, ro.override_rank, ro.reason
            ORDER BY avg_percentage DESC, secondary_score DESC, team_name ASC
            """,
            tuple(query_params),
        ).fetchall()
    else:
        # Overall rankings (Aggregate across all rounds)
        query_params = []
        if category in ["HW", "SW"]:
            query_params.append(category)

        rows = db.execute(
            f"""
            WITH round_percentages AS (
                SELECT
                    t.id AS team_id,
                    r.id AS round_id,
                    SUM(fs.score) * 100.0 / (SELECT SUM(max_score) FROM round_criteria WHERE round_id = r.id) AS percentage
                FROM teams t
                JOIN round_final_scores fs ON fs.team_id = t.id
                JOIN rounds r ON r.id = fs.round_id
                JOIN round_final_submissions sub 
                    ON sub.team_id = fs.team_id 
                    AND sub.judge_id = fs.judge_id 
                    AND sub.round_id = fs.round_id
                GROUP BY t.id, r.id, fs.judge_id
            ),
            team_round_avgs AS (
                SELECT team_id, round_id, AVG(percentage) as round_avg
                FROM round_percentages
                GROUP BY team_id, round_id
            )
            SELECT
                t.id AS team_id,
                t.name AS team_name,
                t.category,
                0 AS avg_total_score,
                ROUND(AVG(tra.round_avg), 4) AS avg_percentage,
                0 AS secondary_score,
                (SELECT COUNT(DISTINCT judge_id) FROM round_final_submissions WHERE team_id = t.id) AS submitted_judges,
                NULL AS override_rank,
                NULL AS override_reason
            FROM teams t
            LEFT JOIN team_round_avgs tra ON tra.team_id = t.id
            WHERE 1=1 {category_filter}
            GROUP BY t.id, t.name, t.category
            ORDER BY avg_percentage DESC, team_name ASC
            """,
            tuple(query_params),
        ).fetchall()

    auto_sorted = sorted(
        rows,
        key=lambda r: (
            -float(r["avg_percentage"] or 0),
            -float(r["secondary_score"] or 0),
            r["team_name"],
        ),
    )
    overrides = {
        int(r["override_rank"]): r["team_id"]
        for r in rows
        if r.get("override_rank") is not None
    }
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
            if auto_idx < len(next_auto):
                row = next_auto[auto_idx]
                auto_idx += 1
            else:
                break
        out = dict(row)
        out["rank"] = rank
        final_ranked.append(out)
        rank += 1
    return final_ranked



def register_routes(app):
    @app.errorhandler(sqlite3.IntegrityError)
    def handle_integrity_error(_error):
        return jsonify({"error": "Database constraint violation"}), 409

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

    @app.get("/api/rounds")
    @login_required
    def rounds_for_user(_user):
        return jsonify(_list_rounds(get_db()))

    @app.get("/api/admin/rounds")
    @role_required("admin")
    def list_rounds(_user):
        return jsonify(_list_rounds(get_db()))

    @app.post("/api/admin/rounds")
    @role_required("admin")
    def create_round(_user):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        sequence = payload.get("sequence")
        if not name:
            return jsonify({"error": "Round name is required"}), 400
        try:
            sequence = int(sequence)
        except (TypeError, ValueError):
            return jsonify({"error": "sequence must be an integer"}), 400
        with transaction():
            db = get_db()
            db.execute(
                "INSERT INTO rounds (name, sequence) VALUES (?, ?)",
                (name, sequence),
            )
        return jsonify({"success": True}), 201

    @app.put("/api/admin/rounds/<int:round_id>")
    @role_required("admin")
    def update_round(_user, round_id):
        payload = request.get_json(silent=True) or {}
        fields = []
        values = []
        if "name" in payload:
            name = (payload.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Name cannot be empty"}), 400
            fields.append("name = ?")
            values.append(name)
        if "sequence" in payload:
            try:
                seq = int(payload.get("sequence"))
            except (TypeError, ValueError):
                return jsonify({"error": "sequence must be integer"}), 400
            fields.append("sequence = ?")
            values.append(seq)
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(round_id)
        with transaction():
            db = get_db()
            updated = db.execute(
                f"UPDATE rounds SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
            ).rowcount
        if not updated:
            return jsonify({"error": "Round not found"}), 404
        return jsonify({"success": True})

    @app.delete("/api/admin/rounds/<int:round_id>")
    @role_required("admin")
    def delete_round(_user, round_id):
        with transaction():
            db = get_db()
            remaining = db.execute("SELECT COUNT(*) AS c FROM rounds").fetchone()["c"]
            if remaining <= 1:
                return jsonify({"error": "At least one round is required"}), 400
            deleted = db.execute("DELETE FROM rounds WHERE id = ?", (round_id,)).rowcount
            if not deleted:
                return jsonify({"error": "Round not found"}), 404
            active = db.execute(
                "SELECT value FROM settings WHERE key='active_round_id' LIMIT 1"
            ).fetchone()
            if active and int(active["value"]) == round_id:
                first = db.execute(
                    "SELECT id FROM rounds ORDER BY sequence, id LIMIT 1"
                ).fetchone()
                if first:
                    db.execute(
                        """
                        INSERT INTO settings (key, value) VALUES ('active_round_id', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (str(first["id"]),),
                    )
        return jsonify({"success": True})

    @app.get("/api/admin/settings/active-round")
    @role_required("admin")
    def get_active_round(_user):
        db = get_db()
        rid, round_row, err = _resolve_round_id(db, required=False)
        if err:
            return err
        return jsonify({"active_round_id": rid, "round": round_row})

    @app.put("/api/admin/settings/active-round")
    @role_required("admin")
    def update_active_round(_user):
        db = get_db()
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        with transaction():
            db.execute(
                """
                INSERT INTO settings (key, value) VALUES ('active_round_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(rid),),
            )
        return jsonify({"success": True, "active_round_id": rid})

    @app.get("/api/admin/judges")
    @role_required("admin")
    def list_judges(_user):
        db = get_db()
        rid, _round_row, err = _resolve_round_id(db, required=False)
        if err:
            return err
        judges = db.execute(
            """
            SELECT u.id, u.name, u.role
            FROM users u
            WHERE u.role = 'judge'
            ORDER BY u.name
            """
        ).fetchall()
        out = []
        for j in judges:
            assigned = 0
            if rid is not None:
                assigned = db.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM round_assignments
                    WHERE judge_id = ? AND round_id = ?
                    """,
                    (j["id"], rid),
                ).fetchone()["c"]
            item = dict(j)
            item["assigned_teams"] = assigned
            out.append(item)
        return jsonify(out)

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
            db = get_db()
            updated = db.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id = ? AND role = 'judge'",
                tuple(values),
            ).rowcount
        if not updated:
            return jsonify({"error": "Judge not found"}), 404
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
        rid, _round_row, err = _resolve_round_id(db, required=False)
        if err:
            return err
        teams = db.execute(
            """
            SELECT t.id, t.name, t.ps_id, t.category, t.problem_statement, t.expected_solution
            FROM teams t
            ORDER BY t.name
            """
        ).fetchall()

        out = []
        for t in teams:
            assigned = 0
            if rid is not None:
                assigned = db.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM round_assignments
                    WHERE team_id = ? AND round_id = ?
                    """,
                    (t["id"], rid),
                ).fetchone()["c"]
            item = dict(t)
            item["assigned_judges"] = assigned
            out.append(item)
        return jsonify(out)

    @app.post("/api/admin/teams")
    @role_required("admin")
    def create_team(_user):
        payload = request.get_json(silent=True) or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Team name is required"}), 400
        with transaction():
            db = get_db()
            db.execute(
                """
                INSERT INTO teams (name, ps_id, problem_statement, expected_solution, category)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    name,
                    (payload.get("ps_id") or "").strip(),
                    payload.get("problem_statement") or "",
                    payload.get("expected_solution") or "",
                    payload.get("category") or "SW",
                ),
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
        if "ps_id" in payload:
            fields.append("ps_id = ?")
            values.append((payload.get("ps_id") or "").strip())
        if "category" in payload:
            fields.append("category = ?")
            values.append(payload.get("category") or "SW")
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(team_id)
        with transaction():
            db = get_db()
            updated = db.execute(
                f"UPDATE teams SET {', '.join(fields)} WHERE id = ?",
                tuple(values),
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

    @app.get("/api/admin/teams/<int:team_id>/details")
    @role_required("admin")
    def team_details(_user, team_id):
        db = get_db()
        team = db.execute(
            "SELECT id, name, ps_id, category, problem_statement, expected_solution FROM teams WHERE id = ? LIMIT 1",
            (team_id,)
        ).fetchone()
        if not team:
            return jsonify({"error": "Team not found"}), 404

        # Scores across all rounds
        scores = db.execute(
            """
            SELECT
                r.name AS round_name,
                u.name AS judge_name,
                c.name AS criterion_name,
                s.score,
                c.max_score
            FROM round_final_scores s
            JOIN rounds r ON r.id = s.round_id
            JOIN users u ON u.id = s.judge_id
            JOIN round_criteria c ON c.id = s.criterion_id
            WHERE s.team_id = ?
            ORDER BY r.sequence, u.name, c.id
            """,
            (team_id,)
        ).fetchall()

        # Remarks across all rounds
        remarks = db.execute(
            """
            SELECT
                r.name AS round_name,
                u.name AS judge_name,
                rm.text AS remarks
            FROM round_final_remarks rm
            JOIN rounds r ON r.id = rm.round_id
            JOIN users u ON u.id = rm.judge_id
            WHERE rm.team_id = ?
            ORDER BY r.sequence, u.name
            """,
            (team_id,)
        ).fetchall()

        return jsonify({
            "team": dict(team),
            "scores": [dict(s) for s in scores],
            "remarks": [dict(rem) for rem in remarks]
        })

    @app.get("/api/admin/criteria")
    @role_required("admin")
    def list_criteria(_user):
        db = get_db()
        rid, _round_row, err = _resolve_round_id(db, required=False)
        if err:
            return err
        if rid is None:
            return jsonify([])
        criteria = db.execute(
            """
            SELECT c.id, c.round_id, r.name AS round_name, c.name, c.max_score
            FROM round_criteria c
            JOIN rounds r ON r.id = c.round_id
            WHERE c.round_id = ?
            ORDER BY c.id
            """,
            (rid,),
        ).fetchall()
        return jsonify(criteria)

    @app.post("/api/admin/criteria")
    @role_required("admin")
    def create_criteria(_user):
        db = get_db()
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        name = (payload.get("name") or "").strip()
        max_score = payload.get("max_score")
        if not name:
            return jsonify({"error": "Criterion name is required"}), 400
        try:
            max_score = float(max_score)
            if max_score <= 0 or max_score > 10:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "max_score must be > 0 and <= 10"}), 400
        with transaction():
            db.execute(
                """
                INSERT INTO round_criteria (round_id, name, max_score)
                VALUES (?, ?, ?)
                """,
                (rid, name, max_score),
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
                if max_score <= 0 or max_score > 10:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({"error": "max_score must be > 0 and <= 10"}), 400
            fields.append("max_score = ?")
            values.append(max_score)
        if not fields:
            return jsonify({"error": "No updates provided"}), 400
        values.append(criterion_id)
        with transaction():
            db = get_db()
            updated = db.execute(
                f"UPDATE round_criteria SET {', '.join(fields)} WHERE id = ?",
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
            deleted = db.execute(
                "DELETE FROM round_criteria WHERE id = ?",
                (criterion_id,),
            ).rowcount
        if not deleted:
            return jsonify({"error": "Criterion not found"}), 404
        return jsonify({"success": True})

    @app.get("/api/admin/assignments")
    @role_required("admin")
    def list_assignments(_user):
        db = get_db()
        rid, _round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        rows = db.execute(
            """
            SELECT
                a.round_id,
                a.judge_id, u.name AS judge_name,
                a.team_id, t.name AS team_name
            FROM round_assignments a
            JOIN users u ON u.id = a.judge_id
            JOIN teams t ON t.id = a.team_id
            WHERE a.round_id = ?
            ORDER BY u.name, t.name
            """,
            (rid,),
        ).fetchall()
        return jsonify(rows)

    @app.put("/api/admin/assignments/<int:judge_id>")
    @role_required("admin")
    def set_assignments(_user, judge_id):
        db = get_db()
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        team_ids = payload.get("team_ids")
        if not isinstance(team_ids, list):
            return jsonify({"error": "team_ids must be a list"}), 400
        try:
            clean_ids = sorted(set(int(team_id) for team_id in team_ids))
        except (TypeError, ValueError):
            return jsonify({"error": "team_ids must be integers"}), 400
        with transaction():
            judge = db.execute(
                "SELECT id FROM users WHERE id = ? AND role = 'judge'",
                (judge_id,),
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
                            {"error": "Some team_ids are invalid", "invalid_team_ids": invalid_ids}
                        ),
                        400,
                    )
            db.execute(
                "DELETE FROM round_assignments WHERE judge_id = ? AND round_id = ?",
                (judge_id, rid),
            )
            for team_id in clean_ids:
                db.execute(
                    """
                    INSERT INTO round_assignments (judge_id, team_id, round_id)
                    VALUES (?, ?, ?)
                    """,
                    (judge_id, team_id, rid),
                )
        return jsonify({"success": True})

    @app.get("/api/admin/scores")
    @role_required("admin")
    def admin_scores(_user):
        db = get_db()
        rid, _round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        rows = db.execute(
            """
            SELECT
                s.judge_id,
                u.name AS judge_name,
                s.team_id,
                t.name AS team_name,
                t.category AS team_category,
                s.criterion_id,
                c.name AS criterion_name,
                c.max_score,
                s.score,
                s.updated_at,
                CASE WHEN sub.id IS NULL THEN 0 ELSE 1 END AS is_submitted,
                sub.submitted_at,
                COALESCE(rm.text, '') AS remarks
            FROM round_final_scores s
            JOIN users u ON u.id = s.judge_id
            JOIN teams t ON t.id = s.team_id
            JOIN round_criteria c ON c.id = s.criterion_id
            LEFT JOIN round_final_submissions sub
                ON sub.judge_id = s.judge_id
                AND sub.team_id = s.team_id
                AND sub.round_id = s.round_id
            LEFT JOIN round_final_remarks rm
                ON rm.judge_id = s.judge_id
                AND rm.team_id = s.team_id
                AND rm.round_id = s.round_id
            WHERE s.round_id = ?
            ORDER BY t.name, u.name, c.id
            """,
            (rid,),
        ).fetchall()
        return jsonify(rows)


    @app.get("/api/admin/rankings")
    @role_required("admin")
    def rankings(_user):
        db = get_db()
        round_param = request.args.get("round_id")
        category = request.args.get("category")
        
        if round_param == "overall":
            rid = None
            round_row = {"id": "overall", "name": "Overall Leaderboard", "sequence": 999}
        else:
            rid, round_row, err = _resolve_round_id(db, requested=round_param, required=True)
            if err:
                return err
        
        rows = _build_rankings(db, rid, category=category)
        return jsonify(
            {
                "round_id": rid or "overall",
                "round": round_row,
                "rows": rows,
            }
        )


    @app.put("/api/admin/rankings/override")
    @role_required("admin")
    def upsert_override(user):
        db = get_db()
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        try:
            team_id = int(payload.get("team_id"))
            override_rank = int(payload.get("override_rank"))
            if override_rank <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "team_id and override_rank must be positive integers"}), 400
        reason = payload.get("reason") or ""
        with transaction():
            db.execute(
                """
                INSERT INTO round_ranking_overrides (
                    round_id, team_id, override_rank, reason, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(round_id, team_id)
                DO UPDATE SET
                    override_rank = excluded.override_rank,
                    reason = excluded.reason,
                    updated_by = excluded.updated_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (rid, team_id, override_rank, reason, user["id"]),
            )
        return jsonify({"success": True})

    @app.delete("/api/admin/rankings/override/<int:team_id>")
    @role_required("admin")
    def delete_override(_user, team_id):
        db = get_db()
        rid, _round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        with transaction():
            db.execute(
                "DELETE FROM round_ranking_overrides WHERE round_id = ? AND team_id = ?",
                (rid, team_id),
            )
        return jsonify({"success": True})

    @app.get("/api/admin/dashboard")
    @role_required("admin")
    def dashboard(_user):
        db = get_db()
        rid, round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        counts = {
            "judges": db.execute("SELECT COUNT(*) AS c FROM users WHERE role='judge'").fetchone()["c"],
            "teams": db.execute("SELECT COUNT(*) AS c FROM teams").fetchone()["c"],
            "criteria": db.execute(
                "SELECT COUNT(*) AS c FROM round_criteria WHERE round_id = ?",
                (rid,),
            ).fetchone()["c"],
            "assignments": db.execute(
                "SELECT COUNT(*) AS c FROM round_assignments WHERE round_id = ?",
                (rid,),
            ).fetchone()["c"],
            "submitted": db.execute(
                "SELECT COUNT(*) AS c FROM round_final_submissions WHERE round_id = ?",
                (rid,),
            ).fetchone()["c"],
            "submission_total_records": db.execute(
                "SELECT COUNT(*) AS c FROM round_final_submissions WHERE round_id = ?",
                (rid,),
            ).fetchone()["c"],
        }
        return jsonify(
            {
                "counts": counts,
                "round_id": rid,
                "round": round_row,
                "rankings": _build_rankings(db, rid),
                "server_time": datetime.now().isoformat(),
            }
        )

    @app.get("/api/admin/export/csv")
    @role_required("admin")
    def export_csv(_user):
        db = get_db()
        rid, round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        rankings_rows = _build_rankings(db, rid)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Round",
                "Rank",
                "Team ID",
                "Team Name",
                "Average Total Score",
                "Average Percentage",
                "Secondary Score",
                "Submitted Judges",
                "Override Rank",
                "Override Reason",
            ]
        )
        for row in rankings_rows:
            writer.writerow(
                [
                    round_row["name"] if round_row else f"Round {rid}",
                    row["rank"],
                    row["team_id"],
                    row["team_name"],
                    row["avg_total_score"],
                    row["avg_percentage"],
                    row["secondary_score"],
                    row["submitted_judges"],
                    row["override_rank"] if row["override_rank"] is not None else "",
                    row["override_reason"] or "",
                ]
            )
        output.seek(0)
        safe_name = (round_row["name"] if round_row else f"round_{rid}").replace(" ", "_")
        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8")),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"hackathon_rankings_{safe_name}.csv",
        )

    @app.get("/api/admin/teams/<int:team_id>/remarks")
    @role_required("admin")
    def get_team_remarks(_user, team_id):
        db = get_db()
        rows = db.execute(
            """
            SELECT
                r.name AS round_name,
                u.name AS judge_name,
                rm.text AS remarks,
                rm.updated_at
            FROM round_final_remarks rm
            JOIN rounds r ON r.id = rm.round_id
            JOIN users u ON u.id = rm.judge_id
            WHERE rm.team_id = ?
            ORDER BY r.sequence, u.name
            """,
            (team_id,),
        ).fetchall()
        return jsonify(rows)

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
        rid, round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        rows = db.execute(
            """
            SELECT
                t.id,
                t.name,
                t.ps_id,
                t.category,
                CASE WHEN fs.id IS NULL THEN 0 ELSE 1 END AS is_submitted,
                fs.submitted_at,
                COALESCE(draft_counts.score_count, 0) AS draft_score_count,
                COALESCE(dr.text, '') AS draft_remarks
            FROM round_assignments a
            JOIN teams t ON t.id = a.team_id
            LEFT JOIN round_final_submissions fs
                ON fs.judge_id = a.judge_id
                AND fs.team_id = a.team_id
                AND fs.round_id = a.round_id
            LEFT JOIN (
                SELECT judge_id, team_id, round_id, COUNT(*) AS score_count
                FROM round_draft_scores
                GROUP BY judge_id, team_id, round_id
            ) draft_counts
                ON draft_counts.judge_id = a.judge_id
                AND draft_counts.team_id = a.team_id
                AND draft_counts.round_id = a.round_id
            LEFT JOIN round_draft_remarks dr
                ON dr.judge_id = a.judge_id
                AND dr.team_id = a.team_id
                AND dr.round_id = a.round_id
            WHERE a.judge_id = ? AND a.round_id = ?
            ORDER BY t.name
            """,
            (user["id"], rid),
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
            item["round_id"] = rid
            item["round_name"] = round_row["name"] if round_row else None
            items.append(item)
        return jsonify({"round_id": rid, "round": round_row, "teams": items})

    @app.get("/api/judge/teams/<int:team_id>/evaluation")
    @role_required("judge")
    def get_evaluation(user, team_id):
        db = get_db()
        rid, round_row, err = _resolve_round_id(db, required=True)
        if err:
            return err
        if not _require_round_assignment(db, user["id"], team_id, rid):
            return jsonify({"error": "Team not assigned to this judge for this round"}), 403

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

        criteria = db.execute(
            """
            SELECT id, round_id, name, max_score
            FROM round_criteria
            WHERE round_id = ?
            ORDER BY id
            """,
            (rid,),
        ).fetchall()
        final_submission = db.execute(
            """
            SELECT submitted_at
            FROM round_final_submissions
            WHERE judge_id = ? AND team_id = ? AND round_id = ?
            LIMIT 1
            """,
            (user["id"], team_id, rid),
        ).fetchone()

        score_source_table = "round_draft_scores"
        remarks_source_table = "round_draft_remarks"
        if final_submission:
            score_source_table = "round_final_scores"
            remarks_source_table = "round_final_remarks"

        score_rows = db.execute(
            f"""
            SELECT criterion_id, score
            FROM {score_source_table}
            WHERE judge_id = ? AND team_id = ? AND round_id = ?
            """,
            (user["id"], team_id, rid),
        ).fetchall()
        scores = {str(row["criterion_id"]): row["score"] for row in score_rows}
        remarks = db.execute(
            f"""
            SELECT text
            FROM {remarks_source_table}
            WHERE judge_id = ? AND team_id = ? AND round_id = ?
            LIMIT 1
            """,
            (user["id"], team_id, rid),
        ).fetchone()

        deadline = _get_submission_deadline(db)
        is_submitted = 1 if final_submission else 0
        editable = _is_editable(db) and not final_submission
        return jsonify(
            {
                "round_id": rid,
                "round": round_row,
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
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        if not _require_round_assignment(db, user["id"], team_id, rid):
            return jsonify({"error": "Team not assigned to this judge for this round"}), 403
        if not _is_editable(db):
            return jsonify({"error": "Submission deadline passed. Editing is locked."}), 403
        submitted = db.execute(
            """
            SELECT id
            FROM round_final_submissions
            WHERE judge_id = ? AND team_id = ? AND round_id = ?
            LIMIT 1
            """,
            (user["id"], team_id, rid),
        ).fetchone()
        if submitted:
            return jsonify({"error": "Final submission is immutable."}), 409

        ok, errors = _save_round_draft_evaluation(
            db=db,
            judge_id=user["id"],
            team_id=team_id,
            round_id=rid,
            scores_payload=payload.get("scores") or {},
            remarks_text=payload.get("remarks"),
        )
        if not ok:
            return jsonify({"error": "Invalid draft payload", "details": errors}), 400
        return jsonify(
            {"success": True, "status": "in_progress", "synced": True, "round_id": rid}
        )

    @app.post("/api/judge/teams/<int:team_id>/submit")
    @role_required("judge")
    def submit_scores(user, team_id):
        db = get_db()
        payload = request.get_json(silent=True) or {}
        rid, _round_row, err = _resolve_round_id(db, payload.get("round_id"), required=True)
        if err:
            return err
        if not _require_round_assignment(db, user["id"], team_id, rid):
            return jsonify({"error": "Team not assigned to this judge for this round"}), 403
        if not _is_editable(db):
            return jsonify({"error": "Submission deadline passed. Editing is locked."}), 403

        ok, errors, already_submitted = _submit_round_final_evaluation(
            db=db,
            judge_id=user["id"],
            team_id=team_id,
            round_id=rid,
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
                "round_id": rid,
            }
        )


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
