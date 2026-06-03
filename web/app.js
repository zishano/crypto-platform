/**
 * Crypto Platform · Phase 1 前端
 *
 * 设计:
 *  - 无构建链。纯原生 JS + lightweight-charts CDN。
 *  - 轮询 /api/snapshot (5s) 与 /api/candles (15s)。
 *  - 收藏：仅 localStorage，不污染只读后端 API。
 *  - 列表布局 + Tab(全部/收藏) + 排序，专为 40+ 个币设计。
 */

(() => {
  "use strict";

  const POLL_SNAPSHOT_MS = 5000;
  const POLL_CANDLES_MS = 15000;
  const CANDLE_LIMIT = 200;
  const FAV_KEY = "crypto-platform.favorites.v1";
  const TAB_KEY = "crypto-platform.tab.v1";
  const SORT_KEY = "crypto-platform.sort.v1";
  const TF_KEY = "crypto-platform.timeframe.v1";
  const ALLOWED_TIMEFRAMES = ["15m", "1h", "4h", "1d"];
  const DEFAULT_TIMEFRAME = "1d";

  const el = {
    metaExchange: document.getElementById("meta-exchange"),
    metaTimeframe: document.getElementById("meta-timeframe"),
    metaInterval: document.getElementById("meta-interval"),
    status: document.getElementById("status"),
    statusLabel: document.getElementById("status-label"),
    chartTitle: document.getElementById("chart-title"),
    chartSub: document.getElementById("chart-sub"),
    chartStats: document.getElementById("chart-stats"),
    chart: document.getElementById("chart"),
    chartEmpty: document.getElementById("chart-empty"),
    list: document.getElementById("list"),
    listEmpty: document.getElementById("list-empty"),
    countAll: document.getElementById("count-all"),
    countFav: document.getElementById("count-fav"),
    sort: document.getElementById("sort"),
    footerSync: document.getElementById("footer-sync"),
    footerRecords: document.getElementById("footer-records"),
    footerError: document.getElementById("footer-error"),
    infoModal: document.getElementById("info-modal"),
    infoClose: document.getElementById("info-close"),
    infoLogo: document.getElementById("info-logo"),
    infoName: document.getElementById("info-name"),
    infoMeta: document.getElementById("info-meta"),
    infoBody: document.getElementById("info-body"),
    searchInput: document.getElementById("search-input"),
    searchClear: document.getElementById("search-clear"),
  };

  const state = {
    meta: null,
    snapshot: null,
    selectedSymbol: null,
    favorites: loadFavorites(),
    tab: localStorage.getItem(TAB_KEY) === "favorites" ? "favorites" : "all",
    sort: localStorage.getItem(SORT_KEY) || "change_today_desc",
    timeframe: ALLOWED_TIMEFRAMES.includes(localStorage.getItem(TF_KEY))
      ? localStorage.getItem(TF_KEY)
      : DEFAULT_TIMEFRAME,
    searchQuery: "",
  };

  const MARKET_TYPE_LABEL = {
    spot: "现货",
    swap: "永续",
    future: "合约",
    option: "期权",
    margin: "杠杆",
  };

  let chart = null;
  let candleSeries = null;
  let volumeSeries = null;
  // 是否已经渲染过至少一次有数据的图。决定下一次 setData 后是否 fitContent。
  let chartHasInitialFit = false;

  // -------------------- helpers --------------------

  const fmtPrice = (n) => {
    if (n == null || !Number.isFinite(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 2 });
    if (abs >= 1)    return n.toLocaleString("en-US", { maximumFractionDigits: 4 });
    return n.toLocaleString("en-US", { maximumFractionDigits: 6 });
  };

  const fmtPct = (n) => {
    if (n == null || !Number.isFinite(n)) return "—";
    const sign = n > 0 ? "+" : "";
    return `${sign}${n.toFixed(2)}%`;
  };

  const fmtVolume = (n) => {
    if (n == null || !Number.isFinite(n)) return "—";
    if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(2) + "K";
    return n.toFixed(2);
  };

  const fmtTime = (ms) => {
    if (!ms) return "—";
    const d = new Date(ms);
    const pad = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };

  const dirOf = (n) => (n == null ? "flat" : n > 0 ? "up" : n < 0 ? "down" : "flat");

  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
    return r.json();
  }

  // -------------------- favorites (localStorage) --------------------

  function loadFavorites() {
    try {
      const raw = localStorage.getItem(FAV_KEY);
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr : []);
    } catch {
      return new Set();
    }
  }

  function saveFavorites() {
    localStorage.setItem(FAV_KEY, JSON.stringify([...state.favorites]));
  }

  function toggleFavorite(symbol) {
    if (state.favorites.has(symbol)) state.favorites.delete(symbol);
    else state.favorites.add(symbol);
    saveFavorites();
    renderList();
    renderTabCounts();
  }

  // -------------------- chart --------------------

  function ensureChart() {
    if (chart) return;
    chart = LightweightCharts.createChart(el.chart, {
      autoSize: true,
      layout: {
        background: { type: "solid", color: "rgba(0,0,0,0)" },
        textColor: "#a8b0c0",
        fontFamily: "ui-monospace, JetBrains Mono, SF Mono, Menlo, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.04)" },
        horzLines: { color: "rgba(255,255,255,0.05)" },
      },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: {
        borderColor: "rgba(255,255,255,0.08)",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    candleSeries = chart.addCandlestickSeries({
      upColor: "#34d399",
      downColor: "#f43f5e",
      wickUpColor: "#34d399",
      wickDownColor: "#f43f5e",
      borderVisible: false,
    });

    volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "rgba(120,140,180,0.35)",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });
  }

  function renderCandles(candles, { fit = false } = {}) {
    ensureChart();
    if (!candles || candles.length === 0) {
      el.chartEmpty.classList.remove("hidden");
      candleSeries.setData([]);
      volumeSeries.setData([]);
      chartHasInitialFit = false;
      return;
    }
    el.chartEmpty.classList.add("hidden");

    candleSeries.setData(
      candles.map((c) => ({
        time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
      })),
    );

    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open ? "rgba(52,211,153,0.35)" : "rgba(244,63,94,0.35)",
      })),
    );

    // 只在第一次有数据 / 显式 fit（切币 / 切周期 / 点重置）时才回到默认视图。
    // 自动轮询不 fit -> 保留用户的缩放和平移位置。
    if (fit || !chartHasInitialFit) {
      chart.timeScale().fitContent();
      chartHasInitialFit = true;
    }
  }

  function resetChartView() {
    if (!chart) return;
    chart.timeScale().fitContent();
  }

  function scrollChartToLatest() {
    if (!chart) return;
    chart.timeScale().scrollToRealTime();
  }

  // -------------------- list --------------------

  function matchesSearch(item, query) {
    if (!query) return true;
    const q = query.toLowerCase();
    const sym = item.symbol.toLowerCase();
    const base = sym.split("/")[0];
    if (sym.includes(q) || base.includes(q)) return true;
    if ((item.market_type || "").toLowerCase().includes(q)) return true;
    if (MARKET_TYPE_LABEL[item.market_type] &&
        MARKET_TYPE_LABEL[item.market_type].includes(query)) return true;
    if ((item.tags || []).some((t) => t.toLowerCase().includes(q))) return true;
    return false;
  }

  function visibleItems() {
    const items = state.snapshot?.items || [];
    let filtered = state.tab === "favorites"
      ? items.filter((i) => state.favorites.has(i.symbol))
      : items.slice();

    if (state.searchQuery) {
      filtered = filtered.filter((i) => matchesSearch(i, state.searchQuery));
    }

    const cmpNum = (a, b) => {
      if (a == null && b == null) return 0;
      if (a == null) return 1;
      if (b == null) return -1;
      return a - b;
    };

    const SORTERS = {
      change_today_desc: (a, b) => cmpNum(b.change_today_pct, a.change_today_pct),
      change_today_asc:  (a, b) => cmpNum(a.change_today_pct, b.change_today_pct),
      change_24h_desc: (a, b) => cmpNum(b.change_24h_pct, a.change_24h_pct),
      change_24h_asc:  (a, b) => cmpNum(a.change_24h_pct, b.change_24h_pct),
      change_7d_desc:  (a, b) => cmpNum(b.change_7d_pct,  a.change_7d_pct),
      change_7d_asc:   (a, b) => cmpNum(a.change_7d_pct,  b.change_7d_pct),
      change_30d_desc: (a, b) => cmpNum(b.change_30d_pct, a.change_30d_pct),
      change_30d_asc:  (a, b) => cmpNum(a.change_30d_pct, b.change_30d_pct),
      change_90d_desc: (a, b) => cmpNum(b.change_90d_pct, a.change_90d_pct),
      change_90d_asc:  (a, b) => cmpNum(a.change_90d_pct, b.change_90d_pct),
      volume_desc:     (a, b) => cmpNum(b.volume_24h,     a.volume_24h),
      price_desc:      (a, b) => cmpNum(b.price,          a.price),
      price_asc:       (a, b) => cmpNum(a.price,          b.price),
      symbol_asc:      (a, b) => a.symbol.localeCompare(b.symbol),
    };
    const sorter = SORTERS[state.sort];
    if (sorter) filtered.sort(sorter);
    return filtered;
  }

  function renderList() {
    const items = visibleItems();
    el.list.innerHTML = "";
    el.listEmpty.hidden = items.length > 0;
    if (items.length === 0) {
      if (state.searchQuery) {
        el.listEmpty.textContent = `无匹配「${state.searchQuery}」的币种。`;
      } else if (state.tab === "favorites") {
        el.listEmpty.textContent = "还没有收藏。点列表中星标添加。";
      } else {
        el.listEmpty.textContent = "暂无数据。";
      }
      return;
    }

    const frag = document.createDocumentFragment();
    items.forEach((item) => {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.symbol = item.symbol;
      if (item.symbol === state.selectedSymbol) row.classList.add("active");

      const isFav = state.favorites.has(item.symbol);
      const marketLabel = MARKET_TYPE_LABEL[item.market_type] || item.market_type || "现货";
      const tagChips = [
        `<span class="tag-chip market-type">${marketLabel}</span>`,
        ...(item.tags || []).map((t) => `<span class="tag-chip" data-tag="${t}">${t}</span>`),
      ].join("");

      row.innerHTML = `
        <span class="col col-fav">
          <button class="fav-btn ${isFav ? "is-fav" : ""}" aria-label="收藏">${isFav ? "★" : "☆"}</button>
        </span>
        <span class="col col-symbol">
          <span class="symbol-name" title="点击查看币种信息">${item.symbol}</span>
          ${tagChips}
        </span>
        <span class="col col-price">${fmtPrice(item.price)}</span>
        ${pctCell(item.change_today_pct)}
        ${pctCell(item.change_7d_pct)}
        ${pctCell(item.change_30d_pct)}
        ${pctCell(item.change_90d_pct)}
        <span class="col col-volume">${fmtVolume(item.volume_24h)}</span>
      `;

      row.querySelector(".fav-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        toggleFavorite(item.symbol);
      });
      row.querySelector(".symbol-name").addEventListener("click", (e) => {
        e.stopPropagation();
        const base = item.symbol.split("/")[0];
        openInfoModal(base);
      });
      row.addEventListener("click", () => selectSymbol(item.symbol));

      frag.appendChild(row);
    });
    el.list.appendChild(frag);
  }

  function pctCell(pct) {
    if (pct == null || !Number.isFinite(pct)) {
      return `<span class="col col-change"><span class="change-pill" data-dir="na">—</span></span>`;
    }
    return `<span class="col col-change"><span class="change-pill" data-dir="${dirOf(pct)}">${fmtPct(pct)}</span></span>`;
  }

  function renderTabCounts() {
    const total = state.snapshot?.items.length ?? 0;
    el.countAll.textContent = total;
    el.countFav.textContent = state.favorites.size;
  }

  function setTab(tab) {
    if (tab !== "all" && tab !== "favorites") return;
    state.tab = tab;
    localStorage.setItem(TAB_KEY, tab);
    document.querySelectorAll(".tab").forEach((t) => {
      t.setAttribute("aria-selected", String(t.dataset.tab === tab));
    });
    renderList();
  }

  // -------------------- chart header --------------------

  function renderMeta(meta) {
    el.metaExchange.textContent = meta.exchange.toUpperCase();
    el.metaTimeframe.textContent = meta.kline_timeframe;
    el.metaInterval.textContent = meta.sync_interval_seconds;
  }

  function setStatus(running, error) {
    if (error) {
      el.status.dataset.state = "error";
      el.statusLabel.textContent = "同步异常";
    } else if (running) {
      el.status.dataset.state = "ok";
      el.statusLabel.textContent = "实时同步中";
    } else {
      el.status.dataset.state = "idle";
      el.statusLabel.textContent = "已停止";
    }
  }

  function renderChartHeader(item) {
    if (!item) {
      el.chartTitle.textContent = "—";
      el.chartSub.textContent = "—";
      el.chartStats.innerHTML = "";
      return;
    }
    el.chartTitle.textContent = item.symbol;
    el.chartSub.textContent =
      `${state.meta.exchange.toUpperCase()} · ${state.timeframe} K 线 · 更新于 ${fmtTime(item.price_timestamp_ms)}`;

    const stats = [
      { label: "最新价",     value: fmtPrice(item.price) },
      { label: "今日 (00:00)", value: fmtPct(item.change_today_pct), klass: dirOf(item.change_today_pct) },
      { label: "24h 滚动",   value: fmtPct(item.change_24h_pct),   klass: dirOf(item.change_24h_pct) },
      { label: "7 天",      value: fmtPct(item.change_7d_pct),    klass: dirOf(item.change_7d_pct) },
      { label: "30 天",     value: fmtPct(item.change_30d_pct),   klass: dirOf(item.change_30d_pct) },
      { label: "90 天",     value: fmtPct(item.change_90d_pct),   klass: dirOf(item.change_90d_pct) },
      { label: "24h 高",    value: fmtPrice(item.high_24h) },
      { label: "24h 低",    value: fmtPrice(item.low_24h) },
      { label: "24h 量",    value: fmtVolume(item.volume_24h) },
    ];
    el.chartStats.innerHTML = stats
      .map((s) => `
        <div class="stat">
          <span class="stat-label">${s.label}</span>
          <span class="stat-value ${s.klass || ""}">${s.value}</span>
        </div>`)
      .join("");
  }

  function renderFooter(snapshot) {
    el.footerSync.textContent = `上次同步: ${fmtTime(snapshot.last_sync_ms)}`;
    const totalKL = snapshot.items.reduce((sum, i) => sum + i.kline_count, 0);
    const totalP = snapshot.items.reduce((sum, i) => sum + i.price_count, 0);
    el.footerRecords.textContent = `价格 ${totalP} 条 · K 线 ${totalKL} 条`;
    el.footerError.textContent = snapshot.last_error ? "最近错误: " + snapshot.last_error : "";
  }

  function selectSymbol(symbol) {
    if (state.selectedSymbol === symbol) return;
    state.selectedSymbol = symbol;
    const item = state.snapshot?.items.find((i) => i.symbol === symbol);
    if (item) renderChartHeader(item);
    document.querySelectorAll(".row").forEach((r) => {
      r.classList.toggle("active", r.dataset.symbol === symbol);
    });
    chartHasInitialFit = false;        // 换币切到新数据，下一次 setData 重新 fit
    void refreshCandles({ fit: true });
  }

  // -------------------- polling --------------------

  async function refreshSnapshot() {
    try {
      const snap = await getJSON("/api/snapshot");
      state.snapshot = snap;
      if (!state.selectedSymbol && snap.items.length > 0) {
        state.selectedSymbol = snap.items[0].symbol;
      }
      setStatus(snap.running, snap.last_error);
      renderTabCounts();
      renderList();
      const cur = snap.items.find((i) => i.symbol === state.selectedSymbol);
      renderChartHeader(cur);
      renderFooter(snap);
    } catch (err) {
      console.warn("snapshot error", err);
      setStatus(false, err.message);
    }
  }

  async function refreshCandles({ fit = false } = {}) {
    if (!state.selectedSymbol) return;
    try {
      const params = new URLSearchParams({
        symbol: state.selectedSymbol,
        timeframe: state.timeframe,
        limit: String(CANDLE_LIMIT),
      });
      const candles = await getJSON(`/api/candles?${params.toString()}`);
      renderCandles(candles, { fit });
    } catch (err) {
      console.warn("candles error", err);
    }
  }

  // -------------------- info modal --------------------

  function escapeHtml(s) {
    return String(s ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function openInfoModal(base) {
    el.infoModal.hidden = false;
    el.infoLogo.hidden = true;
    el.infoLogo.removeAttribute("src");
    el.infoName.textContent = base;
    el.infoMeta.textContent = "加载中…";
    el.infoBody.innerHTML = `<p class="info-loading">加载中…</p>`;
    void fetchInfoAndRender(base);
  }

  function closeInfoModal() {
    el.infoModal.hidden = true;
  }

  async function fetchInfoAndRender(base) {
    try {
      const data = await getJSON(`/api/info/${encodeURIComponent(base)}`);
      renderInfoModal(data);
    } catch (err) {
      el.infoBody.innerHTML =
        `<p class="info-error">加载失败：${escapeHtml(err.message)}</p>` +
        `<p class="info-hint">CoinGecko 可能被限速或网络故障。稍后重试。</p>`;
    }
  }

  function buildLinkButton(url, label) {
    if (!url) return "";
    return `<a class="info-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
  }

  function renderInfoModal(d) {
    if (!d || !d.found) {
      el.infoName.textContent = d?.base || "—";
      el.infoMeta.textContent = "—";
      el.infoBody.innerHTML =
        `<p class="info-error">${escapeHtml(d?.error || "没有找到该币种的信息。")}</p>` +
        `<p class="info-hint">可能是非常新的币或 CoinGecko 暂未收录。Phase 2 上线买入前请独立尽职调查。</p>`;
      return;
    }

    if (d.image) {
      el.infoLogo.src = d.image;
      el.infoLogo.hidden = false;
    }
    el.infoName.textContent = `${d.name} (${d.symbol})`;
    el.infoMeta.textContent = [
      d.market_cap_rank ? `市值排名 #${d.market_cap_rank}` : null,
      d.genesis_date ? `创世 ${d.genesis_date}` : null,
      d.country_origin || null,
    ].filter(Boolean).join("  ·  ") || "—";

    const desc = d.description_zh || d.description_en || "";
    const isLong = desc.length > 320;

    const facts = [];
    if (d.genesis_date)       facts.push({ label: "创世日期",  value: d.genesis_date });
    if (d.market_cap_rank)    facts.push({ label: "市值排名",  value: "#" + d.market_cap_rank });
    if (d.hashing_algorithm)  facts.push({ label: "哈希算法",  value: d.hashing_algorithm });
    if (d.country_origin)     facts.push({ label: "起源国家",  value: d.country_origin });

    const linkBlocks = [];
    (d.links?.homepage || []).forEach((u, i) =>
      linkBlocks.push(buildLinkButton(u, i ? `官网 ${i + 1}` : "官网")));
    linkBlocks.push(buildLinkButton(d.links?.whitepaper, "白皮书"));
    (d.links?.github || []).slice(0, 2).forEach((u, i) =>
      linkBlocks.push(buildLinkButton(u, i ? `GitHub ${i + 1}` : "GitHub")));
    linkBlocks.push(buildLinkButton(d.links?.twitter, "Twitter"));
    linkBlocks.push(buildLinkButton(d.links?.reddit, "Reddit"));
    (d.links?.explorer || []).slice(0, 2).forEach((u, i) =>
      linkBlocks.push(buildLinkButton(u, i ? `区块浏览器 ${i + 1}` : "区块浏览器")));
    const linksHtml = linkBlocks.filter(Boolean).join("");

    const categoriesHtml = (d.categories || []).slice(0, 8)
      .map((c) => `<span class="info-category">${escapeHtml(c)}</span>`)
      .join("");

    el.infoBody.innerHTML = `
      ${desc ? `
        <div class="info-section">
          <h4>项目简介</h4>
          <div class="info-desc ${isLong ? "collapsed" : ""}" id="info-desc-text">${escapeHtml(desc)}</div>
          ${isLong ? `<button class="info-toggle" id="info-desc-toggle">展开全文 ↓</button>` : ""}
        </div>` : ""}

      ${facts.length ? `
        <div class="info-section">
          <h4>关键信息</h4>
          <div class="info-facts">
            ${facts.map((f) => `
              <div class="info-fact">
                <div class="info-fact-label">${escapeHtml(f.label)}</div>
                <div class="info-fact-value">${escapeHtml(f.value)}</div>
              </div>
            `).join("")}
          </div>
        </div>` : ""}

      ${categoriesHtml ? `
        <div class="info-section">
          <h4>分类</h4>
          <div class="info-categories">${categoriesHtml}</div>
        </div>` : ""}

      ${linksHtml ? `
        <div class="info-section">
          <h4>相关链接</h4>
          <div class="info-links">${linksHtml}</div>
        </div>` : ""}

      <div class="info-section">
        <h4>重大动态</h4>
        <p class="info-hint">
          CoinGecko 暂不提供逐条事件流。请通过<strong>官方网站</strong>、<strong>GitHub Releases</strong>、
          <strong>Twitter 公告</strong>追踪具体进展。Phase 2 计划接入新闻 API（CryptoCompare / NewsAPI）。
        </p>
      </div>
    `;

    const toggle = document.getElementById("info-desc-toggle");
    if (toggle) {
      toggle.addEventListener("click", () => {
        const txt = document.getElementById("info-desc-text");
        const collapsed = txt.classList.toggle("collapsed");
        toggle.textContent = collapsed ? "展开全文 ↓" : "收起 ↑";
      });
    }
  }

  function setTimeframe(tf) {
    if (!ALLOWED_TIMEFRAMES.includes(tf)) return;
    if (state.timeframe === tf) return;
    state.timeframe = tf;
    localStorage.setItem(TF_KEY, tf);
    document.querySelectorAll(".tf-btn").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.tf === tf));
    });
    // 切换时图表先回到 loading 态，再用新数据填充
    el.chartEmpty.classList.remove("hidden");
    el.chartEmpty.textContent = `加载 ${tf} K 线…`;
    chartHasInitialFit = false;        // 强制下一次 setData fit 一下
    void refreshCandles({ fit: true });

    const cur = state.snapshot?.items.find((i) => i.symbol === state.selectedSymbol);
    if (cur) renderChartHeader(cur);
  }

  // -------------------- bootstrap --------------------

  function bindControls() {
    document.querySelectorAll(".tab").forEach((t) => {
      t.addEventListener("click", () => setTab(t.dataset.tab));
    });
    setTab(state.tab);

    el.sort.value = state.sort;
    el.sort.addEventListener("change", () => {
      state.sort = el.sort.value;
      localStorage.setItem(SORT_KEY, state.sort);
      renderList();
    });

    document.querySelectorAll(".tf-btn").forEach((b) => {
      b.setAttribute("aria-selected", String(b.dataset.tf === state.timeframe));
      b.addEventListener("click", () => setTimeframe(b.dataset.tf));
    });

    const resetBtn = document.getElementById("chart-reset");
    if (resetBtn) {
      resetBtn.addEventListener("click", resetChartView);
      resetBtn.addEventListener("dblclick", scrollChartToLatest);
    }

    bindSearch();

    // 信息弹窗：X 按钮、点击 backdrop、ESC 键三种关闭方式
    el.infoClose.addEventListener("click", closeInfoModal);
    el.infoModal.addEventListener("click", (e) => {
      if (e.target === el.infoModal) closeInfoModal();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !el.infoModal.hidden) closeInfoModal();
    });
  }

  function bindSearch() {
    const apply = () => {
      state.searchQuery = el.searchInput.value.trim();
      el.searchClear.hidden = !state.searchQuery;
      renderList();
    };
    el.searchInput.addEventListener("input", apply);
    el.searchClear.addEventListener("click", () => {
      el.searchInput.value = "";
      state.searchQuery = "";
      el.searchClear.hidden = true;
      renderList();
      el.searchInput.focus();
    });

    // 全局快捷键：
    //  - "/"   聚焦搜索框（已在输入框 / 弹窗打开时不抢）
    //  - Esc   在搜索框里：有内容先清空，没内容才失焦
    document.addEventListener("keydown", (e) => {
      const inEditable =
        e.target.tagName === "INPUT" ||
        e.target.tagName === "TEXTAREA" ||
        e.target.isContentEditable;
      const modalOpen = !el.infoModal.hidden;

      if (e.key === "/" && !inEditable && !modalOpen) {
        e.preventDefault();
        el.searchInput.focus();
        el.searchInput.select();
        return;
      }

      if (e.key === "Escape" && document.activeElement === el.searchInput) {
        if (state.searchQuery) {
          el.searchInput.value = "";
          state.searchQuery = "";
          el.searchClear.hidden = true;
          renderList();
        } else {
          el.searchInput.blur();
        }
      }
    });
  }

  async function bootstrap() {
    bindControls();

    try {
      state.meta = await getJSON("/api/meta");
      renderMeta(state.meta);
    } catch (err) {
      el.statusLabel.textContent = "无法连接后端";
      el.status.dataset.state = "error";
      console.error(err);
      return;
    }

    await refreshSnapshot();
    await refreshCandles({ fit: true });

    // 用箭头函数包一层，避免 setInterval 把 tick id 当成 opts 传进去。
    setInterval(() => refreshSnapshot(), POLL_SNAPSHOT_MS);
    setInterval(() => refreshCandles(), POLL_CANDLES_MS);
  }

  document.addEventListener("DOMContentLoaded", bootstrap);
})();
