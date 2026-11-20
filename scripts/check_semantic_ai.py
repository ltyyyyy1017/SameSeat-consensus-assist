"""用真实本地模型检查定案提取、条件句和低词面重合的意见归属。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tongxi import adapters


def segment(identifier, text):
    return {"id": identifier, "rev": 1, "stable": True, "text": text}


def main():
    local = adapters.LocalSemantic()
    room = {"segments": [segment('s1', '方案B要使用学校实验室，安排在周六。'),
                        segment('s2', '那就选择方案B，定在周六。'),
                        segment('s3', '另一项是制作活动海报，周三之前发给宣传组。')],
            "decisions": [{"id": "d1", "title": "活动场地与时间", "conclusion": "周六在实验室开展活动",
                           "evidence": [{"segment": "s1", "rev": 1}, {"segment": "s2", "rev": 1}]},
                          {"id": "d2", "title": "海报制作", "conclusion": "周三提交海报",
                           "evidence": [{"segment": "s3", "rev": 1}]}]}
    records = []
    try:
        started = time.monotonic()
        definite = adapters.analyze_discussion({"segments": room['segments'][:2]}, local)
        records.append({"case": "explicit_decision", "result": definite, "seconds": round(time.monotonic()-started, 2)})
        conditional = adapters.analyze_discussion({"segments": [segment('s1', '如果选B，需要预约实验室。'),
                       segment('s2', '我们还没决定，先讨论其他方案。')]}, local)
        records.append({"case": "conditional_not_decided", "result": conditional})
        paraphrase = adapters.semantic_match(room, '周六那栋楼的大门锁着，根本进不去，我建议换一天。', local)
        records.append({"case": "paraphrase_retrieval", "result": paraphrase, "note": "仅核对议题归属，关系标签和解释另需人工审查"})
        ambiguous = adapters.semantic_match(room, '那天进不去。', local)
        records.append({"case": "ambiguous_reference_observation", "result": ambiguous,
                        "note": "指代缺少上下文，记录输出用于人工审查，不计为正确关联"})
        unrelated = adapters.semantic_match(room, '我今天晚饭打算吃一碗面。', local)
        records.append({"case": "unrelated", "result": unrelated})
        success = bool(definite['items']) and not conditional['items'] and [x['decision'] for x in paraphrase['items']] == ['d1'] and not unrelated['items']
    finally:
        local.close()
    folder = Path(__file__).resolve().parents[1] / 'artifacts'
    folder.mkdir(exist_ok=True)
    report = {"passed": success, "model": adapters.capabilities()['semantic']['label'], "cases": records,
              "note": "小规模构造案例回归，不能替代真实讨论评测"}
    (folder/'semantic-ai-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    assert success, 'See artifacts/semantic-ai-check.json'


if __name__ == '__main__':
    main()
