"""简单Markdown表格的原文定位；拒绝缺列/多列，不推断合并单元格。"""

import re
from dataclasses import dataclass


def cells(line):
    # 转义竖线是单元格正文；偶数个反斜杠之后的竖线仍是列边界。
    values, start, slashes = [], 0, 0
    for index, char in enumerate(line):
        if char == "|" and slashes % 2 == 0:
            values.append(line[start:index].strip())
            start = index + 1
        slashes = slashes + 1 if char == "\\" else 0
    values.append(line[start:].strip())
    if line.lstrip().startswith("|"):
        values.pop(0)
    if line.rstrip().endswith("|") and not values[-1]:
        values.pop()
    return values


@dataclass(frozen=True)
class Row:
    values: tuple[str, ...]
    span: tuple[int, int]


@dataclass(frozen=True)
class Table:
    headers: tuple[str, ...]
    header_span: tuple[int, int]
    separator_span: tuple[int, int]
    rows: tuple[Row, ...]


def tables(text):
    lines = list(re.finditer(r"[^\n]+", text))
    result, index, fence = [], 0, None
    while index < len(lines):
        line = lines[index]
        stripped = line.group().lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker[1]
            if fence is None:
                fence = (token[0], len(token))
            elif (
                token[0] == fence[0]
                and len(token) >= fence[1]
                and not stripped[len(token) :].strip()
            ):
                fence = None
            index += 1
            continue
        if fence or index + 1 >= len(lines) or "|" not in line.group():
            index += 1
            continue
        separator = lines[index + 1]
        headers, marks = cells(line.group()), cells(separator.group())
        if (
            text[line.end() : separator.start()] not in ("\n", "\r\n")
            or not headers
            or len(headers) != len(marks)
            or not all(re.fullmatch(r":?-{3,}:?", mark.replace(" ", "")) for mark in marks)
        ):
            index += 1
            continue
        rows, cursor, valid = [], index + 2, True
        previous = separator
        while cursor < len(lines):
            candidate = lines[cursor]
            if "|" not in candidate.group() or text[previous.end() : candidate.start()] not in (
                "\n",
                "\r\n",
            ):
                break
            values = cells(candidate.group())
            if len(values) != len(headers):
                valid = False
            rows.append(Row(tuple(values), (candidate.start(), candidate.end())))
            previous, cursor = candidate, cursor + 1
        if valid and rows:
            result.append(
                Table(
                    tuple(headers),
                    (line.start(), line.end()),
                    (separator.start(), separator.end()),
                    tuple(rows),
                )
            )
        index = max(cursor, index + 1)
    return result
