"""
Core data model. One schema for every vertical.

Rule: NO `if vertical == "..."` anywhere in business logic. Everything
vertical-specific lives in configs/*.yaml and in Asset.attributes (JSON).
"""
import enum
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    """One business using the platform (repair shop, salon, clinic)."""
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    vertical: Mapped[str] = mapped_column(String(50))
    phone: Mapped[str | None] = mapped_column(String(32))
    # INTEGRATION POINT: contract / account number in the billing system
    billing_account: Mapped[str | None] = mapped_column(String(64))
    # INTEGRATION POINT: location of the premises in the mapping system
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    clients: Mapped[list["Client"]] = relationship(back_populates="tenant")


class Client(Base):
    """
    End customer of the business. Phone is the primary identifier and is
    unique within a tenant — the same person may be a customer of both the
    repair shop and the salon, and those are two separate records.
    """
    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_client_tenant_phone"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    address: Mapped[str | None] = mapped_column(String(300))
    # INTEGRATION POINT (maps): where the customer is, for on-site work
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    # INTEGRATION POINT: id of this record in a foreign system
    # (billing, legacy CRM). Lets us match records without creating duplicates.
    external_source: Mapped[str | None] = mapped_column(String(50), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    tenant: Mapped["Tenant"] = relationship(back_populates="clients")
    assets: Mapped[list["Asset"]] = relationship(back_populates="client")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="client")


class Asset(Base):
    """
    The thing being serviced: a vehicle, a pet, a patient record.

    One table for every vertical. What goes in `attributes` is described by
    the vertical's YAML — a car carries {plate, brand, mileage}, a pet
    carries {species, breed, weight}. A salon has no assets at all.
    """
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    client: Mapped["Client"] = relationship(back_populates="assets")


class Resource(Base):
    """
    Whoever or whatever is occupied for a block of time: a stylist, a vet,
    a service bay. Availability checks hang off this.
    """
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(100))
    mobile: Mapped[bool] = mapped_column(Boolean, default=False)  # on-site crew
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Service(Base):
    """
    Service catalogue. `repeat_days` is the repeat-business engine: once a
    visit is closed, the next offer is scheduled that many days out.
    """
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    price: Mapped[float] = mapped_column(Float, default=0)
    repeat_days: Mapped[int | None] = mapped_column(Integer)
    onsite: Mapped[bool] = mapped_column(Boolean, default=False)  # travel to customer
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppointmentStatus(str, enum.Enum):
    scheduled = "scheduled"
    confirmed = "confirmed"
    done = "done"
    cancelled = "cancelled"
    no_show = "no_show"


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    resource_id: Mapped[int | None] = mapped_column(ForeignKey("resources.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.scheduled
    )
    source: Mapped[str] = mapped_column(String(30), default="operator")
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    client: Mapped["Client"] = relationship(back_populates="appointments")
    service: Mapped["Service"] = relationship()
    asset: Mapped["Asset"] = relationship()
    resource: Mapped["Resource"] = relationship()
    invoice: Mapped["Invoice | None"] = relationship(back_populates="appointment")


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus), default=InvoiceStatus.draft
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    # INTEGRATION POINT (billing): invoice number and payment link
    external_source: Mapped[str | None] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(100))
    pay_link: Mapped[str | None] = mapped_column(String(500))

    appointment: Mapped["Appointment"] = relationship(back_populates="invoice")


class MessageStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    failed = "failed"
    cancelled = "cancelled"


class ScheduledMessage(Base):
    """
    Outbound queue. The scheduler picks up whatever has come due and sends
    it. Reminders and repeat offers live here — this is the core of the
    product's value.
    """
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    kind: Mapped[str] = mapped_column(String(30))
    template_key: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    send_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus), default=MessageStatus.pending, index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)

    client: Mapped["Client"] = relationship()


class AuditLog(Base):
    """Who touched what and when. Written from day one with clinics in mind."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100), default="operator")
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
