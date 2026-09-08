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
    assert rep["racks"] == 96 and rep["rack_rows"] == 4
    assert all(g >= 1000 for g in rep["aisle_gaps_mm"])

