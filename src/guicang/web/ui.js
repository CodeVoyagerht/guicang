"use strict";

const TOKEN_KEY = "guicang_token";
const SEL_KEY = "guicang_selection";

const state = {
  filters: {},
  offset: 0,
  limit: 120,
  total: 0,
  items: [],
  selected: new Set(),
  collections: [],
  status: null,
  collectionId: null,
};

const $ = (id) => document.getElementById(id);

function token() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("t");
  if (fromUrl) {
    localStorage.setItem(TOKEN_KEY, fromUrl);
    return fromUrl;
  }
  return localStorage.getItem(TOKEN_KEY) || "";
}

function withToken(path, params) {
  const url = new URL(path, location.origin);
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  });
  const t = token();
  if (t) url.searchParams.set("t", t);
  return url.toString();
}

async function api(path, params, options) {
  const response = await fetch(withToken(path, params), options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body.error || message;
    } catch (_) { /* 保持状态文本 */ }
    throw new Error(message);
  }
  return response.json();
}

function toast(message, ms) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, ms || 3200);
}

function fmtBytes(bytes) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Number(bytes || 0);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index === 0 ? 0 : 1)}${units[index]}`;
}

// ── 数据 ──────────────────────────────────────────────────────────────────

async function loadStatus() {
  const status = await api("/api/status");
  state.status = status;
  $("library").textContent = status.library;
  const stats = status.stats || {};
  $("stats").textContent = `${stats.assets || 0} 个素材 · ${fmtBytes(stats.bytes)} · 待定 ${
    (stats.status && stats.status.needs_review) || 0
  }`;
  const syncNote = $("sync-note");
  syncNote.textContent = status.sync && status.sync.ready
    ? `云端已配置（${status.sync.backend}），可用 guicang sync 上传。`
    : "云端未配置，素材只在本地。";
  renderCollections(status.collections || []);
}

function renderCollections(items) {
  state.collections = items;
  const box = $("collections");
  box.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "还没有选集";
    box.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "collection" + (state.collectionId === item.id ? " on" : "");
    row.innerHTML = `<span>${escapeHtml(item.name)}</span><span class="n">${item.count}</span>`;
    row.onclick = () => {
      state.collectionId = state.collectionId === item.id ? null : item.id;
      reload();
      renderCollections(state.collections);
    };
    box.appendChild(row);
  });
}

async function loadFacets() {
  const facets = await api("/api/facets");
  const shotBox = $("facet-shot");
  shotBox.innerHTML = "";
  const total = (facets.shot_type || []).reduce((sum, item) => sum + item.count, 0);
  [["", "全部", total], ...(facets.shot_type || []).map((item) => [item.value, item.value, item.count])]
    .forEach(([value, label, count]) => {
      const chip = document.createElement("button");
      chip.className = "chip" + ((state.filters.shot || "") === value ? " on" : "");
      chip.innerHTML = `${escapeHtml(label)}<span class="n">${count}</span>`;
      chip.onclick = () => {
        state.filters.shot = value || undefined;
        loadFacets();
        reload();
      };
      shotBox.appendChild(chip);
    });

  fillSelect("facet-region", "全部区域", facets.region || [], (value) => { state.filters.region = value || undefined; reload(); });
  // 园区区域只在真的有这一层时才露出来（只有一个园区就不占地方）
  const areaSelect = $("facet-area");
  if (areaSelect) {
    const areas = (facets.area || []).filter((item) => item.value && item.value !== "—");
    areaSelect.hidden = areas.length === 0;
    fillSelect("facet-area", "全部园区区域", areas, (value) => { state.filters.area = value || undefined; reload(); });
  }
  fillSelect("facet-activity", "全部活动", facets.activity || [], (value) => { state.filters.activity = value || undefined; reload(); });
  fillSelect("facet-month", "全部时间", facets.month || [], (value) => { state.filters.month = value || undefined; reload(); });
}

function fillSelect(id, placeholder, items, onChange) {
  const select = $(id);
  const current = select.value;
  select.innerHTML = "";
  const first = document.createElement("option");
  first.value = "";
  first.textContent = placeholder;
  select.appendChild(first);
  items.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.value;
    option.textContent = `${item.value}（${item.count}）`;
    select.appendChild(option);
  });
  if (current && items.some((item) => item.value === current)) select.value = current;
  select.onchange = () => onChange(select.value);
}

function params() {
  const query = { ...state.filters };
  if (state.collectionId) query.collection = state.collectionId;
  return query;
}

async function reload() {
  state.offset = 0;
  state.items = [];
  $("grid").innerHTML = "";
  await loadPage();
}

async function loadPage() {
  const query = { ...params(), limit: state.limit, offset: state.offset };
  const data = await api("/api/assets", query);
  state.total = data.total;
  state.items = state.items.concat(data.items);
  renderGrid(data.items);
  $("result-count").textContent = `共 ${data.total} 个，已显示 ${state.items.length}`;
  $("btn-more").disabled = state.items.length >= data.total;
}

function renderGrid(items) {
  const grid = $("grid");
  items.forEach((item) => {
    const card = document.createElement("article");
    card.className = "card" + (state.selected.has(item.id) ? " sel" : "");
    card.dataset.id = item.id;

    const thumb = document.createElement("div");
    thumb.className = "thumb";
    thumb.textContent = item.kind === "video" ? "▶" : "◻";
    const image = new Image();
    image.loading = "lazy";
    image.src = withToken("/api/thumb", { id: item.id });
    image.style.cssText = "width:100%;height:100%;object-fit:cover;";
    image.onload = () => { thumb.textContent = ""; thumb.appendChild(image); };
    card.appendChild(thumb);

    const tick = document.createElement("div");
    tick.className = "tick";
    tick.textContent = state.selected.has(item.id) ? "✓" : "";
    tick.onclick = (event) => {
      event.stopPropagation();
      toggleSelection(item.id, card);
    };
    card.appendChild(tick);

    const body = document.createElement("div");
    body.className = "card-body";
    const name = document.createElement("div");
    name.className = "card-name";
    name.textContent = item.name;
    body.appendChild(name);

    const sub = document.createElement("div");
    sub.className = "card-sub";
    const badge = document.createElement("span");
    badge.className = "badge " + (item.status === "needs_review" ? "待定" : item.shot_type || "");
    badge.textContent = item.status === "needs_review" ? "待定" : item.shot_type || "未定";
    sub.appendChild(badge);
    const where = document.createElement("span");
    // 区域主枝显示完整身份（市/地点/园区区域），活动主枝显示活动+届次
    where.textContent = item.branch === "activity"
      ? `${item.activity || "活动"} ${item.edition || ""}`
      : [item.city, item.region, item.area].filter(Boolean).join("/") || "未识别";
    sub.appendChild(where);
    body.appendChild(sub);

    const when = document.createElement("div");
    when.className = "card-sub";
    when.textContent = (item.captured_at || "时间未知").slice(0, 16);
    body.appendChild(when);

    card.appendChild(body);
    card.onclick = () => openDetail(item);
    grid.appendChild(card);
  });
}

function toggleSelection(id, card) {
  if (state.selected.has(id)) state.selected.delete(id);
  else state.selected.add(id);
  card.classList.toggle("sel", state.selected.has(id));
  const tick = card.querySelector(".tick");
  if (tick) tick.textContent = state.selected.has(id) ? "✓" : "";
  persistSelection();
  renderSelection();
}

function persistSelection() {
  localStorage.setItem(SEL_KEY, JSON.stringify([...state.selected]));
}

function restoreSelection() {
  try {
    const raw = JSON.parse(localStorage.getItem(SEL_KEY) || "[]");
    if (Array.isArray(raw)) raw.forEach((id) => state.selected.add(id));
  } catch (_) { /* 忽略坏数据 */ }
}

function renderSelection() {
  const count = state.selected.size;
  $("selbar").hidden = count === 0;
  $("sel-count").textContent = `已选 ${count}`;
  $("selection-note").textContent = count ? `已选 ${count}` : "";
}

// ── 详情 ──────────────────────────────────────────────────────────────────

async function openDetail(item) {
  const modal = $("modal");
  const media = $("modal-media");
  media.innerHTML = "";
  const url = withToken("/api/media", { id: item.id });
  if (item.kind === "video") {
    const video = document.createElement("video");
    video.src = url;
    video.controls = true;
    video.preload = "metadata";
    video.style.maxHeight = "76vh";
    media.appendChild(video);
  } else {
    const image = document.createElement("img");
    image.src = url;
    media.appendChild(image);
  }

  const meta = $("modal-meta");
  const rows = [
    ["拍摄时间", item.captured_at || "未知"],
    ["拍摄类型", `${item.shot_type || "?"}（${(item.shot_conf || 0).toFixed(2)}）`],
    ["区域", `${[item.province, item.city, item.region, item.area].filter(Boolean).join(" → ") || "未识别"}（${(item.region_conf || 0).toFixed(2)}）`],
    ["活动", item.activity ? `${item.activity} ${item.edition || ""}（${(item.activity_conf || 0).toFixed(2)}）` : "—"],
    ["落位", item.target_rel || "—"],
    ["机型", item.camera || "未知"],
    ["尺寸", item.width && item.height ? `${item.width}×${item.height}` : "—"],
    ["时长", item.duration ? `${item.duration.toFixed(1)}s` : "—"],
    ["大小", item.size_human],
    ["路径", item.rel_path],
  ];
  meta.innerHTML = `<h2>${escapeHtml(item.name)}</h2><dl>${
    rows.map(([key, value]) => `<dt>${key}</dt><dd>${escapeHtml(String(value))}</dd>`).join("")
  }</dl><div class="evidence" id="evidence">正在取证据…</div>`;
  renderReviewForm([item.rel_path]);
  $("modal").classList.remove("review-only");
  modal.hidden = false;

  try {
    const detail = await api("/api/asset", { id: item.id });
    const box = $("evidence");
    const groups = [
      ["拍摄类型", (detail.evidence || {}).shot],
      ["区域", (detail.evidence || {}).region],
      ["活动", (detail.evidence || {}).activity],
    ];
    const html = groups.map(([title, list]) => {
      if (!list || !list.length) return "";
      return `<div><strong>${title}</strong><ul>${
        list.map((row) => `<li>${escapeHtml(row.detail)} → ${Number(row.weight).toFixed(2)}</li>`).join("")
      }</ul></div>`;
    }).join("");
    box.innerHTML = html || "没有留下证据（可能是默认判定）。";
  } catch (error) {
    $("evidence").textContent = `证据读取失败：${error.message}`;
  }
}

function closeDetail() {
  $("modal").hidden = true;
  $("modal-media").innerHTML = "";
  $("modal-review").innerHTML = "";
  reviewPaths = [];
}

// ── 改判定 ────────────────────────────────────────────────────────────────

const SHOTS = ["航拍", "地拍", "延时", "FPV", "其他"];
const BRANCHES = [["region", "区域"], ["activity", "活动"], ["douyin", "抖音"]];
let reviewPaths = [];

function selectOptions(items) {
  const blank = `<option value="">不改</option>`;
  return blank + items.map(([value, label]) =>
    `<option value="${escapeHtml(String(value))}">${escapeHtml(String(label))}</option>`).join("");
}

function renderReviewForm(paths) {
  reviewPaths = paths.slice();
  const box = $("modal-review");
  if (!box) return;
  if (!paths.length) { box.innerHTML = ""; return; }
  const status = state.status || {};
  const places = (status.places || []).map((name) => [name, name]);
  const activities = (status.activities || []).map((name) => [name, name]);
  box.innerHTML = `
    <h3>改判定 <span class="muted">只写 manual.json，素材一根手指都不碰</span></h3>
    <div class="review-grid">
      <label>拍摄类型<select id="rv-shot">${selectOptions(SHOTS.map((s) => [s, s]))}</select></label>
      <label>分支<select id="rv-branch">${selectOptions(BRANCHES)}</select></label>
      <label>地点<select id="rv-region">${selectOptions(places)}</select></label>
      <label>园区区域<input id="rv-area" placeholder="例如：水世界"></label>
      <label>活动<select id="rv-activity">${selectOptions(activities)}</select></label>
      <label>届次<input id="rv-edition" placeholder="例如：第39届"></label>
    </div>
    <div class="review-actions">
      <button class="primary" id="rv-save">保存判定</button>
      <button class="ghost" id="rv-clear">清除人工判定</button>
      <span class="muted">这批 ${paths.length} 条</span>
    </div>`;
  $("rv-save").addEventListener("click", () => submitReview("set"));
  $("rv-clear").addEventListener("click", () => submitReview("clear"));
}

async function submitReview(action) {
  if (!reviewPaths.length) return;
  const payload = { paths: reviewPaths, action };
  if (action === "set") {
    const fields = {
      shot_type: $("rv-shot").value,
      branch: $("rv-branch").value,
      region: $("rv-region").value,
      area: $("rv-area").value.trim(),
      activity: $("rv-activity").value,
      edition: $("rv-edition").value.trim(),
    };
    let touched = 0;
    Object.entries(fields).forEach(([key, value]) => {
      if (value) { payload[key] = value; touched += 1; }
    });
    if (!touched) return toast("至少选一项要改的");
  }
  try {
    const result = await api("/api/review", {}, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    toast(`已改 ${result.reviewed} 条判定。${result.summary}`, 7000);
    closeDetail();
    await loadStatus();
    await loadFacets();
    await reload();
  } catch (error) {
    toast(`改判定失败：${error.message}`, 5000);
  }
}

async function rescan() {
  try {
    toast("正在扫描，大库可能要一会儿…", 8000);
    const result = await api("/api/rescan", {}, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    toast(result.summary, 8000);
    await loadStatus();
    await loadFacets();
    await reload();
  } catch (error) {
    toast(`扫描失败：${error.message}`, 5000);
  }
}

function openBulkReview() {
  if (!state.selected.size) return toast("先勾选素材");
  const paths = state.items
    .filter((item) => state.selected.has(item.id))
    .map((item) => item.rel_path);
  const stray = [...state.selected].filter((id) => !state.items.some((item) => item.id === id));
  if (!paths.length) return toast("勾选的素材不在当前页，翻回去再勾一次");
  if (stray.length) toast(`有 ${stray.length} 条不在当前页，只改这 ${paths.length} 条`, 5000);
  openReviewModal(paths, `批量改判定 · ${paths.length} 条`);
}

function openReviewModal(paths, title) {
  $("modal-media").innerHTML = "";
  $("modal-meta").innerHTML = `<h2>${escapeHtml(title)}</h2>`;
  renderReviewForm(paths);
  $("modal").classList.add("review-only");
  $("modal").hidden = false;
}

// ── 动作 ──────────────────────────────────────────────────────────────────

async function saveCollection() {
  if (!state.selected.size) return toast("先勾选素材");
  const name = $("sel-name").value.trim() || `选集 ${new Date().toLocaleString("zh-CN")}`;
  try {
    const result = await api("/api/collections", {}, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, ids: [...state.selected] }),
    });
    toast(`已存为「${name}」，新增 ${result.added} 条`);
    state.collectionId = result.id;
    await loadStatus();
    await reload();
  } catch (error) {
    toast(`保存失败：${error.message}`);
  }
}

async function exportSelection() {
  if (!state.selected.size) return toast("先勾选素材");
  const name = $("sel-name").value.trim() || `选集 ${new Date().toLocaleString("zh-CN")}`;
  try {
    const saved = await api("/api/collections", {}, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, ids: [...state.selected] }),
    });
    const result = await api("/api/export", { collection: saved.id });
    toast(`已导出到 ${result.folder}：${result.files.map((file) => file.name).join("、")}`, 6000);
  } catch (error) {
    toast(`导出失败：${error.message}`);
  }
}

async function exportLedger() {
  try {
    const result = await api("/api/export", params());
    toast(`台账已导出：${result.files[0].name}（${result.folder}）`, 6000);
  } catch (error) {
    toast(`导出失败：${error.message}`);
  }
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

// ── 绑定 ──────────────────────────────────────────────────────────────────

function bind() {
  $("btn-refresh").onclick = async () => {
    try { await loadStatus(); await loadFacets(); await reload(); toast("已刷新"); }
    catch (error) { toast(`刷新失败：${error.message}`); }
  };
  $("btn-export").onclick = exportLedger;
  $("btn-rescan").onclick = rescan;
  $("btn-review").onclick = openBulkReview;
  $("btn-export-sel").onclick = exportSelection;
  $("btn-save").onclick = saveCollection;
  $("btn-clear").onclick = () => {
    state.selected.clear();
    persistSelection();
    renderSelection();
    document.querySelectorAll(".card").forEach((card) => {
      card.classList.remove("sel");
      const tick = card.querySelector(".tick");
      if (tick) tick.textContent = "";
    });
  };
  $("btn-more").onclick = async () => {
    state.offset += state.limit;
    try { await loadPage(); } catch (error) { toast(`加载失败：${error.message}`); }
  };
  $("facet-status").onchange = () => {
    state.filters.status = $("facet-status").value || undefined;
    reload();
  };
  let timer = null;
  $("facet-query").oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      state.filters.q = $("facet-query").value.trim() || undefined;
      reload();
    }, 320);
  };
  $("modal-close").onclick = closeDetail;
  $("modal").onclick = (event) => { if (event.target.id === "modal") closeDetail(); };
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDetail();
  });
}

async function boot() {
  bind();
  restoreSelection();
  renderSelection();
  try {
    await loadStatus();
    await loadFacets();
    await reload();
  } catch (error) {
    toast(`加载失败：${error.message}`, 8000);
  }
}

boot();
