# ai-cad-mvp

基于大模型的数据中心机房 CAD 图纸生成 —— 最小可行方案（MVP）

**路径**：自然语言 → 大模型输出布局 JSON → GB 50174 规则校验 → ezdxf 程序化生成 DXF → 审计器量化比对。
大模型只做"理解与规划"，几何生成与规范核查全部由确定性代码完成，输出可在 AutoCAD / 中望 CAD / 浩辰 CAD 中直接编辑。


## 机房要素覆盖（房间级）

机柜行与编号、冷/热通道、列头柜（PD）、液冷 CDU（列端）、精密空调 AHU（沿墙）、门与安全出口、柱网、
疏散/搬运主通道（≥1.5 m）、灭火器点位、气体灭火喷头网格、疏散指示（EXIT）、尺寸标注、状态/版本水印。
全部按图层组织（WALL/COLUMN/RACK/POWER/CDU/AHU/AISLE/EVAC/FIRE/DOOR/TEXT/DIM），设备为块引用。

## 人机协同（Human-in-the-loop）

```bash
python collab.py session.json        # 以 examples/spec_example.json 起一个会话
collab> report                       # 审核清单：规则状态 / 要素清单 / 待工程师确认问题
collab> edit 把B行减少2个柜子          # 自然语言修改：确定性指令解析（无需大模型），解析不了回退到大模型
collab> edit 冷通道改为1.5m
collab> edit 东墙增加6台AHU
collab> render out.dxf               # 出图（带 rev 与状态水印）
collab> import out_edited.dxf        # 工程师在 CAD 里改完 -> DXF 反解析回 JSON，自动生成变更记录
collab> approve 张工                  # 审批门禁：有规则违规时不可批准；待审核 -> 已审核 -> 已批准
collab> undo
```

设计原则：AI 只产出"待审核"草图；每次修改 rev+1 并回到待审核；批准必须规则全部通过；JSON 与 DXF 双向可回读，
工程师可在 CAD 中直接改图，系统回读后继续规则复核。

## 层级覆盖

| 层级 | 对象 | 生成内容 | 内置规则（可配置） |
|---|---|---|---|
| 园区 campus | 用地红线、消防车道、数据中心楼、变电站、冷冻站、柴发、综合楼 | `campus.dxf` 总平面 | 消防车道净宽≥4 m、建筑间距、退红线 |
| 楼层 facility | IT机房、电力电池室（设备间）、制冷间、走廊、辅助用房 | `<楼>_<层>.dxf` 楼层平面（IT机房自动排柜、制冷间自动排冷机、电池室自动排电池架） | 房间不重叠、电池室最小面积、冷机检修净距 |
| 房间 room | 机柜行、冷/热通道、标注 | `room_<id>.dxf` 机房详图 | 冷通道≥1.2 m、热通道≥1.0 m、列端到墙≥1.0 m |

```bash
python site_schema_to_dxf.py examples/site_example.json examples/site_out   # 一次生成园区/楼层/房间三级 DXF
```

## 快速开始

```bash
pip install -r requirements.txt

# 1. 不接大模型：直接由示例 JSON 出图
python pipeline.py --spec examples/spec_example.json out.dxf

# 2. 接大模型（任意 OpenAI 兼容接口：DeepSeek / 通义千问 / GLM …）
export LLM_API_KEY=sk-xxx           # 必填
export LLM_BASE_URL=https://api.deepseek.com   # 可选
export LLM_MODEL=deepseek-chat                 # 可选
python pipeline.py "机房净尺寸18m×12m，4行机柜，每行24个600宽柜，面对面冷通道封闭，冷热通道均1.2m" out.dxf

# 3. 审计任意 DXF（含真实图纸，DWG 请先用 ODA File Converter 转 DXF）
python dxf_audit.py out.dxf --json

# 4. 批量验证（10 个案例）与单元测试
python examples/batch_cases.py
pytest -q
```

## 目录

```
layout_schema_to_dxf.py   房间级：规则校验 + ezdxf 绘图引擎（图层/块/编号/尺寸/通道标注）
site_schema_to_dxf.py     园区/楼层级：消防车道、建筑、IT机房、设备间、制冷间，逐级出图并复用房间级引擎
collab.py                 人机协同：审核清单 / 自然语言修改 / DXF 反解析 / 变更记录 / 版本与审批
dxf_audit.py              DXF 结构化审计：图幅、图层、块、通道净宽、到墙距离
llm_client.py             OpenAI 兼容接口调用，强制 JSON 输出
pipeline.py               端到端：LLM → 校验（不通过回传重生成）→ DXF → 审计
docs/llm_prompt.md        system prompt 与 JSON schema
examples/spec_example.json 房间级示例（18m×12m，4×24 柜）
examples/site_example.json 园区级示例（2 栋数据中心 + 变电站/冷冻站/柴发，含一层楼层平面）
examples/batch_cases.py   10 案例批量验证，输出 summary.json
tests/                    pytest
```

## JSON schema（中间表示）

```json
{
  "title": "string",
  "room": {"width": 18000, "depth": 12000, "wall_t": 200},
  "rack_rows": [
    {"id": "A", "x": 1500, "y": 1500, "count": 24,
     "rack_w": 600, "rack_d": 1200, "face": "N"}
  ]
}
```

单位 mm，原点为房间左下角内墙角；`face` 为机柜正面朝向，用于自动判别冷/热通道。

## 内置规则（可在 `RULES` 字典中增删）

| 规则 | 阈值 | 依据 |
|---|---|---|
| 冷通道净宽 | ≥ 1200 mm | GB 50174-2017 |
| 热通道净宽 | ≥ 1000 mm | GB 50174-2017 |
| 机柜列端到墙 | ≥ 1000 mm | GB 50174-2017 |
| 疏散/搬运主通道 | ≥ 1500 mm | GB 50174-2017 |
| 安全出口数量 | 面积>100 m² 时 ≥2 | GB 50174-2017 |
| AHU 检修净距 / 与机柜冲突 | ≥ 800 mm / 列端留 3.0 m | 示例，按项目核定 |
| 消防车道净宽（园区） | ≥ 4 m | GB 50016 |
| 建筑间距 / 退红线（园区） | 6 m / 5 m | 示例，按项目核定 |
| 冷机检修净距 / 电池室最小面积（楼层） | 1.0 m / 20 m² | 示例，按项目核定 |

## 路线图

- schema 扩展：柱网避让、精密空调、列头柜、地板走线、消防
- 规范知识库：GB 50174 / YD/T 条文结构化为规则
- 历史图纸学习：用审计器抽取布局模式，训练布局先验

## License

MIT
