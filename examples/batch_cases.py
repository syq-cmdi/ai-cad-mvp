"""
论文 4.2 节第一层验证: 10 条需求 -> JSON(这里用参数化模板代替 LLM, 也可改为调用 llm_client)
运行: python examples/batch_cases.py   -> examples/batch_out/*.dxf + summary.json
"""
import json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from layout_schema_to_dxf import check, build
import dxf_audit

CASES = [  # (房间宽, 深, 行数, 每行柜数, 冷通道, 热通道)
    (6000, 9000, 2, 6, 1200, 1000), (9000, 9000, 2, 10, 1200, 1200),
    (12000, 10000, 3, 14, 1200, 1000), (15000, 12000, 4, 20, 1200, 1200),
    (18000, 12000, 4, 24, 1200, 1200), (20000, 14000, 5, 28, 1500, 1000),
    (24000, 16000, 6, 32, 1200, 1200), (26000, 18000, 6, 36, 1500, 1200),
    (30000, 20000, 8, 40, 1200, 1000), (30000, 20000, 8, 44, 1500, 1500),
]


def make_spec(W, D, n, cnt, cold, hot):
    total = n * 1200 + (n // 2) * cold + ((n - 1) // 2) * hot
    y = (D - total) // 2
    x = (W - cnt * 600) // 2
    rows, faces = [], ["N", "S"]
    for i in range(n):
        rows.append({"id": chr(65 + i), "x": x, "y": y, "count": cnt,
                     "rack_w": 600, "rack_d": 1200, "face": faces[i % 2]})
        y += 1200 + (cold if i % 2 == 0 else hot)
    return {"title": f"案例 {W//1000}m×{D//1000}m {n}行×{cnt}柜",
            "room": {"width": W, "depth": D, "wall_t": 200}, "rack_rows": rows}


if __name__ == "__main__":
    out = pathlib.Path(__file__).parent / "batch_out"; out.mkdir(exist_ok=True)
    summary = []
    for i, c in enumerate(CASES, 1):
        spec = make_spec(*c); t0 = time.time()
        errs = check(spec)
        f = out / f"case{i:02d}.dxf"
        if not errs:
            build(spec, str(f))
        rep = dxf_audit.audit(str(f), quiet=True) if not errs else {}
        summary.append({"case": i, "params": c, "errors": errs, "time_s": round(time.time() - t0, 3),
                        "racks": rep.get("racks"), "aisles": rep.get("aisle_gaps_mm"), "min_x": rep.get("min_x_mm")})
    json.dump(summary, open(out / "summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    ok = sum(1 for s in summary if not s["errors"])
    print(f"{ok}/{len(CASES)} 生成成功")

