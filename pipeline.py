"""
端到端: 自然语言 -> LLM -> JSON -> 规则校验(不通过则带错误回传重生成) -> DXF -> 审计
用法: python pipeline.py "需求描述" out.dxf
      python pipeline.py --spec examples/spec_example.json out.dxf   (跳过LLM)
"""
import json, sys, time
from layout_schema_to_dxf import check, build
import dxf_audit


def run(text=None, spec=None, out="out.dxf", max_round=3):
    t0 = time.time()
    if spec is None:
        from llm_client import ask
        prompt = text
        for rnd in range(max_round):
            spec = ask(prompt)
            errs = check(spec)
            if not errs:
                break
            print(f"[round {rnd+1}] 校验未通过, 回传重生成: {errs}")
            prompt = text + "\n\n上一轮输出违反约束: " + "; ".join(errs) + "\n请修正后重新输出JSON。"
        else:
            raise SystemExit("多轮重生成仍不合规")
    else:
        errs = check(spec)
        if errs:
            raise SystemExit("规则校验未通过: " + "; ".join(errs))
    build(spec, out)
    print(f"OK -> {out}  ({time.time()-t0:.1f}s)")
    dxf_audit.audit(out)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--spec":
        run(spec=json.load(open(a[1], encoding="utf-8")), out=a[2])
    else:
        run(text=a[0], out=a[1])

