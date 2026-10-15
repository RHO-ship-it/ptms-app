import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Génère une clé forte persistée dans un fichier local si non définie en env
_secret_file = os.path.join(BASE_DIR, '.secret_key')
def _get_secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if os.path.exists(_secret_file):
        with open(_secret_file, 'r') as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(_secret_file, 'w') as f:
        f.write(key)
    return key

class Config:
    SECRET_KEY = _get_secret_key()
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'ptms.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    EXPORT_FOLDER = os.path.join(BASE_DIR, "exports")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB
