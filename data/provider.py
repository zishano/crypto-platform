"""
行情采集器 - Phase 1（只读）

铁律 / IRON RULES:
  1. 仅基于 CCXT 公共 API（不传 apiKey / secret），不可下单、不可签名。
  2. 所有公开方法返回不可变 dataclass，避免外部就地修改采集结果。
  3. 为 Phase 2 预留交易接口位（execute_order），当前会显式 raise NotImplementedError。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import ccxt


logger = logging.getLogger(__name__)


class MarketDataError(RuntimeError):
    """采集层统一抛出的错误。"""


@dataclass(frozen=True)
class PricePoint:
    symbol: str
    price: float
    timestamp_ms: int
    source: str
    # ticker 24h 富字段（来自交易所同一次 fetch_ticker，保证和 price 一致）
    change_24h_abs: Optional[float] = None
    change_24h_pct: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    open_24h: Optional[float] = None


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _safe_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _ticker_to_pricepoint(symbol: str, ticker: dict, source: str) -> Optional[PricePoint]:
    """把 CCXT ticker dict 抽成 PricePoint。拿不到价格就返回 None。"""
    price = _safe_float(ticker.get("last")) or _safe_float(ticker.get("close"))
    if price is None:
        bid = _safe_float(ticker.get("bid"))
        ask = _safe_float(ticker.get("ask"))
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
    if price is None:
        return None

    return PricePoint(
        symbol=symbol,
        price=price,
        timestamp_ms=int(ticker.get("timestamp") or time.time() * 1000),
        source=source,
        change_24h_abs=_safe_float(ticker.get("change")),
        change_24h_pct=_safe_float(ticker.get("percentage")),
        high_24h=_safe_float(ticker.get("high")),
        low_24h=_safe_float(ticker.get("low")),
        volume_24h=_safe_float(ticker.get("baseVolume")),
        open_24h=_safe_float(ticker.get("open")),
    )


def _build_public_exchange(exchange_name: str):
    if not hasattr(ccxt, exchange_name):
        raise MarketDataError(f"CCXT 不支持的交易所: {exchange_name!r}")
    exchange_cls = getattr(ccxt, exchange_name)
    config = {
        "enableRateLimit": True,
        "timeout": 60000,
    }
    return exchange_cls(config)


class MarketDataProvider:
    """
    交易所行情采集器（只读）。

    用法:
        provider = MarketDataProvider("gate")
        price = provider.fetch_current_price("BTC/USDT")
        tickers = provider.fetch_tickers(["BTC/USDT", "ETH/USDT"])
        candles = provider.fetch_historical_candles("BTC/USDT", "1h", limit=200)
    """

    def __init__(self, exchange_name: str, *, max_retries: int = 3, retry_backoff_seconds: float = 1.5):
        if max_retries < 1:
            raise ValueError("max_retries 必须 >= 1")
        self._exchange_name = exchange_name
        self._exchange = _build_public_exchange(exchange_name)
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._guard_no_credentials()

    @property
    def exchange_name(self) -> str:
        return self._exchange_name

    # --- 单个交易对 ---------------------------------------------------------

    def fetch_current_price(self, symbol: str) -> PricePoint:
        symbol = self._normalize_symbol(symbol)

        def _do() -> PricePoint:
            ticker = self._exchange.fetch_ticker(symbol)
            point = _ticker_to_pricepoint(symbol, ticker, self._exchange_name)
            if point is None:
                raise MarketDataError(f"{symbol} ticker 没有可用价格字段")
            return point

        return self._with_retry(f"fetch_current_price({symbol})", _do)

    # --- 批量交易对（推荐：40+ 币时单次请求 vs 40 次）----------------------

    def fetch_tickers(self, symbols: Sequence[str]) -> Dict[str, PricePoint]:
        """
        批量拉取多个 ticker。返回 { symbol: PricePoint }。
        失败的单个 symbol 不会让整体失败，会被静默跳过（带 warning 日志）。
        """
        normalized = [self._normalize_symbol(s) for s in symbols]
        wanted = set(normalized)

        if not self._exchange.has.get("fetchTickers"):
            # 交易所不支持批量，退化为逐个调用。
            return self._fallback_individual(normalized)

        def _do() -> Dict[str, PricePoint]:
            try:
                tickers = self._exchange.fetch_tickers(list(normalized))
            except (ccxt.BadSymbol, ccxt.NotSupported, ccxt.ArgumentsRequired):
                # 某些交易所不允许带 symbols 过滤；那就拉全部然后客户端过滤。
                tickers = self._exchange.fetch_tickers()

            result: Dict[str, PricePoint] = {}
            for sym, t in tickers.items():
                if sym not in wanted:
                    continue
                point = _ticker_to_pricepoint(sym, t, self._exchange_name)
                if point is not None:
                    result[sym] = point
            return result

        return self._with_retry("fetch_tickers", _do)

    def _fallback_individual(self, symbols: Sequence[str]) -> Dict[str, PricePoint]:
        result: Dict[str, PricePoint] = {}
        for s in symbols:
            try:
                result[s] = self.fetch_current_price(s)
            except MarketDataError as exc:
                logger.warning("跳过 %s: %s", s, exc)
        return result

    # --- 市场元信息（类型 / 是否活跃）-------------------------------------

    def get_market_types(self, symbols: Sequence[str]) -> Dict[str, str]:
        """
        返回 { symbol: 'spot' | 'swap' | 'future' | 'option' | 'unknown' }。
        失败时静默回退到 'spot'，不让 UI 因此挂掉。
        """
        try:
            self._exchange.load_markets()
        except Exception as exc:  # noqa: BLE001
            logger.warning("load_markets 失败，市场类型回退到 spot: %s", exc)
            return {s: "spot" for s in symbols}

        out: Dict[str, str] = {}
        for s in symbols:
            m = self._exchange.markets.get(s)
            t = (m.get("type") if m else None) or "spot"
            out[s] = t
        return out

    # --- 自动发现 Top N -----------------------------------------------------

    # 稳定币 / 包装资产 / 法币代币 —— 自动发现时过滤掉，
    # 它们波动几乎为 0，会把"涨跌榜"打散。
    _STABLE_OR_FIAT_BASES = frozenset({
        "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDD", "PYUSD",
        "USDE", "USDP", "GUSD", "LUSD", "EURI", "EURS", "EURC", "EURT",
        "XAUT", "PAXG",
    })

    # 杠杆代币后缀（Gate / Binance / FTX 等的命名）。
    _LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR", "HALF")

    def discover_top_symbols(
        self,
        quote: str = "USDT",
        limit: int = 100,
    ) -> Tuple[str, ...]:
        """
        从交易所一次性拉全部 ticker，按 24h 报价币种成交量降序取 Top N。
        过滤掉：非目标 quote / 杠杆代币 / 稳定币本身。
        """
        quote_upper = quote.upper()

        def _do() -> Tuple[str, ...]:
            tickers = self._exchange.fetch_tickers()
            rows = []
            for sym, t in tickers.items():
                if not isinstance(sym, str) or "/" not in sym:
                    continue
                base, sym_quote = sym.split("/", 1)
                if sym_quote.upper() != quote_upper:
                    continue
                base_upper = base.upper()
                if base_upper in self._STABLE_OR_FIAT_BASES:
                    continue
                if any(base_upper.endswith(suf) for suf in self._LEVERAGED_SUFFIXES):
                    continue
                volume = _safe_float(t.get("quoteVolume"))
                if volume is None:
                    last = _safe_float(t.get("last")) or _safe_float(t.get("close"))
                    base_vol = _safe_float(t.get("baseVolume"))
                    if last is not None and base_vol is not None:
                        volume = last * base_vol
                if volume is None or volume <= 0:
                    continue
                rows.append((sym, volume))
            rows.sort(key=lambda x: -x[1])
            return tuple(sym for sym, _ in rows[:limit])

        return self._with_retry(f"discover_top_symbols({quote},{limit})", _do)

    # --- K 线 --------------------------------------------------------------

    def fetch_historical_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 200,
        since_ms: int | None = None,
    ) -> Tuple[Candle, ...]:
        symbol = self._normalize_symbol(symbol)
        if not self._exchange.has.get("fetchOHLCV"):
            raise MarketDataError(f"交易所 {self._exchange_name} 不支持 fetchOHLCV")
        if limit <= 0:
            raise ValueError("limit 必须为正整数")

        def _do() -> Tuple[Candle, ...]:
            rows = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
            return tuple(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
                for row in rows
                if row and len(row) >= 6
            )

        return self._with_retry(f"fetch_historical_candles({symbol},{timeframe})", _do)

    # --- Phase 2 占位 ------------------------------------------------------

    def execute_order(self, *args, **kwargs):
        raise NotImplementedError(
            "Phase 1 是只读版本，不支持任何下单操作。"
            "请等待 Phase 2 鉴权与交易模块上线后再调用。"
        )

    # --- 内部 --------------------------------------------------------------

    def _guard_no_credentials(self) -> None:
        for attr in ("apiKey", "secret", "uid", "password", "privateKey"):
            if getattr(self._exchange, attr, None):
                raise MarketDataError(
                    f"检测到交易所实例携带敏感字段 {attr!r}，Phase 1 不允许。"
                )

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        if not symbol or "/" not in symbol:
            raise ValueError(f"非法交易对: {symbol!r}")
        return symbol.upper()

    def _with_retry(self, op_name: str, fn):
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return fn()
            except ccxt.NetworkError as exc:
                last_exc = exc
                logger.warning(
                    "[%s] 网络错误，重试 %d/%d: %s",
                    op_name, attempt, self._max_retries, exc,
                )
            except ccxt.ExchangeError as exc:
                raise MarketDataError(f"{op_name} 失败: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "[%s] 未预期错误，重试 %d/%d: %s",
                    op_name, attempt, self._max_retries, exc,
                )
            time.sleep(self._retry_backoff_seconds * attempt)

        raise MarketDataError(f"{op_name} 多次重试后仍失败: {last_exc}") from last_exc
