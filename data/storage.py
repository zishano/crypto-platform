"""
本地存储 - SQLite

设计要点:
  1. 两张表: market_prices (含 24h ticker 富字段) / klines (OHLCV)。
  2. 借助 UNIQUE 约束 + INSERT OR IGNORE 实现去重 / 增量写入。
  3. 通过上下文管理器封装事务，避免半成品写入。
  4. 启动时自动给老 DB 加新列（轻量 schema migration）。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .provider import Candle, PricePoint


logger = logging.getLogger(__name__)


SCHEMA_STATEMENTS: Sequence[str] = (
    """
    CREATE TABLE IF NOT EXISTS market_prices (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_ms    INTEGER NOT NULL,
        symbol          TEXT    NOT NULL,
        price           REAL    NOT NULL,
        source          TEXT    NOT NULL,
        change_24h_abs  REAL,
        change_24h_pct  REAL,
        high_24h        REAL,
        low_24h         REAL,
        volume_24h      REAL,
        open_24h        REAL,
        UNIQUE (symbol, timestamp_ms, source)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_market_prices_symbol_ts
        ON market_prices (symbol, timestamp_ms DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS klines (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol       TEXT    NOT NULL,
        timeframe    TEXT    NOT NULL,
        timestamp_ms INTEGER NOT NULL,
        open         REAL    NOT NULL,
        high         REAL    NOT NULL,
        low          REAL    NOT NULL,
        close        REAL    NOT NULL,
        volume       REAL    NOT NULL,
        UNIQUE (symbol, timeframe, timestamp_ms)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_klines_lookup
        ON klines (symbol, timeframe, timestamp_ms DESC)
    """,
)

# 老 DB 升级：把缺失的列加上（不会动既有数据）。
PRICE_COLUMNS_TO_ENSURE = (
    ("change_24h_abs", "REAL"),
    ("change_24h_pct", "REAL"),
    ("high_24h", "REAL"),
    ("low_24h", "REAL"),
    ("volume_24h", "REAL"),
    ("open_24h", "REAL"),
)


class MarketStorage:
    """SQLite 行情存储。线程安全：内部用一把锁保护连接。"""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        self._migrate_price_columns()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "MarketStorage":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- 写入 --------------------------------------------------------------

    def save_price(self, point: PricePoint) -> bool:
        return self.save_prices((point,)) > 0

    def save_prices(self, points: Iterable[PricePoint]) -> int:
        rows = [
            (
                p.timestamp_ms, p.symbol, p.price, p.source,
                p.change_24h_abs, p.change_24h_pct,
                p.high_24h, p.low_24h, p.volume_24h, p.open_24h,
            )
            for p in points
        ]
        if not rows:
            return 0
        with self._transaction() as cur:
            cur.executemany(
                """
                INSERT OR IGNORE INTO market_prices
                    (timestamp_ms, symbol, price, source,
                     change_24h_abs, change_24h_pct,
                     high_24h, low_24h, volume_24h, open_24h)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return cur.rowcount

    def save_candles(self, candles: Iterable[Candle]) -> int:
        rows = [
            (c.symbol, c.timeframe, c.timestamp_ms,
             c.open, c.high, c.low, c.close, c.volume)
            for c in candles
        ]
        if not rows:
            return 0
        with self._transaction() as cur:
            cur.executemany(
                """
                INSERT OR IGNORE INTO klines
                    (symbol, timeframe, timestamp_ms, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            return cur.rowcount

    # --- 查询 --------------------------------------------------------------

    def latest_price(self, symbol: str) -> Optional[dict]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT symbol, price, timestamp_ms, source,
                       change_24h_abs, change_24h_pct,
                       high_24h, low_24h, volume_24h, open_24h
                  FROM market_prices
                 WHERE symbol = ?
                 ORDER BY timestamp_ms DESC
                 LIMIT 1
                """,
                (symbol,),
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def latest_prices_map(self, symbols: Sequence[str]) -> dict[str, dict]:
        """一次性把多个 symbol 的最新行情读出来，返回 dict 便于路由聚合。"""
        out: dict[str, dict] = {}
        for sym in symbols:
            row = self.latest_price(sym)
            if row is not None:
                out[sym] = row
        return out

    def recent_candles(self, symbol: str, timeframe: str, limit: int = 50) -> List[dict]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT timestamp_ms, open, high, low, close, volume
                  FROM klines
                 WHERE symbol = ? AND timeframe = ?
                 ORDER BY timestamp_ms DESC
                 LIMIT ?
                """,
                (symbol, timeframe, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def count_prices(self, symbol: str | None = None) -> int:
        with self._lock:
            if symbol is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM market_prices")
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM market_prices WHERE symbol = ?",
                    (symbol,),
                )
            return int(cur.fetchone()[0])

    def count_candles(self, symbol: str | None = None, timeframe: str | None = None) -> int:
        with self._lock:
            if symbol is None and timeframe is None:
                cur = self._conn.execute("SELECT COUNT(*) FROM klines")
            elif timeframe is None:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM klines WHERE symbol = ?", (symbol,),
                )
            else:
                cur = self._conn.execute(
                    "SELECT COUNT(*) FROM klines WHERE symbol = ? AND timeframe = ?",
                    (symbol, timeframe),
                )
            return int(cur.fetchone()[0])

    # --- 内部 --------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._transaction() as cur:
            for stmt in SCHEMA_STATEMENTS:
                cur.execute(stmt)

    def _migrate_price_columns(self) -> None:
        with self._lock:
            existing = {
                row["name"] for row in self._conn.execute("PRAGMA table_info(market_prices)")
            }
        for name, ddl in PRICE_COLUMNS_TO_ENSURE:
            if name in existing:
                continue
            try:
                with self._transaction() as cur:
                    cur.execute(f"ALTER TABLE market_prices ADD COLUMN {name} {ddl}")
                logger.info("market_prices 增加列 %s", name)
            except sqlite3.OperationalError as exc:
                # 并发 / 已存在等情况下直接忽略
                logger.debug("跳过列 %s 迁移: %s", name, exc)

    @contextmanager
    def _transaction(self):
        with self._lock:
            cur = None
            try:
                self._conn.execute("BEGIN")
                cur = self._conn.cursor()
                yield cur
                self._conn.execute("COMMIT")
            except Exception:
                # 即便底层 I/O 出错 BEGIN 未真正生效，也不要让 rollback 失败遮蔽原始异常。
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
            finally:
                if cur is not None:
                    cur.close()
