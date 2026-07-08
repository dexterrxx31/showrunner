"""Catalog and ingest-job tables.

The AssetRow/SegmentRow pair is the persistent form of the timeline's
Asset/Segment dataclasses. `position` sets the channel play order (until the
Phase-3 scheduler owns ordering); segments keep their `idx` so a playlist
reassembles in the exact order they were cut.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class AssetRow(Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    segments: Mapped[list["SegmentRow"]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        order_by="SegmentRow.idx",
    )


class SegmentRow(Base):
    __tablename__ = "segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    uri: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[float] = mapped_column(Float, nullable=False)

    asset: Mapped["AssetRow"] = relationship(back_populates="segments")


class IngestJobRow(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
