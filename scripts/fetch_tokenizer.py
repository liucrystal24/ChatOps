"""下载官方固定修订的分词器；不下载模型权重、不接触 API Key。"""

import hashlib
import sys
import urllib.request
from pathlib import Path

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
SHA256 = "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08"


def main():
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / "backend/assets/bge-m3-tokenizer.json"
    )
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print("tokenizer verified")
        return
    with urllib.request.urlopen(
        f"https://huggingface.co/BAAI/bge-m3/resolve/{REVISION}/tokenizer.json",
        timeout=120,
    ) as response:
        content = response.read(20 * 1024**2)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise SystemExit("tokenizer SHA-256 mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print("tokenizer downloaded and verified")


if __name__ == "__main__":
    main()
