"""演示种子数据：覆盖六个重量验收场景与六个时空证据场景。幂等——已存在合同时跳过。

重量场景：① 主计量点合同 ② 跨场称重差异（区间重叠） ③ 签收后离场（时钟校正）
          ④ 校准更正（复审中） ⑤ 双批次争用（冻结分配） ⑥ 跨阶梯区间（边界金额）
时空场景：⑦ 可信基线 ⑧ 低精度接近（待核） ⑨ 双走廊（待核） ⑩ 退役终端（不可认证）
          ⑪ 补传载荷冲突（不可认证） ⑫ 采样空洞（受限）
"""

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import (
    Batch,
    CalibrationRecord,
    Contract,
    GeoArea,
    PositionSegment,
    RateCatalog,
    RoadModel,
    User,
)
from app.security import make_password_hash
from app.services import adjudicator, compiler, departure, evidence, publisher, registry
from app.services import calibration as calibration_svc
from app.services import path_inference, spacetime, track

CST = timezone(timedelta(hours=8))


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=CST)


def seed():
    db = SessionLocal()
    try:
        if db.get(Contract, "HT-2026-001"):
            return  # 已初始化

        # ---------------- 用户（测试账号） ----------------
        db.add_all(
            [
                User(
                    username="analyst",
                    password_hash=make_password_hash("Analyst@2026"),
                    role="analyst",
                    display_name="数据团队·分析员",
                ),
                User(
                    username="auditor",
                    password_hash=make_password_hash("Auditor@2026"),
                    role="auditor",
                    display_name="财务·复核员",
                ),
            ]
        )

        # ---------------- 合同 ----------------
        db.add_all(
            [
                Contract(
                    id="HT-2026-001",
                    name="城东生活垃圾收运合同（主计量点）",
                    contract_type="主计量点合同",
                    strategy="priority",
                    source_priority=["facility_receipt", "weighbridge_reading", "gate_record"],
                    time_tolerance_minutes=45,
                    quality_tolerance_pct=0.005,
                ),
                Contract(
                    id="HT-2026-002",
                    name="跨区转运计量合同（区间裁定）",
                    contract_type="区间裁定合同",
                    strategy="interval",
                    source_priority=["weighbridge_reading", "facility_receipt"],
                    time_tolerance_minutes=60,
                    quality_tolerance_pct=0.005,
                ),
                Contract(
                    id="HT-2026-004",
                    name="西场固废收运合同（主计量点·地磅）",
                    contract_type="主计量点合同",
                    strategy="priority",
                    source_priority=["weighbridge_reading", "facility_receipt", "gate_record"],
                    time_tolerance_minutes=45,
                    quality_tolerance_pct=0.005,
                ),
            ]
        )

        # ---------------- 费率目录（冻结；v2 为服务后修订版） ----------------
        db.add_all(
            [
                RateCatalog(
                    version=1,
                    effective_from=dt("2026-01-01 00:00"),
                    effective_to=dt("2026-11-01 00:00"),
                    frozen=True,
                    tiers=[
                        {"up_to": 10, "rate": 80.0},
                        {"up_to": 20, "rate": 95.0},
                        {"up_to": None, "rate": 110.0},
                    ],
                    note="2026 年度处理费目录（累进阶梯，元/吨）",
                ),
                RateCatalog(
                    version=2,
                    effective_from=dt("2026-11-01 00:00"),
                    effective_to=None,
                    frozen=True,
                    tiers=[
                        {"up_to": 10, "rate": 85.0},
                        {"up_to": 20, "rate": 100.0},
                        {"up_to": None, "rate": 118.0},
                    ],
                    note="2026-11 修订：上调各阶梯单价（不回写 10 月服务）",
                ),
            ]
        )

        # ---------------- 校准记录 ----------------
        cal_wb01 = CalibrationRecord(
            device_id="WB-01", version=2, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="东场地磅周期检定",
        )
        cal_wb02 = CalibrationRecord(
            device_id="WB-02", version=3, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="西场地磅周期检定",
        )
        cal_wb03 = CalibrationRecord(
            device_id="WB-03", version=1, effective_from=dt("2026-01-01 00:00"),
            factor=1.0, status="active", note="转运站地磅周期检定",
        )
        db.add_all([cal_wb01, cal_wb02, cal_wb03])
        db.commit()

        def ev(**kw):
            return evidence.ingest(db, **kw)

        def mk_batch(bid, cid, vehicle, when):
            b = Batch(id=bid, contract_id=cid, vehicle_id=vehicle, service_time=dt(when))
            db.add(b)
            db.commit()
            return b

        def link(batch, event, ltype, confirm=True):
            l = registry.add_link(db, batch, event.id, ltype)
            if confirm and l.status == "candidate":
                l = registry.confirm_link(db, l, "种子数据：证据链完整")
            return l

        # ================ 场景① 主计量点合同（签收为主计量点） ================
        b1 = mk_batch("PC-20261012-001", "HT-2026-001", "沪A-D2101", "2026-10-12 08:55")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1012-01：城东 3 号线，车 沪A-D2101",
               event_time=dt("2026-10-12 08:55"), site_id="EAST-LINE-3",
               payload={"task_no": "RW-1012-01", "plate": "沪A-D2101"})
        link(b1, e, "task")
        e = ev(source_type="gate_record", raw_text="东门岗记录：沪A-D2101 进场，估重 12.50t",
               event_time=dt("2026-10-12 09:05"), device_id="GATE-EAST", site_id="EAST",
               payload={"weight_tons": 12.50, "plate": "沪A-D2101"})
        link(b1, e, "gate")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：毛重 21.40t 皮重 9.00t 净重 12.40t",
               event_time=dt("2026-10-12 09:12"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 12.40, "plate": "沪A-D2101"})
        link(b1, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 12.35t，批次合格",
               event_time=dt("2026-10-12 09:40"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 12.35, "plate": "沪A-D2101"})
        link(b1, e, "receipt")
        adjudicator.adjudicate(db, b1)          # 签收 12.35 选定，地磅/门岗为差异佐证
        compiler.compile_amounts(db, b1)        # 目录 v1：10×80 + 2.35×95 = 1023.25
        publisher.publish(db, b1)               # PUB-PC-20261012-001-v1
        ev(source_type="accounting_receipt", raw_text="账务回执 FV-20261031-001：确认金额 1023.25 元",
           event_time=dt("2026-10-31 18:00"), site_id="FINANCE",
           payload={"voucher": "FV-20261031-001", "amount": 1023.25, "ref": "PUB-PC-20261012-001-v1"})

        # ================ 场景② 跨场称重差异（区间重叠） ================
        b2 = mk_batch("PC-20261015-002", "HT-2026-002", "沪B-D3302", "2026-10-15 14:00")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1015-02：跨区转运，车 沪B-D3302",
               event_time=dt("2026-10-15 14:00"), site_id="CROSS-1",
               payload={"task_no": "RW-1015-02", "plate": "沪B-D3302"})
        link(b2, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 18.20t",
               event_time=dt("2026-10-15 14:10"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 18.20, "plate": "沪B-D3302"})
        link(b2, e, "weighing")
        e = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 18.05t",
               event_time=dt("2026-10-15 14:35"), device_id="WB-03", site_id="TRANSFER",
               calibration_version=1, payload={"weight_tons": 18.05, "plate": "沪B-D3302"})
        link(b2, e, "weighing")
        adjudicator.adjudicate(db, b2)          # 区间 [18.109, 18.14025] 重叠 → 签发区间
        compiler.compile_amounts(db, b2)        # 边界金额 1570.35 / 1573.32，不跨阶梯
        publisher.publish(db, b2)

        # ================ 场景③ 签收后离场（时钟校正后不认定矛盾） ================
        b3 = mk_batch("PC-20261018-003", "HT-2026-001", "沪A-D2101", "2026-10-18 08:50")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1018-03：城东 3 号线",
               event_time=dt("2026-10-18 08:50"), site_id="EAST-LINE-3",
               payload={"task_no": "RW-1018-03", "plate": "沪A-D2101"})
        link(b3, e, "task")
        e = ev(source_type="gate_record", raw_text="东门岗记录：沪A-D2101 进场，估重 9.90t",
               event_time=dt("2026-10-18 09:00"), device_id="GATE-EAST", site_id="EAST",
               payload={"weight_tons": 9.90, "plate": "沪A-D2101"})
        link(b3, e, "gate")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 9.80t",
               event_time=dt("2026-10-18 09:08"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 9.80, "plate": "沪A-D2101"})
        link(b3, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 9.75t",
               event_time=dt("2026-10-18 09:35"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 9.75, "plate": "沪A-D2101"})
        link(b3, e, "receipt")
        # 车载终端时钟快 30 分钟：原始最后定位 10:07 晚于签收 09:35，校正后 09:37 在容差内
        db.add_all(
            [
                PositionSegment(vehicle_id="沪A-D2101", site_id="FAC-01",
                                start_time=dt("2026-10-18 09:00"), end_time=dt("2026-10-18 09:40"),
                                clock_offset_seconds=1800),
                PositionSegment(vehicle_id="沪A-D2101", site_id="ROAD-S7",
                                start_time=dt("2026-10-18 10:05"), end_time=dt("2026-10-18 10:07"),
                                clock_offset_seconds=1800),
            ]
        )
        db.commit()
        departure.check_departure(db, b3, tolerance_minutes=10)  # consistent
        adjudicator.adjudicate(db, b3)
        compiler.compile_amounts(db, b3)        # 9.75×80 = 780.00
        publisher.publish(db, b3)
        ev(source_type="accounting_receipt", raw_text="账务回执 FV-20261031-002：确认金额 780.00 元",
           event_time=dt("2026-10-31 18:05"), site_id="FINANCE",
           payload={"voucher": "FV-20261031-002", "amount": 780.00, "ref": "PUB-PC-20261018-003-v1"})

        # ================ 场景④ 校准更正（计量事实转复审中） ================
        b4 = mk_batch("PC-20261020-004", "HT-2026-004", "沪C-D7710", "2026-10-20 09:55")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1020-04：西场固废",
               event_time=dt("2026-10-20 09:55"), site_id="WEST",
               payload={"task_no": "RW-1020-04", "plate": "沪C-D7710"})
        link(b4, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="西场地磅 WB-02：净重 22.00t",
               event_time=dt("2026-10-20 10:05"), device_id="WB-02", site_id="WEST",
               calibration_version=3, payload={"weight_tons": 22.00, "plate": "沪C-D7710"})
        link(b4, e, "weighing")
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 21.90t",
               event_time=dt("2026-10-20 10:30"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 21.90, "plate": "沪C-D7710"})
        link(b4, e, "receipt")
        adjudicator.adjudicate(db, b4)          # 地磅为主计量点：22.00 选定
        compiler.compile_amounts(db, b4)        # 800+10×95+2×110 = 1970.00
        publisher.publish(db, b4)               # v1 已发布（旧账务视图保留原依据）
        # 检定机构更正 WB-02 v3：实际偏差 +2% → 新计费视图暂停引用
        calibration_svc.correct_calibration(db, cal_wb02, factor=1.02, note="检定机构更正：v3 期间示值偏低 2%")
        publisher.publish(db, b4)               # v2 paused：复审中，暂停引用

        # ================ 场景⑤ 双批次争用（关系册冻结分配） ================
        b5a = mk_batch("PC-20261022-005A", "HT-2026-001", "沪D-D8801", "2026-10-22 10:00")
        b5b = mk_batch("PC-20261022-005B", "HT-2026-001", "沪E-D9902", "2026-10-22 10:05")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1022-05A",
               event_time=dt("2026-10-22 10:00"), site_id="EAST-LINE-7",
               payload={"task_no": "RW-1022-05A", "plate": "沪D-D8801"})
        link(b5a, e, "task")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1022-05B",
               event_time=dt("2026-10-22 10:05"), site_id="EAST-LINE-8",
               payload={"task_no": "RW-1022-05B", "plate": "沪E-D9902"})
        link(b5b, e, "task")
        shared = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 23.10t（车牌识别待核）",
                    event_time=dt("2026-10-22 10:12"), device_id="WB-03", site_id="TRANSFER",
                    calibration_version=1, payload={"weight_tons": 23.10, "plate": "待核"})
        registry.add_link(db, b5a, shared.id, "weighing")   # 先候选
        registry.add_link(db, b5b, shared.id, "weighing")   # 触发争用 → 双方冻结
        e = ev(source_type="facility_receipt", raw_text="处理场 FAC-01 签收单：净重 23.02t，车 沪D-D8801",
               event_time=dt("2026-10-22 10:40"), device_id="FAC-01-SCALE", site_id="FAC-01",
               payload={"weight_tons": 23.02, "plate": "沪D-D8801"})
        link(b5a, e, "receipt")

        # ================ 场景⑥ 跨阶梯区间（边界金额分列，不取中点） ================
        b6 = mk_batch("PC-20261025-006", "HT-2026-002", "沪F-D5555", "2026-10-25 11:00")
        e = ev(source_type="collection_task", raw_text="收运任务单 RW-1025-06：跨区转运",
               event_time=dt("2026-10-25 11:00"), site_id="CROSS-2",
               payload={"task_no": "RW-1025-06", "plate": "沪F-D5555"})
        link(b6, e, "task")
        e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01：净重 9.98t",
               event_time=dt("2026-10-25 11:10"), device_id="WB-01", site_id="EAST",
               calibration_version=2, payload={"weight_tons": 9.98, "plate": "沪F-D5555"})
        link(b6, e, "weighing")
        e = ev(source_type="weighbridge_reading", raw_text="转运站地磅 WB-03：净重 10.05t",
               event_time=dt("2026-10-25 11:35"), device_id="WB-03", site_id="TRANSFER",
               calibration_version=1, payload={"weight_tons": 10.05, "plate": "沪F-D5555"})
        link(b6, e, "weighing")
        adjudicator.adjudicate(db, b6)          # 区间 [9.99975, 10.0299] 跨越 10t 阶梯
        compiler.compile_amounts(db, b6)        # 边界金额 799.98 / 802.84，amount 留空
        publisher.publish(db, b6)

        # ============================================================
        # 时空证据场景：地理区域、道路模型、终端注册与定位点
        # ============================================================

        def rect(w, s, e, n):
            return {"type": "Polygon", "coordinates": [[
                [w, s], [e, s], [e, n], [w, n], [w, s],
            ]]}

        def point_geom(lon, lat):
            return {"type": "Point", "coordinates": [lon, lat]}

        db.add_all([
            # 目标收运区域（处理场 FAC-01 多边形）
            GeoArea(id="AREA-FAC01", name="FAC-01 处理场目标区域", kind="target", site_id="FAC-01",
                    geometry=rect(121.4700, 31.2300, 121.4712, 31.2309)),
            # 门岗：必须命中该点缓冲才算通过
            GeoArea(id="GATE-FAC01", name="FAC-01 门岗", kind="gate", site_id="EAST-GATE",
                    geometry=point_geom(121.47015, 31.23005), buffer_radius_m=25),
            # 门岗外的停留缓冲区：停留于此不构成门岗通过
            GeoArea(id="BUFFER-NEAR", name="门岗外等候缓冲区", kind="buffer", site_id=None,
                    geometry=point_geom(121.4694, 31.2303), buffer_radius_m=30),
            # 地磅锚点区域
            GeoArea(id="ANCHOR-WB01", name="东场地磅 WB-01 锚点", kind="anchor", site_id="EAST",
                    geometry=point_geom(121.4705, 31.2304), buffer_radius_m=25),
            # 两条道路走廊
            GeoArea(id="COR-MAIN", name="主进场走廊（南路）", kind="corridor", site_id=None,
                    geometry=rect(121.4660, 31.2296, 121.4703, 31.23015)),
            GeoArea(id="COR-ALT", name="备用进场走廊（北路）", kind="corridor", site_id=None,
                    geometry=rect(121.4660, 31.23085, 121.4703, 31.23125)),
        ])
        db.commit()

        db.add(RoadModel(
            version=1, calibrated=True, note="FAC-01 周边路网（南主走廊+北备用走廊）",
            nodes=[
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
            ],
            edges=[
                {"from": "n1", "to": "n2", "speed_limit_kmh": 40, "corridor_id": "COR-MAIN"},
                {"from": "n2", "to": "n3", "speed_limit_kmh": 40, "corridor_id": "COR-MAIN"},
                {"from": "n3", "to": "n4", "speed_limit_kmh": 20, "corridor_id": "COR-MAIN"},
                {"from": "n4", "to": "n5", "speed_limit_kmh": 15, "corridor_id": "COR-MAIN"},
                {"from": "n5", "to": "n6", "speed_limit_kmh": 15, "corridor_id": "COR-MAIN"},
                {"from": "n7", "to": "n8", "speed_limit_kmh": 40, "corridor_id": "COR-ALT"},
                {"from": "n8", "to": "n9", "speed_limit_kmh": 40, "corridor_id": "COR-ALT"},
                {"from": "n9", "to": "n10", "speed_limit_kmh": 20, "corridor_id": "COR-ALT"},
                {"from": "n10", "to": "n6", "speed_limit_kmh": 15, "corridor_id": "COR-ALT"},
            ],
        ))
        db.commit()

        def tp(term, vehicle, seq, when, lon, lat, acc=8.0, delayed=False, offset=0, received=None):
            return track.ingest_point(
                db, terminal_id=term, vehicle_id=vehicle, seq=seq, event_time=dt(when),
                lon=lon, lat=lat, accuracy_m=acc, delayed=delayed,
                clock_offset_seconds=offset, raw_text=f"{term} seq{seq} @{when}",
                received_at=dt(received) if received else None,
            )

        def st_chain(bid, vehicle, when_date):
            """时空场景通用任务+门岗+地磅+签收锚点证据链。"""
            b = mk_batch(bid, "HT-2026-001", vehicle, f"{when_date} 09:00")
            e = ev(source_type="collection_task", raw_text=f"收运任务单（时空场景）{bid}",
                   event_time=dt(f"{when_date} 09:00"), site_id="EAST-LINE-3",
                   payload={"task_no": bid, "plate": vehicle})
            link(b, e, "task")
            e = ev(source_type="gate_record", raw_text="东门岗记录（时空场景）",
                   event_time=dt(f"{when_date} 08:52"), device_id="GATE-EAST", site_id="EAST-GATE",
                   payload={"plate": vehicle})
            link(b, e, "gate")
            e = ev(source_type="weighbridge_reading", raw_text="东场地磅 WB-01（时空场景）",
                   event_time=dt(f"{when_date} 08:55"), device_id="WB-01", site_id="EAST",
                   calibration_version=2, payload={"weight_tons": 8.80, "plate": vehicle})
            link(b, e, "weighing")
            e = ev(source_type="facility_receipt", raw_text="FAC-01 签收单（时空场景）",
                   event_time=dt(f"{when_date} 09:00"), device_id="FAC-01-SCALE", site_id="FAC-01",
                   payload={"weight_tons": 8.75, "plate": vehicle})
            link(b, e, "receipt")
            return b

        # ================ 场景⑦ 可信基线（高精度+单终端+锚点互证） ================
        b7 = st_chain("PC-20261027-007", "沪G-T7001", "2026-10-27")
        track.register_terminal(db, terminal_id="T-7001", vehicle_id="沪G-T7001",
                                install_at=dt("2026-09-01 00:00"), note="主力车载终端")
        for seq, (when, lon, lat) in enumerate([
            ("2026-10-27 08:46", 121.4662, 31.2299),
            ("2026-10-27 08:48", 121.4675, 31.2299),
            ("2026-10-27 08:50", 121.4688, 31.2299),
            ("2026-10-27 08:52", 121.47015, 31.23005),
            ("2026-10-27 08:54", 121.4705, 31.2304),
            ("2026-10-27 08:56", 121.4708, 31.2306),
        ], start=1):
            tp("T-7001", "沪G-T7001", seq, when, lon, lat, acc=8.0)
        adjudicator.adjudicate(db, b7)
        compiler.compile_amounts(db, b7)
        publisher.publish(db, b7)
        spacetime.evaluate_spacetime(db, b7, "AREA-FAC01")       # trusted
        path_inference.infer_paths(db, b7, "AREA-FAC01")         # 入域概率 1.0，主走廊候选

        # ================ 场景⑧ 低精度接近（边缘待核；缓冲区停留≠门岗） ================
        b8 = st_chain("PC-20261027-008", "沪G-T8002", "2026-10-27")
        track.register_terminal(db, terminal_id="T-8002", vehicle_id="沪G-T8002",
                                install_at=dt("2026-09-01 00:00"), note="民用级 GPS，精度较差")
        for seq, (when, lon, lat) in enumerate([
            ("2026-10-27 08:46", 121.4662, 31.2299),
            ("2026-10-27 08:48", 121.4678, 31.2299),
            ("2026-10-27 08:50", 121.4690, 31.2299),
            ("2026-10-27 08:52", 121.4694, 31.2303),   # 门岗外缓冲区停留
            ("2026-10-27 08:54", 121.4699, 31.2303),   # 精度圆覆盖目标边界，未确定入域
        ], start=1):
            tp("T-8002", "沪G-T8002", seq, when, lon, lat, acc=45.0)
        spacetime.evaluate_spacetime(db, b8, "AREA-FAC01")       # review
        path_inference.infer_paths(db, b8, "AREA-FAC01")         # 候选保留，入域概率不夸大

        # ================ 场景⑨ 双走廊（轨迹跨两条走廊，归属待核） ================
        b9 = st_chain("PC-20261027-009", "沪G-T9003", "2026-10-27")
        track.register_terminal(db, terminal_id="T-9003", vehicle_id="沪G-T9003",
                                install_at=dt("2026-09-01 00:00"))
        for seq, (when, lon, lat, acc) in enumerate([
            ("2026-10-27 08:46", 121.4662, 31.2299, 10.0),   # 南主走廊
            ("2026-10-27 08:48", 121.4680, 31.2299, 10.0),
            ("2026-10-27 08:50", 121.4688, 31.2311, 10.0),   # 跳到北备用走廊
            ("2026-10-27 08:52", 121.4700, 31.2311, 40.0),   # 目标北边缘，精度待核
        ], start=1):
            tp("T-9003", "沪G-T9003", seq, when, lon, lat, acc=acc)
        spacetime.evaluate_spacetime(db, b9, "AREA-FAC01")       # review（R6 双走廊）
        path_inference.infer_paths(db, b9, "AREA-FAC01")         # 南北候选均保留，空洞不补画

        # ================ 场景⑩ 退役终端上报（不可认证） ================
        b10 = st_chain("PC-20261027-010", "沪G-T1004", "2026-10-27")
        track.register_terminal(db, terminal_id="T-1004-OLD", vehicle_id="沪G-T1004",
                                install_at=dt("2026-08-01 00:00"), note="旧终端")
        track.retire_terminal(db, terminal_id="T-1004-OLD", vehicle_id="沪G-T1004",
                              retire_at=dt("2026-10-20 00:00"), note="换新退役")
        track.register_terminal(db, terminal_id="T-1004-NEW", vehicle_id="沪G-T1004",
                                install_at=dt("2026-10-21 00:00"), note="新终端（安装期不重叠）")
        for seq, (when, lon, lat) in enumerate([
            ("2026-10-27 08:46", 121.4688, 31.2299),
            ("2026-10-27 08:48", 121.47015, 31.23005),
            ("2026-10-27 08:50", 121.4705, 31.2304),       # 旧终端“画”出了完整入域路线
        ], start=1):
            tp("T-1004-OLD", "沪G-T1004", seq, when, lon, lat, acc=8.0)
        for seq, (when, lon, lat) in enumerate([
            ("2026-10-27 08:54", 121.4662, 31.2299),       # 新终端只有场外稀疏点
            ("2026-10-27 08:56", 121.4675, 31.2299),
        ], start=1):
            tp("T-1004-NEW", "沪G-T1004", seq, when, lon, lat, acc=10.0)
        spacetime.evaluate_spacetime(db, b10, "AREA-FAC01")      # uncertifiable（R1 退役阻断）
        path_inference.infer_paths(db, b10, "AREA-FAC01")        # 仅用新终端点，入域概率 0

        # ================ 场景⑪ 长时间补传 + 同序列哈希冲突（不可认证） ================
        b11 = st_chain("PC-20261027-011", "沪G-T1105", "2026-10-27")
        track.register_terminal(db, terminal_id="T-1105", vehicle_id="沪G-T1105",
                                install_at=dt("2026-09-01 00:00"))
        tp("T-1105", "沪G-T1105", 1, "2026-10-27 08:46", 121.4662, 31.2299)
        tp("T-1105", "沪G-T1105", 2, "2026-10-27 08:48", 121.4675, 31.2299)
        tp("T-1105", "沪G-T1105", 3, "2026-10-27 08:50", 121.4688, 31.2299)
        tp("T-1105", "沪G-T1105", 4, "2026-10-27 08:52", 121.47015, 31.23005)
        _p, outcome = tp("T-1105", "沪G-T1105", 4, "2026-10-27 08:52", 121.47060, 31.23010)
        assert outcome == "conflict"                    # 同序列不同哈希：两份载荷都保留
        tp("T-1105", "沪G-T1105", 5, "2026-10-27 08:54", 121.4705, 31.2304,
           delayed=True, received="2026-10-27 09:40")
        tp("T-1105", "沪G-T1105", 6, "2026-10-27 08:56", 121.4708, 31.2306,
           delayed=True, received="2026-10-27 09:42")
        spacetime.evaluate_spacetime(db, b11, "AREA-FAC01")      # uncertifiable（R7 载荷冲突阻断）
        path_inference.infer_paths(db, b11, "AREA-FAC01")        # 候选保留双载荷分支，不洗成可信

        # ================ 场景⑫ 受限（轨迹完整但含采样空洞，跳速保持未知） ================
        b12 = st_chain("PC-20261027-012", "沪G-T1206", "2026-10-27")
        track.register_terminal(db, terminal_id="T-1206", vehicle_id="沪G-T1206",
                                install_at=dt("2026-09-01 00:00"), note="通信信号一般的车载终端")
        for seq, (when, lon, lat) in enumerate([
            ("2026-10-27 08:46", 121.4662, 31.2299),
            ("2026-10-27 08:52", 121.47015, 31.23005),   # 与上点间隔 6 分钟：采样空洞
            ("2026-10-27 08:54", 121.4705, 31.2304),
            ("2026-10-27 08:56", 121.4708, 31.2306),
        ], start=1):
            tp("T-1206", "沪G-T1206", seq, when, lon, lat, acc=8.0)
        spacetime.evaluate_spacetime(db, b12, "AREA-FAC01")      # limited（仅 R2/R3 warn）
        path_inference.infer_paths(db, b12, "AREA-FAC01")        # 道路模型在空洞内有真实边，候选无 gaps

        db.commit()
        print(
            "[seed] 演示数据已写入：6 个重量验收场景 + 6 个时空证据场景（"
            "可信/受限/低精度待核/双走廊待核/退役终端不可认证/补传冲突不可认证）、"
            "6 个地理区域、1 个道路模型、7 台终端注册、2 个测试账号"
        )
    finally:
        db.close()


if __name__ == "__main__":
    from app.db import init_db

    init_db()
    seed()
