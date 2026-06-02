"""API 响应模型 - 仅暴露只读字段。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class MetaResponse(BaseModel):
    exchange: str
    symbols: List[str]
    kline_timeframe: str
    sync_interval_seconds: int
    phase: str
    read_only: bool


class PricePointModel(BaseModel):
    symbol: str
    price: float
    timestamp_ms: int
    source: str
    change_24h_abs: Optional[float] = None
    change_24h_pct: Optional[float] = None
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    open_24h: Optional[float] = None


class CandleModel(BaseModel):
    time: int          # K 线开盘时间（秒，TradingView Lightweight Charts 约定）
    open: float
    high: float
    low: float
    close: float
    volume: float


class SnapshotItem(BaseModel):
    symbol: str
    market_type: str = "spot"       # spot / swap / future / option
    tags: List[str] = []            # 主流 / Meme / AI / L2 / DeFi / 新币 / 热门
    price: Optional[float]
    price_timestamp_ms: Optional[int]
    change_24h_abs: Optional[float]
    change_24h_pct: Optional[float]
    change_7d_pct: Optional[float]  # 来自日 K 比较，下同
    change_30d_pct: Optional[float]
    change_90d_pct: Optional[float]
    high_24h: Optional[float]
    low_24h: Optional[float]
    volume_24h: Optional[float]
    price_count: int
    kline_count: int


class SnapshotResponse(BaseModel):
    items: List[SnapshotItem]
    last_sync_ms: Optional[int]
    last_error: Optional[str]
    running: bool


class CoinInfoResponse(BaseModel):
    found: bool
    base: str
    error: Optional[str] = None
    # 以下字段在 found=False 时全部为空。
    id: Optional[str] = None
    symbol: Optional[str] = None
    name: Optional[str] = None
    image: Optional[str] = None
    description_zh: str = ""
    description_en: str = ""
    genesis_date: Optional[str] = None
    market_cap_rank: Optional[int] = None
    categories: List[str] = []
    country_origin: Optional[str] = None
    hashing_algorithm: Optional[str] = None
    links: Dict[str, Any] = {}
