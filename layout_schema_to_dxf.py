"""
最小可行方案：结构化机房布局 JSON -> DXF 图纸
- 大模型只负责输出符合 schema 的 JSON（不直接写 DXF）
- 本脚本负责几何生成、规则校验、DXF 落盘（ezdxf）
用法: python layout_schema_to_dxf.py spec.json out.dxf
"""
import json, sys
import ezdxf
from ezdxf.enums import TextEntityAlignment

# ---------- 1. 规则校验（对应 GB 50174-2017 的部分硬约束，可扩展） ----------
RULES = {
    "cold_aisle_min": 1200,   # 冷通道净宽 ≥1.2 m
    "hot_aisle_min": 1000,    # 热通道净宽 ≥1.0 m
    "rack_to_wall_min": 1000, # 机柜列端到墙 ≥1.0 m（主通道）
}

def check(spec):
    errs = []
    W, D = spec["room"]["width"], spec["room"]["depth"]
    rows = spec["rack_rows"]
    for r in rows:
        x0 = r["x"]; x1 = r["x"] + r["count"] * r["rack_w"]
        if x0 < RULES["rack_to_wall_min"] or W - x1 < RULES["rack_to_wall_min"]:
            errs.append(f"行{r['id']}: 列端到墙距离不足 {RULES['rack_to_wall_min']}mm")
    rows_sorted = sorted(rows, key=lambda r: r["y"])
    for a, b in zip(rows_sorted, rows_sorted[1:]):
        gap = b["y"] - (a["y"] + a["rack_d"])
        need = RULES["cold_aisle_min"] if a["face"] == "N" and b["face"] == "S" else RULES["hot_aisle_min"]
        if gap < need:
            errs.append(f"行{a['id']}-{b['id']} 通道净宽 {gap}mm < {need}mm")
    return errs

# ---------- 2. 几何生成 ----------
def build(spec, path):
    doc = ezdxf.new("R2018")
    doc.units = ezdxf.units.MM
    for name, color in [("WALL", 7), ("RACK", 4), ("AISLE", 3), ("TEXT", 2), ("DIM", 1)]:
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    W, D = spec["room"]["width"], spec["room"]["depth"]

    # 墙体：内外双线
    t = spec["room"].get("wall_t", 200)
    msp.add_lwpolyline([(0, 0), (W, 0), (W, D), (0, D)], close=True, dxfattribs={"layer": "WALL"})
    msp.add_lwpolyline([(-t, -t), (W + t, -t), (W + t, D + t), (-t, D + t)], close=True, dxfattribs={"layer": "WALL"})

    # 机柜块定义
    blk = doc.blocks.new(name="RACK600x1200")
    blk.add_lwpolyline([(0, 0), (600, 0), (600, 1200), (0, 1200)], close=True)
    blk.add_line((0, 0), (600, 1200)); blk.add_line((0, 1200), (600, 0))  # 对角线示意

    for r in spec["rack_rows"]:
        for i in range(r["count"]):
            x = r["x"] + i * r["rack_w"]
            ref = msp.add_blockref("RACK600x1200", (x, r["y"]), dxfattribs={"layer": "RACK"})
            ref.set_attrib = None
            msp.add_text(f"{r['id']}{i+1:02d}", height=120, dxfattribs={"layer": "TEXT"}
                         ).set_placement((x + 300, r["y"] + 600), align=TextEntityAlignment.MIDDLE_CENTER)
        # 行尺寸标注
        msp.add_linear_dim(base=(r["x"], r["y"] - 400), p1=(r["x"], r["y"]),
                           p2=(r["x"] + r["count"] * r["rack_w"], r["y"]),
                           dxfattribs={"layer": "DIM"}).render()

    # 通道标注
    rows_sorted = sorted(spec["rack_rows"], key=lambda r: r["y"])
    for a, b in zip(rows_sorted, rows_sorted[1:]):
        y0, y1 = a["y"] + a["rack_d"], b["y"]
        kind = "冷通道" if a["face"] == "N" and b["face"] == "S" else "热通道"
        msp.add_lwpolyline([(a["x"], y0), (a["x"] + a["count"] * a["rack_w"], y0),
                            (a["x"] + a["count"] * a["rack_w"], y1), (a["x"], y1)],
                           close=True, dxfattribs={"layer": "AISLE", "linetype": "DASHED"} if "DASHED" in doc.linetypes else {"layer": "AISLE"})
        msp.add_text(f"{kind} {y1-y0}mm", height=150, dxfattribs={"layer": "TEXT"}
                     ).set_placement(((a["x"] + a["x"] + a["count"] * a["rack_w"]) / 2, (y0 + y1) / 2),
                                     align=TextEntityAlignment.MIDDLE_CENTER)

    # 总体尺寸
    msp.add_linear_dim(base=(0, -1200), p1=(0, 0), p2=(W, 0), dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(-1200, 0), p1=(0, 0), p2=(0, D), angle=90, dxfattribs={"layer": "DIM"}).render()
    msp.add_text(spec.get("title", "机房平面布置图"), height=300, dxfattribs={"layer": "TEXT"}
                 ).set_placement((W / 2, D + 800), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(path)

if __name__ == "__main__":
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    errs = check(spec)
    if errs:
        print("规则校验未通过:\n  " + "\n  ".join(errs)); sys.exit(1)
    build(spec, sys.argv[2])
    print("OK ->", sys.argv[2])
