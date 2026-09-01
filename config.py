import os

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "sps3-secret-key")
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

    APP_DATA_DIR = os.environ.get("APP_DATA_DIR", BASE_DIR)

    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL")
        or f"sqlite:///{os.path.join(APP_DATA_DIR, 'database', 'db.sqlite3')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER") or os.path.join(APP_DATA_DIR, "uploads")
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
