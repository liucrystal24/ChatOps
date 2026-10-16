import asyncio
import codecs
import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from app.core.errors import AppError
from app.modules.documents.schemas import filename


class Files:
    def __init__(self, settings):
        self.root = settings.files_dir
        self.max_bytes = settings.upload_max_bytes
        self.disk_limit = settings.files_max_bytes
        self.root.mkdir(parents=True, exist_ok=True)
        self.slots = asyncio.Semaphore(1)

    def path(self, key: str) -> Path:
        # 只接受服务端 UUID；展示文件名从不参与路径拼接。
        UUID(key)
        return self.root / key

    async def store(self, upload):
        try:
            name = filename(upload.filename or "")
        except ValueError:
            raise AppError(422, "INVALID_FILENAME", "文件名无效") from None
        suffix = Path(name).suffix.lower().lstrip(".")
        if suffix not in {"txt", "md", "pdf"}:
            raise AppError(415, "DOCUMENT_TYPE_UNSUPPORTED", "仅支持 UTF-8 TXT、MD 和文字 PDF")
        key = str(uuid4())
        temp = self.root / (key + ".tmp")
        size = 0
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        prefix = b""
        async with self.slots:
            try:
                usage = await asyncio.to_thread(self.usage)
                if usage >= self.disk_limit:
                    raise AppError(413, "FILE_STORAGE_LIMIT", "文件存储配额已用完")
                with temp.open("xb") as output:
                    os.chmod(temp, 0o600)
                    while block := await upload.read(65536):
                        size += len(block)
                        if size > self.max_bytes or usage + size > self.disk_limit:
                            raise AppError(413, "DOCUMENT_TOO_LARGE", "文件超过大小或存储限制")
                        prefix = (prefix + block)[:8]
                        if suffix != "pdf":
                            text = decoder.decode(block)
                            if "\x00" in text:
                                raise ValueError
                        await asyncio.to_thread(output.write, block)
                        digest.update(block)
                    if suffix != "pdf":
                        decoder.decode(b"", final=True)
                    await asyncio.to_thread(output.flush)
                    await asyncio.to_thread(os.fsync, output.fileno())
                if size == 0:
                    raise AppError(422, "DOCUMENT_TEXT_EMPTY", "文件为空")
                if (suffix == "pdf") != prefix.startswith(b"%PDF-"):
                    raise AppError(415, "DOCUMENT_TYPE_UNSUPPORTED", "扩展名与实际格式不符")
                temp.replace(self.path(key))
                media = {"txt": "text/plain", "md": "text/markdown", "pdf": "application/pdf"}[
                    suffix
                ]
                return dict(
                    filename=name,
                    file_type=suffix,
                    media_type=media,
                    storage_key=key,
                    size_bytes=size,
                    content_sha256=digest.hexdigest(),
                )
            except (UnicodeError, ValueError):
                raise AppError(
                    415, "DOCUMENT_TYPE_UNSUPPORTED", "文本必须是有效 UTF-8 且不含二进制内容"
                ) from None
            finally:
                temp.unlink(missing_ok=True)

    def usage(self):
        total = 0
        for path in self.root.iterdir():
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass
        return total

    async def remove(self, key):
        await asyncio.to_thread(self.path(key).unlink, missing_ok=True)
