"""阶段 4 独立项目验收；需要本地真实免费 Embedding Key，不打印密钥。"""

import argparse

from verify_stage2 import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--services-only",
        action="store_true",
        help="仅执行已构建服务的网络、故障、持久化与泄漏检查；不运行 pytest",
    )
    args = parser.parse_args()
    main(stage=4, services_only=args.services_only)
