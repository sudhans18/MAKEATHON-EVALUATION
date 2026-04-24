import os
import sys
import tempfile
import threading
import unittest

from werkzeug.security import generate_password_hash

CURRENT_DIR = os.path.dirname(__file__)
BACKEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app import create_app
from db import get_db


class HackathonApiTests(unittest.TestCase):
    def setUp(self):
        fd, db_path = tempfile.mkstemp(prefix="judging_test_", suffix=".db")
        os.close(fd)
        self.db_path = db_path
        os.environ["DATABASE_PATH"] = db_path
        os.environ["SECRET_KEY"] = "test-secret"
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.team_id = None
        self.judge_ids = {}
        self.criteria_ids = []
        with self.app.app_context():
            db = get_db()
            db.executescript(
                """
                DELETE FROM ranking_overrides;
                DELETE FROM submissions;
                DELETE FROM remarks;
                DELETE FROM scores;
                DELETE FROM assignments;
                DELETE FROM criteria;
                DELETE FROM teams;
                DELETE FROM users;
                """
            )
            db.execute(
                "INSERT INTO users (name, role, password_hash) VALUES (?, 'admin', ?)",
                ("admin", generate_password_hash("admin123")),
            )
            db.execute(
                "INSERT INTO users (name, role, password_hash) VALUES (?, 'judge', ?)",
                ("judge_a", generate_password_hash("judge123")),
            )
            db.execute(
                "INSERT INTO users (name, role, password_hash) VALUES (?, 'judge', ?)",
                ("judge_b", generate_password_hash("judge123")),
            )
            db.execute(
                "INSERT INTO teams (name, problem_statement, expected_solution) VALUES (?, ?, ?)",
                ("Team One", "Problem", "Expected"),
            )
            db.execute(
                "INSERT INTO criteria (name, max_score) VALUES (?, ?)",
                ("Innovation", 10),
            )
            db.execute(
                "INSERT INTO criteria (name, max_score) VALUES (?, ?)",
                ("Feasibility", 10),
            )
            judge_rows = db.execute("SELECT id FROM users WHERE role = 'judge'").fetchall()
            self.team_id = db.execute(
                "SELECT id FROM teams WHERE name = 'Team One'"
            ).fetchone()["id"]
            self.judge_ids = {
                row["name"]: row["id"]
                for row in db.execute(
                    "SELECT id, name FROM users WHERE role = 'judge'"
                ).fetchall()
            }
            self.criteria_ids = [
                row["id"] for row in db.execute("SELECT id FROM criteria ORDER BY id").fetchall()
            ]
            for judge in judge_rows:
                db.execute(
                    "INSERT INTO assignments (judge_id, team_id) VALUES (?, ?)",
                    (judge["id"], self.team_id),
                )
            db.commit()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _login(self, client, name, password):
        return client.post(
            "/api/auth/login",
            json={"name": name, "password": password},
        )

    def test_draft_scores_persist(self):
        with self.app.test_client() as client:
            self._login(client, "judge_a", "judge123")
            c1, c2 = self.criteria_ids
            draft_resp = client.put(
                f"/api/judge/teams/{self.team_id}/draft",
                json={
                    "scores": {str(c1): 8, str(c2): 9},
                    "remarks": "Strong architecture and robust UX.",
                },
            )
            self.assertEqual(draft_resp.status_code, 200)

            eval_resp = client.get(f"/api/judge/teams/{self.team_id}/evaluation")
            self.assertEqual(eval_resp.status_code, 200)
            payload = eval_resp.get_json()
            self.assertEqual(payload["scores"][str(c1)], 8.0)
            self.assertEqual(payload["scores"][str(c2)], 9.0)
            self.assertIn("Strong architecture", payload["remarks"])

    def test_no_duplicate_submission_records(self):
        with self.app.test_client() as client:
            self._login(client, "judge_a", "judge123")
            c1, c2 = self.criteria_ids
            payload = {"scores": {str(c1): 7, str(c2): 8}, "remarks": "Submission test"}
            first = client.post(f"/api/judge/teams/{self.team_id}/submit", json=payload)
            second = client.post(f"/api/judge/teams/{self.team_id}/submit", json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)

        with self.app.app_context():
            db = get_db()
            count = db.execute(
                "SELECT COUNT(*) AS c FROM submissions WHERE judge_id = ? AND team_id = ?",
                (self.judge_ids["judge_a"], self.team_id),
            ).fetchone()["c"]
            self.assertEqual(count, 1)

    def test_concurrent_submissions(self):
        def judge_submit(name):
            with self.app.test_client() as client:
                self._login(client, name, "judge123")
                c1, c2 = self.criteria_ids
                client.post(
                    f"/api/judge/teams/{self.team_id}/submit",
                    json={"scores": {str(c1): 6, str(c2): 7}, "remarks": f"From {name}"},
                )

        t1 = threading.Thread(target=judge_submit, args=("judge_a",))
        t2 = threading.Thread(target=judge_submit, args=("judge_b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        with self.app.test_client() as client:
            self._login(client, "admin", "admin123")
            dashboard = client.get("/api/admin/dashboard")
            self.assertEqual(dashboard.status_code, 200)
            data = dashboard.get_json()
            self.assertGreaterEqual(data["counts"]["submitted"], 2)


if __name__ == "__main__":
    unittest.main()
