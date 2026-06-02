"""
FastAPI 服务装配

启动流程 (lifespan):
  1. 加载配置 -> 拒绝任何凭证字段
  2. 打开 SQLite 存储
  3. 构造只读 CCXT provider
  4. 启动后台同步线程
  5. 把以上句柄挂到 app.state，供路由读取

停止流程:
  - 触发 stop_event -> 等线程退出 -> 关闭 SQLite
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import load_config
from core import SyncWorker
from data import MarketDataError, MarketDataProvider, MarketStorage

from .routes import router as api_router


# 自动发现失败时的最小可用回退列表（保证应用能起来）。
_FALLBACK_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT")


logger = logging.getLogger("crypto-platform")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    cfg = load_config()

    storage = MarketStorage(cfg.db_path)
    provider = MarketDataProvider(cfg.exchange_name)

    # 自动发现 Top N：在 worker 启动前一次性解析出实际的 symbols。
    if cfg.discover_top_n:
        cfg = _resolve_auto_symbols(cfg, provider)

    logger.info(
        "启动 Crypto Platform Phase 1 (只读) 交易所=%s 交易对数=%d 同步=%ds K线=%s",
        cfg.exchange_name, len(cfg.symbols),
        cfg.sync_interval_seconds, cfg.kline_timeframe,
    )
    if cfg.symbols:
        logger.info("前 10 个: %s", list(cfg.symbols[:10]))

    # 缓存市场类型 (spot / swap / ...)，路由层用作 UI 标签。
    market_types = provider.get_market_types(cfg.symbols)

    stop_event = threading.Event()
    worker = SyncWorker(cfg, provider, storage, stop_event)
    worker.start()

    app.state.cfg = cfg
    app.state.storage = storage
    app.state.provider = provider
    app.state.worker = worker
    app.state.stop_event = stop_event
    app.state.market_types = market_types

    try:
        yield
    finally:
        logger.info("停止中...")
        stop_event.set()
        worker.join(timeout=5)
        storage.close()
        logger.info("已停止。")


def _resolve_auto_symbols(cfg, provider):
    """启动期一次性解析自动发现的交易对；失败时回退到最小列表，不让应用启动失败。"""
    n = cfg.discover_top_n
    quote = cfg.discover_quote
    logger.info("自动发现模式：拉取 %s 上按 24h 成交量 Top %d (%s 报价)...",
                cfg.exchange_name, n, quote)
    try:
        discovered = provider.discover_top_symbols(quote, limit=n)
    except MarketDataError as exc:
        logger.error("自动发现失败，回退到 %s: %s", list(_FALLBACK_SYMBOLS), exc)
        discovered = _FALLBACK_SYMBOLS

    if not discovered:
        logger.warning("自动发现返回空列表，回退到 %s", list(_FALLBACK_SYMBOLS))
        discovered = _FALLBACK_SYMBOLS

    return replace(cfg, symbols=tuple(discovered))


def build_app() -> FastAPI:
    app = FastAPI(
        title="Crypto Platform · Phase 1 (Read-Only)",
        version="0.1.0",
        description="本地加密货币行情看板。只读，不接受任何凭证 / 不发起任何下单。",
        lifespan=_lifespan,
    )

    app.include_router(api_router)

    if not WEB_DIR.exists():
        logger.warning("Web 目录不存在: %s", WEB_DIR)
    else:
        # 静态目录必须最后挂载——FileResponse(index.html) 会接管所有未匹配路径。
        app.mount(
            "/",
            StaticFiles(directory=str(WEB_DIR), html=True),
            name="web",
        )

    return app
