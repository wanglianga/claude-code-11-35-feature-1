"""时空证据 API：状态输出、逐项规则下钻、候选路径与基础数据登记。

读接口对全部登录角色开放；登记/评估/推断类写操作仅 analyst。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as OrmSession

from ..db import get_db
from ..models import (
    Batch,
    GeoArea,
    PathInference,
    RoadModel,
    SpacetimeEvidence,
    TerminalRegistry,
    TrackPayloadConflict,
    TrackPoint,
    User,
)
from ..schemas import (
    GeoAreaIn,
    RoadModelIn,
    TerminalRegisterIn,
    TerminalRetireIn,
    TrackPointIn,
)
from ..security import get_current_user, require_analyst
from ..services import path_inference, spacetime, track

router = APIRouter(prefix="/api", tags=["spacetime"])


# ============================================================ 基础登记（写）


@router.post("/terminals/register", status_code=201)
def register_terminal(body: TerminalRegisterIn, db: OrmSession = Depends(get_db),
                      _: User = Depends(require_analyst)):
    reg = track.register_terminal(db, **body.model_dump())
    return track.terminal_to_dict(reg)


@router.post("/terminals/retire", status_code=201)
def retire_terminal(body: TerminalRetireIn, db: OrmSession = Depends(get_db),
                    _: User = Depends(require_analyst)):
    reg = track.retire_terminal(db, **body.model_dump())
    return track.terminal_to_dict(reg)


@router.post("/track-points", status_code=201)
def ingest_track_point(body: TrackPointIn, db: OrmSession = Depends(get_db),
                       _: User = Depends(require_analyst)):
    point, outcome = track.ingest_point(db, **body.model_dump())
    return {**track.point_to_dict(point), "outcome": outcome}


@router.post("/geo-areas", status_code=201)
def create_geo_area(body: GeoAreaIn, db: OrmSession = Depends(get_db),
                    _: User = Depends(require_analyst)):
    if db.get(GeoArea, body.id):
        raise HTTPException(409, "区域 ID 已存在")
    area = GeoArea(**body.model_dump())
    db.add(area)
    db.commit()
    db.refresh(area)
    return geo_area_to_dict(area)


@router.post("/road-models", status_code=201)
def create_road_model(body: RoadModelIn, db: OrmSession = Depends(get_db),
                      _: User = Depends(require_analyst)):
    latest = db.query(RoadModel).order_by(RoadModel.version.desc()).first()
    model = RoadModel(version=(latest.version + 1) if latest else 1, **body.model_dump())
    db.add(model)
    db.commit()
    db.refresh(model)
    return {"id": model.id, "version": model.version, "calibrated": model.calibrated,
            "nodes": len(model.nodes), "edges": len(model.edges)}


# ============================================================ 基础登记（读）


@router.get("/terminals")
def list_terminals(vehicle_id: str | None = None, db: OrmSession = Depends(get_db),
                   _: User = Depends(get_current_user)):
    q = db.query(TerminalRegistry)
    if vehicle_id:
        q = q.filter_by(vehicle_id=vehicle_id)
    return [track.terminal_to_dict(t) for t in q.order_by(
        TerminalRegistry.vehicle_id, TerminalRegistry.install_at).all()]


@router.get("/track-points")
def list_track_points(vehicle_id: str | None = None, terminal_id: str | None = None,
                      db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    q = db.query(TrackPoint)
    if vehicle_id:
        q = q.filter_by(vehicle_id=vehicle_id)
    if terminal_id:
        q = q.filter_by(terminal_id=terminal_id)
    rows = q.order_by(TrackPoint.event_time).limit(1000).all()
    return [track.point_to_dict(p) for p in rows]


@router.get("/track-conflicts")
def list_track_conflicts(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        {"id": c.id, "terminal_id": c.terminal_id, "seq": c.seq,
         "point_id_a": c.point_id_a, "point_id_b": c.point_id_b,
         "hash_a": c.hash_a, "hash_b": c.hash_b, "note": c.note,
         "created_at": c.created_at.isoformat()}
        for c in db.query(TrackPayloadConflict).order_by(TrackPayloadConflict.id).all()
    ]


def geo_area_to_dict(a: GeoArea) -> dict:
    return {
        "id": a.id, "name": a.name, "kind": a.kind, "site_id": a.site_id,
        "geometry": a.geometry, "buffer_radius_m": a.buffer_radius_m,
        "created_at": a.created_at.isoformat(),
    }


@router.get("/geo-areas")
def list_geo_areas(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [geo_area_to_dict(a) for a in db.query(GeoArea).order_by(GeoArea.id).all()]


@router.get("/road-models")
def list_road_models(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    return [
        {"id": m.id, "version": m.version, "calibrated": m.calibrated, "note": m.note,
         "nodes": len(m.nodes), "edges": len(m.edges), "created_at": m.created_at.isoformat()}
        for m in db.query(RoadModel).order_by(RoadModel.version).all()
    ]


# ============================================================ 评估与推断（写）


@router.post("/batches/{batch_id}/spacetime/evaluate", status_code=201)
def evaluate(batch_id: str, body: dict, db: OrmSession = Depends(get_db),
             _: User = Depends(require_analyst)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    target_area_id = body.get("target_area_id")
    if not target_area_id:
        raise HTTPException(422, "缺少 target_area_id")
    return spacetime.to_dict(spacetime.evaluate_spacetime(db, batch, target_area_id))


@router.post("/batches/{batch_id}/spacetime/infer", status_code=201)
def infer(batch_id: str, body: dict, db: OrmSession = Depends(get_db),
          _: User = Depends(require_analyst)):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    target_area_id = body.get("target_area_id")
    if not target_area_id:
        raise HTTPException(422, "缺少 target_area_id")
    return path_inference.to_dict(path_inference.infer_paths(db, batch, target_area_id))


# ============================================================ 状态总览与下钻（读）


@router.get("/spacetime/statuses")
def spacetime_statuses(db: OrmSession = Depends(get_db), _: User = Depends(get_current_user)):
    """每个批次最新一次时空证据状态 + 最新路径推断的入域概率。"""
    out = []
    for b in db.query(Batch).order_by(Batch.service_time.desc()).all():
        ev = (db.query(SpacetimeEvidence).filter_by(batch_id=b.id)
              .order_by(SpacetimeEvidence.version.desc()).first())
        inf = (db.query(PathInference).filter_by(batch_id=b.id)
               .order_by(PathInference.version.desc()).first())
        out.append({
            "batch_id": b.id,
            "vehicle_id": b.vehicle_id,
            "service_time": b.service_time.isoformat(),
            "status": ev.status if ev else None,
            "evaluation_version": ev.version if ev else None,
            "target_area_id": ev.target_area_id if ev else None,
            "target_probability": inf.target_probability if inf else None,
            "candidate_count": len(inf.candidates) if inf else None,
            "note": ev.note if ev else "尚未评估",
        })
    return out


@router.get("/spacetime/batches/{batch_id}")
def spacetime_drilldown(batch_id: str, db: OrmSession = Depends(get_db),
                        _: User = Depends(get_current_user)):
    """下钻一站视图：定位点（含有效性标注）、终端安装期、精度、走廊、门岗、地磅、签收锚点。"""
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, "批次不存在")
    points = track.points_for_batch(db, batch)
    point_rows = []
    for p in points:
        entry = track.registry_entry_at(db, p.terminal_id, track.corrected_time(p))
        validity = "valid"
        if entry is None or entry.vehicle_id != batch.vehicle_id:
            validity = "no_install"
        elif entry.status == "retired" or (entry.retire_at and entry.retire_at <= track.corrected_time(p)):
            validity = "retired"
        point_rows.append({
            **track.point_to_dict(p),
            "terminal_validity": validity,
            "install_window": track.terminal_to_dict(entry) if entry else None,
        })

    evals = (db.query(SpacetimeEvidence).filter_by(batch_id=batch_id)
             .order_by(SpacetimeEvidence.version.desc()).all())
    infs = (db.query(PathInference).filter_by(batch_id=batch_id)
            .order_by(PathInference.version.desc()).all())
    conflicts = db.query(TrackPayloadConflict).filter(
        TrackPayloadConflict.terminal_id.in_({p.terminal_id for p in points} or [""])
    ).all()
    return {
        "batch_id": batch_id,
        "vehicle_id": batch.vehicle_id,
        "service_time": batch.service_time.isoformat(),
        "terminals": [
            track.terminal_to_dict(t)
            for t in db.query(TerminalRegistry).filter_by(vehicle_id=batch.vehicle_id)
            .order_by(TerminalRegistry.install_at).all()
        ],
        "points": point_rows,
        "payload_conflicts": [
            {"id": c.id, "terminal_id": c.terminal_id, "seq": c.seq,
             "point_id_a": c.point_id_a, "point_id_b": c.point_id_b, "note": c.note}
            for c in conflicts
        ],
        "geo_areas": [geo_area_to_dict(a) for a in db.query(GeoArea).order_by(GeoArea.id).all()],
        "evaluations": [spacetime.to_dict(e) for e in evals],
        "inferences": [path_inference.to_dict(i) for i in infs],
        "thresholds": spacetime.RULE_THRESHOLDS,
    }
