"""API 入参模型（出参直接由服务层组装为 dict，保证审计字段完整透传）。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    username: str
    password: str


class EvidenceIn(BaseModel):
    source_type: str = Field(pattern="^(collection_task|gate_record|weighbridge_reading|facility_receipt|rate_catalog|accounting_receipt)$")
    raw_text: str
    event_time: datetime
    device_id: str | None = None
    site_id: str | None = None
    calibration_version: int | None = None
    payload: dict = Field(default_factory=dict)


class BatchIn(BaseModel):
    id: str
    contract_id: str
    vehicle_id: str
    service_time: datetime


class LinkIn(BaseModel):
    event_id: int
    link_type: str = Field(pattern="^(task|weighing|receipt|gate)$")


class LinkDecisionIn(BaseModel):
    reason: str = ""


class CatalogIn(BaseModel):
    effective_from: datetime
    tiers: list[dict]  # [{"up_to": 10|null, "rate": 80.0}, ...]
    note: str = ""


class CalibrationCorrectIn(BaseModel):
    factor: float
    note: str = ""


class DepartureCheckIn(BaseModel):
    tolerance_minutes: int = 10
