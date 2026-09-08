"""
层级扩展：园区(campus) -> 楼栋/楼层(facility) -> 房间(room: IT机房 / 设备间 / 制冷间 ...)
同一套"JSON -> 规则校验 -> ezdxf"范式，逐级出图：
  campus.dxf   园区总平面：用地红线、建筑轮廓、消防车道、变电站/冷冻站等
  <bld>_<floor>.dxf  楼层平面：IT机房、电力电池室(设备间)、制冷间、走廊；IT机房内自动排柜，制冷间内自动排冷机
用法: python site_schema_to_dxf.py examples/site_example.json outdir/
"""
import json, sys, pathlib
import ezdxf
from ezdxf.enums import TextEntityAlignment
from layout_schema_to_dxf import check as check_room, build as build_room, RULES as ROOM_RULES

# ---------- 规则（单位 mm；标注"示例"的阈值需按项目适用规范核定） ----------
RULES = {
    "fire_lane_min": 4000,          # 消防车道净宽 ≥4 m（GB 50016-2014(2018) 7.1.8）
    "building_gap_min": 6000,       # 建筑最小间距（示例，按耐火等级/高度核定）
    "setback_min": 5000,            # 建筑退红线（示例）
    "chiller_service_gap_min": 1000,# 冷机之间/到墙检修净距（示例）
    "battery_room_min_area": 20e6,  # 电池室最小面积 20 m²（示例）
    "it_hall_rack_density_max": 0.6,# 机柜占地/机房面积上限（示例，保证通道与设备间）
}
ROOM_TYPES = {"it_hall": "IT机房", "power_room": "电力电池室", "cooling_plant": "制冷间",
              "equipment_room": "设备间", "corridor": "走廊", "office": "辅助用房"}


def _rect_gap(a, b):
    """两矩形 (x,y,w,h) 的最小轴向间距；相交为负"""
    dx = max(b["x"] - (a["x"] + a["w"]), a["x"] - (b["x"] + b["w"]))
    dy = max(b["y"] - (a["y"] + a["h"]), a["y"] - (b["y"] + b["h"]))
    return max(dx, dy)


# ---------- 园区级校验 ----------
def check_campus(site):
    errs = []
    S = site["site"]
    for l in site.get("fire_lanes", []):
        if l["width"] < RULES["fire_lane_min"]:
            errs.append(f"消防车道{l['id']}净宽 {l['width']}mm < {RULES['fire_lane_min']}mm")
    blds = site["buildings"]
    for b in blds:
        if (b["x"] < RULES["setback_min"] or b["y"] < RULES["setback_min"] or
                S["width"] - (b["x"] + b["w"]) < RULES["setback_min"] or S["depth"] - (b["y"] + b["h"]) < RULES["setback_min"]):
            errs.append(f"建筑{b['id']}退红线不足 {RULES['setback_min']}mm")
    for i, a in enumerate(blds):
        for b in blds[i + 1:]:
            g = _rect_gap(a, b)
            if g < RULES["building_gap_min"]:
                errs.append(f"建筑{a['id']}-{b['id']}间距 {g}mm < {RULES['building_gap_min']}mm")
    return errs


# ---------- 楼层级校验 ----------
def check_floor(floor):
    errs = []
    rooms = floor["rooms"]
    for i, a in enumerate(rooms):
        for b in rooms[i + 1:]:
            if _rect_gap(a, b) < 0:
                errs.append(f"房间{a['id']}与{b['id']}重叠")
    for r in rooms:
        if r["type"] == "power_room" and r["w"] * r["h"] < RULES["battery_room_min_area"]:
            errs.append(f"电池室{r['id']}面积 {r['w']*r['h']/1e6:.1f}m² 过小")
        if r["type"] == "cooling_plant":
            n, cw, cd = r.get("chillers", 0), r.get("chiller_w", 2000), r.get("chiller_d", 4000)
            need = n * cw + (n + 1) * RULES["chiller_service_gap_min"]
            if n and need > r["w"]:
                errs.append(f"制冷间{r['id']}宽度 {r['w']}mm 不足以布置 {n} 台冷机（需 {need}mm）")
            if n and cd + 2 * RULES["chiller_service_gap_min"] > r["h"]:
                errs.append(f"制冷间{r['id']}进深不足")
    return errs


# ---------- 自动生成 IT 机房内的机柜行（复用房间级引擎） ----------
def it_hall_to_room_spec(r):
    cold, hot = r.get("cold_aisle", 1200), r.get("hot_aisle", 1000)
    rack_w, rack_d = 600, 1200
    margin = ROOM_RULES["rack_to_wall_min"] + 500
    right = margin + 2000 + 600  # 东墙 AHU 进深 + CDU
    cnt = int((r["w"] - margin - 600 - right) // rack_w)
    rows, y, i = [], max(margin, 1500) + 300, 0
    while y + rack_d + margin <= r["h"]:
        rows.append({"id": chr(65 + i), "x": margin + 600, "y": y, "count": cnt,
                     "rack_w": rack_w, "rack_d": rack_d, "face": "N" if i % 2 == 0 else "S"})
        y += rack_d + (cold if i % 2 == 0 else hot); i += 1
    if rows and (rows[-1]["y"] + rack_d + margin > r["h"]):
        rows.pop()
    for row in rows:
        row["row_head"] = r.get("row_head", True); row["cdu"] = r.get("cdu", False)
    spec = {"title": f"{r['id']} {ROOM_TYPES['it_hall']}平面布置图",
            "room": {"width": r["w"], "depth": r["h"], "wall_t": 200}, "rack_rows": rows,
            "main_aisle": max(margin, 1500),
            "doors": r.get("doors", [{"wall": "S", "pos": 1500, "width": 1500}, {"wall": "S", "pos": r["w"] - 3000, "width": 1500}]),
            "ahu": r.get("ahu", {"count": max(2, r["h"] // 4000), "side": "E"}),
            "fire": {"gas": True}}
    dens = len(rows) * cnt * rack_w * rack_d / (r["w"] * r["h"])
    return spec, dens


# ---------- 绘图 ----------
def _layers(doc):
    for name, color in [("SITE", 7), ("BUILDING", 5), ("ROAD", 8), ("FIRE_LANE", 1), ("UTILITY", 6),
                        ("ROOM", 5), ("RACK", 4), ("CHILLER", 6), ("BATTERY", 30), ("TEXT", 7), ("DIM", 1)]:
        if name not in doc.layers:
            doc.layers.add(name, color=color)


def _rect(msp, x, y, w, h, layer, close=True):
    return msp.add_lwpolyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], close=close, dxfattribs={"layer": layer})


def _label(msp, x, y, t, h=500):
    msp.add_text(t, height=h, dxfattribs={"layer": "TEXT"}).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)


def build_campus(site, path):
    doc = ezdxf.new("R2018"); doc.units = ezdxf.units.MM; _layers(doc); msp = doc.modelspace()
    S = site["site"]
    _rect(msp, 0, 0, S["width"], S["depth"], "SITE")
    _label(msp, S["width"] / 2, S["depth"] + 3000, site.get("title", "园区总平面图"), 1500)
    for l in site.get("fire_lanes", []):
        if l["dir"] == "H":
            _rect(msp, l["x"], l["y"], l["length"], l["width"], "FIRE_LANE")
            _label(msp, l["x"] + l["length"] / 2, l["y"] + l["width"] / 2, f"消防车道 {l['width']/1000:.1f}m", 1200)
        else:
            _rect(msp, l["x"], l["y"], l["width"], l["length"], "FIRE_LANE")
            _label(msp, l["x"] + l["width"] / 2, l["y"] + l["length"] / 2, f"消防车道 {l['width']/1000:.1f}m", 1200)
    for b in site["buildings"]:
        layer = "UTILITY" if b.get("kind") in ("substation", "cooling_station", "diesel") else "BUILDING"
        _rect(msp, b["x"], b["y"], b["w"], b["h"], layer)
        _label(msp, b["x"] + b["w"] / 2, b["y"] + b["h"] / 2, f"{b['name']} {b.get('floors',1)}F", 1500)
    msp.add_linear_dim(base=(0, -4000), p1=(0, 0), p2=(S["width"], 0), dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(-4000, 0), p1=(0, 0), p2=(0, S["depth"]), angle=90, dxfattribs={"layer": "DIM"}).render()
    doc.saveas(path)


def build_floor(floor, path, outdir):
    doc = ezdxf.new("R2018"); doc.units = ezdxf.units.MM; _layers(doc); msp = doc.modelspace()
    W, D = floor["width"], floor["depth"]
    _rect(msp, 0, 0, W, D, "BUILDING")
    _rect(msp, -300, -300, W + 600, D + 600, "BUILDING")
    _label(msp, W / 2, D + 2000, floor.get("title", "楼层平面图"), 1000)
    # 房间块定义
    blk = doc.blocks.new(name="CHILLER"); blk.add_lwpolyline([(0, 0), (2000, 0), (2000, 4000), (0, 4000)], close=True)
    blk.add_circle((1000, 1000), 600); blk.add_circle((1000, 3000), 600)
    bat = doc.blocks.new(name="BATTERY_RACK"); bat.add_lwpolyline([(0, 0), (800, 0), (800, 600), (0, 600)], close=True)
    for r in floor["rooms"]:
        _rect(msp, r["x"], r["y"], r["w"], r["h"], "ROOM")
        _label(msp, r["x"] + r["w"] / 2, r["y"] + r["h"] - 600, f"{r['id']} {ROOM_TYPES.get(r['type'], r['type'])} {r['w']*r['h']/1e6:.0f}m²", 400)
        if r["type"] == "it_hall":
            spec, dens = it_hall_to_room_spec(r)
            for row in spec["rack_rows"]:
                for i in range(row["count"]):
                    x = r["x"] + row["x"] + i * row["rack_w"]; y = r["y"] + row["y"]
                    _rect(msp, x, y, row["rack_w"], row["rack_d"], "RACK")
            # 同时输出房间级详图
            errs = check_room(spec)
            if not errs:
                build_room(spec, str(pathlib.Path(outdir) / f"room_{r['id']}.dxf"))
        elif r["type"] == "cooling_plant":
            n, cw = r.get("chillers", 0), r.get("chiller_w", 2000)
            gap = (r["w"] - n * cw) / (n + 1) if n else 0
            for i in range(n):
                msp.add_blockref("CHILLER", (r["x"] + gap + i * (cw + gap), r["y"] + RULES["chiller_service_gap_min"]), dxfattribs={"layer": "CHILLER"})
        elif r["type"] == "power_room":
            cols = int((r["w"] - 2000) // 1000); rows = int((r["h"] - 2000) // 1400)
            for i in range(cols):
                for j in range(rows):
                    msp.add_blockref("BATTERY_RACK", (r["x"] + 1000 + i * 1000, r["y"] + 1000 + j * 1400), dxfattribs={"layer": "BATTERY"})
    msp.add_linear_dim(base=(0, -2500), p1=(0, 0), p2=(W, 0), dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(-2500, 0), p1=(0, 0), p2=(0, D), angle=90, dxfattribs={"layer": "DIM"}).render()
    doc.saveas(path)


def run(site, outdir):
    outdir = pathlib.Path(outdir); outdir.mkdir(parents=True, exist_ok=True)
    errs = check_campus(site)
    if errs:
        return {"level": "campus", "errors": errs}
    build_campus(site, str(outdir / "campus.dxf"))
    report = {"campus": "campus.dxf", "floors": [], "rooms": []}
    for b in site["buildings"]:
        for f in b.get("floor_plans", []):
            fe = check_floor(f)
            if fe:
                return {"level": f"floor {b['id']}/{f['id']}", "errors": fe}
            name = f"{b['id']}_{f['id']}.dxf"
            build_floor(f, str(outdir / name), outdir)
            report["floors"].append(name)
            for r in f["rooms"]:
                if r["type"] == "it_hall":
                    spec, dens = it_hall_to_room_spec(r)
                    report["rooms"].append({"id": r["id"], "racks": sum(x["count"] for x in spec["rack_rows"]),
                                            "rows": len(spec["rack_rows"]), "density": round(dens, 2)})
    return report


if __name__ == "__main__":
    site = json.load(open(sys.argv[1], encoding="utf-8"))
    print(json.dumps(run(site, sys.argv[2]), ensure_ascii=False, indent=2))
