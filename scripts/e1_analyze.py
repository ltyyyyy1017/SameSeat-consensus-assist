"""离线计算 E1 的 A/B/C。没有真人记录时不得写成参与者结果。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tongxi.e1 import engineering_gate, evaluate, replay


def main():
    if len(sys.argv) != 2:
        print("用法: python scripts/e1_analyze.py 导出的记录.json", file=sys.stderr)
        return 2
    exported = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not exported.get("participants") or not any(person.get("sessions") for person in exported["participants"]):
        print("没有可分析的讨论记录。空白表不是结果。")
        return 0
    if not exported.get("synthetic") and not exported.get("global_reference_ready"):
        print("global_reference_ready=false。第一次讨论的通用参照还没有冻结，不能把这份输出当成真人第二次讨论的对照。")
        return 3
    result = evaluate(replay(exported))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("Unknown 和未标注区间不进入 Precision、Recall、F1。这不是听障有效性证明。")
    gate = engineering_gate(result["micro"]["A"], result["micro"]["C"], result["label_coverage"])
    print(json.dumps(gate, ensure_ascii=False))
    print("门槛判断没有修改产品阈值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
