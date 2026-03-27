import json
import os
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

_data_dir = Path(os.environ.get('FORGE_DATA_DIR', '.'))
DATABASE_URL = f"sqlite:///{_data_dir / 'forge.db'}"
SETTINGS_PATH = _data_dir / 'forge_settings.json'

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    if SETTINGS_PATH.exists():
        return json.loads(SETTINGS_PATH.read_text())
    return {}


def save_settings(data: dict) -> None:
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))
