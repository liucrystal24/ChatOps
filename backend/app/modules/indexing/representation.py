"""向量输入表示与可定位正文分离；派生字段不得作为引用正文。"""

from app.modules.documents.markdown_tables import tables


def representation_name(title_context, table_context):
    if table_context:
        return "heading-table-rows-v2" if title_context else "table-rows-v2"
    return "heading-body-v2" if title_context else "body-v1"


def embedding_text(text, title=None, title_context=False, table_context=False, target_row=False):
    found = tables(text) if table_context else []
    represented = text
    for table in reversed(found):
        # 字段名与本行值一起编码；原文Chunk及字符偏移保持不变。
        rows = [
            "；".join(
                f"{name}：{value}" for name, value in zip(table.headers, row.values, strict=True)
            )
            for row in (table.rows[-1:] if target_row else table.rows)
        ]
        represented = (
            represented[: table.header_span[0]]
            + "\n".join(rows)
            + represented[table.rows[-1].span[1] :]
        )
    if title and (title_context or found):
        represented = "章节：" + title + "\n" + represented
    return represented


def row_windows(text, left, right, limit):
    block = text[left:right]
    found = tables(block)
    if len(block) <= limit and len(found) == 1:
        table = found[0]
        prefix = block[: table.header_span[0]]
        suffix = block[table.rows[-1].span[1] :]
        if not suffix.strip() and all(
            not line.strip() or line.lstrip().startswith("#") for line in prefix.splitlines()
        ):
            # 每行一个语义锚点，引用正文仍是表头至本行的连续原文窗口。
            return [(left, left + row.span[1], True) for row in table.rows]
    return [(left, right, False)]
