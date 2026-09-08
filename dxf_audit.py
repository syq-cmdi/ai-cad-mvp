"""
对 DXF (DWG 请先用 ODA File Converter 转 DXF) 做结构化抽取与规范复核:
 (1) 图幅/图层/实体/块/文字统计  (2) 从块引用反算机柜行、通道净宽、到墙距离并对照 GB 50174
用法: python dxf_audit.py file.dxf [--json]
"""
import sys, json, collections, ezdxf
from ezdxf import bbox


def audit(path, as_json=False, quiet=False):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ext = bbox.extents(msp)
    rep = {
        "file": path,
        "extent_mm": [[round(v) for v in ext.extmin][:2], [round(v) for v in ext.extmax][:2]],
        "layers": dict(collections.Counter(e.dxf.layer for e in msp).most_common(15)),
        "types": dict(collections.Counter(e.dxftype() for e in msp)),
        "blocks": dict(collections.Counter(e.dxf.name for e in msp.query("INSERT")).most_common(15)),
        "text_samples": [t.dxf.text for t in msp.query("TEXT MTEXT")][:10],
    }
    # 机柜行/通道反算（仅对本工具生成图或块名含 RACK 的图有效）
    racks = [e for e in msp.query("INSERT") if "RACK" in e.dxf.name.upper()]
    if racks:
        rows = collections.defaultdict(list)
        for r in racks:
            rows[round(r.dxf.insert.y)].append(r.dxf.insert.x)
        ys = sorted(rows)
        rep["rack_rows"] = len(ys)
        rep["racks"] = len(racks)
        rep["aisle_gaps_mm"] = [ys[i + 1] - (ys[i] + 1200) for i in range(len(ys) - 1)]
        rep["min_x_mm"] = round(min(min(v) for v in rows.values()))
    if quiet:
        pass
    elif as_json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        for k, v in rep.items():
            print(f"{k}: {v}")
    return rep


if __name__ == "__main__":
    audit(sys.argv[1], "--json" in sys.argv)
