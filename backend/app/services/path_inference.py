"""状态空间路径推断。

把道路模型、定位精度、时间连续性与门岗/地磅/签收锚点合并为一个 HMM 式状态空间：
- 发射概率：定位点到道路节点的距离相对精度半径的高斯似然；
- 转移概率：相邻观测间沿真实道路边的最短路所需速度是否合理，缺边/超速不做补画；
- 保留 top-K 候选路径并归一化概率，输出经过目标区域的合并概率。

稀疏点下尤其有用：观测越少候选越多，全部保留，绝不收敛成单条“看起来完整”的路线。
道路模型未校准、采样空洞、弱吸附点都会在 assumptions / reasons 中显式声明。
"""

import math
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    Batch,
    GeoArea,
    PathInference,
    RoadModel,
    TrackPayloadConflict,
    TrackPoint,
)
from . import geo, track

TOP_K = 6
MAX_SPEED_KMH = 90.0
SPEED_PENALTY_SCALE = 25.0      # 速度偏离的软惩罚尺度
SNAP_MULTIPLIER = 3.0           # 吸附搜索半径 = 精度半径 × 3
MIN_SIGMA_M = 5.0


def _next_version(db: Session, batch_id: str) -> int:
    latest = (
        db.query(PathInference)
        .filter_by(batch_id=batch_id)
        .order_by(PathInference.version.desc())
        .first()
    )
    return (latest.version + 1) if latest else 1


class RoadGraph:
    def __init__(self, model: RoadModel):
        self.model = model
        self.coords = {n["id"]: (float(n["lon"]), float(n["lat"])) for n in model.nodes}
        self.adj = defaultdict(list)  # node -> [(neighbor, length_m, edge)]
        self.edge_map = {}
        for e in model.edges:
            a, b = e["from"], e["to"]
            length = geo.haversine_m(*self.coords[a], *self.coords[b])
            self.edge_map[(a, b)] = e
            self.adj[a].append((b, length, e))
            self.adj[b].append((a, length, e))  # 道路默认双向通行

    def shortest(self, src: str, dst: str):
        """Dijkstra 返回 (距离米, 节点序列)；不可达返回 (None, None)，不虚构边。"""
        if src == dst:
            return 0.0, [src]
        import heapq

        dist = {src: 0.0}
        prev = {}
        heap = [(0.0, src)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, float("inf")):
                continue
            if u == dst:
                break
            for v, length, _ in self.adj[u]:
                nd = d + length
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        if dst not in dist:
            return None, None
        path = [dst]
        while path[-1] != src:
            path.append(prev[path[-1]])
        return dist[dst], path[::-1]


def _emission_candidates(point: TrackPoint, graph: RoadGraph):
    """定位点在精度半径内的候选节点 + 对数似然。"""
    sigma = max(point.accuracy_m, MIN_SIGMA_M)
    out = []
    for node_id, (lon, lat) in graph.coords.items():
        d = geo.haversine_m(point.lon, point.lat, lon, lat)
        if d <= max(sigma * SNAP_MULTIPLIER, 60.0):
            logp = -(d * d) / (2 * sigma * sigma)
            out.append({"node_id": node_id, "distance_m": round(d, 2), "log_emission": logp})
    if not out:  # 弱吸附：只取最近点并显式标注，不伪造高置信
        nearest = min(
            graph.coords.items(),
            key=lambda kv: geo.haversine_m(point.lon, point.lat, *kv[1]),
        )
        d = geo.haversine_m(point.lon, point.lat, *nearest[1])
        out.append({"node_id": nearest[0], "distance_m": round(d, 2),
                    "log_emission": -12.0, "weak_snap": True})
    return out


def infer_paths(db: Session, batch: Batch, target_area_id: str) -> PathInference:
    target = db.get(GeoArea, target_area_id)
    if target is None or target.kind != "target":
        raise HTTPException(404, "目标区域不存在或 kind 不是 target")
    model = db.query(RoadModel).order_by(RoadModel.version.desc()).first()
    if model is None:
        raise HTTPException(409, "无道路模型，无法执行状态空间路径推断（需先登记 road model）")
    graph = RoadGraph(model)

    points = track.points_for_batch(db, batch)
    if not points:
        raise HTTPException(409, "评估窗口内无定位点，无法推断路径")

    # 与规则引擎一致：剔除退役/无安装期终端的点
    valid_points = []
    for p in points:
        entry = track.registry_entry_at(db, p.terminal_id, track.corrected_time(p))
        if entry is not None and entry.vehicle_id == batch.vehicle_id and not (
            entry.status == "retired" or (entry.retire_at and entry.retire_at <= track.corrected_time(p))
        ):
            valid_points.append(p)
    if not valid_points:
        raise HTTPException(409, "全部定位点均来自无效/退役终端，路径推断无可用观测")

    anchor_areas = db.query(GeoArea).filter(GeoArea.site_id.isnot(None)).all()

    # Viterbi：states = [{"last_node", "score", "nodes", "obs_nodes", "gaps", "reasons",
    #                     "anchor_hits", "corridors", "weak_snaps", "cycles"}]
    states = []
    obs_times = []
    for idx, point in enumerate(valid_points):
        t = track.corrected_time(point)
        obs_times.append(t)
        cands = _emission_candidates(point, graph)
        if idx == 0:
            states = [
                {"last_node": c["node_id"], "score": c["log_emission"],
                 "nodes": [c["node_id"]], "obs_nodes": [c["node_id"]], "gaps": [], "reasons": [
                     f"观测#{point.seq} 吸附到 {c['node_id']}（{c['distance_m']}m）"
                     + ("，弱吸附" if c.get("weak_snap") else "")],
                 "anchor_hits": [], "corridors": set(), "weak_snaps": int(bool(c.get("weak_snap"))),
                 "cycles": 0}
                for c in cands
            ]
            continue

        dt_h = max((t - obs_times[idx - 1]).total_seconds() / 3600.0, 1e-6)
        new_states = []
        for st in states:
            for c in cands:
                dist_m, node_path = graph.shortest(st["last_node"], c["node_id"])
                gap = None
                cycle_penalty = 0.0
                if dist_m is None:
                    # 道路模型缺边：不补画，给固定惩罚并登记空洞
                    log_trans = -8.0
                    gap = {"from_node": st["last_node"], "to_node": c["node_id"],
                           "reason": "道路模型无连通路径，缺失路段不补画，候选在空洞两侧独立保留"}
                    traveled_nodes = [c["node_id"]]
                else:
                    speed_kmh = (dist_m / 1000.0) / dt_h
                    if speed_kmh > MAX_SPEED_KMH:
                        log_trans = -10.0
                        gap = {"from_node": st["last_node"], "to_node": c["node_id"],
                               "reason": f"最短路需 {speed_kmh:.0f} km/h 超上限 {MAX_SPEED_KMH} km/h，"
                                         "可能存在采样空洞，速度保持未知且不补画"}
                    else:
                        log_trans = -abs(speed_kmh - 30.0) / SPEED_PENALTY_SCALE
                    traveled_nodes = node_path[1:] if node_path else [c["node_id"]]

                # 回绕惩罚：进场轨迹出现节点重复说明吸附不稳，降权但不删除候选
                visited = set(st["nodes"])
                revisits = [nid for nid in traveled_nodes if nid in visited]
                if revisits:
                    cycle_penalty = -5.0 * len(set(revisits))

                corridors = set(st["corridors"])
                anchor_hits = list(st["anchor_hits"])
                for nid in traveled_nodes:
                    edge = graph.edge_map.get((st["last_node"], nid)) or graph.edge_map.get((nid, st["last_node"]))
                    if edge and edge.get("corridor_id"):
                        corridors.add(edge["corridor_id"])
                    lon, lat = graph.coords[nid]
                    for area in anchor_areas:
                        inter = geo.area_intersection(area, lon, lat, accuracy_m=0.0)
                        if inter["relation"] == "inside":
                            hit = {"area_id": area.id, "kind": area.kind, "node_id": nid,
                                   "at_time": t.isoformat()}
                            if hit not in anchor_hits:
                                anchor_hits.append(hit)

                new_states.append({
                    "last_node": c["node_id"],
                    "score": st["score"] + log_trans + c["log_emission"] + cycle_penalty,
                    "nodes": st["nodes"] + traveled_nodes,
                    "obs_nodes": st["obs_nodes"] + [c["node_id"]],
                    "gaps": st["gaps"] + ([gap] if gap else []),
                    "reasons": st["reasons"] + [
                        f"观测#{point.seq} → {c['node_id']}（{c['distance_m']}m）"
                        + ("，弱吸附" if c.get("weak_snap") else "")
                        + (f"；路径回绕降权：{sorted(set(revisits))}" if revisits else "")
                        + (f"；{gap['reason']}" if gap else "")],
                    "anchor_hits": anchor_hits,
                    "corridors": corridors,
                    "weak_snaps": st["weak_snaps"] + int(bool(c.get("weak_snap"))),
                    "cycles": st["cycles"] + len(set(revisits)),
                })
        # 每个终点节点只保留最优，再全局截 top-K，保证稀疏点下仍有多候选
        best_per_node = {}
        for st in new_states:
            if st["last_node"] not in best_per_node or st["score"] > best_per_node[st["last_node"]]["score"]:
                best_per_node[st["last_node"]] = st
        states = sorted(best_per_node.values(), key=lambda s: s["score"], reverse=True)[:TOP_K]

    if not states:
        raise HTTPException(409, "状态空间推断未产生任何候选路径")

    # softmax 归一化候选概率（先取未舍入值，最后修正残差保证总和恰为 1）
    scores = [s["score"] for s in states]
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    raw_probs = [w / total for w in exps]
    probs = [round(x, 4) for x in raw_probs]
    residual = round(1.0 - sum(probs), 4)
    if probs:
        probs[max(range(len(probs)), key=lambda i: probs[i])] = round(probs[max(range(len(probs)), key=lambda i: probs[i])] + residual, 4)

    candidates = []
    target_prob, uncertain_prob = 0.0, 0.0
    for st, prob in zip(states, probs):
        # 入域判定只采信“观测吸附节点”，并带入该观测自身的精度：
        # 道路上的插值节点不补画入域事实，低精度观测最多给出边缘待核。
        obs_relations = []
        for nid, point in zip(st["obs_nodes"], valid_points):
            lon, lat = graph.coords[nid]
            obs_relations.append(geo.area_intersection(target, lon, lat, point.accuracy_m)["relation"])
        enters = any(v == "inside" for v in obs_relations)
        edge = any(v == "edge_uncertain" for v in obs_relations) and not enters
        if enters:
            target_prob += prob
        elif edge:
            uncertain_prob += prob
        corridor_list = sorted(st["corridors"])
        candidates.append({
            "path": st["nodes"],
            "probability": prob,
            "enters_target": enters,
            "target_edge_uncertain": edge,
            "observation_relations": obs_relations,
            "corridors": corridor_list,
            "dual_corridor": len(corridor_list) > 1,
            "anchor_hits": st["anchor_hits"],
            "gaps": st["gaps"],
            "weak_snap_count": st["weak_snaps"],
            "cycle_count": st["cycles"],
            "reasons": st["reasons"],
        })

    assumptions = [
        f"道路模型 v{model.version}（calibrated={model.calibrated}）：{model.note or '无备注'}",
        f"发射似然为高斯模型，σ 取定位精度半径（下限 {MIN_SIGMA_M}m）；吸附半径为精度 ×{SNAP_MULTIPLIER}",
        f"转移仅沿道路模型真实边，速度上限 {MAX_SPEED_KMH} km/h；缺边/超速记为空洞，不补画缺失路段",
        f"保留 {len(candidates)} 条候选路径（top-{TOP_K}）并以 softmax 归一化，不收敛为单一路线",
        "采样空洞期间车辆位置保持未知；候选路径中的 gaps 字段即未建模区间",
        "入域判定只基于观测吸附节点并带入观测精度：低精度观测最多产生边缘待核，不插值补画入域事实",
    ]
    # 同序列载荷冲突：推断仍运行（保留分支），但概率不可认证，必须显式标注
    tids = {p.terminal_id for p in valid_points}
    seqs = {p.seq for p in valid_points}
    payload_conflicts = (
        db.query(TrackPayloadConflict)
        .filter(TrackPayloadConflict.terminal_id.in_(tuple(tids) or ("",)))
        .filter(TrackPayloadConflict.seq.in_(tuple(seqs) or (-1,)))
        .all()
    )
    conflict_ids = {(c.terminal_id, c.seq) for c in payload_conflicts}
    if payload_conflicts:
        assumptions.append(
            f"存在 {len(payload_conflicts)} 组同序列不同哈希载荷：两份载荷均保留并各自参与吸附，"
            "目标概率仅为似然估计，整体不可认证，须以时空证据评估状态为准"
        )
    for c in candidates:
        c["payload_conflict_seqs"] = sorted(
            seq for (tid, seq) in conflict_ids if any(p.seq == seq and p.terminal_id == tid for p in valid_points)
        )
    if not model.calibrated:
        assumptions.append("道路模型未经校准：概率仅反映相对似然，不能作为绝对置信度")
    if any(c["weak_snap_count"] for c in candidates):
        assumptions.append("存在弱吸附点（最近节点超出精度半径 3 倍），相关候选可信度下调")

    inference = PathInference(
        batch_id=batch.id,
        version=_next_version(db, batch.id),
        target_area_id=target_area_id,
        road_model_version=model.version,
        target_probability=round(target_prob, 4),
        candidates=candidates,
        assumptions=assumptions,
        note=(f"经过目标区域概率 {target_prob:.2f}（边缘待核 {uncertain_prob:.2f}）；"
              f"基于 {len(valid_points)} 个有效观测，候选 {len(candidates)} 条"),
    )
    db.add(inference)
    db.commit()
    db.refresh(inference)
    return inference


def to_dict(p: PathInference) -> dict:
    return {
        "id": p.id,
        "batch_id": p.batch_id,
        "version": p.version,
        "target_area_id": p.target_area_id,
        "road_model_version": p.road_model_version,
        "target_probability": p.target_probability,
        "candidates": p.candidates,
        "assumptions": p.assumptions,
        "note": p.note,
        "created_at": p.created_at.isoformat(),
    }
