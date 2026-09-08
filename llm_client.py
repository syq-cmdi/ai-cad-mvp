"""
调用兼容 OpenAI 接口的大模型（DeepSeek / 通义千问 / GLM / OpenAI 等），
把自然语言需求转为符合 schema 的布局 JSON。
环境变量:
  LLM_API_KEY   必填
  LLM_BASE_URL  默认 https://api.deepseek.com
  LLM_MODEL     默认 deepseek-chat
用法: python llm_client.py "机房净尺寸18m×12m，4行机柜，每行24个600宽柜，面对面冷通道" > spec.json
"""
import json, os, sys, re, urllib.request, pathlib

SYSTEM = (pathlib.Path(__file__).parent / "docs" / "llm_prompt.md").read_text(encoding="utf-8")


def ask(user_text: str, retries: int = 2) -> dict:
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("请设置环境变量 LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user_text}],
    }
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    for _ in range(retries + 1):
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = json.load(r)["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", txt, re.S)  # 容忍 ```json 包裹
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise RuntimeError("大模型未返回合法 JSON")



EDIT_SYSTEM = ("你是数据中心工艺布局助手。给定当前布局 JSON 和工程师的修改指令，输出修改后的完整 JSON（且仅输出 JSON），"
               "保持未提及字段不变，遵守与生成时相同的硬约束。")


def ask_edit(spec: dict, instruction: str) -> dict:
    """人机协同：自然语言修改指令 -> 新 JSON（大模型回退路径）"""
    key = os.environ.get("LLM_API_KEY")
    if not key:
        raise SystemExit("请设置环境变量 LLM_API_KEY（或使用可被规则解析的指令）")
    base = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    body = {"model": model, "temperature": 0, "messages": [
        {"role": "system", "content": EDIT_SYSTEM + "\n\nschema 与约束:\n" + SYSTEM},
        {"role": "user", "content": "当前布局:\n" + json.dumps(spec, ensure_ascii=False) + "\n\n修改指令: " + instruction}]}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0))


if __name__ == "__main__":
    print(json.dumps(ask(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
