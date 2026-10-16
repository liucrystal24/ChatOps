"""可确定的对象/属性边界；只约束问题与获授权原文，不读取评测标签。"""

import re

EDITIONS = re.compile(r"(?:标准|专业|基础|企业|免费|高级|旗舰|试用)版", re.I)
VERSION = re.compile(r"(?:版\s*(20\d{2})|(20\d{2})(?:年)?版)")
FORMATS = re.compile(r"(?<![a-z0-9_])(?:CSV|PDF|TXT|DOCX)(?![a-z0-9_])", re.I)
ROLE = re.compile(r"(?<![a-z0-9_])(?:owner|editor|viewer)(?![a-z0-9_])", re.I)
ACTION = re.compile(r"导出|下载|上传|调整|恢复")
SIGN = r"签署|签订|签约"


def versions(text):
    return {a or b for a, b in VERSION.findall(text)}


def scope_matches(question, unit):
    body = unit["text"]
    context = unit.get("section_title") or ""
    asked = set(EDITIONS.findall(question))
    # 正文显式套餐优先，不能用章节中的另一个套餐替正文消除冲突。
    declared = set(EDITIONS.findall(body)) or set(EDITIONS.findall(context))
    if asked and declared and not asked & declared:
        return False
    wanted_years = versions(question)
    # 章节记录本段业务版本；正文可能用另一个年份作对比，不能借该年份冒充本版。
    declared_years = versions(context) or versions(body)
    if wanted_years and declared_years and not wanted_years & declared_years:
        return False
    desired_formats = {x.upper() for x in FORMATS.findall(question)}
    actual_formats = {x.upper() for x in FORMATS.findall(body)}
    if desired_formats and actual_formats and not desired_formats & actual_formats:
        return False
    asked_roles = {r.lower() for r in ROLE.findall(question)}
    body_roles = {r.lower() for r in ROLE.findall(body)}
    context_roles = {r.lower() for r in ROLE.findall(context)}
    if (
        asked_roles
        and unit.get("permission") is None
        and not asked_roles & (body_roles or context_roles)
    ):
        return False
    if asked_roles and ACTION.search(question):
        if not set(ACTION.findall(question)) & set(ACTION.findall(body + context)):
            return False
    return True


def attribute_matches(question, unit):
    text = unit["text"]
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    signing = bool(re.search(SIGN, question))
    location = bool(re.search(r"哪里|哪儿|何地|何处|地点|地址|场所", question))
    if signing and location:
        explicit = re.search(rf"(?:{SIGN})(?:地点|地|地址|场所|位置)\s*(?:为|是|在|[:：])", body)
        reverse = re.search(rf"(?:在|于)([^，。；;\n]{{1,40}}?)(?:{SIGN})", body)
        table_location = any(
            re.search(rf"(?:{SIGN})(?:地点|地|地址)", h) for h in unit.get("headers", [])
        )
        section_location = re.search(SIGN, unit.get("section_title") or "") and re.search(
            r"(?m)^(?:[-*]\s*)?(?:地点|地址|场所)\s*(?:为|是|[:：])", body
        )
        if not (
            explicit
            or table_location
            or section_location
            or (reverse and not re.fullmatch(r"[\d年月日\-/:\s]+", reverse[1]))
        ):
            return False
        if re.search(r"未记载|未提供|未公布|不详|未知|尚未", body):
            return False
    if signing and re.search(r"签约方|签署方|签署人|谁签|甲方|乙方", question):
        if not re.search(r"签约方|签署方|签署人|甲方|乙方", body):
            return False
    if re.search(r"(?:还|需要|须|哪些|什么).{0,8}(?:条件|前提)|前置条件", question):
        if not re.search(
            r"仅限|必须|须先|需要先|完成.{0,25}(?:确认|后)|经.{0,25}(?:批准|审批)|前提", body
        ):
            return False
    return True


def complete_scope(question, chosen):
    text = "\n".join(u["text"] + "\n" + (u.get("section_title") or "") for u in chosen)
    return set(EDITIONS.findall(question)) <= set(EDITIONS.findall(text)) and versions(
        question
    ) <= versions(text)
