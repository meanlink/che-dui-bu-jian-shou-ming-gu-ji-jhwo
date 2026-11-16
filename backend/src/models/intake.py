from sqlalchemy import String, JSON, Double, ForeignKey, Integer, Boolean, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class IntakeConvention(Base):
    """A versioned field-conversion convention. Sheets snapshot it on receipt."""
    __tablename__ = 'intake_conventions'
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(160))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(150), default='system')
    created_at: Mapped[float] = mapped_column(Double)


class IntakeSheet(Base):
    """Clearing sheet for one supplier table.

    raw_rows, checksum and convention are frozen at receipt. Accepted rows are
    appended exactly once to the deterministic trial batch (batch_id)."""
    __tablename__ = 'intake_sheets'
    __table_args__ = (UniqueConstraint('name', 'checksum', name='uq_intake_sheet_source'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(200), default='')
    status: Mapped[str] = mapped_column(String(20), default='open')
    raw_rows: Mapped[list] = mapped_column(JSON, default=list)
    checksum: Mapped[str] = mapped_column(String(64), default='')
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    convention: Mapped[dict] = mapped_column(JSON, default=dict)
    convention_version: Mapped[int] = mapped_column(Integer, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_count: Mapped[int] = mapped_column(Integer, default=0)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    decision_seq: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[float] = mapped_column(Double)
    settled_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    settled_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)


class IntakeRow(Base):
    """Immutable original row plus its current disposition.

    raw never changes; draft holds corrections used for replay. An accepted row
    keeps its normalized values and batch_position and is never rewritten."""
    __tablename__ = 'intake_rows'
    __table_args__ = (UniqueConstraint('sheet_id', 'row_index', name='uq_intake_row_index'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sheet_id: Mapped[int] = mapped_column(ForeignKey('intake_sheets.id'), index=True)
    row_index: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    draft: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='quarantined')
    reason_code: Mapped[str] = mapped_column(String(40), default='')
    reason: Mapped[str] = mapped_column(String(600), default='')
    asset: Mapped[str | None] = mapped_column(String(160), nullable=True)
    normalized: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    needs_adjudication: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(String(20), default='pending')
    resolution_note: Mapped[str] = mapped_column(String(300), default='')
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    batch_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[float] = mapped_column(Double, default=0.0)
