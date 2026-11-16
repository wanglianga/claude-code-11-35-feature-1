"""领域模型：来源证据库、批次关系册、计量裁定、费率编译、差异发布。

设计要点：
- 证据（SourceEvent）与费率目录（RateCatalog）创建后不可变，内容哈希用于审计核对。
- 计量事实 / 金额事实 / 发布记录均为版本化追加写，历史版本永不回写。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- 用户与会话


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    # analyst（数据团队，可写） / auditor（财务，只读）
    role: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


# ---------------------------------------------------------------- 合同与费率


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 HT-2026-001
    name: Mapped[str] = mapped_column(String(128))
    contract_type: Mapped[str] = mapped_column(String(64))  # 主计量点合同 / 区间裁定合同
    strategy: Mapped[str] = mapped_column(String(16))  # priority / interval
    # 优先级策略：来源优先级列表，如 ["facility_receipt","weighbridge_reading","gate_record"]
    source_priority: Mapped[list] = mapped_column(JSON, default=list)
    # 质量门槛：事件时间与任务时间的容差（分钟）
    time_tolerance_minutes: Mapped[int] = mapped_column(Integer, default=45)
    # 区间策略：单份重量的质量容差（比例，如 0.005 表示 ±0.5%）
    quality_tolerance_pct: Mapped[float] = mapped_column(Float, default=0.005)
    # 已批准的换算依据（区间不重叠时可用），如 {"factor": 0.98, "note": "..."}
    approved_conversion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateCatalog(Base):
    """费率目录：冻结后不可变；修订产生新版本，不回写历史。"""

    __tablename__ = "rate_catalogs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=True)
    # 累进阶梯：[{"up_to": 10, "rate": 80.0}, {"up_to": 20, "rate": 95.0}, {"up_to": null, "rate": 110.0}]
    tiers: Mapped[list] = mapped_column(JSON)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CalibrationRecord(Base):
    """地磅校准记录：更正产生新版本，旧版本标记 corrected，历史不删除。"""

    __tablename__ = "calibration_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    factor: Mapped[float] = mapped_column(Float, default=1.0)  # 真值 = 读数 × factor
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / corrected
    corrected_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- 来源证据库


class SourceEvent(Base):
    """来源证据：收运任务/门岗/地磅/签收/费率目录/账务回执的原文与元数据。"""

    __tablename__ = "source_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    # collection_task / gate_record / weighbridge_reading / facility_receipt /
    # rate_catalog / accounting_receipt
    raw_text: Mapped[str] = mapped_column(Text)  # 原文
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calibration_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 结构化字段（重量、车牌等）
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PositionSegment(Base):
    """车辆定位片段：raw 时间为设备时钟，clock_offset_seconds 为设备快于真值的秒数。"""

    __tablename__ = "position_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clock_offset_seconds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- 批次关系册


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 PC-20261012-001
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"))
    vehicle_id: Mapped[str] = mapped_column(String(32))
    service_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 任务服务时间
    status: Mapped[str] = mapped_column(String(24), default="open")
    # open / adjudicated / published / under_review
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    contract: Mapped[Contract] = relationship()


class BatchLink(Base):
    """批次与证据的关联：candidate → confirmed / rejected；争用时 frozen。"""

    __tablename__ = "batch_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("source_events.id"))
    link_type: Mapped[str] = mapped_column(String(16))  # task / weighing / receipt / gate
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    # candidate / confirmed / frozen / rejected
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    event: Mapped[SourceEvent] = relationship()


# ---------------------------------------------------------------- 计量裁定与金额事实


class MeasurementFact(Base):
    """计量裁定结果：计费重量或可接受区间、待核冲突、排除原因。版本化追加。"""

    __tablename__ = "measurement_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(16))  # priority / interval
    status: Mapped[str] = mapped_column(String(24), default="issued")
    # issued / pending_conflict / under_review
    billing_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    acceptable_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    acceptable_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discrepancy: Mapped[list] = mapped_column(JSON, default=list)  # 差异佐证
    conflicts: Mapped[list] = mapped_column(JSON, default=list)  # 待核冲突
    exclusions: Mapped[list] = mapped_column(JSON, default=list)  # 排除原因
    calibration_refs: Mapped[list] = mapped_column(JSON, default=list)  # 校准引用
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AmountFact(Base):
    """金额事实：在冻结费率目录上编译生成；解释版本递增，历史不回写。"""

    __tablename__ = "amount_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    measurement_fact_id: Mapped[int] = mapped_column(ForeignKey("measurement_facts.id"))
    interpretation_version: Mapped[int] = mapped_column(Integer)
    catalog_id: Mapped[int] = mapped_column(ForeignKey("rate_catalogs.id"))
    catalog_version: Mapped[int] = mapped_column(Integer)
    weight_used: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # 已确认金额
    amount_low: Mapped[float | None] = mapped_column(Float, nullable=True)  # 区间下界金额
    amount_high: Mapped[float | None] = mapped_column(Float, nullable=True)  # 区间上界金额
    cross_tier: Mapped[bool] = mapped_column(Boolean, default=False)  # 上下界跨越费用阶梯
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued / superseded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Publication(Base):
    """差异发布：计费依据、未决范围、下游引用版本。发布记录不可变。"""

    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="published")
    # published / paused / pending
    billing_basis: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 计费依据
    pending_ranges: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 未决范围
    downstream_ref: Mapped[str] = mapped_column(String(64))  # 下游引用版本号
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DepartureCheck(Base):
    """签收后离场核查：比较支持到场的定位片段、时钟校正与容差。"""

    __tablename__ = "departure_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    result: Mapped[str] = mapped_column(String(16))  # consistent / contradiction / insufficient
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ============================================================
# 时空证据：终端注册、定位点、区域/走廊、锚点、证据状态、路径推断
# ============================================================


class TerminalRegistry(Base):
    """车载终端注册表：记录安装期（install_at ~ retire_at）与退役状态。

    同一车辆在时间重叠的安装期内出现两个有效终端时，归属冲突必须显式标注，
    任一终端的定位都不能被单方面采信。
    """

    __tablename__ = "terminal_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String(64), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    install_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / retired
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeoArea(Base):
    """地理区域：目标多边形、门岗缓冲区、道路走廊均以 GeoJSON 几何存储。

    kind: target（目标收运区域）/ gate（门岗点+缓冲半径）/ corridor（道路走廊）/ buffer（停留缓冲区）
    门岗通过必须命中 gate 区域；仅停留在 buffer 内不构成门岗通过。
    """

    __tablename__ = "geo_areas"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 如 AREA-FAC01、GATE-EAST
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    site_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    geometry: Mapped[dict] = mapped_column(JSON)  # GeoJSON：Point / Polygon / LineString+width
    buffer_radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackPoint(Base):
    """车辆定位点（不可变原始证据）：经纬度、精度半径、设备时钟与上报/补传标记。

    - accuracy_m：定位精度半径（米），值越大越不可靠；低精度边缘的区域交集只能待核。
    - delayed：长时间补传的点（event_time 远早于 received_at）。
    - seq：终端上报序列号；同序列哈希不同的两份载荷均保留，绝不覆盖。
    """

    __tablename__ = "track_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String(64), index=True)
    vehicle_id: Mapped[str] = mapped_column(String(32), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # 设备时钟事件时间
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lon: Mapped[float] = mapped_column(Float)
    lat: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float] = mapped_column(Float, default=10.0)
    delayed: Mapped[bool] = mapped_column(Boolean, default=False)
    clock_offset_seconds: Mapped[int] = mapped_column(Integer, default=0)  # 设备快于真值的秒数
    payload_hash: Mapped[str] = mapped_column(String(64))  # 单载荷内容哈希
    raw_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackPayloadConflict(Base):
    """补传/重传冲突：同一终端同一序列号出现不同哈希时，两份载荷都保留并登记。"""

    __tablename__ = "track_payload_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    terminal_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    point_id_a: Mapped[int] = mapped_column(ForeignKey("track_points.id"))
    point_id_b: Mapped[int] = mapped_column(ForeignKey("track_points.id"))
    hash_a: Mapped[str] = mapped_column(String(64))
    hash_b: Mapped[str] = mapped_column(String(64))
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RoadModel(Base):
    """道路模型（状态空间路径推断依赖）：节点与边，版本化并标注校准状态。

    nodes: [{"id": "n1", "lon": ..., "lat": ...}]
    edges: [{"from": "n1", "to": "n2", "speed_limit_kmh": 40, "corridor_id": "COR-1"}]
    路径推断结果必须标注引用的道路模型版本；模型缺边时不得补画缺失路段。
    """

    __tablename__ = "road_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    nodes: Mapped[list] = mapped_column(JSON)
    edges: Mapped[list] = mapped_column(JSON)
    calibrated: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SpacetimeEvidence(Base):
    """批次时空证据状态评估（版本化追加写）。

    status: trusted（可信）/ limited（受限）/ uncertifiable（不可认证）/ review（待核）
    rule_results：声明式规则逐项结果；anchors：门岗/地磅/签收锚点核对；
    结论只描述证据支持度，绝不把缓冲区停留认作门岗通过，也不补画缺失路段。
    """

    __tablename__ = "spacetime_evidences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)
    target_area_id: Mapped[str] = mapped_column(ForeignKey("geo_areas.id"))
    rule_results: Mapped[list] = mapped_column(JSON, default=list)
    anchors: Mapped[list] = mapped_column(JSON, default=list)
    point_ids: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PathInference(Base):
    """状态空间路径推断结果：保留全部候选路径，不收敛为单条、不补画缺失路段。

    candidates: [{"path": [node_id...], "probability": 0.xx, "enters_target": true/false,
                  "reasons": [...], "anchor_hits": [...]}]
    target_probability：经过目标区域的合并概率；road_model_version 标注依赖与校准。
    """

    __tablename__ = "path_inferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    target_area_id: Mapped[str] = mapped_column(ForeignKey("geo_areas.id"))
    road_model_version: Mapped[int] = mapped_column(Integer)
    target_probability: Mapped[float] = mapped_column(Float)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)  # 依赖、校准与不确定性声明
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
