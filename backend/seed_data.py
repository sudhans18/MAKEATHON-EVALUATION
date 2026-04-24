from werkzeug.security import generate_password_hash

from app import create_app
from db import get_db, transaction


def seed():
    app = create_app()
    with app.app_context():
        db = get_db()
        with transaction():
            users = [
                ("admin", "admin", "admin123"),
                ("judge_1", "judge", "judge123"),
                ("judge_2", "judge", "judge123"),
                ("judge_3", "judge", "judge123"),
            ]
            for name, role, plain_password in users:
                existing = db.execute(
                    "SELECT id FROM users WHERE name = ? LIMIT 1", (name,)
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO users (name, role, password_hash) VALUES (?, ?, ?)",
                        (name, role, generate_password_hash(plain_password)),
                    )

            teams = [
                (
                    "Team Alpha",
                    "Build an offline-first emergency response tracker",
                    "Local queue, synchronization retries, and clear mobile UI.",
                ),
                (
                    "Team Beta",
                    "Design a campus navigation assistant for offline use",
                    "Efficient route caching and intuitive wayfinding interaction.",
                ),
                (
                    "Team Gamma",
                    "Prototype a low-connectivity telemedicine assistant",
                    "Prioritize reliability, secure data handling, and usability.",
                ),
            ]
            for name, statement, expected in teams:
                existing = db.execute(
                    "SELECT id FROM teams WHERE name = ? LIMIT 1", (name,)
                ).fetchone()
                if not existing:
                    db.execute(
                        """
                        INSERT INTO teams (name, problem_statement, expected_solution)
                        VALUES (?, ?, ?)
                        """,
                        (name, statement, expected),
                    )

            criteria = [
                ("Innovation", 10),
                ("Technical Complexity", 10),
                ("UI/UX", 10),
                ("Feasibility", 10),
            ]
            for name, max_score in criteria:
                existing = db.execute(
                    "SELECT id FROM criteria WHERE name = ? LIMIT 1", (name,)
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO criteria (name, max_score) VALUES (?, ?)",
                        (name, max_score),
                    )

            judges = db.execute(
                "SELECT id, name FROM users WHERE role = 'judge' ORDER BY id"
            ).fetchall()
            team_rows = db.execute("SELECT id FROM teams ORDER BY id").fetchall()
            team_ids = [row["id"] for row in team_rows]
            for judge in judges:
                for team_id in team_ids:
                    db.execute(
                        """
                        INSERT OR IGNORE INTO assignments (judge_id, team_id)
                        VALUES (?, ?)
                        """,
                        (judge["id"], team_id),
                    )

            db.execute(
                "DELETE FROM settings WHERE key = 'submission_deadline'"
            )

    print("Sample data seeded.")
    print("Admin login: admin / admin123")
    print("Judge login: judge_1 / judge123")


if __name__ == "__main__":
    seed()

