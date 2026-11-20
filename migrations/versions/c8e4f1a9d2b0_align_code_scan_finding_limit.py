"""align code scan finding limit

Revision ID: c8e4f1a9d2b0
Revises: f7b2c8d4e1a0
Create Date: 2026-11-16 23:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "c8e4f1a9d2b0"
down_revision: str | Sequence[str] | None = "f7b2c8d4e1a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_code_scans_finding_counts_valid"


def upgrade() -> None:
    """Align durable scan summaries with the 10,000-finding product ceiling."""
    _replace_finding_count_constraint(maximum=10_000)


def downgrade() -> None:
    """Restore the original Day 10 persistence ceiling."""
    _replace_finding_count_constraint(maximum=100_000)


def _replace_finding_count_constraint(*, maximum: int) -> None:
    op.drop_constraint(
        op.f(_CONSTRAINT),
        "code_scans",
        schema="chat",
        type_="check",
    )
    op.create_check_constraint(
        op.f(_CONSTRAINT),
        "code_scans",
        (
            f"total_findings BETWEEN 0 AND {maximum} "
            "AND critical_findings >= 0 "
            "AND high_findings >= 0 "
            "AND medium_findings >= 0 "
            "AND low_findings >= 0 "
            "AND info_findings >= 0 "
            "AND total_findings = critical_findings + high_findings "
            "+ medium_findings + low_findings + info_findings"
        ),
        schema="chat",
    )
