"""由第一次讨论的自然坐姿中位数生成、冻结或查看通用参照。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tongxi.e1 import ExperimentStore


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in {"build", "freeze", "show"}:
        print("用法: python scripts/e1_reference.py build 房间编号", file=sys.stderr)
        print("或: python scripts/e1_reference.py freeze 房间编号", file=sys.stderr)
        print("或: python scripts/e1_reference.py show 房间编号", file=sys.stderr)
        return 2
    store = ExperimentStore()
    room = sys.argv[2]
    if sys.argv[1] == "show":
        exported = store.export_room(room)
        print(json.dumps({
            "global_reference_ready": exported["global_reference_ready"],
            "global_reference": exported["global_reference"],
        }, ensure_ascii=False, indent=2))
        return 0
    try:
        record = store.build_reference(room) if sys.argv[1] == "build" else store.freeze_reference(room)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if record.get("ready"):
        print("global_reference_ready=true。第二次讨论可以开始。这个参照只代表本次先导队列，不是全体标准。")
    else:
        print("global_reference_ready=false。不能开始第二次讨论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
