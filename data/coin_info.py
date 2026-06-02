"""
CoinGecko 公共 API 客户端

用途：为 UI 提供币种背景信息（简介 / 创世日期 / 链接 / 分类），
辅助用户在 Phase 2 上线交易前做尽职调查。

铁律仍然适用：
  - 只读，无 API Key，无凭证；CoinGecko 免费 tier 即可。
  - 命中速率限制（429）会被悄悄吞掉返回 None，不抛错卡死 UI。
  - 内存 TTL 缓存：ID 映射 6h，详情 30min，减少对 CoinGecko 的请求。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
import threading
import time
from typing import Optional

import requests


logger = logging.getLogger(__name__)


COINGECKO_API = "https://api.coingecko.com/api/v3"
TIMEOUT_SECONDS = 12
ID_CACHE_TTL = 6 * 3600          # 币种 ID 极少变，缓存 6h
INFO_CACHE_TTL = 30 * 60         # 详情 30min 足够新

# 手工覆盖：避免 /search 在重名 / 改名时挑错。
# 例如 POL 不要被解析成已下架的 MATIC，TON 不要被错配到 TonCoin 同名山寨。
_ID_OVERRIDES = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "BNB":  "binancecoin",
    "XRP":  "ripple",
    "USDT": "tether",
    "USDC": "usd-coin",
    "ADA":  "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "TRX":  "tron",
    "DOT":  "polkadot",
    "POL":  "polygon-ecosystem-token",
    "MATIC": "matic-network",
    "TON":  "the-open-network",
    "LINK": "chainlink",
    "LTC":  "litecoin",
    "SHIB": "shiba-inu",
    "BCH":  "bitcoin-cash",
    "UNI":  "uniswap",
    "ATOM": "cosmos",
    "NEAR": "near",
    "ETC":  "ethereum-classic",
    "XLM":  "stellar",
    "FIL":  "filecoin",
    "APT":  "aptos",
    "ARB":  "arbitrum",
    "OP":   "optimism",
    "AAVE": "aave",
    "TIA":  "celestia",
    "SAND": "the-sandbox",
    "SUI":  "sui",
    "PEPE": "pepe",
    "GRT":  "the-graph",
    "SEI":  "sei-network",
    "SUSHI": "sushi",
    "WIF":  "dogwifcoin",
    "CRV":  "curve-dao-token",
    "SNX":  "havven",
    "INJ":  "injective-protocol",
    "RUNE": "thorchain",
    "WLD":  "worldcoin-wld",
    "APE":  "apecoin",
}


class _TtlCache:
    """线程安全的简易 TTL 缓存。"""

    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._store: dict = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            ts, val = entry
            if time.time() - ts > self._ttl:
                del self._store[key]
                return None
            return val

    def set(self, key: str, value) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)


_id_cache = _TtlCache(ID_CACHE_TTL)
_info_cache = _TtlCache(INFO_CACHE_TTL)


def _http_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    url = f"{COINGECKO_API}{path}"
    try:
        resp = requests.get(url, params=params or {}, timeout=TIMEOUT_SECONDS)
        if resp.status_code == 429:
            logger.warning("CoinGecko 429 rate limited on %s", path)
            return None
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("CoinGecko %s 失败: %s", path, exc)
        return None


def resolve_coingecko_id(base_symbol: str) -> Optional[str]:
    """CCXT base 符号 (BTC) → CoinGecko coin id (bitcoin)。"""
    key = (base_symbol or "").upper().strip()
    if not key:
        return None

    override = _ID_OVERRIDES.get(key)
    if override:
        return override

    cached = _id_cache.get(key)
    if cached is not None:
        return cached or None  # 空串 = 负缓存

    data = _http_get("/search", params={"query": key})
    coins = (data or {}).get("coins") or []

    cgid: Optional[str] = None
    if coins:
        exact = [c for c in coins if (c.get("symbol") or "").upper() == key]
        ranked = sorted(
            exact or coins,
            key=lambda c: c.get("market_cap_rank") or 10**9,
        )
        cgid = ranked[0].get("id")

    _id_cache.set(key, cgid or "")
    return cgid


_HTML_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def _to_text(html_str: Optional[str]) -> str:
    """CoinGecko 简介常带 <a>/<i>/<br> 标签，统一剥成纯文本。"""
    if not html_str:
        return ""
    s = _HTML_BR.sub("\n", html_str)
    s = _HTML_TAG.sub("", s)
    return html_lib.unescape(s).strip()


def fetch_coin_info(base_symbol: str) -> Optional[dict]:
    """返回结构化的币种背景信息 dict，未找到则 None。"""
    cgid = resolve_coingecko_id(base_symbol)
    if not cgid:
        return None

    cached = _info_cache.get(cgid)
    if cached is not None:
        return cached

    data = _http_get(
        f"/coins/{cgid}",
        params={
            "localization": "true",
            "tickers": "false",
            "market_data": "false",
            "community_data": "false",
            "developer_data": "false",
            "sparkline": "false",
        },
    )
    if not data:
        return None

    desc = data.get("description") or {}
    desc_zh = desc.get("zh") or desc.get("zh-cn") or ""
    desc_en = desc.get("en") or ""

    links = data.get("links") or {}
    twitter = links.get("twitter_screen_name") or None
    image = (data.get("image") or {}).get("small") \
        or (data.get("image") or {}).get("thumb")

    info = {
        "id": cgid,
        "symbol": (data.get("symbol") or "").upper(),
        "name": data.get("name"),
        "image": image,
        "description_zh": _to_text(desc_zh),
        "description_en": _to_text(desc_en),
        "genesis_date": data.get("genesis_date"),
        "market_cap_rank": data.get("market_cap_rank"),
        "categories": [c for c in (data.get("categories") or []) if c],
        "country_origin": data.get("country_origin") or None,
        "hashing_algorithm": data.get("hashing_algorithm") or None,
        "links": {
            "homepage": [u for u in (links.get("homepage") or []) if u][:3],
            "whitepaper": links.get("whitepaper") or None,
            "github": [u for u in ((links.get("repos_url") or {}).get("github") or []) if u][:3],
            "twitter": f"https://twitter.com/{twitter}" if twitter else None,
            "reddit": links.get("subreddit_url") or None,
            "explorer": [u for u in (links.get("blockchain_site") or []) if u][:3],
        },
    }
    _info_cache.set(cgid, info)
    return info
