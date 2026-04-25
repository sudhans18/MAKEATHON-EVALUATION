import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-secret-in-production")
    DATABASE_PATH = os.environ.get(
        "DATABASE_PATH",
        os.path.join(os.path.dirname(__file__), "judging.db"),
    )
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    JSON_SORT_KEYS = False
    DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "16"))
    DB_BACKUP_INTERVAL_SECONDS = int(os.environ.get("DB_BACKUP_INTERVAL_SECONDS", "300"))
    DB_BACKUP_DIR = os.environ.get(
        "DB_BACKUP_DIR",
        os.path.join(os.path.dirname(__file__), "backups"),
    )
    LOG_DIR = os.environ.get("LOG_DIR", os.path.join(os.path.dirname(__file__), "logs"))
