import json, pathlib, sys, copy
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from layout_schema_to_dxf import check, build
import dxf_audit

SPEC = json.load(open(pathlib.Path(__file__).parents[1] / "examples" / "spec_example.json", encoding="utf-8"))


def test_valid_spec_passes():
    assert check(SPEC) == []


def test_narrow_cold_aisle_rejected():
    s = copy.deepcopy(SPEC)
    s["rack_rows"][1]["y"] = s["rack_rows"][0]["y"] + 1200 + 1000  # 1.0m 冷通道
    assert any("通道净宽" in e for e in check(s))


def test_rack_too_close_to_wall_rejected():
    s = copy.deepcopy(SPEC)
    s["rack_rows"][0]["x"] = 500
    assert any("到墙" in e for e in check(s))


def test_build_and_audit(tmp_path):
    out = tmp_path / "t.dxf"
    build(SPEC, str(out))
    rep = dxf_audit.audit(str(out))
    assert rep["racks"] == 72 and rep["rack_rows"] == 4
    assert rep["blocks"].get("AHU") == 4 and rep["blocks"].get("CDU") == 4 and rep["blocks"].get("EXTINGUISHER") == 4
    assert all(g >= 1000 for g in rep["aisle_gaps_mm"])


# ---------- 层级扩展 ----------
import site_schema_to_dxf as site

SITE = json.load(open(pathlib.Path(__file__).parents[1] / "examples" / "site_example.json", encoding="utf-8"))


def test_site_valid_and_builds(tmp_path):
    rep = site.run(copy.deepcopy(SITE), tmp_path)
    assert "errors" not in rep
    assert (tmp_path / "campus.dxf").exists() and (tmp_path / "B1_F2.dxf").exists()
    assert (tmp_path / "room_A.dxf").exists()
    assert all(r["racks"] > 0 for r in rep["rooms"])


def test_narrow_fire_lane_rejected():
    s = copy.deepcopy(SITE); s["fire_lanes"][0]["width"] = 3000
    assert any("消防车道" in e for e in site.check_campus(s))


def test_building_gap_rejected():
    s = copy.deepcopy(SITE); s["buildings"][1]["x"] = s["buildings"][0]["x"] + s["buildings"][0]["w"] + 2000
    assert any("间距" in e for e in site.check_campus(s))


def test_cooling_plant_too_many_chillers_rejected():
    f = copy.deepcopy(SITE["buildings"][0]["floor_plans"][0])
    for r in f["rooms"]:
        if r["type"] == "cooling_plant": r["chillers"] = 20
    assert any("制冷间" in e for e in site.check_floor(f))


def test_room_overlap_rejected():
    f = copy.deepcopy(SITE["buildings"][0]["floor_plans"][0])
    f["rooms"][1]["x"] = f["rooms"][0]["x"] + 1000
    assert any("重叠" in e for e in site.check_floor(f))


# ---------- 人机协同 ----------
import collab


def test_instruction_parser_and_roundtrip(tmp_path):
    s2, how = collab.apply_instruction(SPEC, "把B行减少2个柜子", use_llm=False)
    assert how == "parser" and s2["rack_rows"][1]["count"] == SPEC["rack_rows"][1]["count"] - 2
    s3, _ = collab.apply_instruction(s2, "冷通道改为1.5m", use_llm=False)
    assert check(s3) == []
    build(s3, str(tmp_path / "e.dxf"))
    back = collab.dxf_to_spec(str(tmp_path / "e.dxf"), s3)
    assert collab.diff(s3, back) == ["（无变化）"]


def test_session_approve_blocks_on_violation(tmp_path):
    import json as _j, copy as _c
    p = tmp_path / "sess.json"; bad = _c.deepcopy(SPEC); bad["doors"] = []
    _j.dump(bad, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    S = collab.Session(str(p))
    assert "不能批准" in S.approve("张工")
    S.edit("南墙开一个门 位置 2000"); S.edit("南墙开一个门 位置 14000")
    assert "已审核" in S.approve("张工") and S.spec["meta"]["rev"] == 2
