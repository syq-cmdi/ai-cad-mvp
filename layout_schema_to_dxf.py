"""
房间级：结构化机房布局 JSON -> DXF 图纸
- 大模型只负责输出符合 schema 的 JSON（不直接写 DXF）
- 本脚本负责几何生成、规则校验、DXF 落盘（ezdxf）
要素：机柜行 / 冷热通道 / 列头柜 / CDU(液冷分配单元) / AHU(精密空调) / 门 / 柱网 /
      消防疏散主通道 / 灭火器点位 / 气体灭火喷头 / 疏散指示 / 尺寸标注
用法: python layout_schema_to_dxf.py spec.json out.dxf
"""
import json, sys, math
import ezdxf
from ezdxf.enums import TextEntityAlignment

# ---------- 1. 规则（对应 GB 50174-2017 等的硬约束；标"示例"者需按项目核定） ----------
RULES = {
    "cold_aisle_min": 1200,        # 面对面机柜正面间距（冷通道）≥1.2 m  GB 50174 4.3
    "hot_aisle_min": 1000,         # 背对背机柜背面间距（热通道）≥1.0 m  GB 50174 4.3
    "rack_to_wall_min": 1000,      # 机柜列端到墙 ≥1.0 m                 GB 50174 4.3
    "main_aisle_min": 1500,        # 搬运设备/疏散主通道净宽 ≥1.5 m       GB 50174 4.3
    "two_exits_area": 100e6,       # 面积>100 m² 的主机房安全出口不少于 2 个 GB 50174 13.2
    "ahu_service_gap": 800,        # 精密空调之间/到墙检修净距（示例）
    "extinguisher_per_door": 2,    # 每个安全出口附近灭火器数量（示例）
    "gas_nozzle_spacing": 3600,    # 气体灭火喷头布置间距（示例）
}
DEF = {"rack_w": 600, "rack_d": 1200, "ahu_w": 1000, "ahu_d": 2000, "cdu_w": 600, "cdu_d": 1200, "head_w": 600}


def _rows_sorted(spec):
    return sorted(spec["rack_rows"], key=lambda r: r["y"])


def check(spec):
    errs = []
    W, D = spec["room"]["width"], spec["room"]["depth"]
    rows = spec["rack_rows"]
    for r in rows:
        x0 = r["x"] - (DEF["head_w"] if r.get("row_head") else 0)
        x1 = r["x"] + r["count"] * r["rack_w"] + (DEF["cdu_w"] if r.get("cdu") else 0)
        if x0 < RULES["rack_to_wall_min"] or W - x1 < RULES["rack_to_wall_min"]:
            errs.append(f"行{r['id']}: 列端到墙距离不足 {RULES['rack_to_wall_min']}mm")
    rs = _rows_sorted(spec)
    for a, b in zip(rs, rs[1:]):
        gap = b["y"] - (a["y"] + a["rack_d"])
        need = RULES["cold_aisle_min"] if a["face"] == "N" and b["face"] == "S" else RULES["hot_aisle_min"]
        if gap < need:
            errs.append(f"行{a['id']}-{b['id']} 通道净宽 {gap}mm < {need}mm")
    # 疏散主通道：机柜区到有门的墙之间
    ma = spec.get("main_aisle", RULES["main_aisle_min"])
    if ma < RULES["main_aisle_min"]:
        errs.append(f"疏散主通道 {ma}mm < {RULES['main_aisle_min']}mm")
    if rs:
        if rs[0]["y"] < ma:
            errs.append(f"机柜区到南墙疏散主通道 {rs[0]['y']}mm < {ma}mm")
    # 安全出口数量
    doors = spec.get("doors", [])
    if W * D > RULES["two_exits_area"] and len(doors) < 2:
        errs.append(f"主机房面积 {W*D/1e6:.0f}m² > 100m²，安全出口应 ≥2 个（当前 {len(doors)}）")
    # AHU 能否沿指定墙布置
    ahu = spec.get("ahu")
    if ahu and ahu.get("count"):
        side_len = D if ahu.get("side", "W") in ("W", "E") else W
        need = ahu["count"] * DEF["ahu_w"] + (ahu["count"] + 1) * RULES["ahu_service_gap"]
        if need > side_len:
            errs.append(f"AHU {ahu['count']} 台沿 {ahu.get('side','W')} 墙布置需 {need}mm > 墙长 {side_len}mm")
        # AHU 占位与机柜区冲突（W/E 墙）
        if ahu.get("side", "W") in ("W", "E") and rows:
            xmin = min(r["x"] - (DEF["head_w"] if r.get("row_head") else 0) for r in rows)
            xmax = max(r["x"] + r["count"] * r["rack_w"] + (DEF["cdu_w"] if r.get("cdu") else 0) for r in rows)
            if ahu.get("side") == "W" and xmin < DEF["ahu_d"] + RULES["rack_to_wall_min"]:
                errs.append("西墙 AHU 与机柜列端冲突，列端应留出 AHU 进深+1.0 m")
            if ahu.get("side") == "E" and W - xmax < DEF["ahu_d"] + RULES["rack_to_wall_min"]:
                errs.append("东墙 AHU 与机柜列端冲突，列端应留出 AHU 进深+1.0 m")
    return errs


# ---------- 2. 几何生成 ----------
LAYERS = [("WALL", 7), ("COLUMN", 8), ("RACK", 4), ("POWER", 30), ("CDU", 6), ("AHU", 5), ("AISLE", 3),
          ("EVAC", 1), ("FIRE", 1), ("DOOR", 2), ("TEXT", 7), ("DIM", 1)]


def _blocks(doc):
    b = doc.blocks.new(name="RACK600x1200"); b.add_lwpolyline([(0, 0), (600, 0), (600, 1200), (0, 1200)], close=True)
    b.add_line((0, 0), (600, 1200)); b.add_line((0, 1200), (600, 0))
    b = doc.blocks.new(name="ROW_HEAD"); b.add_lwpolyline([(0, 0), (600, 0), (600, 1200), (0, 1200)], close=True)
    b.add_text("PD", height=200).set_placement((300, 600), align=TextEntityAlignment.MIDDLE_CENTER)
    b = doc.blocks.new(name="CDU"); b.add_lwpolyline([(0, 0), (600, 0), (600, 1200), (0, 1200)], close=True)
    b.add_circle((300, 400), 180); b.add_circle((300, 800), 180)
    b.add_text("CDU", height=150).set_placement((300, 1100), align=TextEntityAlignment.MIDDLE_CENTER)
    b = doc.blocks.new(name="AHU"); b.add_lwpolyline([(0, 0), (1000, 0), (1000, 2000), (0, 2000)], close=True)
    b.add_circle((500, 700), 300); b.add_line((200, 1400), (800, 1400)); b.add_line((200, 1600), (800, 1600))
    b.add_text("AHU", height=180).set_placement((500, 1850), align=TextEntityAlignment.MIDDLE_CENTER)
    b = doc.blocks.new(name="EXTINGUISHER"); b.add_circle((0, 0), 150); b.add_text("灭", height=160).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)
    b = doc.blocks.new(name="GAS_NOZZLE"); b.add_circle((0, 0), 100); b.add_line((-140, -140), (140, 140)); b.add_line((-140, 140), (140, -140))
    b = doc.blocks.new(name="EXIT_SIGN"); b.add_lwpolyline([(-300, 0), (300, 0), (300, 200), (-300, 200)], close=True)
    b.add_text("EXIT", height=120).set_placement((0, 100), align=TextEntityAlignment.MIDDLE_CENTER)


def _txt(msp, x, y, t, h=150, layer="TEXT"):
    msp.add_text(t, height=h, dxfattribs={"layer": layer}).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)


def build(spec, path):
    doc = ezdxf.new("R2018"); doc.units = ezdxf.units.MM
    for name, color in LAYERS:
        doc.layers.add(name, color=color)
    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern=[600, 300, -300])
    _blocks(doc)
    msp = doc.modelspace()
    W, D = spec["room"]["width"], spec["room"]["depth"]
    t = spec["room"].get("wall_t", 200)

    # 墙体
    msp.add_lwpolyline([(0, 0), (W, 0), (W, D), (0, D)], close=True, dxfattribs={"layer": "WALL"})
    msp.add_lwpolyline([(-t, -t), (W + t, -t), (W + t, D + t), (-t, D + t)], close=True, dxfattribs={"layer": "WALL"})

    # 柱网（可选）
    grid = spec.get("columns")
    if grid:
        s = grid.get("size", 600)
        for i in range(grid.get("nx", 0) + 1):
            for j in range(grid.get("ny", 0) + 1):
                cx, cy = grid.get("x0", 0) + i * grid["dx"], grid.get("y0", 0) + j * grid["dy"]
                if 0 <= cx <= W and 0 <= cy <= D:
                    msp.add_lwpolyline([(cx - s / 2, cy - s / 2), (cx + s / 2, cy - s / 2), (cx + s / 2, cy + s / 2), (cx - s / 2, cy + s / 2)],
                                       close=True, dxfattribs={"layer": "COLUMN"})

    # 门 + 疏散指示 + 灭火器
    for d in spec.get("doors", []):
        w = d.get("width", 1500); wall = d.get("wall", "S"); p = d["pos"]
        if wall in ("S", "N"):
            y = 0 if wall == "S" else D
            msp.add_line((p, y), (p + w, y), dxfattribs={"layer": "DOOR"})
            msp.add_arc((p, y), w, 0 if wall == "S" else 270, 90 if wall == "S" else 360, dxfattribs={"layer": "DOOR"})
            msp.add_blockref("EXIT_SIGN", (p + w / 2, y + (300 if wall == "S" else -300)), dxfattribs={"layer": "FIRE"})
            for k in range(RULES["extinguisher_per_door"]):
                msp.add_blockref("EXTINGUISHER", (p - 400 + k * (w + 800), y + (400 if wall == "S" else -400)), dxfattribs={"layer": "FIRE"})
        else:
            x = 0 if wall == "W" else W
            msp.add_line((x, p), (x, p + w), dxfattribs={"layer": "DOOR"})
            msp.add_arc((x, p), w, 0 if wall == "W" else 90, 90 if wall == "W" else 180, dxfattribs={"layer": "DOOR"})
            msp.add_blockref("EXIT_SIGN", (x + (400 if wall == "W" else -400), p + w / 2), dxfattribs={"layer": "FIRE"})
            for k in range(RULES["extinguisher_per_door"]):
                msp.add_blockref("EXTINGUISHER", (x + (400 if wall == "W" else -400), p - 400 + k * (w + 800)), dxfattribs={"layer": "FIRE"})

    # 机柜行 / 列头柜 / CDU / 行尺寸
    for r in spec["rack_rows"]:
        for i in range(r["count"]):
            x = r["x"] + i * r["rack_w"]
            msp.add_blockref("RACK600x1200", (x, r["y"]), dxfattribs={"layer": "RACK"})
            _txt(msp, x + 300, r["y"] + 600, f"{r['id']}{i+1:02d}", 120)
        if r.get("row_head"):
            msp.add_blockref("ROW_HEAD", (r["x"] - DEF["head_w"], r["y"]), dxfattribs={"layer": "POWER"})
        if r.get("cdu"):
            msp.add_blockref("CDU", (r["x"] + r["count"] * r["rack_w"], r["y"]), dxfattribs={"layer": "CDU"})
        msp.add_linear_dim(base=(r["x"], r["y"] - 400), p1=(r["x"], r["y"]),
                           p2=(r["x"] + r["count"] * r["rack_w"], r["y"]), dxfattribs={"layer": "DIM"}).render()

    # 冷热通道
    rs = _rows_sorted(spec)
    for a, b in zip(rs, rs[1:]):
        y0, y1 = a["y"] + a["rack_d"], b["y"]
        kind = "冷通道" if a["face"] == "N" and b["face"] == "S" else "热通道"
        x0, x1 = a["x"], a["x"] + a["count"] * a["rack_w"]
        msp.add_lwpolyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], close=True, dxfattribs={"layer": "AISLE", "linetype": "DASHED"})
        _txt(msp, (x0 + x1) / 2, (y0 + y1) / 2, f"{kind} {y1-y0}mm", 150)

    # 疏散主通道（机柜区南侧到南墙，虚线标识）
    if rs:
        ma = spec.get("main_aisle", RULES["main_aisle_min"])
        msp.add_lwpolyline([(0, 0), (W, 0), (W, ma), (0, ma)], close=True, dxfattribs={"layer": "EVAC", "linetype": "DASHED"})
        _txt(msp, W / 2, ma / 2, f"疏散/搬运主通道 ≥{ma}mm", 180, "EVAC")
        # 机柜区两侧到东西墙
        xmin = min(r["x"] - (DEF["head_w"] if r.get("row_head") else 0) for r in rs)
        xmax = max(r["x"] + r["count"] * r["rack_w"] + (DEF["cdu_w"] if r.get("cdu") else 0) for r in rs)
        ytop = max(r["y"] + r["rack_d"] for r in rs)
        for xa, xb in ((0, xmin), (xmax, W)):
            msp.add_lwpolyline([(xa, ma), (xb, ma), (xb, ytop), (xa, ytop)], close=True, dxfattribs={"layer": "EVAC", "linetype": "DASHED"})

    # AHU 沿墙布置
    ahu = spec.get("ahu")
    if ahu and ahu.get("count"):
        n, side = ahu["count"], ahu.get("side", "W")
        L = D if side in ("W", "E") else W
        gap = (L - n * DEF["ahu_w"]) / (n + 1)
        for i in range(n):
            p = gap + i * (DEF["ahu_w"] + gap)
            if side == "W":
                msp.add_blockref("AHU", (0, p + DEF["ahu_w"]), dxfattribs={"layer": "AHU", "rotation": -90})
            elif side == "E":
                msp.add_blockref("AHU", (W, p), dxfattribs={"layer": "AHU", "rotation": 90})
            elif side == "N":
                msp.add_blockref("AHU", (p + DEF["ahu_w"], D), dxfattribs={"layer": "AHU", "rotation": 180})
            else:
                msp.add_blockref("AHU", (p, 0), dxfattribs={"layer": "AHU"})

    # 气体灭火喷头网格
    if spec.get("fire", {}).get("gas", True):
        sp = RULES["gas_nozzle_spacing"]
        nx, ny = max(1, round(W / sp)), max(1, round(D / sp))
        for i in range(nx):
            for j in range(ny):
                msp.add_blockref("GAS_NOZZLE", (W * (i + 0.5) / nx, D * (j + 0.5) / ny), dxfattribs={"layer": "FIRE"})

    # 总尺寸、标题、图例
    msp.add_linear_dim(base=(0, -1200), p1=(0, 0), p2=(W, 0), dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(-1200, 0), p1=(0, 0), p2=(0, D), angle=90, dxfattribs={"layer": "DIM"}).render()
    _txt(msp, W / 2, D + 800, spec.get("title", "机房平面布置图"), 300)
    status = spec.get("meta", {}).get("status", "AI草图-待审核")
    _txt(msp, W / 2, D + 400, f"[{status}] rev {spec.get('meta', {}).get('rev', 0)}", 150)
    doc.saveas(path)


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    errs = check(spec)
    if errs:
        print("规则校验未通过:\n  " + "\n  ".join(errs)); sys.exit(1)
    build(spec, sys.argv[2])
    print("OK ->", sys.argv[2])
