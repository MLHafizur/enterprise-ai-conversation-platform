from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def test_alembic_config_is_dsn_free_and_points_to_migrations() -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")

    script_location = config.get_main_option("script_location")

    assert script_location is not None
    assert Path(script_location).resolve() == PROJECT_ROOT / "migrations"
    assert config.get_main_option("sqlalchemy.url") is None


def test_migration_graph_has_exactly_one_head() -> None:
    config = Config(PROJECT_ROOT / "alembic.ini")
    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1
