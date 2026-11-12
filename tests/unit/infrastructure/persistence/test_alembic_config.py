from pathlib import Path

from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def test_alembic_config_is_dsn_free_and_points_to_migrations() -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")

    script_location = config.get_main_option("script_location")

    assert script_location is not None
    assert Path(script_location).resolve() == PROJECT_ROOT / "migrations"
    assert config.get_main_option("sqlalchemy.url") is None
