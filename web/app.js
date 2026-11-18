"use strict";

const byId = id => document.getElementById(id);
const make = (tag, value, className) => {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (className) element.className = className;
  return element;
};

const motionOk = () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const ICONS = {
  send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14m-6-6 6 6-6 6"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  spinner8: '<svg class="thinking-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="12" y1="2.5" x2="12" y2="6" opacity="1"/><line x1="18.7" y1="5.3" x2="16.2" y2="7.8" opacity="0.85"/><line x1="21.5" y1="12" x2="18" y2="12" opacity="0.7"/><line x1="18.7" y1="18.7" x2="16.2" y2="16.2" opacity="0.55"/><line x1="12" y1="21.5" x2="12" y2="18" opacity="0.4"/><line x1="5.3" y1="18.7" x2="7.8" y2="16.2" opacity="0.28"/><line x1="2.5" y1="12" x2="6" y2="12" opacity="0.2"/><line x1="5.3" y1="5.3" x2="7.8" y2="7.8" opacity="0.14"/></svg>'
};

let state = { fields: [], rows: [], sample: {}, capabilities: {} };
let busy = false;
let ready = false;
let pendingId = null;
let selectedId = null;
let viewMode = "cards";
let latestStateRequest = 0;
let retryableRequest = null;
let sendResetTimer = null;

function safeLink(address, sameOrigin = false) {
  if (typeof address !== "string" || !address.trim()) return null;
  try {
    const url = new URL(address, location.origin);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    if (sameOrigin && url.origin !== location.origin) return null;
    return url.href;
  } catch {
    return null;
  }
}

function link(label, url, className) {
  const a = make("a", label, className);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function shortHost(address) {
  try {
    return new URL(address).hostname.replace(/^www\./i, "");
  } catch {
    return "原文";
  }
}

async function copyClipboard(text) {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {}
  const area = make("textarea");
  area.value = text;
  area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  document.body.append(area);
  area.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {}
  area.remove();
  return ok;
}

async function copyWithFeedback(button, text, idleLabel = "复制") {
  const ok = await copyClipboard(text);
  if (!ok) {
    showToast("无法自动复制，请手动选中复制", "error");
    return;
  }
  clearTimeout(button._copyTimer);
  button.classList.add("copied");
  const iconHolder = button.querySelector("svg");
  const textHolder = button.querySelector(".copy-txt");
  if (iconHolder) iconHolder.outerHTML = ICONS.check;
  if (textHolder) swapInlineText(textHolder, "已复制");
  button._copyTimer = setTimeout(() => {
    button.classList.remove("copied");
    const currentSvg = button.querySelector("svg");
    if (currentSvg) currentSvg.outerHTML = ICONS.copy;
    if (textHolder) swapInlineText(textHolder, idleLabel);
  }, 1600);
}

function swapInlineText(el, nextText) {
  if (!el || el.textContent === String(nextText)) return;
  el.textContent = nextText;
  if (motionOk()) {
    el.animate(
      [
        { opacity: 0, filter: "blur(4px)", transform: "translateY(5px)" },
        { opacity: 1, filter: "blur(0)", transform: "translateY(0)" }
      ],
      { duration: 240, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
    );
  }
}

let activeToast = null;
let toastTimer = null;

function dismissToast() {
  clearTimeout(toastTimer);
  const old = activeToast;
  if (!old) return;
  activeToast = null;
  old.classList.add("exit");
  setTimeout(() => old.remove(), 420);
}

function showToast(text, kind = "ok") {
  const region = byId("toast-region");
  clearTimeout(toastTimer);
  if (activeToast) {
    activeToast.classList.remove("exit");
    const iconBox = activeToast.querySelector(".toast-icon");
    iconBox.className = "toast-icon" + (kind === "error" ? " error" : "");
    iconBox.innerHTML = kind === "error" ? ICONS.alert : ICONS.check;
    swapInlineText(activeToast.querySelector(".toast-text"), text);
    if (motionOk()) {
      activeToast.animate(
        [
          { transform: "translateY(0) scale(1)" },
          { transform: "translateY(4px) scale(0.97)" },
          { transform: "translateY(0) scale(1)" }
        ],
        { duration: 300, easing: "ease-out" }
      );
    }
  } else {
    const toast = make("div", undefined, "toast");
    toast.setAttribute("role", "status");
    const iconBox = make("span", undefined, "toast-icon" + (kind === "error" ? " error" : ""));
    iconBox.innerHTML = kind === "error" ? ICONS.alert : ICONS.check;
    const label = make("span", text, "toast-text");
    const closeBtn = make("button", undefined, "toast-close");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "关闭提示");
    closeBtn.innerHTML = ICONS.close;
    closeBtn.addEventListener("click", dismissToast);
    toast.append(iconBox, label, closeBtn);
    region.append(toast);
    activeToast = toast;
    requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add("visible")));
  }
  toastTimer = setTimeout(dismissToast, 2900);
}

function morphSendButton(mode) {
  const btn = byId("send");
  if (!btn || btn.dataset.mode === mode) return;
  clearTimeout(sendResetTimer);
  const prevW = btn.offsetWidth;
  btn.dataset.mode = mode;
  btn.classList.toggle("is-saved", mode === "saved");

  const inner = btn.querySelector(".send-inner");
  if (mode === "loading") {
    inner.innerHTML = '<span class="btn-spinner" aria-hidden="true"></span><span>处理中</span>';
  } else if (mode === "saved") {
    inner.innerHTML = ICONS.check + "<span>已写入</span>";
    sendResetTimer = setTimeout(() => {
      if (!busy) morphSendButton("idle");
    }, 1500);
  } else {
    inner.innerHTML = "<span>发送</span>" + ICONS.send;
  }

  const nextW = btn.offsetWidth;
  if (motionOk() && prevW > 0 && Math.abs(prevW - nextW) > 1) {
    btn.animate([{ width: prevW + "px" }, { width: nextW + "px" }], {
      duration: 320,
      easing: "cubic-bezier(0.22, 1, 0.36, 1)"
    });
    inner.animate(
      [
        { opacity: 0, filter: "blur(5px)", transform: "scale(0.9)" },
        { opacity: 1, filter: "blur(0)", transform: "scale(1)" }
      ],
      { duration: 220, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
    );
  }
}

function playRevealWave() {
  if (!motionOk()) return;
  const stage = byId("records-stage");
  const wave = make("div", undefined, "reveal-wave");
  wave.append(make("i"));
  stage.append(wave);
  setTimeout(() => wave.remove(), 860);
}

/* 双弹簧分段控制器 */
const segButtons = [...document.querySelectorAll(".seg-btn")];
const segHighlight = byId("seg-highlight");
const segSpring = { l: 0, r: 0, vl: 0, vr: 0, tl: 0, tr: 0, raf: 0, t: 0 };

function stepEdge(x, v, target, w, z, dt) {
  const a = -w * w * (x - target) - 2 * z * w * v;
  v += a * dt;
  x += v * dt;
  return [x, v];
}

function tickSegSpring(now) {
  const dt = Math.min(0.032, (now - segSpring.t) / 1000 || 0.016);
  segSpring.t = now;
  const movingRight = segSpring.tr >= segSpring.r && segSpring.tl >= segSpring.l - 0.5;
  const lead = { w: 28, z: 0.72 };
  const lag = { w: 19, z: 0.94 };
  const pr = movingRight ? lead : lag;
  const pl = movingRight ? lag : lead;
  for (let k = 0; k < 2; k++) {
    [segSpring.l, segSpring.vl] = stepEdge(segSpring.l, segSpring.vl, segSpring.tl, pl.w, pl.z, dt / 2);
    [segSpring.r, segSpring.vr] = stepEdge(segSpring.r, segSpring.vr, segSpring.tr, pr.w, pr.z, dt / 2);
  }
  segHighlight.style.left = segSpring.l + "px";
  segHighlight.style.width = Math.max(12, segSpring.r - segSpring.l) + "px";
  if (
    Math.abs(segSpring.l - segSpring.tl) +
      Math.abs(segSpring.r - segSpring.tr) +
      Math.abs(segSpring.vl) +
      Math.abs(segSpring.vr) <
    0.35
  ) {
    segSpring.l = segSpring.tl;
    segSpring.r = segSpring.tr;
    segHighlight.style.left = segSpring.l + "px";
    segHighlight.style.width = segSpring.r - segSpring.l + "px";
    segSpring.raf = 0;
    return;
  }
  segSpring.raf = requestAnimationFrame(tickSegSpring);
}

function selectView(mode, animateSwitch = true, focusBtn = false) {
  const changed = viewMode !== mode;
  viewMode = mode;
  segButtons.forEach(btn => {
    const active = btn.dataset.view === mode;
    btn.setAttribute("aria-checked", String(active));
    btn.tabIndex = active ? 0 : -1;
    if (active && focusBtn) btn.focus();
  });

  const activeBtn = segButtons.find(b => b.dataset.view === mode) || segButtons[0];
  if (activeBtn) {
    segSpring.tl = activeBtn.offsetLeft;
    segSpring.tr = activeBtn.offsetLeft + activeBtn.offsetWidth;
    if (!animateSwitch || !motionOk() || !segSpring.r) {
      segSpring.l = segSpring.tl;
      segSpring.r = segSpring.tr;
      segSpring.vl = segSpring.vr = 0;
      segHighlight.style.left = segSpring.l + "px";
      segHighlight.style.width = segSpring.r - segSpring.l + "px";
    } else if (!segSpring.raf) {
      segSpring.t = performance.now();
      segSpring.raf = requestAnimationFrame(tickSegSpring);
    }
  }

  syncViewVisibility(changed && animateSwitch);
}

function syncViewVisibility(animateIn = false) {
  const hasRows = state.rows.some(row => Object.values(row.fields || {}).some(hasCellValue));
  const cardsEl = byId("cards-view");
  const tableEl = byId("table-view");
  const emptyEl = byId("empty-sheet");

  if (!hasRows) {
    cardsEl.hidden = true;
    tableEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  cardsEl.hidden = viewMode !== "cards";
  tableEl.hidden = viewMode !== "table";

  const shown = viewMode === "cards" ? cardsEl : tableEl;
  if (animateIn && motionOk()) {
    shown.animate(
      [
        { opacity: 0, filter: "blur(5px)", transform: "translateY(6px) scale(0.99)" },
        { opacity: 1, filter: "blur(0)", transform: "translateY(0) scale(1)" }
      ],
      { duration: 280, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
    );
  }
}

function applyTheme(theme) {
  const target = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", target);
  const meta = byId("meta-theme-color");
  if (meta) meta.setAttribute("content", target === "dark" ? "#111114" : "#f6f6f7");
  try {
    localStorage.setItem("paper_theme", target);
  } catch {}
}

(function initTheme() {
  let saved = null;
  try {
    saved = localStorage.getItem("paper_theme");
  } catch {}
  applyTheme(saved || "light");
})();

byId("theme-toggle").addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme");
  applyTheme(current === "dark" ? "light" : "dark");
});

(function initExampleGlide() {
  const grid = byId("example-grid");
  const glide = byId("example-glide");
  const moveGlide = btn => {
    if (!btn || btn.disabled) {
      glide.classList.remove("visible");
      return;
    }
    glide.style.width = btn.offsetWidth + "px";
    glide.style.height = btn.offsetHeight + "px";
    glide.style.transform = `translate3d(${btn.offsetLeft}px, ${btn.offsetTop}px, 0)`;
    glide.classList.add("visible");
  };
  grid.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("pointerenter", () => moveGlide(btn));
    btn.addEventListener("focus", () => moveGlide(btn));
  });
  grid.addEventListener("pointerleave", () => glide.classList.remove("visible"));
  grid.addEventListener("focusout", e => {
    if (!grid.contains(e.relatedTarget)) glide.classList.remove("visible");
  });
})();

function autoResizePrompt() {
  const input = byId("prompt");
  input.style.height = "auto";
  input.style.height = Math.min(150, Math.max(44, input.scrollHeight)) + "px";
}

function controls() {
  const input = byId("prompt");
  byId("send").disabled = busy || !ready || !input.value.trim();
  input.disabled = busy || !ready;
  byId("refresh").disabled = busy;
  byId("pending-badge").hidden = !pendingId;
  document.querySelectorAll("[data-example]").forEach(btn => {
    btn.disabled = busy || !ready || Boolean(pendingId);
  });
  document.querySelectorAll("[data-choice]").forEach(btn => {
    btn.disabled = busy || !ready || btn.dataset.task !== pendingId;
  });
  const emptyCta = byId("empty-try-btn");
  if (emptyCta) emptyCta.disabled = busy || !ready || Boolean(pendingId);
}

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, { cache: "no-store", ...options });
  } catch {
    const error = new Error("连接中断。恢复后可重新发送这条指令。");
    error.networkFailure = true;
    throw error;
  }
  let data;
  try {
    data = await res.json();
  } catch (cause) {
    const error = new Error("服务返回了无法读取的响应。");
    error.networkFailure = cause instanceof TypeError;
    throw error;
  }
  if (!res.ok || data.error) throw new Error(data.error || data.message || "服务暂时不可用。");
  return data;
}

function cellValue(value) {
  if (value == null) return "";
  if (typeof value === "object") {
    if (Array.isArray(value)) return value.map(cellValue).join("、");
    return value.link || value.text || value.name || JSON.stringify(value);
  }
  return String(value);
}

function hasCellValue(value) {
  if (value == null) return false;
  if (Array.isArray(value)) return value.some(hasCellValue);
  if (typeof value === "object") return Object.values(value).some(hasCellValue);
  return String(value).trim().length > 0;
}

function renderValue(container, field, value) {
  const content = cellValue(value);
  const url = field.type === "url" && /^https?:\/\//i.test(content) ? safeLink(content) : null;
  if (url) container.append(link(content, url));
  else container.textContent = content || "—";
  if (content) container.title = content;
}

function makeDetailRow(labelText, rawText, renderFn) {
  const wrap = make("div", undefined, "detail-row");
  const head = make("div", undefined, "detail-row-head");
  head.append(make("dt", labelText));

  if (rawText) {
    const copyBtn = make("button", undefined, "mini-copy-btn");
    copyBtn.type = "button";
    copyBtn.innerHTML = ICONS.copy + '<span class="copy-txt">复制</span>';
    copyBtn.addEventListener("click", () => copyWithFeedback(copyBtn, rawText, "复制"));
    head.append(copyBtn);
  }

  const desc = make("dd");
  renderFn(desc);
  wrap.append(head, desc);
  return wrap;
}

function showDetail(row) {
  const list = byId("detail-content");
  list.replaceChildren();
  list.append(
    makeDetailRow("记录 ID", row.id, dd => {
      dd.textContent = row.id;
      dd.style.fontFamily = "var(--font-mono)";
      dd.style.fontSize = "12.5px";
    })
  );
  for (const field of state.fields) {
    const raw = cellValue(row.fields[field.name]);
    list.append(makeDetailRow(field.name, raw, dd => renderValue(dd, field, row.fields[field.name])));
  }
  byId("detail").showModal();
}

function renderSchemaStrip() {
  const strip = byId("schema-strip");
  strip.replaceChildren(make("span", "字段", "schema-label"));
  state.fields.forEach((field, idx) => {
    const chip = make("span", undefined, "schema-chip");
    chip.style.animationDelay = Math.min(idx * 25, 180) + "ms";
    if (field.type === "url") {
      const ico = make("span", undefined, "schema-ico");
      ico.innerHTML = ICONS.link;
      chip.append(ico);
    }
    chip.append(document.createTextNode(field.name));
    strip.append(chip);
  });
}

function detectPrimaryFields(fields) {
  const titleField =
    fields.find(f => /标题|题名|题目|名称|title|name/i.test(f.name)) ||
    fields.find(f => f.type === "text") ||
    null;
  const urlField =
    fields.find(f => f.type === "url") ||
    fields.find(f => /链接|地址|url|link/i.test(f.name)) ||
    null;
  return { titleField, urlField };
}

function renderCardsView(populatedRows) {
  const container = byId("cards-view");
  container.replaceChildren();
  const { titleField, urlField } = detectPrimaryFields(state.fields);

  populatedRows.forEach((row, index) => {
    const card = make("article", undefined, "paper-card" + (row.id === selectedId ? " selected" : ""));
    card.style.animationDelay = Math.min(index * 45, 240) + "ms";
    card.tabIndex = 0;
    card.setAttribute("aria-label", "第 " + (index + 1) + " 条论文记录");

    const top = make("div", undefined, "paper-card-top");
    const heading = make("div", undefined, "paper-card-heading");
    heading.append(make("span", "#" + (index + 1), "paper-index"));

    const titleText =
      (titleField && cellValue(row.fields[titleField.name])) ||
      (urlField && cellValue(row.fields[urlField.name])) ||
      "未填标题";
    heading.append(make("h3", titleText, "paper-title"));

    const actions = make("div", undefined, "paper-card-actions");
    const rawUrl = urlField ? cellValue(row.fields[urlField.name]) : "";
    const validUrl = /^https?:\/\//i.test(rawUrl) ? safeLink(rawUrl) : null;
    if (validUrl) {
      const urlPill = link(shortHost(validUrl) + " ↗", validUrl, "paper-url-pill");
      urlPill.title = validUrl;
      urlPill.addEventListener("click", e => e.stopPropagation());
      actions.append(urlPill);
    }

    const detailBtn = make("button", "详情", "paper-detail-pill");
    detailBtn.type = "button";
    detailBtn.addEventListener("click", e => {
      e.stopPropagation();
      showDetail(row);
    });
    actions.append(detailBtn);

    top.append(heading, actions);
    card.append(top);

    const extraFields = state.fields.filter(f => {
      if (titleField && f.id === titleField.id && cellValue(row.fields[f.name]) === titleText) return false;
      if (urlField && f.id === urlField.id && validUrl) return false;
      return hasCellValue(row.fields[f.name]);
    });

    if (extraFields.length) {
      const grid = make("div", undefined, "paper-fields-grid");
      extraFields.forEach(field => {
        const box = make("div", undefined, "paper-field-box");
        box.append(make("span", field.name, "paper-field-name"));
        const val = make("div", cellValue(row.fields[field.name]), "paper-field-val");
        val.title = cellValue(row.fields[field.name]);
        box.append(val);
        grid.append(box);
      });
      card.append(grid);
    }

    card.addEventListener("click", () => showDetail(row));
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        showDetail(row);
      }
    });
    container.append(card);
  });
}

function renderTableView(populatedRows) {
  const table = byId("sheet");
  table.replaceChildren();
  const headers = table.createTHead().insertRow();
  ["#", ...state.fields.map(field => field.name)].forEach(name => {
    const th = make("th", name);
    th.scope = "col";
    headers.append(th);
  });
  const body = table.createTBody();
  populatedRows.forEach((row, index) => {
    const tr = body.insertRow();
    if (row.id === selectedId) tr.className = "selected";
    const btn = make("button", String(index + 1), "row-detail");
    btn.type = "button";
    btn.title = "查看详情";
    btn.setAttribute("aria-label", "查看第 " + (index + 1) + " 条记录");
    btn.addEventListener("click", () => showDetail(row));
    tr.insertCell().append(btn);
    for (const field of state.fields) renderValue(tr.insertCell(), field, row.fields[field.name]);
  });
}

function renderState() {
  const mode = byId("mode");
  mode.className = "mode connected";
  mode.lastElementChild.textContent = state.mode === "feishu" ? "飞书多维表格" : "本地 SQLite";

  byId("engine-mode").hidden = !state.capabilities?.model;

  byId("records-heading").textContent = state.table_name || "阅读清单";
  const populatedRows = state.rows.filter(row => Object.values(row.fields || {}).some(hasCellValue));
  const blankCount = state.rows.length - populatedRows.length;

  const rowSummary = blankCount
    ? populatedRows.length + " 条记录（另 " + blankCount + " 空行）"
    : populatedRows.length + " 条记录";
  swapInlineText(byId("row-count"), rowSummary);
  swapInlineText(byId("field-count"), state.fields.length + " 个字段");

  byId("sample").hidden = !state.sample.title;
  byId("sample-title").textContent = state.sample.title || "";
  const download = safeLink(state.download_url, true);
  byId("sample-pdf").hidden = !download;
  if (download) byId("sample-pdf").href = download;
  else byId("sample-pdf").removeAttribute("href");

  renderSchemaStrip();
  renderCardsView(populatedRows);
  renderTableView(populatedRows);

  byId("empty-sheet-text").textContent = blankCount ? "暂无已填写的记录" : "暂无记录";
  byId("empty-try-btn").hidden = Boolean(populatedRows.length);

  syncViewVisibility(false);
  requestAnimationFrame(() => selectView(viewMode, false));
}

async function refreshState(manual = false) {
  const token = ++latestStateRequest;
  const refreshBtn = byId("refresh");
  if (manual) refreshBtn.classList.add("spinning");
  try {
    const snapshot = await api("/api/state");
    if (token !== latestStateRequest) return;
    state = {
      ...snapshot,
      sample: snapshot.sample || {},
      fields: snapshot.fields || [],
      rows: snapshot.rows || [],
      capabilities: snapshot.capabilities || {}
    };
    ready = true;
    renderState();
    if (manual) showToast("表格已刷新");
  } catch (err) {
    if (token !== latestStateRequest) return;
    ready = false;
    byId("mode").className = "mode error";
    byId("mode").lastElementChild.textContent = "连接失败";
    byId("cards-view").hidden = true;
    byId("table-view").hidden = true;
    byId("empty-sheet").hidden = false;
    byId("empty-try-btn").hidden = true;
    byId("empty-sheet-text").textContent = "刷新失败：" + err.message;
    if (!state.rows.length) byId("row-count").textContent = "未取得记录";
    if (manual) showToast(err.message, "error");
  } finally {
    if (manual) setTimeout(() => refreshBtn.classList.remove("spinning"), 650);
    controls();
  }
}

function appendMessage(content, kind = "assistant") {
  const article = make("article", undefined, "message " + kind);
  const bubble = make("div", undefined, "bubble");
  bubble.append(make("p", content));
  article.append(bubble);

  const chat = byId("chat");
  chat.append(article);
  chat.scrollTop = chat.scrollHeight;
  return { article, bubble };
}

function createThinkingLoader() {
  const steps = ["正在处理…", "读取论文…", "核对表格…"];
  let idx = 0;

  const article = make("article", undefined, "message assistant");
  const pill = make("div", undefined, "thinking-pill");
  pill.innerHTML = ICONS.spinner8;
  const label = make("span", steps[0], "thinking-step");
  pill.append(label);
  article.append(pill);

  const chat = byId("chat");
  chat.append(article);
  chat.scrollTop = chat.scrollHeight;

  const timer = setInterval(() => {
    idx = (idx + 1) % steps.length;
    const prevW = pill.offsetWidth;
    label.textContent = steps[idx];
    const nextW = pill.offsetWidth;
    if (motionOk() && prevW > 0 && Math.abs(prevW - nextW) > 1) {
      pill.animate([{ width: prevW + "px" }, { width: nextW + "px" }], {
        duration: 280,
        easing: "cubic-bezier(0.22, 1, 0.36, 1)"
      });
      label.animate(
        [
          { opacity: 0, filter: "blur(4px)", transform: "translateY(5px)" },
          { opacity: 1, filter: "blur(0)", transform: "translateY(0)" }
        ],
        { duration: 240, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );
    }
  }, 800);

  return {
    remove() {
      clearInterval(timer);
      article.remove();
    }
  };
}

function showEvidence(items) {
  const container = byId("evidence");
  container.replaceChildren();
  if (!items.length) {
    container.append(make("p", "未涉及正文摘录。", "muted"));
    return;
  }
  items.forEach((item, index) => {
    const block = make("div", undefined, "evidence-item");
    block.style.animationDelay = Math.min(index * 55, 220) + "ms";
    if (item.field) {
      const top = make("div", undefined, "evidence-top");
      top.append(make("span", item.field, "evidence-tag"));
      block.append(top);
    }
    if (item.quote) block.append(make("blockquote", item.quote));
    if (item.source) {
      const source = make("small", undefined, "evidence-source");
      const sourceUrl = item.url || item.source;
      const address = /^https?:\/\//i.test(sourceUrl) ? safeLink(sourceUrl) : null;
      if (address) source.append(link(item.source + " ↗", address));
      else source.textContent = item.source;
      block.append(source);
    }
    container.append(block);
  });
}

async function submit(text) {
  const instruction = text.trim();
  if (!instruction || busy || !ready) return;
  const task = pendingId;
  const matchingRetry = retryableRequest?.message === instruction && retryableRequest.task === task;
  const request = matchingRetry ? retryableRequest : { id: crypto.randomUUID(), message: instruction, task };
  retryableRequest = null;

  busy = true;
  byId("prompt").value = "";
  autoResizePrompt();
  morphSendButton("loading");
  controls();

  appendMessage(instruction, "user");
  const loader = createThinkingLoader();

  try {
    const result = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: instruction,
        request_id: request.id,
        ...(task ? { pending_id: task } : {})
      })
    });
    loader.remove();
    pendingId = result.pending_id || null;

    const { article } = appendMessage(result.message || "已完成。", "assistant");

    if (pendingId && Array.isArray(result.choices) && result.choices.length) {
      const choices = make("div", undefined, "choices");
      result.choices.forEach((choice, idx) => {
        const isCancel = /^(取消|算了|不用了)$/.test(choice);
        const button = make("button", choice, isCancel ? "choice-cancel" : "");
        button.type = "button";
        button.style.animationDelay = Math.min(idx * 45, 200) + "ms";
        button.dataset.choice = choice;
        button.dataset.task = pendingId;
        button.addEventListener("click", () => {
          button.classList.add("choice-picked");
          submit(choice);
        });
        choices.append(button);
      });
      article.append(choices);
    }

    const download = safeLink(result.download_url, true);
    if (download) {
      const pdfWrap = make("div", undefined, "pdf-actions");
      pdfWrap.append(link("打开 PDF ↗", download, "pdf-pill"));
      const savePdf = link("下载 PDF ↓", download, "pdf-pill");
      savePdf.removeAttribute("target");
      savePdf.download = "";
      pdfWrap.append(savePdf);
      article.append(pdfWrap);
    }

    if (result.row) selectedId = result.row.id;

    if (result.verification) {
      const verifyEl = byId("verification");
      verifyEl.className = "verify-status report";
      verifyEl.textContent = result.verification;
      const iconEl = byId("verify-icon");
      iconEl.classList.remove("verified");
      void iconEl.offsetWidth;
      iconEl.classList.add("verified");
      morphSendButton("saved");
      showToast(result.verification);
    } else {
      morphSendButton("idle");
    }

    if (result.row || result.evidence?.length) {
      showEvidence(Array.isArray(result.evidence) ? result.evidence : []);
    }

    await refreshState(false);
    if (result.row || result.verification) playRevealWave();
  } catch (err) {
    loader.remove();
    morphSendButton("idle");
    if (err.networkFailure) {
      retryableRequest = request;
      byId("prompt").value = instruction;
      autoResizePrompt();
    }
    appendMessage(err.message, "error");
    showToast(err.message, "error");
  } finally {
    busy = false;
    controls();
    byId("chat").scrollTop = byId("chat").scrollHeight;
  }
}

byId("composer").addEventListener("submit", event => {
  event.preventDefault();
  submit(byId("prompt").value);
});

byId("prompt").addEventListener("input", () => {
  autoResizePrompt();
  controls();
});

byId("prompt").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    submit(byId("prompt").value);
  }
});

function runExample(key) {
  const examples = {
    save: "把 " + (state.sample.url || "https://arxiv.org/abs/2005.11401") + " 存进" + (state.table_name || "阅读清单"),
    method: "给刚才那篇补一句方法概述",
    notes: "把刚才那篇的方法写进笔记",
    schema: "新建文本列 审阅意见"
  };
  if (examples[key]) submit(examples[key]);
}

document.querySelectorAll("[data-example]").forEach(button => {
  button.addEventListener("click", () => runExample(button.dataset.example));
});

byId("empty-try-btn").addEventListener("click", () => runExample("save"));

byId("copy-sample-url").addEventListener("click", e => {
  const url = state.sample.url || "https://arxiv.org/abs/2005.11401";
  copyWithFeedback(e.currentTarget, url, "复制链接");
});

segButtons.forEach((btn, idx) => {
  btn.addEventListener("click", () => selectView(btn.dataset.view, true, false));
  btn.addEventListener("keydown", e => {
    let nextIdx;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") nextIdx = (idx + 1) % segButtons.length;
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") nextIdx = (idx - 1 + segButtons.length) % segButtons.length;
    if (e.key === "Home") nextIdx = 0;
    if (e.key === "End") nextIdx = segButtons.length - 1;
    if (nextIdx !== undefined) {
      e.preventDefault();
      selectView(segButtons[nextIdx].dataset.view, true, true);
    }
  });
});

window.addEventListener("resize", () => selectView(viewMode, false));

byId("refresh").addEventListener("click", () => refreshState(true));
byId("close-detail").addEventListener("click", () => byId("detail").close());
byId("detail").addEventListener("click", event => {
  if (event.target === byId("detail")) byId("detail").close();
});

controls();
selectView("cards", false);
refreshState(false);
