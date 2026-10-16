import hashlib
from pathlib import Path

from tokenizers import Tokenizer

from app.core.embedding_profile import TOKENIZER_SHA256
from app.modules.indexing.errors import IndexFailure


def load_tokenizer(path):
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != TOKENIZER_SHA256:
        raise IndexFailure("TOKENIZER_UNAVAILABLE", "分词器缺失或校验失败")
    return Tokenizer.from_file(str(path))
