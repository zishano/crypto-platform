"""
后台同步线程

职责:
  1. 定时批量拉取每个交易对的最新 ticker（含 24h 涨跌）-> 写入 market_prices。
  2. 拉两套 K 线：
        - 图表用：cfg.kline_timeframe (默认 1h)，每 5 分钟刷新
        - 窗口涨跌用：1d 周期 100 根，每小时刷新，供 7d/30d/90d 计算
  3. 暴露当前同步状态（last_sync_ms / last_error）供前端查询。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Iterable

from config import AppConfig
from data import MarketDataError, MarketDataProvider, MarketStorage


logger = logging.getLogger(__name__)

KLINE_REFRESH_SECONDS = 300        # 5 分钟刷一次图表用 K 线
WINDOW_TIMEFRAME = "1d"
WINDOW_LIMIT = 100                 # 100 天日 K，覆盖 90d 窗口
WINDOW_REFRESH_SECONDS = 3600      # 1 小时刷一次日 K（足够，日 K 一天只滚动一次）

# 真正的 24h 滚动涨跌需要 1h K 线对比。
# 25 根 = 当前小时 + 过去 24 小时；够算 hourly[24].open vs current_price。
ROLLING_24H_TIMEFRAME = "1h"
ROLLING_24H_LIMIT = 25
ROLLING_24H_REFRESH_SECONDS = 1800  # 30 分钟刷一次足够


@dataclass(frozen=True)
class SyncState:
    last_sync_ms: int | None
    last_error: str | None
    running: bool


class SyncWorker(threading.Thread):
    def __init__(
        self,
        cfg: AppConfig,
        provider: MarketDataProvider,
        storage: MarketStorage,
        stop_event: threading.Event,
    ):
        super().__init__(name="sync-worker", daemon=True)
        self._cfg = cfg
        self._provider = provider
        self._storage = storage
        self._stop_event = stop_event
        self._last_sync_ms: int | None = None
        self._last_error: str | None = None
        self._state_lock = threading.Lock()

    # --- 状态查询 ----------------------------------------------------------

    def snapshot(self) -> SyncState:
        with self._state_lock:
            return SyncState(
                last_sync_ms=self._last_sync_ms,
                last_error=self._last_error,
                running=self.is_alive() and not self._stop_event.is_set(),
            )

    # --- 主循环 ------------------------------------------------------------

    def run(self) -> None:
        # 启动一次性补齐三套 K 线：日 K (窗口涨跌) / 1h (真 24h 滚动) / 图表用。
        # 图表周期撞库的话靠 UNIQUE 兜底，不会重复落地。
        self._sync_candles(self._cfg.symbols, WINDOW_TIMEFRAME, WINDOW_LIMIT)
        self._sync_candles(self._cfg.symbols, ROLLING_24H_TIMEFRAME, ROLLING_24H_LIMIT)
        if self._cfg.kline_timeframe not in (WINDOW_TIMEFRAME, ROLLING_24H_TIMEFRAME):
            self._sync_candles(self._cfg.symbols, self._cfg.kline_timeframe, self._cfg.kline_limit)
        last_chart_sync = time.monotonic()
        last_window_sync = time.monotonic()
        last_24h_sync = time.monotonic()

        while not self._stop_event.is_set():
            self._sync_prices(self._cfg.symbols)
            now = time.monotonic()
            if now - last_chart_sync >= KLINE_REFRESH_SECONDS:
                self._sync_candles(self._cfg.symbols, self._cfg.kline_timeframe, self._cfg.kline_limit)
                last_chart_sync = now
            if now - last_window_sync >= WINDOW_REFRESH_SECONDS:
                self._sync_candles(self._cfg.symbols, WINDOW_TIMEFRAME, WINDOW_LIMIT)
                last_window_sync = now
            if now - last_24h_sync >= ROLLING_24H_REFRESH_SECONDS:
                self._sync_candles(self._cfg.symbols, ROLLING_24H_TIMEFRAME, ROLLING_24H_LIMIT)
                last_24h_sync = now
            self._stop_event.wait(self._cfg.sync_interval_seconds)

    def _sync_prices(self, symbols: Iterable[str]) -> None:
        symbol_list = list(symbols)
        if not symbol_list:
            return
        try:
            tickers = self._provider.fetch_tickers(symbol_list)
        except MarketDataError as exc:
            logger.error("批量拉取行情失败: %s", exc)
            self._mark_sync_err(str(exc))
            return

        if self._stop_event.is_set():
            return

        missing = [s for s in symbol_list if s not in tickers]
        if missing:
            logger.warning("交易所没有返回这些交易对的 ticker: %s", missing[:10])

        inserted = self._storage.save_prices(tickers.values())
        logger.debug("prices saved=%d / fetched=%d", inserted, len(tickers))
        self._mark_sync_ok()

    def _sync_candles(self, symbols: Iterable[str], timeframe: str, limit: int) -> None:
        total = 0
        added = 0
        for symbol in symbols:
            if self._stop_event.is_set():
                return
            try:
                candles = self._provider.fetch_historical_candles(symbol, timeframe, limit=limit)
                added += self._storage.save_candles(candles)
                total += len(candles)
            except MarketDataError as exc:
                logger.warning("拉取 %s %s K线失败: %s", symbol, timeframe, exc)
                self._mark_sync_err(str(exc))
        logger.info("K 线 %s 同步完成 +%d / 拉取 %d", timeframe, added, total)
        if added or total:
            self._mark_sync_ok()

    def _mark_sync_ok(self) -> None:
        with self._state_lock:
            self._last_sync_ms = int(time.time() * 1000)
            self._last_error = None

    def _mark_sync_err(self, msg: str) -> None:
        with self._state_lock:
            self._last_error = msg
