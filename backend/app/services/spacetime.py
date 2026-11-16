"""声明式时空证据规则引擎。

逐项检查、理由透明，输出四态：
- trusted 可信：全部规则通过，锚点互证，精度充分，单终端有效，区域交集确定。
- limited 受限：证据成立但存在约束（采样空洞、补传、通信中断导致速度未知等）。
- uncertifiable 不可认证：进入目标区域的关键证据来自退役/无安装记录终端，或同序列载荷冲突无法辨伪。
- review 待核：低精度边缘、门岗/区域交集不确定、双走廊归属不明等，需人工复核。

硬纪律：
- 不补画采样空洞之间的缺失路段；
- 缓冲区停留不认定为门岗通过；
- 通信中断后的跳速保持未知，不推断超速也不推断矛盾；
- 签收后的正常离场不认定为时序冲突；
- 同序列哈希不同保留两份载荷并判冲突。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Batch,
    BatchLink,
    GeoArea,
    SourceEvent,
    SpacetimeEvidence,
    TerminalRegistry,
    TrackPayloadConflict,
    TrackPoint,
)
from . import geo, track

# 声明式阈值（集中声明，理由中直接引用，保证规则透明）
RULE_THRESHOLDS = {
    "sampling_gap_seconds": 120,       # 相邻定位超过 2 分钟视为采样空洞
    "max_speed_kmh": 90.0,             # 车辆合理速度上限
    "low_accuracy_m": 30.0,            # 精度半径大于 30 米视为低精度
    "backfill_seconds": 1800,          # 接收滞后 30 分钟以上视为长时间补传
    "anchor_match_minutes": 15,        # 锚点与定位互证的时间窗
}


def _next_version(db: Session, batch_id: str) -> int:
    latest = (
        db.query(SpacetimeEvidence)
        .filter_by(batch_id=batch_id)
        .order_by(SpacetimeEvidence.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


def _rule(rule_id, name, status, message, blocking=False, **details):
    return {
        "rule_id": rule_id,
        "rule_name": name,
        "status": status,
        "blocking": blocking,
        "message": message,
        "details": details,
    }


def _linked_events(db: Session, batch: Batch) -> dict[str, SourceEvent]:
    links = db.query(BatchLink).filter_by(batch_id=batch.id, status="confirmed").all()
    out: dict[str, SourceEvent] = {}
    for l in links:
        out.setdefault(l.link_type, db.get(SourceEvent, l.event_id))
    return out


# ============================================================ 各声明式规则


def rule_terminal_validity(db: Session, batch: Batch, points: list[TrackPoint]):
    """R1 终端有效性：安装期、退役、同车双终端归属冲突。"""
    seen_terminals = {}
    invalid, retired = [], []
    for p in points:
        seen_terminals.setdefault(p.terminal_id, []).append(p)
        corrected = track.corrected_time(p)
        # 先查该终端在观测时刻之前的任意安装记录（含已退役），以区分“从未安装”与“退役后上报”
        any_entry = (
            db.query(TerminalRegistry)
            .filter(
                TerminalRegistry.terminal_id == p.terminal_id,
                TerminalRegistry.install_at <= corrected,
            )
            .order_by(TerminalRegistry.install_at.desc())
            .first()
        )
        entry = track.registry_entry_at(db, p.terminal_id, corrected)
        if any_entry is None:
            invalid.append(
                {"point_id": p.id, "terminal_id": p.terminal_id, "seq": p.seq,
                 "reason": "终端在该时刻无任何安装期记录（未注册终端上报）"}
            )
        elif entry is None or entry.status == "retired" or (
            entry.retire_at and entry.retire_at <= corrected
        ):
            retired.append(
                {"point_id": p.id, "terminal_id": p.terminal_id, "seq": p.seq,
                 "retire_at": (any_entry.retire_at.isoformat() if any_entry.retire_at else None),
                 "reason": "退役终端上报，定位不可采信"}
            )
        elif entry.vehicle_id != batch.vehicle_id:
            invalid.append(
                {"point_id": p.id, "terminal_id": p.terminal_id,
                 "reason": f"终端安装车辆 {entry.vehicle_id} 与批次车辆 {batch.vehicle_id} 不一致"}
            )

    # 同车辆在窗口内同时有效的终端（旧终端未退役即装新终端 → 归属冲突）
    window_times = [track.corrected_time(p) for p in points]
    conflict_terminals: set[str] = set()
    if window_times:
        for t in window_times[:: max(1, len(window_times) // 8 or 1)]:
            actives = track.active_terminals_at(db, batch.vehicle_id, t)
            if len({a.terminal_id for a in actives}) > 1:
                conflict_terminals.update(a.terminal_id for a in actives)

    install_windows = [
        track.terminal_to_dict(a)
        for a in db.query(TerminalRegistry)
        .filter(
            TerminalRegistry.vehicle_id == batch.vehicle_id,
            TerminalRegistry.terminal_id.in_(tuple(seen_terminals) or ("",)),
        )
        .order_by(TerminalRegistry.install_at)
        .all()
    ]

    if conflict_terminals:
        return _rule(
            "R1", "终端有效性与归属", "fail",
            f"车辆同时存在两个有效终端 {sorted(conflict_terminals)}，旧终端与新终端安装期重叠，标注归属冲突，任一方定位不得单方面采信",
            blocking=True, conflict_terminals=sorted(conflict_terminals),
            invalid=invalid, retired=retired, install_windows=install_windows,
        )
    if retired:
        return _rule(
            "R1", "终端有效性与归属", "fail",
            f"{len(retired)} 个定位点来自退役终端，不能用于证明进入目标区域",
            blocking=True, invalid=invalid, retired=retired, install_windows=install_windows,
        )
    if invalid:
        return _rule(
            "R1", "终端有效性与归属", "fail",
            f"{len(invalid)} 个定位点缺乏有效安装期归属",
            blocking=True, invalid=invalid, retired=retired, install_windows=install_windows,
        )
    return _rule("R1", "终端有效性与归属", "pass",
                 f"{len(seen_terminals)} 个终端均在有效安装期内，车辆归属唯一",
                 install_windows=install_windows)


def rule_sampling_gaps(points: list[TrackPoint], valid_ids: set[int]):
    """R2 采样空洞：只陈述空洞，绝不补画缺失路段。"""
    gaps = []
    by_terminal: dict[str, list[TrackPoint]] = {}
    for p in points:
        if p.id in valid_ids:
            by_terminal.setdefault(p.terminal_id, []).append(p)
    for tid, rows in by_terminal.items():
        rows.sort(key=lambda r: track.corrected_time(r))
        for a, b in zip(rows, rows[1:]):
            delta = (track.corrected_time(b) - track.corrected_time(a)).total_seconds()
            if delta > RULE_THRESHOLDS["sampling_gap_seconds"]:
                gaps.append({
                    "terminal_id": tid,
                    "from_point_id": a.id, "to_point_id": b.id,
                    "from_time": track.corrected_time(a).isoformat(),
                    "to_time": track.corrected_time(b).isoformat(),
                    "gap_seconds": round(delta, 1),
                    "note": "空洞期间不补画任何路段，车辆真实位置保持未知",
                })
    if not gaps:
        return _rule("R2", "采样空洞", "pass",
                     f"相邻定位间隔均不超过 {RULE_THRESHOLDS['sampling_gap_seconds']} 秒")
    worst = max(g["gap_seconds"] for g in gaps)
    return _rule("R2", "采样空洞", "warn",
                 f"发现 {len(gaps)} 处采样空洞（最长 {worst:.0f} 秒），缺失路段保持未知，不做插值补画",
                 gaps=gaps)


def rule_speed(points: list[TrackPoint], valid_ids: set[int]):
    """R3 速度检查：通信中断/补传跨越的区间速度保持未知，不用跳速反推矛盾。"""
    unknown, excessive, normal = [], [], 0
    by_terminal: dict[str, list[TrackPoint]] = {}
    for p in points:
        if p.id in valid_ids:
            by_terminal.setdefault(p.terminal_id, []).append(p)
    for tid, rows in by_terminal.items():
        rows.sort(key=lambda r: track.corrected_time(r))
        for a, b in zip(rows, rows[1:]):
            dt_h = (track.corrected_time(b) - track.corrected_time(a)).total_seconds() / 3600.0
            if dt_h <= 0:
                continue
            dist_m = geo.haversine_m(a.lon, a.lat, b.lon, b.lat)
            gap_s = dt_h * 3600.0
            uncertain_dist = dist_m <= (a.accuracy_m + b.accuracy_m)
            if (gap_s > RULE_THRESHOLDS["sampling_gap_seconds"] or a.delayed or b.delayed):
                unknown.append({
                    "from_point_id": a.id, "to_point_id": b.id,
                    "reason": "通信中断/长时间补传后的跳速保持未知，不推断超速也不推断矛盾",
                    "gap_seconds": round(gap_s, 1),
                })
                continue
            speed = dist_m / 1000.0 / dt_h
            if uncertain_dist:
                unknown.append({
                    "from_point_id": a.id, "to_point_id": b.id,
                    "reason": f"两点距离落在双方精度圆内（{dist_m:.1f}m），速度不可靠，记为未知",
                    "speed_kmh_estimate": round(speed, 1),
                })
            elif speed > RULE_THRESHOLDS["max_speed_kmh"]:
                excessive.append({
                    "from_point_id": a.id, "to_point_id": b.id,
                    "speed_kmh": round(speed, 1),
                    "limit_kmh": RULE_THRESHOLDS["max_speed_kmh"],
                })
            else:
                normal += 1
    if excessive:
        return _rule("R3", "速度连续性", "fail",
                     f"{len(excessive)} 个相邻区间速度超过 {RULE_THRESHOLDS['max_speed_kmh']} km/h，存在不可解释跳速",
                     blocking=True, excessive=excessive, unknown=unknown, normal_segments=normal)
    if unknown:
        return _rule("R3", "速度连续性", "warn",
                     f"{normal} 个区间速度正常；{len(unknown)} 个区间速度保持未知（通信中断/补传/精度不足）",
                     excessive=excessive, unknown=unknown, normal_segments=normal)
    return _rule("R3", "速度连续性", "pass",
                 f"{normal} 个相邻区间速度均在 {RULE_THRESHOLDS['max_speed_kmh']} km/h 以内",
                 normal_segments=normal)


def rule_area_and_gate(db: Session, batch: Batch, points: list[TrackPoint],
                       valid_ids: set[int], target: GeoArea, corridors: list[GeoArea]):
    """R4 区域交集 + R5 门岗/缓冲区 + 走廊归属。低精度边缘一律待核。"""
    per_point, inside_ids, edge_ids = [], [], []
    corridor_hits: dict[str, list[int]] = {c.id: [] for c in corridors}
    for p in points:
        if p.id not in valid_ids:
            continue
        inter = geo.area_intersection(target, p.lon, p.lat, p.accuracy_m)
        low_acc = p.accuracy_m >= RULE_THRESHOLDS["low_accuracy_m"]
        row = {
            "point_id": p.id, "terminal_id": p.terminal_id, "seq": p.seq,
            "corrected_time": track.corrected_time(p).isoformat(),
            "accuracy_m": p.accuracy_m, "low_accuracy": low_acc, **inter,
        }
        per_point.append(row)
        if inter["relation"] == "inside":
            inside_ids.append(p.id)
        elif inter["relation"] == "edge_uncertain":
            edge_ids.append(p.id)
        for c in corridors:
            ci = geo.area_intersection(c, p.lon, p.lat, p.accuracy_m)
            if ci["relation"] == "inside":
                corridor_hits[c.id].append(p.id)

    # 门岗：必须命中 kind=gate 区域；仅落在 buffer 区域的停留不算通过
    gate_areas = db.query(GeoArea).filter_by(kind="gate").all()
    buffer_areas = db.query(GeoArea).filter_by(kind="buffer").all()
    gate_hits, buffer_only = [], []
    for p in points:
        if p.id not in valid_ids:
            continue
        for g in gate_areas:
            gi = geo.area_intersection(g, p.lon, p.lat, p.accuracy_m)
            if gi["relation"] in ("inside", "edge_uncertain"):
                gate_hits.append({"point_id": p.id, "gate_id": g.id, "relation": gi["relation"],
                                  "accuracy_m": p.accuracy_m})
        in_buffer = any(
            geo.area_intersection(buff, p.lon, p.lat, p.accuracy_m)["relation"] == "inside"
            for buff in buffer_areas
        )
        in_gate = any(h["point_id"] == p.id for h in gate_hits)
        if in_buffer and not in_gate:
            buffer_only.append({"point_id": p.id, "corrected_time": track.corrected_time(p).isoformat()})

    used_corridors = sorted([cid for cid, ids in corridor_hits.items() if ids])
    area_rule = _rule(
        "R4", "目标区域交集",
        "pass" if inside_ids else ("review" if edge_ids else "fail"),
        (f"{len(inside_ids)} 个有效点确定位于目标区域内" if inside_ids else
         f"无确定入域点；{len(edge_ids)} 个点处于低精度边缘，交集待核" if edge_ids else
         "无任何有效点与目标区域相交，不能证明车辆进入目标区域"),
        blocking=not inside_ids and not edge_ids,
        per_point=per_point, inside_point_ids=inside_ids, edge_point_ids=edge_ids,
        target_area_id=target.id,
    )
    gate_status = "pass" if any(h["relation"] == "inside" for h in gate_hits) else (
        "review" if gate_hits else "review")
    gate_rule = _rule(
        "R5", "门岗通过与缓冲区", gate_status,
        (f"门岗区域命中 {len(gate_hits)} 点；缓冲区停留 {len(buffer_only)} 点，缓冲区停留不构成门岗通过"
         + ("，门岗命中含精度边缘点，需待核" if all(h["relation"] != "inside" for h in gate_hits) and gate_hits else "")),
        gate_hits=gate_hits, buffer_only_dwell=buffer_only,
    )
    corridor_rule = _rule(
        "R6", "道路走廊归属",
        "review" if len(used_corridors) > 1 else ("pass" if used_corridors else "warn"),
        (f"轨迹同时落在两条走廊 {used_corridors}，入域路径归属不明，保留双走廊候选" if len(used_corridors) > 1
         else f"轨迹归属走廊 {used_corridors}" if used_corridors else "轨迹未命中任何已建模走廊"),
        corridor_hits={cid: ids for cid, ids in corridor_hits.items() if ids},
    )
    return area_rule, gate_rule, corridor_rule


def rule_backfill_and_conflict(db: Session, points: list[TrackPoint]):
    """R7 补传与载荷冲突：长滞后标注；同序列哈希不同保留两份并判不可认证。"""
    delayed = [
        {"point_id": p.id, "terminal_id": p.terminal_id, "seq": p.seq,
         "lag_seconds": round((p.received_at - p.event_time).total_seconds(), 1)}
        for p in points if p.delayed
    ]
    tids = {p.terminal_id for p in points}
    seqs = {p.seq for p in points}
    conflicts = (
        db.query(TrackPayloadConflict)
        .filter(TrackPayloadConflict.terminal_id.in_(tids or [""]))
        .filter(TrackPayloadConflict.seq.in_(seqs or [-1]))
        .all()
    )
    conflict_rows = [
        {"id": c.id, "terminal_id": c.terminal_id, "seq": c.seq,
         "point_id_a": c.point_id_a, "point_id_b": c.point_id_b,
         "hash_a": c.hash_a[:16], "hash_b": c.hash_b[:16], "note": c.note}
        for c in conflicts
    ]
    if conflict_rows:
        return _rule("R7", "补传与载荷完整性", "fail",
                     f"存在 {len(conflict_rows)} 组同序列号不同哈希载荷，两份均保留；真伪不可认证",
                     blocking=True, delayed=delayed, payload_conflicts=conflict_rows)
    if delayed:
        return _rule("R7", "补传与载荷完整性", "review",
                     f"{len(delayed)} 个点为长时间补传（滞后超 {RULE_THRESHOLDS['backfill_seconds']} 秒），事件时间以设备时钟为准并单独标注",
                     delayed=delayed, payload_conflicts=[])
    return _rule("R7", "补传与载荷完整性", "pass", "无补传滞后，载荷哈希无冲突",
                 delayed=[], payload_conflicts=[])


def rule_anchors_and_sequence(db: Session, batch: Batch, points: list[TrackPoint],
                              valid_ids: set[int]):
    """R8 锚点互证（门岗/地磅/签收）与时序；签收后的正常离场不算时序冲突。"""
    linked = _linked_events(db, batch)
    anchors = []
    site_areas = [a for a in db.query(GeoArea).all() if a.site_id]
    for ltype, label in [("gate", "门岗"), ("weighing", "地磅"), ("receipt", "签收")]:
        ev = linked.get(ltype)
        if ev is None:
            anchors.append({"anchor": label, "present": False, "message": "批次无已确认的该类锚点证据"})
            continue
        areas = [a for a in site_areas if a.site_id == ev.site_id]
        support, edge = [], []
        for area in areas:
            for p in points:
                if p.id not in valid_ids:
                    continue
                inter = geo.area_intersection(area, p.lon, p.lat, p.accuracy_m)
                if inter["relation"] == "inside" and abs(
                    (track.corrected_time(p) - ev.event_time).total_seconds()
                ) <= RULE_THRESHOLDS["anchor_match_minutes"] * 60:
                    support.append(p.id)
                elif inter["relation"] == "edge_uncertain":
                    edge.append(p.id)
        anchors.append({
            "anchor": label, "present": True, "event_id": ev.id,
            "event_time": ev.event_time.isoformat(), "site_id": ev.site_id,
            "device_id": ev.device_id,
            "area_ids": [a.id for a in areas],
            "track_support_point_ids": sorted(set(support)),
            "edge_point_ids": sorted(set(edge)),
            "match": "confirmed" if support else ("edge_review" if edge else "unsupported"),
        })

    # 时序：门岗 → 地磅 → 签收（到场物理事件必须先后有序）；
    # 收运任务是计划单据而非物理到场事件，只作时间窗参照，不参与时序冲突判定；
    # 签收之后的定位属于正常离场，白名单不计冲突。
    order = [("gate", "门岗"), ("weighing", "地磅"), ("receipt", "签收")]
    times = [(label, linked[k].event_time) for k, label in order if linked.get(k)]
    seq_violations = []
    for (la, ta), (lb, tb) in zip(times, times[1:]):
        if ta > tb:
            seq_violations.append(f"{la} 晚于 {lb}")
    receipt = linked.get("receipt")
    post_receipt = []
    if receipt:
        post_receipt = [
            p.id for p in points
            if p.id in valid_ids and track.corrected_time(p) > receipt.event_time
        ]
    seq_status = "fail" if seq_violations else "pass"
    seq_rule = _rule(
        "R8", "锚点时序与签收后离场", seq_status,
        (f"锚点时序存在冲突：{seq_violations}" if seq_violations
         else f"锚点时序正常；签收后定位 {len(post_receipt)} 点属正常离场，不认定时序冲突"),
        blocking=bool(seq_violations), post_receipt_point_ids=post_receipt,
    )
    return anchors, seq_rule


# ============================================================ 评估入口


def evaluate_spacetime(db: Session, batch: Batch, target_area_id: str) -> SpacetimeEvidence:
    target = db.get(GeoArea, target_area_id)
    if target is None or target.kind != "target":
        raise HTTPException(404, "目标区域不存在或 kind 不是 target")
    corridors = db.query(GeoArea).filter_by(kind="corridor").all()
    points = track.points_for_batch(db, batch)
    if not points:
        raise HTTPException(409, "评估窗口内无该车辆任何定位点，无法形成时空证据")

    r1 = rule_terminal_validity(db, batch, points)
    # 有效终端点：R1 未判失效时全部有效；否则剔除退役/无安装点
    bad_ids = {x["point_id"] for x in (r1["details"].get("invalid", []) + r1["details"].get("retired", []))}
    valid_ids = {p.id for p in points} - bad_ids

    r2 = rule_sampling_gaps(points, valid_ids)
    r3 = rule_speed(points, valid_ids)
    r4, r5, r6 = rule_area_and_gate(db, batch, points, valid_ids, target, corridors)
    r7 = rule_backfill_and_conflict(db, points)
    anchors, r8 = rule_anchors_and_sequence(db, batch, points, valid_ids)
    rules = [r1, r2, r3, r4, r5, r6, r7, r8]

    # 状态聚合：阻断性失败 → 不可认证；其次待核；再次受限；全过 → 可信
    if any(r["status"] == "fail" and r["blocking"] for r in rules):
        status = "uncertifiable"
    elif any(r["status"] in ("fail", "review") for r in rules):
        status = "review"
    elif any(r["status"] == "warn" for r in rules):
        status = "limited"
    else:
        status = "trusted"

    # 锚点互证可把“仅轨迹待核”上调为受限而非停留在待核？——不允许：规则透明，状态不被锚点覆盖，
    # 锚点只在下钻中独立展示，避免用业务单据把低精度轨迹“洗成”可信。
    note = {
        "trusted": "全部声明式规则通过，轨迹与门岗/地磅/签收锚点互证，时空证据可信",
        "limited": "证据链成立但存在明确约束（见 warn 规则），结论受限可用",
        "uncertifiable": "关键轨迹证据不可认证（退役终端/载荷冲突/无入域证据），不得据此确认收运过程",
        "review": "低精度边缘或走廊/门岗归属待核，需人工复核后才能采信",
    }[status]

    ev = SpacetimeEvidence(
        batch_id=batch.id,
        version=_next_version(db, batch.id),
        status=status,
        target_area_id=target_area_id,
        rule_results=rules,
        anchors=anchors,
        point_ids=[p.id for p in points],
        note=f"{note}。阈值：{RULE_THRESHOLDS}",
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def to_dict(e: SpacetimeEvidence) -> dict:
    return {
        "id": e.id,
        "batch_id": e.batch_id,
        "version": e.version,
        "status": e.status,
        "target_area_id": e.target_area_id,
        "rule_results": e.rule_results,
        "anchors": e.anchors,
        "point_ids": e.point_ids,
        "note": e.note,
        "created_at": e.created_at.isoformat(),
    }
