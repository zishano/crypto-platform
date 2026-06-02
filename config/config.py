"""
配置加载器 - Phase 1（只读）

铁律 / IRON RULES:
  1. 本模块只读取公共配置（交易所、交易对、频率、数据库路径）。
  2. 严禁出现任何 API_SECRET / PRIVATE_KEY / API_KEY 命名的字段。
  3. 启动时若在 .env / 环境变量里检测到上述敏感命名，立即拒绝启动。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from dotenv import load_dotenv


DEFAULT_EXCHANGE = "binance"
DEFAULT_SYMBOLS: Tuple[str, ...] = ("BTC/USDT", "ETH/USDT")
DEFAULT_SYNC_INTERVAL_SECONDS = 30
DEFAULT_KLINE_TIMEFRAME = "1h"
DEFAULT_KLINE_LIMIT = 200
DEFAULT_DB_PATH = "./market_data.db"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_DISCOVER_TOP_N = 100
DEFAULT_DISCOVER_QUOTE = "USDT"
MIN_DISCOVER_N = 1
MAX_DISCOVER_N = 500

AUTO_SPEC_PATTERN = re.compile(r"^\s*auto(?:\s*:\s*(\d+))?\s*$", re.IGNORECASE)

VALID_TIMEFRAMES = frozenset({
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
})

# 任何命中这些模式的环境变量都会让程序立刻中止启动。
FORBIDDEN_KEY_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"API[_-]?SECRET", re.IGNORECASE),
    re.compile(r"SECRET[_-]?KEY", re.IGNORECASE),
    re.compile(r"PRIVATE[_-]?KEY", re.IGNORECASE),
    re.compile(r"API[_-]?KEY", re.IGNORECASE),
    re.compile(r"ACCESS[_-]?TOKEN", re.IGNORECASE),
    re.compile(r"PASSPHRASE", re.IGNORECASE),
    re.compile(r"MNEMONIC", re.IGNORECASE),
    re.compile(r"SEED[_-]?PHRASE", re.IGNORECASE),
)


@dataclass(frozen=True)
class AppConfig:
    """不可变配置对象。任何修改都应通过 dataclasses.replace 创建新实例。"""

    exchange_name: str
    symbols: Tuple[str, ...]
    sync_interval_seconds: int
    kline_timeframe: str
    kline_limit: int
    db_path: str
    log_level: str
    # 自动发现模式：当 SYMBOLS=auto[:N] 时启用；启动时拉一次 fetch_tickers
    # 按 24h 成交量取 Top N，自动包含当日热门 / 高波动币。
    discover_top_n: Optional[int] = None
    discover_quote: str = DEFAULT_DISCOVER_QUOTE


class ForbiddenSecretError(RuntimeError):
    """检测到禁止的敏感配置项时抛出。"""


def _ensure_no_secrets(env_keys) -> None:
    offenders = []
    for key in env_keys:
        for pattern in FORBIDDEN_KEY_PATTERNS:
            if pattern.search(key):
                offenders.append(key)
                break
    if offenders:
        raise ForbiddenSecretError(
            "Phase 1 是只读版本，禁止配置交易凭证。\n"
            f"检测到敏感字段: {offenders}\n"
            "请从 .env / 环境变量中移除后再启动。"
        )


def _parse_symbols(raw: str) -> Tuple[str, ...]:
    parts = [s.strip().upper() for s in raw.split(",") if s.strip()]
    cleaned = []
    for symbol in parts:
        if "/" not in symbol:
            raise ValueError(f"非法的交易对格式: {symbol!r}，期望形如 'BTC/USDT'")
        base, quote = symbol.split("/", 1)
        if not base or not quote:
            raise ValueError(f"非法的交易对格式: {symbol!r}")
        cleaned.append(f"{base}/{quote}")
    if not cleaned:
        return DEFAULT_SYMBOLS
    return tuple(dict.fromkeys(cleaned))  # 去重，保留顺序


def _parse_symbols_spec(raw: str) -> Tuple[Tuple[str, ...], Optional[int]]:
    """
    解析 SYMBOLS 字段。
      'auto'        -> 自动发现，默认 N=100
      'auto:80'     -> 自动发现，N=80
      'BTC/USDT,..' -> 手动列表（保留旧行为）

    返回: (symbols, discover_top_n)
        discover_top_n 非 None 表示自动模式；symbols 在启动时由 server 填充。
    """
    m = AUTO_SPEC_PATTERN.match(raw or "")
    if m:
        n = int(m.group(1)) if m.group(1) else DEFAULT_DISCOVER_TOP_N
        if not (MIN_DISCOVER_N <= n <= MAX_DISCOVER_N):
            raise ValueError(
                f"SYMBOLS=auto:N 中 N 必须在 {MIN_DISCOVER_N}..{MAX_DISCOVER_N}，收到 {n}"
            )
        return (), n
    return _parse_symbols(raw), None


def _parse_positive_int(raw: str, default: int, name: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，收到: {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"{name} 必须为正整数，收到: {value}")
    return value


def _parse_timeframe(raw: str) -> str:
    tf = (raw or DEFAULT_KLINE_TIMEFRAME).strip()
    if tf not in VALID_TIMEFRAMES:
        raise ValueError(
            f"非法的 K 线周期: {tf!r}，允许: {sorted(VALID_TIMEFRAMES)}"
        )
    return tf


def load_config(env_path: str | Path | None = None) -> AppConfig:
    """从 .env 与进程环境变量中加载配置，返回不可变的 AppConfig。"""
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    env_file = Path(env_path)
    if env_file.exists():
        load_dotenv(env_file, override=False)

    _ensure_no_secrets(os.environ.keys())

    exchange = (os.getenv("EXCHANGE_NAME") or DEFAULT_EXCHANGE).strip().lower()
    if not exchange.isalnum():
        # CCXT 交易所 id 都是纯字母数字（例如 binance、okx、coinbase）
        raise ValueError(f"非法的交易所名称: {exchange!r}")

    symbols, discover_top_n = _parse_symbols_spec(os.getenv("SYMBOLS", ""))
    sync_interval = _parse_positive_int(
        os.getenv("SYNC_INTERVAL_SECONDS"),
        DEFAULT_SYNC_INTERVAL_SECONDS,
        "SYNC_INTERVAL_SECONDS",
    )
    kline_timeframe = _parse_timeframe(os.getenv("KLINE_TIMEFRAME", DEFAULT_KLINE_TIMEFRAME))
    kline_limit = _parse_positive_int(
        os.getenv("KLINE_LIMIT"),
        DEFAULT_KLINE_LIMIT,
        "KLINE_LIMIT",
    )
    db_path = (os.getenv("DB_PATH") or DEFAULT_DB_PATH).strip()
    log_level = (os.getenv("LOG_LEVEL") or DEFAULT_LOG_LEVEL).strip().upper()

    return AppConfig(
        exchange_name=exchange,
        symbols=symbols,
        sync_interval_seconds=sync_interval,
        kline_timeframe=kline_timeframe,
        kline_limit=kline_limit,
        db_path=db_path,
        log_level=log_level,
        discover_top_n=discover_top_n,
        discover_quote=DEFAULT_DISCOVER_QUOTE,
    )
