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
        os.environ["DISABLE_DB_BACKUP_WORKER"] = "1"
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.team_id = None
        self.round_id = None
        self.judge_ids = {}
        self.criteria_ids = []
        with self.app.app_context():
            db = get_db()
            db.executescript(
                """
                DELETE FROM round_ranking_overrides;
                DELETE FROM round_final_remarks;
                DELETE FROM round_final_scores;
                DELETE FROM round_final_submissions;
                DELETE FROM round_draft_remarks;
                DELETE FROM round_draft_scores;
                DELETE FROM round_assignments;
                DELETE FROM round_criteria;
                DELETE FROM ranking_overrides;
                DELETE FROM final_remarks;
                DELETE FROM final_scores;
                DELETE FROM final_submissions;
                DELETE FROM draft_remarks;
                DELETE FROM draft_scores;
                DELETE FROM submissions;
                DELETE FROM remarks;
                DELETE FROM scores;
                DELETE FROM assignments;
                DELETE FROM criteria;
                DELETE FROM teams;
                DELETE FROM users;
                DELETE FROM rounds;
                """
            )
            db.execute(
                "INSERT INTO rounds (name, sequence) VALUES ('Round 1', 1)"
            )
            self.round_id = db.execute(
                "SELECT id FROM rounds WHERE sequence = 1 LIMIT 1"
            ).fetchone()["id"]
            db.execute(
                """
                INSERT INTO settings (key, value) VALUES ('active_round_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(self.round_id),),
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
                "INSERT INTO round_criteria (round_id, name, max_score) VALUES (?, ?, ?)",
                (self.round_id, "Innovation", 10),
            )
            db.execute(
                "INSERT INTO round_criteria (round_id, name, max_score) VALUES (?, ?, ?)",
                (self.round_id, "Feasibility", 10),
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
                row["id"]
                for row in db.execute(
                    "SELECT id FROM round_criteria WHERE round_id = ? ORDER BY id",
                    (self.round_id,),
                ).fetchall()
            ]
            for judge in judge_rows:
                db.execute(
                    """
                    INSERT INTO round_assignments (judge_id, team_id, round_id)
                    VALUES (?, ?, ?)
                    """,
                    (judge["id"], self.team_id, self.round_id),
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
                    "round_id": self.round_id,
                    "scores": {str(c1): 8, str(c2): 9},
                    "remarks": "Strong architecture and robust UX.",
                },
            )
            self.assertEqual(draft_resp.status_code, 200)

            eval_resp = client.get(
                f"/api/judge/teams/{self.team_id}/evaluation?round_id={self.round_id}"
            )
            self.assertEqual(eval_resp.status_code, 200)
            payload = eval_resp.get_json()
            self.assertEqual(payload["scores"][str(c1)], 8.0)
            self.assertEqual(payload["scores"][str(c2)], 9.0)
            self.assertIn("Strong architecture", payload["remarks"])

    def test_no_duplicate_submission_records(self):
        with self.app.test_client() as client:
            self._login(client, "judge_a", "judge123")
            c1, c2 = self.criteria_ids
            payload = {
                "round_id": self.round_id,
                "scores": {str(c1): 7, str(c2): 8},
                "remarks": "Submission test",
            }
            first = client.post(f"/api/judge/teams/{self.team_id}/submit", json=payload)
            second = client.post(f"/api/judge/teams/{self.team_id}/submit", json=payload)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)

        with self.app.app_context():
            db = get_db()
            count = db.execute(
                """
                SELECT COUNT(*) AS c
                FROM round_final_submissions
                WHERE judge_id = ? AND team_id = ? AND round_id = ?
                """,
                (self.judge_ids["judge_a"], self.team_id, self.round_id),
            ).fetchone()["c"]
            self.assertEqual(count, 1)

    def test_concurrent_submissions(self):
        def judge_submit(name):
            with self.app.test_client() as client:
                self._login(client, name, "judge123")
                c1, c2 = self.criteria_ids
                client.post(
                    f"/api/judge/teams/{self.team_id}/submit",
                    json={
                        "round_id": self.round_id,
                        "scores": {str(c1): 6, str(c2): 7},
                        "remarks": f"From {name}",
                    },
                )

        t1 = threading.Thread(target=judge_submit, args=("judge_a",))
        t2 = threading.Thread(target=judge_submit, args=("judge_b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        with self.app.test_client() as client:
            self._login(client, "admin", "admin123")
            dashboard = client.get(f"/api/admin/dashboard?round_id={self.round_id}")
            self.assertEqual(dashboard.status_code, 200)
            data = dashboard.get_json()
            self.assertGreaterEqual(data["counts"]["submitted"], 2)

    def test_final_submission_is_immutable(self):
        with self.app.test_client() as client:
            self._login(client, "judge_a", "judge123")
            c1, c2 = self.criteria_ids
            submit_resp = client.post(
                f"/api/judge/teams/{self.team_id}/submit",
                json={
                    "round_id": self.round_id,
                    "scores": {str(c1): 9, str(c2): 8},
                    "remarks": "Locked",
                },
            )
            self.assertEqual(submit_resp.status_code, 200)

            edit_after_submit = client.put(
                f"/api/judge/teams/{self.team_id}/draft",
                json={
                    "round_id": self.round_id,
                    "scores": {str(c1): 1, str(c2): 1},
                    "remarks": "Should fail",
                },
            )
            self.assertEqual(edit_after_submit.status_code, 409)


if __name__ == "__main__":
    unittest.main()
