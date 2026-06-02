"""
只读 API 路由

铁律: 这里只有 GET，没有任何写入端点。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from data import MarketDataError, fetch_coin_info

from .models import (
    CandleModel,
    CoinInfoResponse,
    MetaResponse,
    PricePointModel,
    SnapshotItem,
    SnapshotResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["market"])

WINDOW_TIMEFRAME = "1d"
WINDOW_DAYS = (7, 30, 90)

# 允许用户切换的图表周期（白名单，避免任意值打到交易所）。
ALLOWED_CHART_TIMEFRAMES = ("15m", "1h", "4h", "1d")
TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "12h": 43200,
    "1d": 86400, "3d": 259200, "1w": 604800,
}

# 标签分类 - 手工维护，便于在 UI 上做一眼分类。
# 后期可以挪到独立的 JSON / .toml，但目前 ~50 个 base 就够用。
_TAG_MAJOR = frozenset({
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "LTC", "BCH", "TRX",
})
_TAG_MEME = frozenset({
    "DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "BOME", "MEME", "DOG", "MOG", "BRETT", "POPCAT",
    "TURBO", "GOAT", "PNUT", "NEIRO", "MOODENG",
})
_TAG_AI = frozenset({
    "TAO", "FET", "RNDR", "RENDER", "AGIX", "WLD", "GRT", "OCEAN", "INJ", "NEAR", "AI16Z",
    "VIRTUAL", "AIXBT", "AKT", "IO", "SAHARA",
})
_TAG_L2 = frozenset({
    "ARB", "OP", "MATIC", "POL", "MNT", "METIS", "STRK", "ZK", "BASE", "TAIKO", "SCROLL", "BLAST",
})
_TAG_DEFI = frozenset({
    "UNI", "AAVE", "MKR", "COMP", "CRV", "SUSHI", "SNX", "LDO", "JUP", "RUNE", "PENDLE", "ENA",
    "ONDO", "ETHFI", "RAY",
})
_TAG_GAMING = frozenset({
    "SAND", "MANA", "AXS", "IMX", "GALA", "APE", "ENJ", "FLOW", "BEAM", "PIXEL", "ACE",
})


def _compute_tags(symbol: str, has_long_history: bool) -> List[str]:
    base = symbol.split("/", 1)[0].upper()
    tags: List[str] = []
    if base in _TAG_MAJOR:
        tags.append("主流")
    if base in _TAG_MEME:
        tags.append("Meme")
    if base in _TAG_AI:
        tags.append("AI")
    if base in _TAG_L2:
        tags.append("L2")
    if base in _TAG_DEFI:
        tags.append("DeFi")
    if base in _TAG_GAMING:
        tags.append("Gaming")
    if not has_long_history:
        tags.append("新币")
    return tags


def _window_change_pct(
    daily_klines_desc: list[dict],
    days: int,
    current_price: Optional[float],
) -> Optional[float]:
    """
    daily_klines_desc 已按 timestamp DESC 排序 —— [0] 是今日 (进行中) 的日 K。
    7 天前 = [7] 这根日 K 的 close。
    """
    if current_price is None:
        return None
    if not daily_klines_desc or len(daily_klines_desc) <= days:
        return None
    anchor = daily_klines_desc[days].get("close")
    if anchor in (None, 0):
        return None
    return (current_price - anchor) / anchor * 100.0


def _is_stale(rows: list[dict], timeframe: str) -> bool:
    """K 线最新一根的开盘时间 > 1 个周期之前，认为需要重新拉。"""
    if not rows:
        return True
    period_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
    age_ms = int(time.time() * 1000) - rows[0]["timestamp_ms"]
    return age_ms > period_seconds * 1000


def _ensure_candles(provider, storage, symbol: str, timeframe: str, limit: int) -> list[dict]:
    """
    懒加载：缓存够新就直接返回，否则向交易所拉一次再返回。
    用户切换到本机暂无数据的周期（例如默认 1d 下首次切到 15m）时，会在这里发起一次拉取。
    """
    rows = storage.recent_candles(symbol, timeframe, limit=limit)
    needs_fetch = len(rows) < min(limit, 20) or _is_stale(rows, timeframe)
    if needs_fetch:
        try:
            candles = provider.fetch_historical_candles(symbol, timeframe, limit=limit)
            storage.save_candles(candles)
            rows = storage.recent_candles(symbol, timeframe, limit=limit)
        except MarketDataError as exc:
            logger.warning("按需拉取 %s %s 失败: %s", symbol, timeframe, exc)
    return rows


@router.get("/meta", response_model=MetaResponse)
def get_meta(request: Request) -> MetaResponse:
    cfg = request.app.state.cfg
    return MetaResponse(
        exchange=cfg.exchange_name,
        symbols=list(cfg.symbols),
        kline_timeframe=cfg.kline_timeframe,
        sync_interval_seconds=cfg.sync_interval_seconds,
        phase="phase-1-readonly",
        read_only=True,
    )


@router.get("/prices", response_model=list[PricePointModel])
def get_prices(request: Request) -> list[PricePointModel]:
    cfg = request.app.state.cfg
    storage = request.app.state.storage
    rows = storage.latest_prices_map(cfg.symbols)
    return [PricePointModel(**row) for row in rows.values()]


@router.get("/candles", response_model=list[CandleModel])
def get_candles(
    request: Request,
    symbol: str = Query(..., description="交易对，例如 BTC/USDT"),
    timeframe: Optional[str] = Query(None, description="K 线周期；默认使用配置项"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[CandleModel]:
    cfg = request.app.state.cfg
    storage = request.app.state.storage
    provider = request.app.state.provider

    symbol = symbol.upper().strip()
    if symbol not in cfg.symbols:
        raise HTTPException(status_code=404, detail=f"未配置的交易对: {symbol}")

    tf = timeframe or cfg.kline_timeframe
    if tf not in ALLOWED_CHART_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 K 线周期: {tf}，允许: {list(ALLOWED_CHART_TIMEFRAMES)}",
        )

    rows = _ensure_candles(provider, storage, symbol, tf, limit)
    rows.sort(key=lambda r: r["timestamp_ms"])

    return [
        CandleModel(
            time=int(r["timestamp_ms"] // 1000),
            open=r["open"], high=r["high"], low=r["low"],
            close=r["close"], volume=r["volume"],
        )
        for r in rows
    ]


@router.get("/info/{base}", response_model=CoinInfoResponse)
def get_coin_info_route(base: str) -> CoinInfoResponse:
    """
    返回该币种的背景资料（来源：CoinGecko）。

    仅 GET，无写入。CoinGecko 免费 tier，无凭证。
    内部 30 分钟内存缓存，避免重复打 CoinGecko。
    """
    key = (base or "").upper().strip()
    if not key or "/" in key:
        raise HTTPException(status_code=400, detail="非法的 base 符号")

    info = fetch_coin_info(key)
    if info is None:
        return CoinInfoResponse(
            found=False,
            base=key,
            error="未找到该币种的元数据（CoinGecko 暂未收录或网络故障）。",
        )
    return CoinInfoResponse(found=True, base=key, **info)


@router.get("/snapshot", response_model=SnapshotResponse)
def get_snapshot(request: Request) -> SnapshotResponse:
    """
    返回所有交易对：最新价、24h ticker 字段、7/30/90d 窗口涨跌、市场类型、标签。
    """
    cfg = request.app.state.cfg
    storage = request.app.state.storage
    worker = request.app.state.worker
    market_types = getattr(request.app.state, "market_types", {})

    prices = storage.latest_prices_map(cfg.symbols)
    items: list[SnapshotItem] = []

    for symbol in cfg.symbols:
        row = prices.get(symbol)
        current_price = row["price"] if row else None

        daily = storage.recent_candles(symbol, WINDOW_TIMEFRAME, limit=max(WINDOW_DAYS) + 1)
        change_7d  = _window_change_pct(daily, 7,  current_price)
        change_30d = _window_change_pct(daily, 30, current_price)
        change_90d = _window_change_pct(daily, 90, current_price)
        has_long_history = len(daily) >= 30

        items.append(
            SnapshotItem(
                symbol=symbol,
                market_type=market_types.get(symbol, "spot"),
                tags=_compute_tags(symbol, has_long_history),
                price=current_price,
                price_timestamp_ms=row["timestamp_ms"] if row else None,
                change_24h_abs=row["change_24h_abs"] if row else None,
                change_24h_pct=row["change_24h_pct"] if row else None,
                change_7d_pct=change_7d,
                change_30d_pct=change_30d,
                change_90d_pct=change_90d,
                high_24h=row["high_24h"] if row else None,
                low_24h=row["low_24h"] if row else None,
                volume_24h=row["volume_24h"] if row else None,
                price_count=storage.count_prices(symbol),
                kline_count=storage.count_candles(symbol, cfg.kline_timeframe),
            )
        )

    state = worker.snapshot()
    return SnapshotResponse(
        items=items,
        last_sync_ms=state.last_sync_ms,
        last_error=state.last_error,
        running=state.running,
    )
