"""公开语料的稳定位置评分；不推断自然语言等价，不替代人工评审。"""

import hashlib


def validate_cases(cases, corpus):
    seen = set()
    for case in cases:
        if case["case_id"] in seen:
            raise ValueError("重复题号")
        seen.add(case["case_id"])
        units = case.get("evidence_units", [])
        if bool(units) != case["answerable"]:
            raise ValueError("证据单元与可回答标记不一致")
        for unit in units:
            if not unit["alternatives"]:
                raise ValueError("证据单元没有来源")
            for span in unit["alternatives"]:
                content = corpus[span["file"]]
                if hashlib.sha256(content.encode()).hexdigest() != span["file_sha256"]:
                    raise ValueError("语料哈希变化")
                start, end = span["char_start"], span["char_end"]
                if not 0 <= start < end <= len(content) or content[start:end] != span["quote"]:
                    raise ValueError("证据位置与原文不一致")


def unit_hit(unit, sources, mappings):
    # 只认当前评测映射的文档及其原文位置；同名文档/不相关位置不能蒙混过关。
    for span in unit["alternatives"]:
        intervals = []
        for source in sources:
            if source["document_id"] != mappings[span["file"]] or source.get("page_no") != span.get(
                "page_no"
            ):
                continue
            start, end = source["char_start"], source["char_end"]
            if start is None or end is None or end <= start:
                continue
            text = source.get("excerpt", source.get("text", ""))
            left, right = max(start, span["char_start"]), min(end, span["char_end"])
            if (
                left < right
                and text[left - start : right - start]
                == span["quote"][left - span["char_start"] : right - span["char_start"]]
            ):
                intervals.append((left, right))
        # 相邻或重叠的真实片段可以共同覆盖证据；重复块不能重复累计。
        cursor = span["char_start"]
        for left, right in sorted(intervals):
            if left > cursor:
                break
            cursor = max(cursor, right)
        if cursor >= span["char_end"]:
            return True
    return False


def score_case(case, sources, mappings):
    units = case["evidence_units"]
    hits = [unit_hit(unit, sources, mappings) for unit in units]
    return {"hits": sum(hits), "total": len(hits), "complete": bool(hits) and all(hits)}


def summarize_evidence(rows):
    scores = [r["evidence_v2"] for r in rows if r["answerable"]]
    hits = sum(s["hits"] for s in scores)
    total = sum(s["total"] for s in scores)
    return {
        "hits": hits,
        "total": total,
        "recall_at_5": hits / total if total else None,
        "complete_questions": sum(s["complete"] for s in scores),
        "answerable_questions": len(scores),
        "human_annotation_reviewed": False,
    }
