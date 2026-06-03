# Crypto Platform · Phase 1（只读 Web 版）

本地加密货币行情看板。**只读**，不接受任何凭证 / 不发起任何下单。

---

## 🚀 启动（最短路径）

> 项目已经在 `/home/unix/crypto-platform`，依赖装好在 `.venv/`，配置写在 `.env`，可以直接跑。

```bash
cd /home/unix/crypto-platform
.venv/bin/python main.py
```

启动后看到这段日志就 OK：

```
| INFO | crypto-platform | Web 看板地址: http://127.0.0.1:8000/
| INFO | crypto-platform | API 文档:     http://127.0.0.1:8000/docs
| INFO | uvicorn | Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

浏览器打开 **<http://127.0.0.1:8000/>** 就能看到深色看板（卡片 + K 线图）。

按 `Ctrl+C` 优雅退出。

---

## 第一次在新机器上启动（完整流程）

```bash
# 1. 进入项目
cd /home/unix/crypto-platform

# 2. 创建虚拟环境
python3 -m venv .venv

# 3. 安装依赖（国内可加清华镜像）
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
# 慢的话: -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4. 准备配置
cp .env.example .env
# 按需改 EXCHANGE_NAME / SYMBOLS 等

# 5. 启动
.venv/bin/python main.py
```

---

## 启动参数

```bash
.venv/bin/python main.py                          # 默认 127.0.0.1:8000
.venv/bin/python main.py --port 8765              # 换端口
.venv/bin/python main.py --host 0.0.0.0 --port 80 # 局域网可访问
.venv/bin/python main.py --reload                 # 开发模式：代码改动自动重启
```

---

## 启动后的页面

| URL | 用途 |
|-----|------|
| <http://127.0.0.1:8000/>            | **主看板**（K 线 + 40 币列表 + 收藏） |
| <http://127.0.0.1:8000/docs>        | 自动 OpenAPI 文档（可直接试调用） |
| <http://127.0.0.1:8000/api/meta>    | 当前配置（交易所、交易对、周期…） |
| <http://127.0.0.1:8000/api/snapshot>| 行情快照 JSON（含 40 个币 24h 涨跌） |
| <http://127.0.0.1:8000/api/candles?symbol=BTC/USDT&limit=200> | K 线 OHLCV |

---

## 看板交互

- **顶部主图**：K 线 + 全部 stats（最新价 / 今日 / 7d / 30d / 90d / 24h 高低 / 量）。
- **K 线周期**：图表右上角按钮组 `15m / 1h / 4h / 1d`，默认 `1d`。切换会按需向交易所拉一次该周期的历史，之后由后端缓存到 SQLite。偏好存浏览器 `localStorage`。
- **K 线视图保留**：自动刷新时**保留**你当前的缩放和平移；右上 `↻` 单击重置（全景），双击滚到最新。
- **币种信息弹窗**：列表里任意一行点**交易对名**（带蓝色下划线提示），弹出 CoinGecko 背景资料：项目简介（含创立者/创立年份/创立原因，中文优先）、创世日期、市值排名、哈希算法、起源国家、分类标签、官网/白皮书/GitHub/Twitter/Reddit/区块浏览器链接。后端 30 分钟内存缓存。ESC、点空白处、X 按钮都能关。
- **列表列**：★ / 交易对(+标签) / 最新价 / 今日 / 7 天 / 30 天 / 90 天 / 24h 量。
- **币种标签**：
  - 市场类型：`现货 / 永续 / 合约 / 期权 / 杠杆`（CCXT market metadata）
  - 分类：`主流 / Meme / AI / L2 / DeFi / Gaming / 新币`（手工 + 数据驱动；"新币" 会在历史 K 线少于 30 天时自动打）
- **关键词搜索**：列表上方搜索框实时过滤。匹配范围：交易对（`BTC`/`BTC/USDT`）、市场类型（`现货`/`spot`）、分类标签（`Meme` / `AI` / `主流`…）。按 `/` 在任意位置聚焦搜索框，按 `Esc` 一次清空、再按一次失焦。
- **列表 Tab**：「全部」/「★ 收藏」。
- **排序**：四个窗口（今日 / 7d / 30d / 90d）都可以涨幅↓/跌幅↓ 排序，外加成交量、价格、符号。
- **收藏**：每行左侧 ★ 切换，存浏览器 `localStorage`，**不上传后端**。
- **状态点**：顶部绿色脉冲 = 同步中，红色 = 同步异常。

> 涨跌指标全部从本地 K 线自己算，**不再相信 ticker 字段**（不同交易所语义不同——比如 Gate 的 ticker.percentage 其实是今日，不是 24h 滚动）：
> - **今日**：当前价 vs 今日（仍在进行的）日 K open（= UTC 00:00 开盘价）
> - **24h 滚动**：当前价 vs 24 根 1h K 线前的 open（≈ 24 小时前那一刻）
> - **7 / 30 / 90 天**：当前价 vs N 根日 K 前的 close
>
> 三套 K 线：日 K 100 根（窗口）+ 1h × 25 根（真 24h 滚动）+ 图表周期。启动一次性全拉，之后日 K 1h 刷新、1h K 线 30min 刷新、图表 K 线 5min 刷新。

## 修改配置

编辑 `.env` 后**重启进程**生效：

```dotenv
EXCHANGE_NAME=gate                          # binance / okx / gate / kucoin ...
SYMBOLS=auto:100                            # 见下方说明
SYNC_INTERVAL_SECONDS=30                    # ticker (含 24h 涨跌) 刷新节奏
KLINE_TIMEFRAME=1h                          # 1m/5m/15m/1h/4h/1d ...
KLINE_LIMIT=200
DB_PATH=./market_data.db
LOG_LEVEL=INFO
```

### SYMBOLS 两种模式

| 写法 | 行为 |
|------|------|
| `auto` | 自动发现 Top 100 USDT 对（按 24h 成交量） |
| `auto:80` | Top 80（1..500 任意） |
| `BTC/USDT,ETH/USDT,DOGE/USDT` | 手动指定 |

**自动模式**会在启动时拉一次 `fetchTickers`，按 24h 报价币种成交量降序取 Top N，
并自动过滤稳定币（USDC/DAI/FDUSD…）和杠杆代币（BTC3L/BTC3S/ETHUP…）。

为什么默认 auto？BTC/ETH 这种主流币天然波动小（1-5%/天）；想看到 +20% / -20%
的强势 / 暴跌币，必须包括 meme、AI 概念、新币、小市值——而这些恰好就是
24h 成交量榜里的常客。auto 模式每次启动都自动捕捉到当下最热的标的。

> 想刷新榜单？重启进程即可。

> 🚫 任何 `API_SECRET=` / `PRIVATE_KEY=` / `API_KEY=` 等敏感字段会让程序**立即拒绝启动**。
> 这是 Phase 1 的硬性约束，等 Phase 2 鉴权模块独立上线再用。

---

## 启动失败排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `ForbiddenSecretError` | 环境变量里有 API_SECRET 之类的字段 | `unset API_SECRET` 后重试 |
| `address already in use` | 8000 端口被占 | `--port 8765` 换端口 |
| 浏览器一直 "连接中…" | 后端没起来 | 看终端日志，检查是否被防火墙拦 |
| 卡片全是 "—" | 还没拉到数据 | 首次启动等 5–30s |
| `binance GET ... timeout` | 出口 IP 在 Binance 黑名单 | `.env` 改 `EXCHANGE_NAME=gate` 或 `okx` |
| OKX 卡很久 | `loadMarkets` 全量拉数百个交易对 | 等首次完成即可 |

---

## 项目结构

```
crypto-platform/
├── .env / .env.example      # 公共配置（已硬性拦截凭证）
├── requirements.txt
├── config/config.py         # 不可变 AppConfig + secret 守卫
├── data/
│   ├── provider.py          # CCXT 公共行情采集器
│   └── storage.py           # SQLite (market_prices / klines)
├── core/sync_worker.py      # 后台定时同步线程
├── api/
│   ├── server.py            # FastAPI + lifespan
│   ├── routes.py            # GET /api/{meta,prices,candles,snapshot}
│   └── models.py            # Pydantic 响应模型
├── web/
│   ├── index.html           # 单页看板
│   ├── styles.css           # 深色主题 (OKLCH)
│   └── app.js               # 轮询 + lightweight-charts
├── main.py                  # uvicorn 入口
└── market_data.db           # 运行后自动生成
```

---

## 技术选型

| 角色 | 选型 | 参考开源仓库 |
|------|------|--------------|
| 行情采集 | [CCXT](https://github.com/ccxt/ccxt) | 100+ 交易所统一封装，事实标准 |
| 币种元数据 | [CoinGecko](https://www.coingecko.com/zh/api/documentation) Free Tier | 无 Key 公共 API，简介/链接/创世日期等 |
| 后端 | [FastAPI](https://github.com/tiangolo/fastapi) + uvicorn | 异步、自带 OpenAPI |
| 本地存储 | SQLite（标准库） | UNIQUE 约束做去重 |
| K 线图 | [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) | 45KB，MIT，加密看板事实标准 |
| 前端 | 原生 HTML/CSS/JS | 无构建链 |

---

## 安全边界（Phase 2 前不可越过）

| 约束 | 实现位置 |
|------|----------|
| 启动时拒绝任何凭证 | `config/config.py::_ensure_no_secrets` |
| CCXT 只走 Public API | `data/provider.py::_build_public_exchange` + `_guard_no_credentials` |
| `/api/*` 全部 GET，无写入端点 | `api/routes.py` |
| `execute_order` 调用即抛错 | `data/provider.py::MarketDataProvider.execute_order` |

Phase 2 前，本只读核心**不应**被加入任何凭证、签名、私钥逻辑。
