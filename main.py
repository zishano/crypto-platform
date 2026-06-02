"""
主程序入口 - Phase 1 只读 Web 看板

启动:
  python main.py [--host 0.0.0.0] [--port 8000] [--reload]

逻辑:
  - 创建 FastAPI 应用 (api.build_app)
  - 应用 lifespan 自动启动后台同步线程 + 打开 SQLite
  - uvicorn 提供 HTTP 服务，同时托管 web/ 静态前端
  - Ctrl+C 触发 lifespan 的退出流程，干净收尾
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from api import build_app
from config import load_config


def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crypto Platform Phase 1 (read-only)")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"),
                        help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")),
                        help="监听端口 (默认 8000)")
    parser.add_argument("--reload", action="store_true",
                        help="开发模式：代码变更自动重载（不推荐生产使用）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # 提前加载一次配置，校验 .env 没有携带任何凭证；
    # 失败时直接退出，不会启动 web 服务。
    cfg = load_config()
    _setup_logging(cfg.log_level)

    logger = logging.getLogger("crypto-platform")
    logger.info("Web 看板地址: http://%s:%d/", args.host, args.port)
    logger.info("API 文档:     http://%s:%d/docs", args.host, args.port)

    uvicorn.run(
        "api.server:build_app" if args.reload else build_app(),
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=args.reload,
        log_level=cfg.log_level.lower(),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
