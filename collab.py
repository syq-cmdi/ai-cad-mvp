"""
人机协同（Human-in-the-loop）模块
1) review()          : 生成审核清单（规则状态 / 要素清单 / 待工程师确认问题）
2) apply_instruction(): 用自然语言修改布局：先走确定性指令解析（无需大模型），解析不了再回退到大模型
3) dxf_to_spec()     : 工程师在 CAD 中改过的 DXF 反解析回 JSON（闭环：AI 出图 -> 人改图 -> 回读）
4) diff()            : 两个版本的可读变更记录
5) Session           : 版本、状态（AI草图-待审核 -> 已审核 -> 已批准）、审批人、历史与撤销
交互: python collab.py session.json   （REPL: show / check / report / edit <指令> / undo / import <dxf> / render <dxf> / approve <姓名> / quit）
"""
import json, re, copy, sys, pathlib, datetime
import ezdxf
from layout_schema_to_dxf import check, build, RULES, DEF

STATUS = ["AI草图-待审核", "已审核", "已批准"]


# ---------- 1. 审核清单 ----------
def review(spec):
    errs = check(spec)
    W, D = spec["room"]["width"], spec["room"]["depth"]
    rows = spec["rack_rows"]
    racks = sum(r["count"] for r in rows)
    inv = {
        "机柜": racks, "机柜行": len(rows),
        "列头柜": sum(1 for r in rows if r.get("row_head")), "CDU": sum(1 for r in rows if r.get("cdu")),
        "AHU": spec.get("ahu", {}).get("count", 0), "门/安全出口": len(spec.get("doors", [])),
        "灭火器(自动)": len(spec.get("doors", [])) * RULES["extinguisher_per_door"],
        "机房面积m²": round(W * D / 1e6, 1), "机柜密度(柜/m²)": round(racks / (W * D / 1e6), 2),
    }
    questions = []
    if spec.get("ahu", {}).get("count") and not spec.get("ahu", {}).get("confirmed"):
        questions.append(f"AHU 布置在 {spec['ahu'].get('side','W')} 墙，是否与回风/送风方式一致？")
    if any(r.get("cdu") for r in rows):
        questions.append("CDU 位于列端，液冷一次侧管路是否由该侧走线？")
    if not spec.get("columns"):
        questions.append("未提供柱网，请确认机柜行是否避让结构柱。")
    if racks / (W * D / 1e6) > 0.35:
        questions.append("机柜密度较高，请确认供电/制冷容量与地板承重。")
    lines = [f"# 审核清单  {spec.get('title','')}  rev {spec.get('meta',{}).get('rev',0)}  状态: {spec.get('meta',{}).get('status', STATUS[0])}", ""]
    lines.append("## 规则校验")
    lines += ([f"- [x] 全部通过（{len(RULES)} 条规则）"] if not errs else [f"- [ ] {e}" for e in errs])
    lines.append(""); lines.append("## 要素清单")
    lines += [f"- {k}: {v}" for k, v in inv.items()]
    lines.append(""); lines.append("## 待工程师确认")
    lines += [f"- [ ] {q}" for q in questions] or ["- 无"]
    return "\n".join(lines), errs, inv


# ---------- 2. 自然语言修改：确定性解析 + 大模型回退 ----------
_NUM = r"(\d+(?:\.\d+)?)"
PATTERNS = [
    (rf"(?:把)?([A-Z])行(?:减少|去掉|删除)\s*{_NUM}\s*(?:个|台)?(?:机)?柜", "row_minus"),
    (rf"(?:把)?([A-Z])行(?:增加|加)\s*{_NUM}\s*(?:个|台)?(?:机)?柜", "row_plus"),
    (rf"(?:删除|去掉)([A-Z])行", "row_del"),
    (rf"(?:增加|加)一行", "row_add"),
    (rf"冷通道(?:改为|改成|调为|设为)\s*{_NUM}\s*(m|mm)?", "cold"),
    (rf"热通道(?:改为|改成|调为|设为)\s*{_NUM}\s*(m|mm)?", "hot"),
    (rf"主通道(?:改为|改成|调为|设为)\s*{_NUM}\s*(m|mm)?", "main"),
    (rf"([东西南北])墙(?:增加|加|布置)\s*{_NUM}\s*台?AHU", "ahu"),
    (rf"(?:删除|去掉|取消)(?:所有)?CDU", "cdu_off"),
    (rf"(?:增加|加|布置)CDU", "cdu_on"),
    (rf"(?:删除|去掉|取消)(?:所有)?列头柜", "head_off"),
    (rf"(?:增加|加|布置)列头柜", "head_on"),
    (rf"([东西南北])墙(?:增加|加|开)(?:一个|一道)?门(?:.*?位置\s*{_NUM})?", "door"),
]
SIDE = {"东": "E", "西": "W", "南": "S", "北": "N"}


def _mm(v, unit):
    v = float(v)
    return int(v * 1000) if (unit == "m" or (unit is None and v < 50)) else int(v)


def apply_instruction(spec, text, use_llm=True):
    s = copy.deepcopy(spec); text = text.strip()
    for pat, op in PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        g = m.groups()
        rows = {r["id"]: r for r in s["rack_rows"]}
        if op in ("row_minus", "row_plus"):
            r = rows.get(g[0]);
            if not r: raise ValueError(f"无 {g[0]} 行")
            r["count"] = max(1, r["count"] + (int(g[1]) if op == "row_plus" else -int(g[1])))
        elif op == "row_del":
            s["rack_rows"] = [r for r in s["rack_rows"] if r["id"] != g[0]]
        elif op == "row_add":
            last = sorted(s["rack_rows"], key=lambda r: r["y"])[-1]
            gap = RULES["cold_aisle_min"] if last["face"] == "S" else RULES["hot_aisle_min"]
            new = copy.deepcopy(last); new["id"] = chr(ord(last["id"]) + 1)
            new["y"] = last["y"] + last["rack_d"] + gap; new["face"] = "N" if last["face"] == "S" else "S"
            s["rack_rows"].append(new)
        elif op in ("cold", "hot"):
            v = _mm(g[0], g[1]); rs = sorted(s["rack_rows"], key=lambda r: r["y"])
            for a, b in zip(rs, rs[1:]):
                is_cold = a["face"] == "N" and b["face"] == "S"
                if (op == "cold") == is_cold:
                    shift = a["y"] + a["rack_d"] + v - b["y"]
                    for r in rs[rs.index(b):]: r["y"] += shift
        elif op == "main":
            s["main_aisle"] = _mm(g[0], g[1])
        elif op == "ahu":
            s["ahu"] = {"count": int(g[1]), "side": SIDE[g[0]]}
        elif op in ("cdu_off", "cdu_on", "head_off", "head_on"):
            key = "cdu" if "cdu" in op else "row_head"
            for r in s["rack_rows"]: r[key] = op.endswith("on")
        elif op == "door":
            s.setdefault("doors", []).append({"wall": SIDE[g[0]], "pos": int(float(g[1])) if g[1] else 1500, "width": 1500})
        s.setdefault("meta", {})["last_edit"] = {"by": "rule-parser", "instruction": text}
        return s, "parser"
    if not use_llm:
        raise ValueError("无法解析指令，且未启用大模型回退")
    from llm_client import ask_edit
    s2 = ask_edit(s, text); s2.setdefault("meta", {})["last_edit"] = {"by": "llm", "instruction": text}
    return s2, "llm"


# ---------- 3. DXF 反解析（工程师改图后回读） ----------
def dxf_to_spec(path, base=None):
    doc = ezdxf.readfile(path); msp = doc.modelspace()
    spec = copy.deepcopy(base) if base else {"title": pathlib.Path(path).stem, "room": {}, "rack_rows": []}
    ins = list(msp.query("INSERT"))
    racks = [e for e in ins if e.dxf.name == "RACK600x1200"]
    rows = {}
    for e in racks:
        rows.setdefault(round(e.dxf.insert.y), []).append(round(e.dxf.insert.x))
    heads = {round(e.dxf.insert.y) for e in ins if e.dxf.name == "ROW_HEAD"}
    cdus = {round(e.dxf.insert.y) for e in ins if e.dxf.name == "CDU"}
    new_rows = []
    for i, y in enumerate(sorted(rows)):
        xs = sorted(rows[y])
        old = next((r for r in spec["rack_rows"] if abs(r["y"] - y) < 50), None)
        new_rows.append({"id": old["id"] if old else chr(65 + i), "x": xs[0], "y": y, "count": len(xs),
                         "rack_w": DEF["rack_w"], "rack_d": DEF["rack_d"],
                         "face": old["face"] if old else ("N" if i % 2 == 0 else "S"),
                         "row_head": y in heads, "cdu": y in cdus})
    spec["rack_rows"] = new_rows
    ahus = [e for e in ins if e.dxf.name == "AHU"]
    if ahus:
        spec["ahu"] = {"count": len(ahus), "side": spec.get("ahu", {}).get("side", "W")}
    if not spec["room"]:
        walls = [e for e in msp.query("LWPOLYLINE") if e.dxf.layer == "WALL"]
        if walls:
            pts = [p for w in walls for p in w.get_points("xy")]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            spec["room"] = {"width": int(sorted(set(xs))[-2]), "depth": int(sorted(set(ys))[-2]), "wall_t": 200}
    spec.setdefault("meta", {})["last_edit"] = {"by": "engineer-cad", "source": str(path)}
    return spec


# ---------- 4. 变更记录 ----------
def diff(a, b):
    out = []
    ra = {r["id"]: r for r in a["rack_rows"]}; rb = {r["id"]: r for r in b["rack_rows"]}
    for k in sorted(set(ra) | set(rb)):
        if k not in ra: out.append(f"+ 新增行 {k}（{rb[k]['count']} 柜）")
        elif k not in rb: out.append(f"- 删除行 {k}")
        else:
            for f in ("count", "x", "y", "face", "row_head", "cdu"):
                if ra[k].get(f) != rb[k].get(f): out.append(f"~ 行{k}.{f}: {ra[k].get(f)} -> {rb[k].get(f)}")
    for f in ("ahu", "doors", "main_aisle", "columns", "fire"):
        if a.get(f) != b.get(f): out.append(f"~ {f}: {a.get(f)} -> {b.get(f)}")
    return out or ["（无变化）"]


# ---------- 5. 会话 ----------
class Session:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.data = json.load(open(path, encoding="utf-8")) if self.path.exists() else None
        if self.data and "history" not in self.data:  # 直接给了 spec
            self.data = {"current": self.data, "history": [], "log": []}
        self.data["current"].setdefault("meta", {"status": STATUS[0], "rev": 0})

    @property
    def spec(self): return self.data["current"]

    def _commit(self, new, who, what):
        self.data["history"].append(copy.deepcopy(self.spec))
        new["meta"] = dict(self.spec.get("meta", {})); new["meta"]["rev"] = new["meta"].get("rev", 0) + 1
        new["meta"]["status"] = STATUS[0]  # 任何修改回到待审核
        self.data["current"] = new
        self.data["log"].append({"t": datetime.datetime.now().isoformat(timespec="seconds"), "who": who, "what": what,
                                 "rev": new["meta"]["rev"], "diff": diff(self.data["history"][-1], new)})
        self.save()

    def edit(self, text):
        new, how = apply_instruction(self.spec, text); self._commit(new, how, text); return how

    def import_dxf(self, path):
        self._commit(dxf_to_spec(path, self.spec), "engineer-cad", f"import {path}")

    def undo(self):
        if self.data["history"]:
            self.data["current"] = self.data["history"].pop(); self.save()

    def approve(self, who):
        errs = check(self.spec)
        if errs: return f"存在 {len(errs)} 条规则违规，不能批准：{errs}"
        m = self.spec["meta"]; m["status"] = STATUS[min(STATUS.index(m.get("status", STATUS[0])) + 1, 2)]
        m.setdefault("approvals", []).append({"who": who, "t": datetime.datetime.now().isoformat(timespec="seconds"), "status": m["status"]})
        self.save(); return f"状态 -> {m['status']}"

    def render(self, out):
        errs = check(self.spec)
        build(self.spec, out); return errs

    def save(self):
        json.dump(self.data, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def repl(path):
    s = Session(path)
    print(f"会话 {path}  rev {s.spec['meta']['rev']}  状态 {s.spec['meta']['status']}")
    while True:
        try: cmd = input("collab> ").strip()
        except EOFError: break
        if not cmd: continue
        op, _, arg = cmd.partition(" ")
        try:
            if op == "show": print(json.dumps(s.spec, ensure_ascii=False, indent=1))
            elif op == "check": print(check(s.spec) or "全部通过")
            elif op == "report": print(review(s.spec)[0])
            elif op == "edit": print("via", s.edit(arg)); print("\n".join(s.data["log"][-1]["diff"])); print(check(s.spec) or "规则全部通过")
            elif op == "undo": s.undo(); print("已撤销 -> rev", s.spec["meta"]["rev"])
            elif op == "import": s.import_dxf(arg); print("\n".join(s.data["log"][-1]["diff"]))
            elif op == "render": print("规则问题:", s.render(arg) or "无", "->", arg)
            elif op == "approve": print(s.approve(arg or "reviewer"))
            elif op in ("quit", "exit"): break
            else: print("命令: show/check/report/edit <指令>/undo/import <dxf>/render <dxf>/approve <姓名>/quit")
        except Exception as e:
            print("错误:", e)


if __name__ == "__main__":
    repl(sys.argv[1] if len(sys.argv) > 1 else "session.json")
