from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ClientIn(BaseModel):
    phone: str
    name: str | None = None
    notes: str | None = None
    address: str | None = None


class ClientOut(ORM):
    id: int
    phone: str
    name: str | None
    notes: str | None
    address: str | None = None
    lat: float | None = None
    lon: float | None = None


class ClientCreateResult(BaseModel):
    created: bool
    client: ClientOut
    message: str


class AssetIn(BaseModel):
    client_id: int
    title: str
    attributes: dict = {}


class AssetOut(ORM):
    id: int
    client_id: int
    type: str
    title: str
    attributes: dict


class ResourceIn(BaseModel):
    name: str
    role: str | None = None
    mobile: bool = False


class ResourceOut(ORM):
    id: int
    name: str
    role: str | None
    mobile: bool
    active: bool


class ServiceOut(ORM):
    id: int
    name: str
    duration_min: int
    price: float
    repeat_days: int | None
    onsite: bool


class AppointmentIn(BaseModel):
    client_id: int
    service_id: int
    asset_id: int | None = None
    resource_id: int | None = None
    starts_at: datetime
    comment: str | None = None
    source: str = "operator"


class AppointmentOut(ORM):
    id: int
    client_id: int
    asset_id: int | None
    service_id: int
    resource_id: int | None
    starts_at: datetime
    ends_at: datetime
    status: str
    source: str
    comment: str | None


class StatusIn(BaseModel):
    status: str


class SlotCheckOut(BaseModel):
    free: bool
    reason: str | None = None
    conflict_appointment_id: int | None = None


class InvoiceOut(ORM):
    id: int
    appointment_id: int
    amount: float
    status: str
    issued_at: datetime
    paid_at: datetime | None
    external_source: str | None = None
    external_id: str | None = None
    pay_link: str | None = None


class MessageOut(ORM):
    id: int
    client_id: int
    channel: str
    kind: str
    rendered_text: str | None
    send_at: datetime
    status: str
    sent_at: datetime | None


class IncomingCallIn(BaseModel):
    phone: str


class TimeTravelIn(BaseModel):
    days: int = 0
    hours: int = 0
