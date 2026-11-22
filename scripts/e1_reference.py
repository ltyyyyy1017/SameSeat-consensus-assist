"""登记或冻结通用参照。V2.1 没有规定采集方法，因此 ready 保持 false。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tongxi.e1 import ExperimentStore


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in {"set", "freeze", "show"}:
        print("用法: python scripts/e1_reference.py set 房间编号 参照.json", file=sys.stderr)
        print("或: python scripts/e1_reference.py freeze 房间编号", file=sys.stderr)
        print("或: python scripts/e1_reference.py show 房间编号", file=sys.stderr)
        return 2
    store = ExperimentStore()
    room = sys.argv[2]
    if sys.argv[1] == "show":
        exported = store.export_room(room)
        print(json.dumps({"global_reference_ready": exported["global_reference_ready"], "global_reference": exported["global_reference"]}, ensure_ascii=False, indent=2))
        return 0
    if sys.argv[1] == "freeze":
        record = store.freeze_reference(room)
    else:
        record = store.set_reference(room, json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")))
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("global_reference_ready=false。来源方法尚未确定，不能开始真人采集。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
