# ai-cad-mvp

基于大模型的数据中心机房 CAD 图纸生成 —— 最小可行方案（MVP）

**路径**：自然语言 → 大模型输出布局 JSON → GB 50174 规则校验 → ezdxf 程序化生成 DXF → 审计器量化比对。
大模型只做"理解与规划"，几何生成与规范核查全部由确定性代码完成，输出可在 AutoCAD / 中望 CAD / 浩辰 CAD 中直接编辑。

![example](docs/example_output.png)

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
layout_schema_to_dxf.py   规则校验 + ezdxf 绘图引擎（图层/块/编号/尺寸/通道标注）
dxf_audit.py              DXF 结构化审计：图幅、图层、块、通道净宽、到墙距离
llm_client.py             OpenAI 兼容接口调用，强制 JSON 输出
pipeline.py               端到端：LLM → 校验（不通过回传重生成）→ DXF → 审计
docs/llm_prompt.md        system prompt 与 JSON schema
examples/spec_example.json 示例中间表示（18m×12m，4×24 柜）
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

## 路线图

- schema 扩展：柱网避让、精密空调、列头柜、地板走线、消防
- 规范知识库：GB 50174 / YD/T 条文结构化为规则
- 历史图纸学习：用审计器抽取布局模式，训练布局先验

## License

MIT

