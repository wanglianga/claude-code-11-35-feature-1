"""轻量几何工具：球面距离、射线法点面判断、精度感知的区域交集。

不引入 GIS 重依赖，全部基于 GeoJSON（Point / Polygon）与 haversine 距离，
精度半径（米）参与交集判定：低精度点在多边形边缘只能给出“待核”，不能断言进出。
"""

import math

EARTH_RADIUS_M = 6371009.0


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """两点球面距离（米）。"""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def point_in_polygon(lon: float, lat: float, polygon: list[list[list[float]]]) -> bool:
    """射线法判断点是否在多边形内（polygon 为 GeoJSON coordinates，含外环与洞）。"""
    if not polygon:
        return False

    def in_ring(ring):
        inside = False
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            intersects = ((yi > lat) != (yj > lat)) and (
                lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
            )
            if intersects:
                inside = not inside
            j = i
        return inside

    if not in_ring(polygon[0]):
        return False
    for hole in polygon[1:]:  # 洞内不算
        if in_ring(hole):
            return False
    return True


def _point_to_segment_m(px, py, ax, ay, bx, by) -> float:
    """点到线段的最近距离（米，先在经纬度平面投影再用球面距离近似）。"""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return haversine_m(px, py, ax, ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return haversine_m(px, py, qx, qy)


def distance_to_polygon_boundary_m(lon: float, lat: float, polygon: list[list[list[float]]]) -> float:
    best = float("inf")
    for ring in polygon:
        n = len(ring)
        for i in range(n - 1):
            ax, ay = ring[i]
            bx, by = ring[i + 1]
            best = min(best, _point_to_segment_m(lon, lat, ax, ay, bx, by))
    return best


def polygon_centroid(polygon: list[list[list[float]]]) -> tuple[float, float]:
    ring = polygon[0]
    lon = sum(p[0] for p in ring[:-1]) / max(1, len(ring) - 1)
    lat = sum(p[1] for p in ring[:-1]) / max(1, len(ring) - 1)
    return lon, lat


def area_intersection(area, lon: float, lat: float, accuracy_m: float) -> dict:
    """精度感知的区域交集。

    返回 relation：
    - inside：确定在内（到边界余量大于精度半径）
    - outside：确定在外（到边界距离大于精度半径）
    - edge_uncertain：低精度边缘，精度圆覆盖边界，只能待核
    以及到边界/圆心的距离与判定余量。
    """
    geom = area.geometry or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")

    if gtype == "Point":
        cx, cy = coords[0], coords[1]
        radius = float(area.buffer_radius_m or 0.0)
        d = haversine_m(lon, lat, cx, cy)
        margin = radius - d  # >0 在内
        relation = "inside" if margin >= 0 else "outside"
        if abs(margin) < accuracy_m:
            relation = "edge_uncertain"
        return {
            "relation": relation,
            "distance_m": round(d, 2),
            "margin_m": round(margin, 2),
            "accuracy_m": accuracy_m,
        }

    if gtype == "Polygon":
        inside = point_in_polygon(lon, lat, coords)
        d = distance_to_polygon_boundary_m(lon, lat, coords)
        if d < accuracy_m:
            relation = "edge_uncertain"
        else:
            relation = "inside" if inside else "outside"
        return {
            "relation": relation,
            "distance_m": round(d, 2),
            "margin_m": round(d, 2),
            "accuracy_m": accuracy_m,
        }

    return {"relation": "edge_uncertain", "distance_m": None, "margin_m": None, "accuracy_m": accuracy_m}
