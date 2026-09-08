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


if __name__ == "__main__":
    print(json.dumps(ask(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
