"""仅由 Worker 受限子进程执行；stdout 为私有 IPC，父进程不得记入日志。"""

import json
import re
import sys
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.infrastructure.tokenization import load_tokenizer
from app.modules.indexing.errors import IndexFailure


def parse(
    path,
    file_type,
    tokenizer_path,
    chunk_size=800,
    overlap=120,
    structure="recursive-v1",
    title_context=False,
):
    if overlap >= chunk_size:
        raise IndexFailure("CHUNK_CONFIG_INVALID", "重叠长度必须小于切分长度")
    segments = []
    if file_type == "pdf":
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise IndexFailure("DOCUMENT_ENCRYPTED", "暂不支持加密 PDF")
        if len(reader.pages) > 100:
            raise IndexFailure("DOCUMENT_PAGE_LIMIT", "PDF 不能超过 100 页")
        total = 0
        for page_no, page in enumerate(reader.pages, 1):
            content = page.extract_text() or ""
            total += len(content)
            if total > 2_000_000:
                raise IndexFailure("DOCUMENT_TEXT_LIMIT", "提取正文不能超过 200 万字符")
            segments.append((page_no, content))
    else:
        segments.append((None, Path(path).read_text(encoding="utf-8-sig")))
    if sum(len(text) for _, text in segments) > 2_000_000:
        raise IndexFailure("DOCUMENT_TEXT_LIMIT", "提取正文不能超过 200 万字符")
    if not any(text.strip() for _, text in segments):
        raise IndexFailure("DOCUMENT_TEXT_EMPTY", "未提取到正文；扫描 PDF 暂不支持 OCR")
    tokenizer = load_tokenizer(tokenizer_path)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        keep_separator="end",
    )
    chunks = []
    for page_no, text in segments:
        headings = markdown_headings(text) if file_type == "md" else []
        # 按段落分开可避免一个向量混合多个主题；块正文及偏移始终来自原文。
        ranges = (
            paragraph_ranges(text, headings) if structure == "paragraph-v2" else [(0, len(text))]
        )
        for block_start, block_end in ranges:
            for part in splitter.create_documents([text[block_start:block_end]]):
                start = block_start + part.metadata["start_index"]
                if (
                    part.metadata["start_index"] < 0
                    or text[start : start + len(part.page_content)] != part.page_content
                ):
                    raise IndexFailure("CHUNK_LOCATION_INVALID", "无法确认正文来源位置")
                title = next((title for pos, title in reversed(headings) if pos <= start), None)
                if structure == "recursive-v1" and file_type == "md":
                    legacy = list(
                        re.finditer(r"(?m)^#{1,6}\s+(.+)$", text[: start + len(part.page_content)])
                    )
                    title = legacy[-1][1][:255] if legacy else None
                token_count = len(tokenizer.encode(part.page_content).ids)
                represented = (
                    "章节：" + title + "\n" if title_context and title else ""
                ) + part.page_content
                if len(tokenizer.encode(represented).ids) > 8192:
                    raise IndexFailure("CHUNK_TOKEN_LIMIT", "正文片段超过模型 token 上限")
                chunks.append(
                    dict(
                        text=part.page_content,
                        page_no=page_no,
                        section_title=title,
                        char_start=start,
                        char_end=start + len(part.page_content),
                        token_count=token_count,
                    )
                )
                if len(chunks) > 5000:
                    raise IndexFailure("DOCUMENT_CHUNK_LIMIT", "文档不能超过 5000 个片段")
    if not chunks:
        raise IndexFailure("DOCUMENT_TEXT_EMPTY", "未提取到可索引正文")
    return chunks


def markdown_headings(text):
    headings, stack, fence, offset = [], [], None, 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
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
        elif fence is None and (match := re.match(r" {0,3}(#{1,6})\s+(.+?)\s*$", line)):
            level, title = len(match[1]), re.sub(r"\s+#+$", "", match[2])
            stack = [(n, t) for n, t in stack if n < level] + [(level, title)]
            headings.append((offset, " > ".join(t for _, t in stack)[-255:]))
        offset += len(line)
    return headings


def paragraph_ranges(text, headings):
    # 空行与真实标题形成边界；围栏代码和连续表格行不被空行再次拆开。
    boundaries, offset, fence = {0, len(text)}, 0, None
    heading_positions = {pos for pos, _ in headings}
    pending_heading = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker[1]
            if fence is None:
                pending_heading = False
                fence = (token[0], len(token))
            elif (
                token[0] == fence[0]
                and len(token) >= fence[1]
                and not stripped[len(token) :].strip()
            ):
                fence = None
        elif fence is None:
            if offset in heading_positions:
                boundaries.add(offset)
                pending_heading = True
            elif not line.strip():
                if not pending_heading:
                    boundaries.add(offset + len(line))
            else:
                # 标题必须与首个正文段同块，防止孤立日期/数字失去所属对象。
                if not pending_heading and re.match(r" {0,3}(?:[-+*]|\d+[.)])\s+", line):
                    boundaries.add(offset)
                pending_heading = False
        offset += len(line)
    ordered = sorted(boundaries)
    return [
        (left, right)
        for left, right in zip(ordered, ordered[1:], strict=False)
        if text[left:right].strip()
    ]


def main():
    # Linux/macOS 的资源限制在子进程内设置；解析超时由父进程杀死并回收。
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        chunks = parse(
            *sys.argv[1:4],
            int(sys.argv[4]),
            int(sys.argv[5]),
            sys.argv[6] if len(sys.argv) > 6 else "recursive-v1",
            len(sys.argv) > 7 and sys.argv[7] == "true",
        )
        result = {"chunks": chunks}
    except IndexFailure as exc:
        result = {"error": {"code": exc.code, "message": exc.message}}
    except Exception:
        result = {
            "error": {"code": "DOCUMENT_PARSE_FAILED", "message": "文档损坏或格式不支持，无法解析"}
        }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
