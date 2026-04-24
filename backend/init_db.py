from app import create_app
from db import init_db


def main():
    app = create_app()
    with app.app_context():
        init_db()
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()

