"""定位终端注册与定位点摄入。

关键纪律：
- 终端有安装期；退役后上报的点在规则引擎中不可采信。
- 同车辆安装期重叠的两个有效终端 = 归属冲突，不单方面采信任一方。
- 定位点内容哈希内容寻址；同序列号相同哈希视为重复，不同哈希则两份载荷都保留并登记冲突。
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import TerminalRegistry, TrackPayloadConflict, TrackPoint


# ---------------------------------------------------------------- 终端注册


def register_terminal(db: Session, *, terminal_id, vehicle_id, install_at, note="") -> TerminalRegistry:
    reg = TerminalRegistry(
        terminal_id=terminal_id,
        vehicle_id=vehicle_id,
        install_at=install_at,
        retire_at=None,
        status="active",
        note=note,
    )
    db.add(reg)
    db.commit()
    db.refresh(reg)
    return reg


def retire_terminal(db: Session, *, terminal_id, vehicle_id, retire_at, note="") -> TerminalRegistry:
    reg = (
        db.query(TerminalRegistry)
        .filter_by(terminal_id=terminal_id, vehicle_id=vehicle_id, status="active")
        .order_by(TerminalRegistry.install_at.desc())
        .first()
    )
    if reg is None:
        raise HTTPException(404, f"终端 {terminal_id} 无有效安装记录，不能退役")
    reg.status = "retired"
    reg.retire_at = retire_at
    if note:
        reg.note = (reg.note + "；" + note) if reg.note else note
    db.commit()
    db.refresh(reg)
    return reg


def registry_entry_at(db: Session, terminal_id: str, at_time: datetime) -> TerminalRegistry | None:
    """终端在指定时刻所处的安装期记录。"""
    return (
        db.query(TerminalRegistry)
        .filter(
            TerminalRegistry.terminal_id == terminal_id,
            TerminalRegistry.install_at <= at_time,
            (TerminalRegistry.retire_at.is_(None)) | (TerminalRegistry.retire_at > at_time),
        )
        .order_by(TerminalRegistry.install_at.desc())
        .first()
    )


def active_terminals_at(db: Session, vehicle_id: str, at_time: datetime) -> list[TerminalRegistry]:
    """车辆在指定时刻全部处于有效安装期内的终端（可能多于一个 → 归属冲突）。"""
    return (
        db.query(TerminalRegistry)
        .filter(
            TerminalRegistry.vehicle_id == vehicle_id,
            TerminalRegistry.install_at <= at_time,
            (TerminalRegistry.retire_at.is_(None)) | (TerminalRegistry.retire_at > at_time),
        )
        .order_by(TerminalRegistry.install_at)
        .all()
    )


# ---------------------------------------------------------------- 定位点


def compute_point_hash(*, terminal_id, seq, event_time, lon, lat, accuracy_m, delayed,
                       clock_offset_seconds, raw_text) -> str:
    if event_time.tzinfo is not None:
        event_time = event_time.astimezone(timezone.utc)
    canonical = json.dumps(
        {
            "terminal_id": terminal_id,
            "seq": seq,
            "event_time": event_time.isoformat(),
            "lon": lon,
            "lat": lat,
            "accuracy_m": accuracy_m,
            "delayed": delayed,
            "clock_offset_seconds": clock_offset_seconds,
            "raw_text": raw_text,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def corrected_time(point: TrackPoint) -> datetime:
    """设备时钟校正后的真实事件时间（与 PositionSegment 约定一致：raw - offset）。"""
    return point.event_time - timedelta(seconds=point.clock_offset_seconds)


def ingest_point(db: Session, *, terminal_id, vehicle_id, seq, event_time, lon, lat,
                 accuracy_m=10.0, delayed=False, clock_offset_seconds=0, raw_text="",
                 received_at=None) -> tuple[TrackPoint, str]:
    """摄入一个定位点。

    返回 (point, outcome)，outcome ∈ {"new", "duplicate", "conflict"}：
    - duplicate：同终端同序列且哈希一致，幂等返回既有记录；
    - conflict：同终端同序列但哈希不同，两份载荷都保留并登记 TrackPayloadConflict。
    """
    payload_hash = compute_point_hash(
        terminal_id=terminal_id, seq=seq, event_time=event_time, lon=lon, lat=lat,
        accuracy_m=accuracy_m, delayed=delayed, clock_offset_seconds=clock_offset_seconds,
        raw_text=raw_text,
    )
    existing = (
        db.query(TrackPoint)
        .filter_by(terminal_id=terminal_id, seq=seq)
        .order_by(TrackPoint.id)
        .all()
    )
    same = next((p for p in existing if p.payload_hash == payload_hash), None)
    if same is not None:
        return same, "duplicate"

    point = TrackPoint(
        terminal_id=terminal_id,
        vehicle_id=vehicle_id,
        seq=seq,
        event_time=event_time,
        received_at=received_at or datetime.now(timezone.utc),
        lon=lon,
        lat=lat,
        accuracy_m=accuracy_m,
        delayed=delayed,
        clock_offset_seconds=clock_offset_seconds,
        payload_hash=payload_hash,
        raw_text=raw_text,
    )
    db.add(point)
    db.flush()
    if existing:  # 同序列不同哈希：保留两份载荷，登记冲突
        for other in existing:
            db.add(
                TrackPayloadConflict(
                    terminal_id=terminal_id,
                    seq=seq,
                    point_id_a=other.id,
                    point_id_b=point.id,
                    hash_a=other.payload_hash,
                    hash_b=payload_hash,
                    note="同序列号补传/重传载荷哈希不同，两份载荷均保留，不得覆盖",
                )
            )
        db.commit()
        db.refresh(point)
        return point, "conflict"

    db.commit()
    db.refresh(point)
    return point, "new"


def points_for_batch(db: Session, batch, window_minutes: int = 180) -> list[TrackPoint]:
    """取批次服务时间窗口内、校正时间排序后的车辆定位点（含全部冲突载荷）。"""
    lo = batch.service_time - timedelta(minutes=window_minutes)
    hi = batch.service_time + timedelta(minutes=window_minutes)
    rows = db.query(TrackPoint).filter_by(vehicle_id=batch.vehicle_id).all()
    rows = [p for p in rows if lo <= corrected_time(p) <= hi]
    return sorted(rows, key=lambda p: (corrected_time(p), p.id))


def terminal_to_dict(t: TerminalRegistry) -> dict:
    return {
        "id": t.id,
        "terminal_id": t.terminal_id,
        "vehicle_id": t.vehicle_id,
        "install_at": t.install_at.isoformat(),
        "retire_at": t.retire_at.isoformat() if t.retire_at else None,
        "status": t.status,
        "note": t.note,
    }


def point_to_dict(p: TrackPoint) -> dict:
    return {
        "id": p.id,
        "terminal_id": p.terminal_id,
        "vehicle_id": p.vehicle_id,
        "seq": p.seq,
        "event_time": p.event_time.isoformat(),
        "corrected_time": corrected_time(p).isoformat(),
        "received_at": p.received_at.isoformat(),
        "lon": p.lon,
        "lat": p.lat,
        "accuracy_m": p.accuracy_m,
        "delayed": p.delayed,
        "clock_offset_seconds": p.clock_offset_seconds,
        "payload_hash": p.payload_hash,
        "raw_text": p.raw_text,
    }
