"""阶段 5 独立 Compose 验收：unit/integration/live、真实 POST SSE 及恢复。"""

import argparse

from verify_stage2 import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--services-only", action="store_true", help="只验证服务，不构建或运行 pytest"
    )
    args = parser.parse_args()
    main(stage=5, services_only=args.services_only)
