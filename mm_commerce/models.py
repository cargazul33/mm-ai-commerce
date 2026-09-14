"""Esquema SQLAlchemy mínimo — SQLite por defecto."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64), default="codineu")
    title: Mapped[str] = mapped_column(Text, default="")
    organism: Mapped[str] = mapped_column(String(255), default="")
    rubros: Mapped[str] = mapped_column(Text, default="")
    modality: Mapped[str] = mapped_column(String(255), default="")
    opening_at: Mapped[str] = mapped_column(String(64), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(32), default="RADAR")  # state machine
    risk_level: Mapped[str] = mapped_column(String(16), default="")
    approval_status: Mapped[str] = mapped_column(
        String(32), default="PENDIENTE"
    )  # PENDIENTE|APROBADO|RECHAZADO|BLOQUEADO
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_reason: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    tender: Mapped[Optional["Tender"]] = relationship(back_populates="opportunity")
    offers: Mapped[list["Offer"]] = relationship(back_populates="opportunity")
    approvals: Mapped[list["Approval"]] = relationship(back_populates="opportunity")


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    process_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_path: Mapped[str] = mapped_column(Text, default="")
    delivery_notes: Mapped[str] = mapped_column(Text, default="")
    deadlines: Mapped[str] = mapped_column(Text, default="")
    extraction_status: Mapped[str] = mapped_column(
        String(32), default="NO VERIFICADO"
    )
    notes: Mapped[str] = mapped_column(Text, default="")

    opportunity: Mapped["Opportunity"] = relationship(back_populates="tender")
    items: Mapped[list["TenderItem"]] = relationship(back_populates="tender")


class TenderItem(Base):
    __tablename__ = "tender_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[int] = mapped_column(ForeignKey("tenders.id"))
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    product: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str] = mapped_column(String(32), default="u")
    brand: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    specs: Mapped[str] = mapped_column(Text, default="")
    verification: Mapped[str] = mapped_column(
        String(32), default="NO VERIFICADO"
    )  # CONFIRMADO|PROBABLE|NO VERIFICADO

    tender: Mapped["Tender"] = relationship(back_populates="items")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(128), default="", index=True)
    name: Mapped[str] = mapped_column(Text, default="")
    brand: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    specs: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(128), default="")


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    source: Mapped[str] = mapped_column(String(64), default="allowlist")
    url: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str] = mapped_column(String(8), default="AR")
    notes: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    quotes: Mapped[list["SupplierQuote"]] = relationship(back_populates="supplier")


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"))
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    tender_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tender_items.id"), nullable=True
    )
    product_label: Mapped[str] = mapped_column(Text, default="")
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="ARS")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    verification: Mapped[str] = mapped_column(
        String(32), default="NO VERIFICADO"
    )  # CONFIRMADO|PROBABLE|NO VERIFICADO
    url: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    supplier: Mapped["Supplier"] = relationship(back_populates="quotes")


class Offer(Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    cost_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precio_objetivo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_multiplier: Mapped[float] = mapped_column(Float, default=1.90)
    tax_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    logistics_status: Mapped[str] = mapped_column(String(32), default="PENDING")
    package_path: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(
        String(32), default="BORRADOR"
    )  # NEVER auto-submit
    notes: Mapped[str] = mapped_column(Text, default="")

    opportunity: Mapped["Opportunity"] = relationship(back_populates="offers")
    items: Mapped[list["OfferItem"]] = relationship(back_populates="offer")


class OfferItem(Base):
    __tablename__ = "offer_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    tender_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tender_items.id"), nullable=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    qty: Mapped[float] = mapped_column(Float, default=1.0)
    unit_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verification: Mapped[str] = mapped_column(String(32), default="NO VERIFICADO")

    offer: Mapped["Offer"] = relationship(back_populates="items")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    status: Mapped[str] = mapped_column(
        String(32), default="PENDIENTE"
    )  # PENDIENTE|APROBADO|RECHAZADO|BLOQUEADO
    actor: Mapped[str] = mapped_column(String(64), default="human")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="approvals")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="OK")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")


class AgentFinding(Base):
    __tablename__ = "agent_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    opportunity_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("opportunities.id"), nullable=True
    )
    severity: Mapped[str] = mapped_column(String(16), default="INFO")
    code: Mapped[str] = mapped_column(String(64), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[bool] = mapped_column(Boolean, default=False)


class LogisticsQuote(Base):
    """Stub — tasas desconocidas = PENDING, nunca inventar."""

    __tablename__ = "logistics_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"))
    carrier: Mapped[str] = mapped_column(String(128), default="")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="ARS")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    notes: Mapped[str] = mapped_column(Text, default="REQUIERE COTIZACIÓN REAL")


_engine = None
_SessionLocal = None


def get_engine(database_url: str | None = None):
    global _engine, _SessionLocal
    from mm_commerce.config import get_settings

    url = database_url or get_settings().database_url
    if _engine is None or str(_engine.url) != url.replace("sqlite:///", "sqlite:///"):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def init_db(database_url: str | None = None):
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


def get_session(database_url: str | None = None):
    get_engine(database_url)
    assert _SessionLocal is not None
    return _SessionLocal()
