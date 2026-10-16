"""从获授权的候选正文构造可追溯摘录；不执行检索、授权或通用语义证明。"""

import json
import re

from app.infrastructure.chat_model import ModelFailure

DATE = re.compile(r"(?:19|20)\d{2}(?:[-年]\d{1,2}(?:[-月]\d{1,2}日?)?)")
BYTES = re.compile(r"\d[\d,.]*\s*(?:[KMGT]i?B|字节)", re.I)
ROLES = ("owner", "editor", "viewer")


def cells(line):
    # 转义竖线不是列边界；只识别简单 Markdown 表格，复杂表格保守不推断权限。
    return [part.strip() for part in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def units(sources):
    result = []
    for source in sources:
        text, header, header_span = source["excerpt"], None, None
        heading_span, fenced = None, False
        for line_match in re.finditer(r"[^\n]+", text):
            line = line_match.group()
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
                continue
            if fenced:
                continue
            if line.lstrip().startswith("|"):
                columns = cells(line)
                if any(c.strip(" `") in ("权限", "角色") for c in columns):
                    header, header_span = columns, [line_match.start(), line_match.end()]
                    continue
                if all(re.fullmatch(r":?-+:?", c.replace(" ", "")) for c in columns):
                    continue
                # 不把没有表头的片段猜成角色/操作关系。
                if header is None or len(header) != len(columns):
                    continue
                spans = [header_span, [line_match.start(), line_match.end()]]
                role_index = next(
                    i for i, h in enumerate(header) if h.strip(" `") in ("权限", "角色")
                )
                result.append(
                    dict(
                        source_label=source["source_label"],
                        spans=spans,
                        text="\n".join(text[a:b] for a, b in spans),
                        permission=columns[role_index],
                    )
                )
                continue
            header = header_span = None
            if line.lstrip().startswith("#"):
                heading_span = [line_match.start(), line_match.end()]
                continue
            # “仅/按/但”等承接分句携带条件或对象，不能只摘前半句抹掉限制。
            # 独立主语（如任务总超时；Worker租约）仍分开，避免不同期限互相混入。
            ranges = []
            for sentence in re.finditer(r"[^。！？；;]+[。！？；;]?", line):
                if (
                    ranges
                    and line[ranges[-1][1] - 1] in "；;"
                    and re.match(
                        r"\s*(?:仅|按|但|且|并|否则|不能|不|永久|临时|保留|超过)", sentence.group()
                    )
                ):
                    ranges[-1][1] = sentence.end()
                else:
                    ranges.append([sentence.start(), sentence.end()])
            for begin, finish in ranges:
                start = line_match.start() + begin
                end = line_match.start() + finish
                while start < end and text[start].isspace():
                    start += 1
                if start < end:
                    spans = [[start, end]]
                    if heading_span and DATE.search(text[start:end]):
                        spans.insert(0, heading_span)
                    result.append(
                        dict(
                            source_label=source["source_label"],
                            spans=spans,
                            text="\n".join(text[a:b] for a, b in spans),
                            permission=None,
                        )
                    )
    source_map = {s["source_label"]: s for s in sources}
    return [
        dict(
            unit,
            evidence_id=f"E{i}",
            section_title=source_map[unit["source_label"]].get("section_title"),
            filename=source_map[unit["source_label"]].get("filename"),
        )
        for i, unit in enumerate(result, 1)
    ]


def permitted(question, unit):
    q, text = question.lower(), unit["text"]
    asked_roles = [r for r in ROLES if re.search(rf"(?<![a-z0-9_]){r}(?![a-z0-9_])", q)]
    if asked_roles and unit["permission"] is not None:
        permission = unit["permission"].lower()
        # + 仅按项目明确的角色层级解释；owner 专属行不能提供给 viewer。
        allowed = set()
        first_role = re.match(r"`?(owner|editor|viewer)`?(\+)?(?=$|[^a-z0-9_])", permission)
        if first_role:
            role = first_role[1]
            allowed.update(ROLES[: ROLES.index(role) + 1] if first_role[2] else [role])
        if not all(role in allowed for role in asked_roles):
            return False
    if re.search(r"签署|签订|签约", q) and re.search(r"日期|哪天|何时|什么时候|时间", q):
        # 日期与签约事件必须同一证据句明确共现，交付标题的日期不能借用。
        if not DATE.search(text) or not re.search(r"签署|签订|签约", text):
            return False
        if re.search(r"未|没有|无|尚未|不详", text):
            return False
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        if re.search(r"更新|编写|交付|修改", body) and not re.search(r"签署|签订|签约", body):
            return False
    if re.search(r"容量|文件卷|文件累计|空间|大小|多大", q):
        if not BYTES.search(text):
            return False
    if "租约" in q and re.search(r"多久|多长|多少秒|几秒|间隔", q):
        if not re.search(r"租约|续租", text) or not re.search(r"\d+\s*(?:秒|分钟|小时)", text):
            return False
    return True


def catalog(question, sources):
    return [u for u in units(sources) if permitted(question, u)]


def render_plan(raw, question, sources):
    from app.modules.chat.prompt import ABSTENTION

    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"evidence_ids"}:
            raise ValueError
        ids = value["evidence_ids"]
        if (
            not isinstance(ids, list)
            or len(ids) > 12
            or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError
        known = {u["evidence_id"]: u for u in catalog(question, sources)}
        chosen = [known[i] for i in ids]
    except (ValueError, KeyError, TypeError) as error:
        raise ModelFailure("ANSWER_PLAN_INVALID", "答案证据结构未通过验证") from error
    if not chosen:
        return ABSTENTION
    # 同时问每库与整个文件卷时，不能只回答其中一个指标。
    if re.search(r"每.*库|单.*库", question) and "文件卷" in question:
        combined = "".join(u["text"] for u in chosen)
        if not re.search(r"每库|单库|知识库", combined) or "文件卷" not in combined:
            return ABSTENTION
    source_map = {s["source_label"]: s for s in sources}
    rendered = []
    for unit in chosen:
        source = source_map[unit["source_label"]]
        if "\n".join(source["excerpt"][a:b] for a, b in unit["spans"]) != unit["text"]:
            raise ModelFailure("ANSWER_PLAN_INVALID", "答案原文区间不匹配")
        # 只输出原文字面摘录；模型无权自由生成数值、权限结论或添加引用。
        quote = "\n".join("> " + line for line in unit["text"].splitlines())
        rendered.append(f"{quote}\n\n[{unit['source_label']}]")
    return "\n\n".join(rendered)
