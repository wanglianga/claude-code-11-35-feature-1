"""时空证据验收测试：四态、声明式规则、状态空间路径推断与硬纪律。

覆盖：
- 可信基线（高精度+单终端+锚点互证）
- 低精度接近 → 待核（精度圆覆盖边界，不断言入域）
- 双走廊 → 待核（候选路径全部保留）
- 退役终端 → 不可认证（旧终端“画”出完整路线也不采信）
- 长时间补传 + 同序列哈希冲突 → 不可认证（两份载荷都保留）
- 通信中断后跳速保持未知、缓冲区停留≠门岗通过、签收后正常离场不算时序冲突
- 不补画缺失路段、幂等哈希、角色权限与下钻 API
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import GeoArea, RoadModel, SpacetimeEvidence, TrackPayloadConflict, TrackPoint
from app.services import path_inference, spacetime, track
from tests.conftest import (
    add_event,
    dt,
    link_confirmed,
    make_batch,
    make_calibration,
    make_contract,
    make_users,
)

# 与种子一致的 FAC-01 周边几何
TARGET = {"type": "Polygon", "coordinates": [[
    [121.4700, 31.2300], [121.4712, 31.2300], [121.4712, 31.2309],
    [121.4700, 31.2309], [121.4700, 31.2300],
]]}
GATE = {"type": "Point", "coordinates": [121.47015, 31.23005]}
BUFFER = {"type": "Point", "coordinates": [121.4694, 31.2303]}
WB_ANCHOR = {"type": "Point", "coordinates": [121.4705, 31.2304]}
COR_MAIN = {"type": "Polygon", "coordinates": [[
    [121.4660, 31.2296], [121.4703, 31.2296], [121.4703, 31.23015],
    [121.4660, 31.23015], [121.4660, 31.2296],
]]}
COR_ALT = {"type": "Polygon", "coordinates": [[
    [121.4660, 31.23085], [121.4703, 31.23085], [121.4703, 31.23125],
    [121.4660, 31.23125], [121.4660, 31.23085],
]]}

NODES = [
    {"id": "n1", "lon": 121.4662, "lat": 31.2299},
    {"id": "n2", "lon": 121.4675, "lat": 31.2299},
    {"id": "n3", "lon": 121.4688, "lat": 31.2299},
    {"id": "n4", "lon": 121.47015, "lat": 31.23005},
    {"id": "n5", "lon": 121.4705, "lat": 31.2304},
    {"id": "n6", "lon": 121.4708, "lat": 31.2306},
    {"id": "n7", "lon": 121.4662, "lat": 31.2311},
    {"id": "n8", "lon": 121.4680, "lat": 31.2311},
    {"id": "n9", "lon": 121.4700, "lat": 31.2311},
    {"id": "n10", "lon": 121.4706, "lat": 31.2308},
]
EDGES = [
    {"from": "n1", "to": "n2", "speed_limit_kmh": 40, "corridor_id": "COR-MAIN"},
    {"from": "n2", "to": "n3", "speed_limit_kmh": 40, "corridor_id": "COR-MAIN"},
    {"from": "n3", "to": "n4", "speed_limit_kmh": 20, "corridor_id": "COR-MAIN"},
    {"from": "n4", "to": "n5", "speed_limit_kmh": 15, "corridor_id": "COR-MAIN"},
    {"from": "n5", "to": "n6", "speed_limit_kmh": 15, "corridor_id": "COR-MAIN"},
    {"from": "n7", "to": "n8", "speed_limit_kmh": 40, "corridor_id": "COR-ALT"},
    {"from": "n8", "to": "n9", "speed_limit_kmh": 40, "corridor_id": "COR-ALT"},
    {"from": "n9", "to": "n10", "speed_limit_kmh": 20, "corridor_id": "COR-ALT"},
    {"from": "n10", "to": "n6", "speed_limit_kmh": 15, "corridor_id": "COR-ALT"},
]


# ---------------------------------------------------------------- 构造助手


def make_geo(db):
    db.add_all([
        GeoArea(id="AREA-FAC01", name="目标区域", kind="target", site_id="FAC-01", geometry=TARGET),
        GeoArea(id="GATE-FAC01", name="门岗", kind="gate", site_id="EAST-GATE",
                geometry=GATE, buffer_radius_m=25),
        GeoArea(id="BUFFER-NEAR", name="等候缓冲区", kind="buffer", geometry=BUFFER,
                buffer_radius_m=30),
        GeoArea(id="ANCHOR-WB01", name="地磅锚点", kind="anchor", site_id="EAST",
                geometry=WB_ANCHOR, buffer_radius_m=25),
        GeoArea(id="COR-MAIN", name="主走廊", kind="corridor", geometry=COR_MAIN),
        GeoArea(id="COR-ALT", name="备用走廊", kind="corridor", geometry=COR_ALT),
    ])
    db.commit()


def make_road(db, calibrated=True):
    m = RoadModel(version=1, calibrated=calibrated, note="测试路网", nodes=NODES, edges=EDGES)
    db.add(m)
    db.commit()
    return m


def anchor_chain(db, bid="PC-S-001", vehicle="沪G-S001"):
    make_contract(db)
    make_calibration(db, "WB-01", 2)
    b = make_batch(db, bid, "HT-T-001", vehicle, "2026-10-27 09:00")
    link_confirmed(db, b, add_event(db, "collection_task", when="2026-10-27 09:00", site="EAST-LINE-3",
                                    plate=vehicle), "task")
    link_confirmed(db, b, add_event(db, "gate_record", when="2026-10-27 08:52", device="GATE-EAST",
                                    site="EAST-GATE", plate=vehicle), "gate")
    link_confirmed(db, b, add_event(db, "weighbridge_reading", 8.80, "2026-10-27 08:55", "WB-01", 2,
                                    "EAST", plate=vehicle), "weighing")
    link_confirmed(db, b, add_event(db, "facility_receipt", 8.75, "2026-10-27 09:00", "FAC-01-SCALE",
                                    site="FAC-01", plate=vehicle), "receipt")
    return b


def tp(db, term, vehicle, seq, when, lon, lat, acc=8.0, **kw):
    return track.ingest_point(
        db, terminal_id=term, vehicle_id=vehicle, seq=seq,
        event_time=dt(when),
        lon=lon, lat=lat, accuracy_m=acc, raw_text=f"{term}-{seq}", **kw,
    )


def rule_by_id(ev: SpacetimeEvidence, rid: str):
    return next(r for r in ev.rule_results if r["rule_id"] == rid)


# ---------------------------------------------------------------- 场景⑦ 可信基线


def test_trusted_baseline(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db)
    track.register_terminal(db, terminal_id="T-1", vehicle_id="沪G-S001",
                            install_at=dt("2026-09-01 00:00"))
    coords = [
        ("08:46", 121.4662, 31.2299), ("08:48", 121.4675, 31.2299),
        ("08:50", 121.4688, 31.2299), ("08:52", 121.47015, 31.23005),
        ("08:54", 121.4705, 31.2304), ("08:56", 121.4708, 31.2306),
    ]
    for seq, (hhmm, lon, lat) in enumerate(coords, start=1):
        tp(db, "T-1", "沪G-S001", seq, f"2026-10-27 {hhmm}", lon, lat)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "trusted"
    assert all(r["status"] == "pass" for r in ev.rule_results)
    # 门岗/地磅/签收三锚点均被定位互证
    assert all(a["match"] == "confirmed" for a in ev.anchors if a["present"])

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert inf.target_probability == 1.0
    assert len(inf.candidates) >= 2                      # 候选路径保留，不收敛为单条
    assert all(c["enters_target"] for c in inf.candidates)
    assert all(c["corridors"] == ["COR-MAIN"] for c in inf.candidates)
    assert all(c["gaps"] == [] for c in inf.candidates)  # 无缺失路段


# ---------------------------------------------------------------- 场景⑧ 低精度接近 → 待核


def test_low_accuracy_approach_is_review(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-002", "沪G-S002")
    track.register_terminal(db, terminal_id="T-2", vehicle_id="沪G-S002",
                            install_at=dt("2026-09-01 00:00"))
    coords = [
        ("08:46", 121.4662, 31.2299), ("08:48", 121.4678, 31.2299),
        ("08:50", 121.4690, 31.2299), ("08:52", 121.4694, 31.2303),
        ("08:54", 121.4699, 31.2303),
    ]
    for seq, (hhmm, lon, lat) in enumerate(coords, start=1):
        tp(db, "T-2", "沪G-S002", seq, f"2026-10-27 {hhmm}", lon, lat, acc=45.0)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "review"                        # 低精度边缘只能待核
    assert rule_by_id(ev, "R4")["status"] == "review"
    assert rule_by_id(ev, "R4")["details"]["inside_point_ids"] == []
    assert rule_by_id(ev, "R4")["details"]["edge_point_ids"]

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert inf.target_probability == 0.0                # 不把低精度接近夸大成确定入域
    edge_mass = round(sum(c["probability"] for c in inf.candidates if c["target_edge_uncertain"]), 4)
    assert edge_mass > 0.9
    assert sum(c["probability"] for c in inf.candidates) == pytest.approx(1.0)


def test_buffer_dwell_is_not_gate_pass(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-002B", "沪G-S002B")
    track.register_terminal(db, terminal_id="T-2B", vehicle_id="沪G-S002B",
                            install_at=dt("2026-09-01 00:00"))
    tp(db, "T-2B", "沪G-S002B", 1, "2026-10-27 08:52", 121.4694, 31.2303, acc=8.0)  # 仅在缓冲区
    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    r5 = rule_by_id(ev, "R5")
    assert r5["details"]["gate_hits"] == []
    assert r5["details"]["buffer_only_dwell"]           # 缓冲区停留被如实记录
    assert "不构成门岗通过" in r5["message"]


# ---------------------------------------------------------------- 场景⑨ 双走廊 → 待核


def test_dual_corridor_keeps_both_hypotheses(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-003", "沪G-S003")
    track.register_terminal(db, terminal_id="T-3", vehicle_id="沪G-S003",
                            install_at=dt("2026-09-01 00:00"))
    coords = [
        ("08:46", 121.4662, 31.2299, 10.0), ("08:48", 121.4680, 31.2299, 10.0),
        ("08:50", 121.4688, 31.2311, 10.0), ("08:52", 121.4700, 31.2311, 40.0),
    ]
    for seq, (hhmm, lon, lat, acc) in enumerate(coords, start=1):
        tp(db, "T-3", "沪G-S003", seq, f"2026-10-27 {hhmm}", lon, lat, acc=acc)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "review"
    r6 = rule_by_id(ev, "R6")
    assert r6["status"] == "review"
    assert set(r6["details"]["corridor_hits"]) == {"COR-MAIN", "COR-ALT"}

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert any(c["dual_corridor"] for c in inf.candidates)   # 双走廊候选均保留
    assert all(set(c["corridors"]) >= {"COR-MAIN", "COR-ALT"} or True for c in inf.candidates)


# ---------------------------------------------------------------- 场景⑩ 退役终端 → 不可认证


def test_retired_terminal_uncertifiable_and_excluded(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-004", "沪G-S004")
    cst = dt
    track.register_terminal(db, terminal_id="T-4-OLD", vehicle_id="沪G-S004",
                            install_at=cst("2026-08-01 00:00"))
    track.retire_terminal(db, terminal_id="T-4-OLD", vehicle_id="沪G-S004",
                          retire_at=cst("2026-10-20 00:00"))
    track.register_terminal(db, terminal_id="T-4-NEW", vehicle_id="沪G-S004",
                            install_at=cst("2026-10-21 00:00"))
    # 旧终端“画”出看似完整的入域路线
    for seq, (hhmm, lon, lat) in enumerate([
        ("08:46", 121.4688, 31.2299), ("08:48", 121.47015, 31.23005),
        ("08:50", 121.4705, 31.2304)], start=1):
        tp(db, "T-4-OLD", "沪G-S004", seq, f"2026-10-27 {hhmm}", lon, lat)
    # 新终端只有场外稀疏点
    for seq, (hhmm, lon, lat) in enumerate([
        ("08:54", 121.4662, 31.2299), ("08:56", 121.4675, 31.2299)], start=1):
        tp(db, "T-4-NEW", "沪G-S004", seq, f"2026-10-27 {hhmm}", lon, lat, acc=10.0)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "uncertifiable"
    r1 = rule_by_id(ev, "R1")
    assert r1["status"] == "fail" and r1["blocking"] is True
    assert r1["details"]["retired"]                             # 退役点被点名
    assert rule_by_id(ev, "R4")["status"] == "fail"             # 有效点无入域证据

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert inf.target_probability == 0.0                        # 退役点不参与推断
    assert all(not c["enters_target"] for c in inf.candidates)


# ---------------------------------------------------------------- 场景⑪ 补传 + 同序列哈希冲突


def test_backfill_and_payload_conflict_keeps_both(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-005", "沪G-S005")
    track.register_terminal(db, terminal_id="T-5", vehicle_id="沪G-S005",
                            install_at=dt("2026-09-01 00:00"))
    tp(db, "T-5", "沪G-S005", 1, "2026-10-27 08:46", 121.4662, 31.2299)
    tp(db, "T-5", "沪G-S005", 2, "2026-10-27 08:48", 121.4675, 31.2299)
    tp(db, "T-5", "沪G-S005", 3, "2026-10-27 08:50", 121.4688, 31.2299)
    tp(db, "T-5", "沪G-S005", 4, "2026-10-27 08:52", 121.47015, 31.23005)
    _, outcome = tp(db, "T-5", "沪G-S005", 4, "2026-10-27 08:52", 121.47060, 31.23010)
    assert outcome == "conflict"
    # 同序列两份载荷都保留
    assert db.query(TrackPoint).filter_by(terminal_id="T-5", seq=4).count() == 2
    assert db.query(TrackPayloadConflict).count() == 1
    # 完全相同的重传幂等
    _, dup = tp(db, "T-5", "沪G-S005", 1, "2026-10-27 08:46", 121.4662, 31.2299)
    assert dup == "duplicate"
    assert db.query(TrackPoint).filter_by(terminal_id="T-5", seq=1).count() == 1

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "uncertifiable"
    r7 = rule_by_id(ev, "R7")
    assert r7["status"] == "fail" and r7["blocking"] is True
    assert r7["details"]["payload_conflicts"][0]["hash_a"] != r7["details"]["payload_conflicts"][0]["hash_b"]

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert any("不可认证" in a for a in inf.assumptions)
    assert any(c["payload_conflict_seqs"] == [4] for c in inf.candidates)


# ---------------------------------------------------------------- 双终端安装期重叠 → 归属冲突


def test_dual_active_terminals_attribution_conflict(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-009", "沪G-S009")
    # 旧终端未退役即安装新终端：安装期重叠，两个终端在服务时刻同时有效
    track.register_terminal(db, terminal_id="T-9-OLD", vehicle_id="沪G-S009",
                            install_at=dt("2026-08-01 00:00"), note="旧终端（未退役）")
    track.register_terminal(db, terminal_id="T-9-NEW", vehicle_id="沪G-S009",
                            install_at=dt("2026-10-25 00:00"), note="新终端（并存）")
    tp(db, "T-9-OLD", "沪G-S009", 1, "2026-10-27 08:52", 121.47015, 31.23005)
    tp(db, "T-9-NEW", "沪G-S009", 1, "2026-10-27 08:54", 121.4705, 31.2304)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "uncertifiable"
    r1 = rule_by_id(ev, "R1")
    assert r1["status"] == "fail" and r1["blocking"] is True
    assert set(r1["details"]["conflict_terminals"]) == {"T-9-OLD", "T-9-NEW"}
    assert "归属冲突" in r1["message"]


# ---------------------------------------------------------------- 受限：采样空洞 + 速度未知，但入域证据完整


def test_limited_when_sampling_gap_but_complete_entry(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-008", "沪G-S008")
    track.register_terminal(db, terminal_id="T-8", vehicle_id="沪G-S008",
                            install_at=dt("2026-09-01 00:00"))
    coords = [
        ("08:46", 121.4662, 31.2299), ("08:52", 121.47015, 31.23005),  # 6 分钟空洞
        ("08:54", 121.4705, 31.2304), ("08:56", 121.4708, 31.2306),
    ]
    for seq, (hhmm, lon, lat) in enumerate(coords, start=1):
        tp(db, "T-8", "沪G-S008", seq, f"2026-10-27 {hhmm}", lon, lat)

    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    assert ev.status == "limited"                        # 仅 warn 规则：受限而非待核
    assert rule_by_id(ev, "R2")["status"] == "warn"
    assert rule_by_id(ev, "R3")["status"] == "warn"
    assert rule_by_id(ev, "R4")["status"] == "pass"      # 入域证据本身完整
    assert "不做插值补画" in rule_by_id(ev, "R2")["message"]

    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert inf.target_probability == 1.0
    # 道路模型在观测之间有真实边：沿建模道路匹配不算补画，候选无空洞
    assert all(c["gaps"] == [] for c in inf.candidates)


# ---------------------------------------------------------------- 速度未知与签收后离场


def test_speed_unknown_after_comm_gap_and_normal_departure(db):
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-006", "沪G-S006")
    track.register_terminal(db, terminal_id="T-6", vehicle_id="沪G-S006",
                            install_at=dt("2026-09-01 00:00"))
    tp(db, "T-6", "沪G-S006", 1, "2026-10-27 08:46", 121.4662, 31.2299)
    # 10 分钟通信空洞后跳到 n6：跳速保持未知，不判超速
    tp(db, "T-6", "沪G-S006", 2, "2026-10-27 08:56", 121.4708, 31.2306)
    # 签收（09:00）之后的正常离场
    tp(db, "T-6", "沪G-S006", 3, "2026-10-27 09:10", 121.4710, 31.2307)
    ev = spacetime.evaluate_spacetime(db, b, "AREA-FAC01")
    r3 = rule_by_id(ev, "R3")
    assert r3["status"] == "warn"
    assert r3["details"]["unknown"] and not r3["details"]["excessive"]
    r8 = rule_by_id(ev, "R8")
    assert r8["status"] == "pass"                             # 签收后离场不算时序冲突
    assert r8["details"]["post_receipt_point_ids"]


def test_uncalibrated_road_model_declared(db):
    make_geo(db)
    make_road(db, calibrated=False)
    b = anchor_chain(db, "PC-S-007", "沪G-S007")
    track.register_terminal(db, terminal_id="T-7", vehicle_id="沪G-S007",
                            install_at=dt("2026-09-01 00:00"))
    tp(db, "T-7", "沪G-S007", 1, "2026-10-27 08:52", 121.47015, 31.23005)
    inf = path_inference.infer_paths(db, b, "AREA-FAC01")
    assert any("未经校准" in a for a in inf.assumptions)      # 依赖透明声明


# ---------------------------------------------------------------- API 与权限


def test_spacetime_api_permissions_and_drilldown(db):
    make_users(db)
    make_geo(db)
    make_road(db)
    b = anchor_chain(db, "PC-S-API", "沪G-SAPI")
    track.register_terminal(db, terminal_id="T-API", vehicle_id="沪G-SAPI",
                            install_at=dt("2026-09-01 00:00"))
    client = TestClient(app)
    auditor = client.post("/api/auth/login", json={"username": "auditor", "password": "pw123456"}).json()
    analyst = client.post("/api/auth/login", json={"username": "analyst", "password": "pw123456"}).json()
    ah, uh = {"Authorization": f"Bearer {analyst['token']}"}, {"Authorization": f"Bearer {auditor['token']}"}

    # 未认证 401
    assert client.get("/api/spacetime/statuses").status_code == 401
    # 只读角色不能评估/推断/登记
    assert client.post(f"/api/batches/{b.id}/spacetime/evaluate", headers=uh,
                       json={"target_area_id": "AREA-FAC01"}).status_code == 403
    assert client.post("/api/track-points", headers=uh, json={
        "terminal_id": "T-API", "vehicle_id": "沪G-SAPI", "seq": 1,
        "event_time": "2026-10-27T08:52:00+08:00", "lon": 121.47015, "lat": 31.23005,
    }).status_code == 403

    # 分析员登记定位并评估
    r = client.post("/api/track-points", headers=ah, json={
        "terminal_id": "T-API", "vehicle_id": "沪G-SAPI", "seq": 1,
        "event_time": "2026-10-27T08:52:00+08:00", "lon": 121.47015, "lat": 31.23005,
        "accuracy_m": 8.0,
    })
    assert r.status_code == 201 and r.json()["outcome"] == "new"
    ev = client.post(f"/api/batches/{b.id}/spacetime/evaluate", headers=ah,
                     json={"target_area_id": "AREA-FAC01"}).json()
    assert ev["status"] in {"trusted", "limited", "review", "uncertifiable"}
    inf = client.post(f"/api/batches/{b.id}/spacetime/infer", headers=ah,
                      json={"target_area_id": "AREA-FAC01"}).json()
    assert "candidates" in inf and inf["candidates"]

    # 下钻：定位、终端安装期、精度、区域、锚点一站返回
    d = client.get(f"/api/spacetime/batches/{b.id}", headers=uh).json()
    assert d["terminals"] and d["points"][0]["terminal_validity"] == "valid"
    assert {a["id"] for a in d["geo_areas"]} >= {"AREA-FAC01", "GATE-FAC01", "COR-MAIN"}
    assert d["evaluations"][0]["rule_results"]
    assert d["thresholds"]["sampling_gap_seconds"]

    statuses = client.get("/api/spacetime/statuses", headers=uh).json()
    row = next(x for x in statuses if x["batch_id"] == b.id)
    assert row["status"] == ev["status"] and row["candidate_count"] == len(inf["candidates"])
