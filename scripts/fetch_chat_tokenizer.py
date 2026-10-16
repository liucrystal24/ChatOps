"""仅下载并核验 Qwen3 官方固定修订 tokenizer，不下载模型权重。"""

import hashlib
import sys
import urllib.request
from pathlib import Path

REVISION = "b968826d9c46dd6066d109eabc6255188de91218"
SHA256 = "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"


def main():
    path = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / "backend/assets/qwen3-tokenizer.json"
    )
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SHA256:
        print("chat tokenizer verified")
        return
    with urllib.request.urlopen(
        f"https://huggingface.co/Qwen/Qwen3-8B/resolve/{REVISION}/tokenizer.json",
        timeout=120,
    ) as response:
        content = response.read(20 * 1024**2)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise SystemExit("chat tokenizer SHA-256 mismatch")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print("chat tokenizer downloaded and verified")


if __name__ == "__main__":
    main()
