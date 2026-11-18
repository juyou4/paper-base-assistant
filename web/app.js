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
let switchingModel = false;
let refreshingState = false;
let pendingId = null;
let reviewId = null;
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

function feishuTableLink(address) {
  if (typeof address !== "string") return null;
  try {
    const url = new URL(address);
    const allowedDomains = ["feishu.cn", "larksuite.com", "larkoffice.com"];
    if (url.protocol !== "https:" || url.username || url.password || url.port ||
        !allowedDomains.some(domain => url.hostname === domain || url.hostname.endsWith("." + domain)) ||
        !/^\/(?:base|wiki)\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) return null;
    const target = new URL(url.pathname, url.origin);
    for (const [key, pattern] of [["table", /^tbl[A-Za-z0-9_-]+$/], ["view", /^vew[A-Za-z0-9_-]+$/]]) {
      const values = url.searchParams.getAll(key);
      if (!values.length) continue;
      if (values.length !== 1 || !pattern.test(values[0])) return null;
      target.searchParams.set(key, values[0]);
    }
    return target.href;
  } catch {
    return null;
  }
}

function modelName(agent) {
  return [agent?.provider, agent?.model]
    .filter(value => typeof value === "string" && value.trim())
    .map(value => value.trim())
    .join(" / ");
}

function renderConnectionDetails() {
  const status = byId("engine-mode");
  const agent = state.capabilities.agent;
  const enabled = agent?.enabled ?? state.capabilities.model;
  status.hidden = !ready || typeof enabled !== "boolean";
  if (!status.hidden) {
    const name = modelName(agent);
    status.textContent = enabled ? "已配置模型" + (name ? " · " + name : "") : "模型未配置";
  }
  const select = byId("model-select");
  const models = Array.isArray(agent?.models)
    ? [...new Set(agent.models.filter(model => typeof model === "string" && model.trim()))] : [];
  byId("model-picker").hidden = !ready || agent?.selectable !== true || !models.includes(agent.model);
  if (models.length !== select.length || models.some((model, index) => model !== select.options[index].value)) {
    select.replaceChildren(...models.map(model => new Option(model, model)));
  }
  select.value = agent?.model || "";
  const target = ready && state.mode === "feishu" ? feishuTableLink(state.feishu_url) : null;
  const external = byId("feishu-link");
  external.hidden = !target;
  if (target) external.href = target;
  else external.removeAttribute("href");
}

function appendModelTrace(article, agent) {
  const steps = Array.isArray(agent?.steps)
    ? agent.steps.filter(step => typeof step === "string" && step.trim()) : [];
  if (!steps.length) {
    article.append(make("p", "本次回复 · 未调用模型", "model-trace"));
    return;
  }
  const trace = make("div", undefined, "model-trace");
  trace.append(make("p", "本次使用模型 · " + (modelName(agent) || "已调用")));
  trace.append(make("p", steps.join(" → "), "model-steps"));
  article.append(trace);
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
  const locked = busy || switchingModel || refreshingState || !ready;
  byId("send").disabled = locked || !input.value.trim();
  input.disabled = busy || !ready;
  byId("refresh").disabled = busy || switchingModel || refreshingState;
  byId("model-select").disabled = locked;
  byId("model-picker").setAttribute("aria-busy", String(switchingModel));
  byId("model-picker-label").textContent = switchingModel ? "切换中" : "模型";
  byId("pending-badge").hidden = !pendingId;
  document.querySelectorAll("[data-example]").forEach(btn => {
    btn.disabled = locked || Boolean(pendingId);
  });
  document.querySelectorAll("[data-choice]").forEach(btn => {
    btn.disabled = locked || btn.dataset.task !== pendingId;
  });
  byId("review-confirm").disabled = byId("review-cancel").disabled =
    locked || !reviewId || reviewId !== pendingId;
  const emptyCta = byId("empty-try-btn");
  if (emptyCta) emptyCta.disabled = locked || Boolean(pendingId);
}

async function switchModel() {
  const select = byId("model-select");
  const requested = select.value;
  const current = state.capabilities.agent;
  if (!ready || busy || switchingModel || refreshingState || !current?.selectable ||
      !current.models?.includes(requested) || current.model === requested) return;

  // 发送前以会话回执为准。网络中断时，再读一次服务端状态。
  const restoreFocus = document.activeElement === select;
  switchingModel = true;
  controls();
  try {
    const result = await api("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: requested })
    });
    if (result.agent?.model !== requested || !Array.isArray(result.agent.models) ||
        !result.agent.models.includes(requested)) throw new Error("服务未确认所选模型。");
    state.capabilities.agent = result.agent;
    renderConnectionDetails();
    showToast(result.message || "已切换至 " + requested);
  } catch (err) {
    select.value = current.model;
    await refreshState();
    showToast(err.message, "error");
  } finally {
    switchingModel = false;
    controls();
    if (restoreFocus && document.activeElement === document.body && !byId("model-picker").hidden) {
      select.focus({ preventScroll: true });
    }
  }
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
  if (!res.ok || data.error) {
    const error = new Error(data.error || data.message || "服务暂时不可用。");
    error.activity = data.activity;
    error.agent = data.agent;
    throw error;
  }
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
  else PaperRichText.render(container, content || "—");
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
        const val = PaperRichText.render(make("div", undefined, "paper-field-val"), cellValue(row.fields[field.name]));
        val.title = cellValue(row.fields[field.name]);
        box.append(val);
        grid.append(box);
      });
      card.append(grid);
    }

    card.addEventListener("click", e => {
      if (!e.target.closest("a,button")) showDetail(row);
    });
    card.addEventListener("keydown", e => {
      if (e.target === card && (e.key === "Enter" || e.key === " ")) {
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
    for (const field of state.fields) {
      const content = make("div", undefined, "sheet-cell-content");
      renderValue(content, field, row.fields[field.name]);
      tr.insertCell().append(content);
    }
  });
}

function renderState() {
  const mode = byId("mode");
  mode.className = "mode connected";
  mode.lastElementChild.textContent = state.mode === "feishu" ? "飞书多维表格" : "本地 SQLite";

  renderConnectionDetails();

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
  refreshingState = true;
  controls();
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
    renderConnectionDetails();
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
    if (token === latestStateRequest) refreshingState = false;
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

function trackActivity(requestId) {
  const article = make("article", undefined, "message assistant activity-message");
  const pill = make("div", undefined, "thinking-pill");
  pill.innerHTML = ICONS.spinner8;
  pill.append(make("span", "正在处理…", "thinking-step"));
  const card = byId("activity-template").content.firstElementChild.cloneNode(true);
  const list = card.querySelector(".activity-list");
  const count = card.querySelector(".activity-count");
  const rows = new Map();
  article.append(pill, card);
  const chat = byId("chat");
  chat.append(article);
  chat.scrollTop = chat.scrollHeight;
  let timer;
  let stopped = false;
  let complete = false;
  const controller = new AbortController();
  const statusNames = { running: "进行中", done: "已完成", failed: "失败" };

  function render(snapshot) {
    if (snapshot?.request_id !== requestId || !Array.isArray(snapshot.events)) return;
    const follow = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40;
    const ids = new Set();
    let failed = false;
    for (const event of snapshot.events) {
      if (!Number.isSafeInteger(event?.id) || ids.has(event.id) ||
          !Object.hasOwn(statusNames, event.status)) continue;
      ids.add(event.id);
      failed ||= event.status === "failed";
      let row = rows.get(event.id);
      if (!row) {
        row = make("li", undefined, "activity-event");
        const marker = make("i", undefined, "activity-marker");
        marker.setAttribute("aria-hidden", "true");
        const elapsedEl = make("span", undefined, "activity-elapsed");
        elapsedEl.setAttribute("role", "timer");
        const description = make("span", undefined, "activity-description");
        description.append(make("span", undefined, "activity-label"), make("span", undefined, "activity-tool"));
        row.append(marker, description, make("span", undefined, "screenreader"), elapsedEl);
        rows.set(event.id, row);
        list.append(row);
      }
      // 事件只显示简短标签、状态、用时，舍弃路径、链接及命令参数。
      const name = typeof event.label === "string" ? event.label.trim() : "";
      const label = name && name.length <= 64 && !/[\\/:?&#=@<>{}\[\]\x00-\x1f]|--\w|[A-Za-z0-9_-]{20,}/.test(name)
        ? name : "工具调用";
      const ms = event.elapsed_ms;
      const elapsed = !Number.isSafeInteger(ms) || ms < 0 ? "" :
        ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(1) + " 秒";
      const [, description, statusEl, timeEl] = row.children;
      const [nameEl, toolEl] = description.children;
      if (nameEl.textContent !== label) nameEl.textContent = label;
      const tool = typeof event.tool === "string" && /^[\p{L}\p{N}][\p{L}\p{N} ·+_-]{0,31}$/u.test(event.tool)
        ? event.tool : "";
      if (toolEl.textContent !== tool) toolEl.textContent = tool;
      toolEl.hidden = !tool;
      if (row.dataset.status !== event.status) {
        row.dataset.status = event.status;
        statusEl.textContent = statusNames[event.status];
      }
      if (timeEl.textContent !== elapsed) timeEl.textContent = elapsed;
    }
    for (const [id, row] of rows) {
      if (!ids.has(id)) {
        row.remove();
        rows.delete(id);
      }
    }
    complete = snapshot.finished === true;
    if (!rows.size) {
      card.hidden = true;
      pill.hidden = false;
      return;
    }
    const phase = complete ? (failed ? "failed" : "done") : "running";
    if (card.hidden || card.dataset.state !== phase) card.open = !complete;
    pill.hidden = true;
    card.hidden = false;
    card.dataset.state = phase;
    const summary = (complete ? (failed ? "含失败步骤" : "已结束") : "进行中") + " · " + rows.size + " 步";
    if (count.textContent !== summary) count.textContent = summary;
    if (follow) chat.scrollTop = chat.scrollHeight;
  }

  async function poll() {
    let wait = document.hidden ? 1500 : 500;
    try {
      const response = await fetch("/api/activity?request_id=" + encodeURIComponent(requestId), {
        cache: "no-store", signal: controller.signal
      });
      if (stopped || [404, 405].includes(response.status)) return;
      if (!response.ok) throw new Error();
      if (!(response.headers.get("Content-Type") || "").includes("json")) return;
      const snapshot = await response.json();
      if (stopped) return;
      render(snapshot);
    } catch {
      wait = 1800;
    }
    if (!stopped && !complete) timer = setTimeout(poll, wait);
  }

  timer = setTimeout(poll, 160);
  return function finish(snapshot, requestFailed = false) {
    if (stopped) return;
    stopped = true;
    clearTimeout(timer);
    controller.abort();
    render(snapshot);
    if (!rows.size) {
      article.remove();
    } else if (!complete || requestFailed) {
      card.dataset.state = complete ? "failed" : "partial";
      count.textContent = (complete ? "请求失败" : "未取得完整回执") + " · " + rows.size + " 步";
      card.open = false;
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
    if (item.quote) block.append(PaperRichText.render(make("blockquote"), item.quote));
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

// 原文引用和原图使用提取器支持的站点。
function paperSourceLink(address) {
  try {
    const url = new URL(address);
    const hosts = ["arxiv.org", "aclanthology.org"];
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port ||
        !hosts.some(host => url.hostname === host || url.hostname.endsWith("." + host))) return null;
    return url.href;
  } catch {
    return null;
  }
}

const evidenceKinds = { metric: "关键数据", figure: "图", table: "表", formula: "公式" };

function reviewItem(item, paperUrl) {
  const block = make("section", undefined, "review-item");
  block.dataset.kind = item.kind;
  const kind = evidenceKinds[item.kind] || "论文摘录";
  const meta = make("div", undefined, "review-meta");
  meta.append(make("span", kind, "evidence-tag"));
  if (item.field) meta.append(make("span", "回填至「" + item.field + "」"));
  block.append(meta, PaperRichText.render(make("h4"), item.label, true));
  const latex = item.kind === "formula" && (item.latex || item.value);
  if (latex) {
    const copy = make("button", undefined, "mini-copy-btn formula-copy");
    copy.type = "button";
    copy.innerHTML = ICONS.copy + '<span class="copy-txt">复制 LaTeX</span>';
    copy.addEventListener("click", () => copyWithFeedback(copy, latex, "复制 LaTeX"));
    meta.append(copy);
    const raw = make("details", undefined, "review-quote formula-source");
    raw.append(make("summary", "查看 LaTeX 原文"), make("pre", latex));
    block.append(PaperRichText.formula(make("div"), latex, item.display !== false), raw);
  } else if (item.value && item.value !== item.label) {
    block.append(PaperRichText.render(make("div", undefined, "review-value"), item.value));
  }

  if (Array.isArray(item.rows) && item.rows.length) {
    const frame = make("div", undefined, "review-table-scroll");
    frame.tabIndex = 0;
    frame.setAttribute("role", "region");
    frame.setAttribute("aria-label", item.label || "原文表格");
    const table = make("table");
    if (Array.isArray(item.headers) && item.headers.length) {
      const head = table.createTHead().insertRow();
      for (const value of item.headers) head.append(PaperRichText.render(make("th"), value, true));
    }
    const body = table.createTBody();
    for (const values of item.rows) {
      if (!Array.isArray(values)) continue;
      const row = body.insertRow();
      for (const value of values) PaperRichText.render(row.insertCell(), value, true);
    }
    frame.append(table);
    block.append(frame);
  }
  const imageUrl = paperSourceLink(item.image_url);
  if (imageUrl) {
    const figure = link("", imageUrl, "review-image");
    const img = make("img");
    img.alt = item.label || "论文原图";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => img.remove(), { once: true });
    img.src = imageUrl;
    figure.append(img, make("span", "打开原图 ↗"));
    block.append(figure);
  }
  if (item.quote && item.quote !== item.value) {
    const quote = make("details", undefined, "review-quote");
    quote.append(make("summary", "查看原文摘录"), PaperRichText.render(make("blockquote"), item.quote));
    block.append(quote);
  }
  const sourceUrl = paperSourceLink(item.source_url) || paperSourceLink(item.source) || paperUrl;
  const label = item.source && !/^https?:/i.test(item.source) ? item.source : "论文原文";
  if (sourceUrl) block.append(link(label + " ↗", sourceUrl, "review-source"));
  else if (item.source) block.append(make("small", label));
  return block;
}

function openEvidenceReview(preview) {
  const dialog = byId("evidence-review");
  const container = byId("review-papers");
  container.replaceChildren();
  const paperSelect = byId("review-paper-select");
  paperSelect.replaceChildren();
  const kindSelect = byId("review-kind");
  reviewId = preview.id;
  const readyPapers = preview.papers.filter(paper => paper.status === "ready");
  const target = (state.mode === "feishu" ? "飞书" : "本地") + "「" + (state.table_name || "阅读清单") + "」";
  byId("review-summary").textContent = preview.papers.length + " 篇论文 · " +
    readyPapers.length + " 篇可回填 · " + target;
  preview.papers.forEach((paper, index) => {
    const paperUrl = paperSourceLink(paper.url);
    const section = make("section", undefined, "review-paper");
    const title = paper.title || paper.url || "论文";
    paperSelect.add(new Option((index + 1) + ". " + title, index));
    const action = { create: "新建记录", update: "更新记录", unchanged: "无需修改" }[paper.action];
    const items = Array.isArray(paper.items) ? paper.items : [];
    section.append(make("h3", title), make("span", paper.status === "ready"
      ? [items.length + " 项", action].filter(Boolean).join(" · ") : "跳过本篇", "review-paper-meta"));
    if (paperUrl) section.append(link("打开论文 ↗", paperUrl, "review-source"));
    if (paper.error) section.append(make("p", paper.error, "review-error"));
    if (paper.notice) section.append(make("p", paper.notice, "review-mapping"));
    if (paper.fields?.length) {
      const modes = { set: "填入", append: "追加", unchanged: "保留原值" };
      const mapping = paper.fields.map(field => (modes[field.mode] || "填入") + "「" + field.name + "」");
      section.append(make("p", mapping.join(" · "), "review-mapping"));
    }
    for (const item of items) section.append(reviewItem(item, paperUrl));
    container.append(section);
  });
  kindSelect.onchange = () => {
    const active = container.children[paperSelect.selectedIndex];
    for (const item of active.querySelectorAll(".review-item")) {
      item.hidden = Boolean(kindSelect.value && item.dataset.kind !== kindSelect.value);
    }
    container.scrollTop = 0;
    if (dialog.open && motionOk()) {
      active.animate([{ opacity: 0.55, transform: "translateY(3px)" }, { opacity: 1, transform: "none" }],
        { duration: 180, easing: "ease-out" });
    }
  };
  paperSelect.onchange = () => {
    const index = paperSelect.selectedIndex;
    Array.from(container.children).forEach((section, i) => { section.hidden = i !== index; });
    const items = preview.papers[index].items || [];
    kindSelect.replaceChildren(new Option("全部（" + items.length + "）", ""));
    for (const [kind, name] of Object.entries(evidenceKinds)) {
      const count = items.filter(item => item.kind === kind).length;
      if (count) kindSelect.add(new Option(name + "（" + count + "）", kind));
    }
    kindSelect.onchange();
  };
  paperSelect.onchange();
  const actionable = Boolean(reviewId && reviewId === pendingId && readyPapers.length);
  dialog.querySelector("footer").hidden = !actionable;
  const totalItems = readyPapers.reduce((total, paper) => total + paper.items.length, 0);
  byId("review-confirm").textContent = "确认回填 " + readyPapers.length + " 篇（" + totalItems + " 项）";
  controls();
  dialog.showModal();
  byId("review-title").focus();
  container.scrollTop = 0;
}

function appendEvidenceResults(article, results) {
  const list = make("ul", undefined, "review-results");
  const statuses = { verified: "回读一致", skipped: "已跳过", failed: "处理失败", unverified: "待核验" };
  for (const result of results) {
    const row = make("li");
    row.dataset.status = result.status;
    row.append(make("strong", statuses[result.status] || "待核验"), make("span", result.title || result.url));
    if (result.message) row.append(make("small", result.message));
    list.append(row);
  }
  article.append(list);
}

async function submit(text) {
  const instruction = text.trim();
  if (!instruction || busy || switchingModel || refreshingState || !ready) return;
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
  const finishActivity = trackActivity(request.id);

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
    finishActivity(result.activity);
    pendingId = result.pending_id || null;

    const { article } = appendMessage(result.message || "已完成。", "assistant");
    appendModelTrace(article, result.agent);

    if (result.evidence_preview?.papers?.length) {
      const preview = result.evidence_preview;
      const review = make("button", "查看提取结果", "icon-pill review-reopen");
      review.type = "button";
      review.addEventListener("click", () => openEvidenceReview(preview));
      article.append(review);
      review.focus({preventScroll: true});
      openEvidenceReview(preview);
    } else if (pendingId && Array.isArray(result.choices) && result.choices.length) {
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
    if (result.evidence_results?.length) appendEvidenceResults(article, result.evidence_results);

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
    const incomplete = result.evidence_results?.some(item => item.status !== "verified");

    if (result.verification) {
      const verifyEl = byId("verification");
      verifyEl.className = "verify-status report" + (incomplete ? " incomplete" : "");
      verifyEl.textContent = result.verification;
      const iconEl = byId("verify-icon");
      iconEl.classList.remove("verified");
      void iconEl.offsetWidth;
      iconEl.classList.toggle("verified", !incomplete);
      morphSendButton(incomplete ? "idle" : "saved");
      showToast(result.verification, incomplete ? "error" : "ok");
    } else {
      morphSendButton("idle");
    }

    if (result.row || result.evidence?.length) {
      showEvidence(Array.isArray(result.evidence) ? result.evidence : []);
    }

    if (!result.evidence_preview) await refreshState(false);
    if ((result.row || result.verification) && !incomplete) playRevealWave();
  } catch (err) {
    finishActivity(err.activity, true);
    morphSendButton("idle");
    if (err.networkFailure) {
      retryableRequest = request;
      byId("prompt").value = instruction;
      autoResizePrompt();
    }
    const { article } = appendMessage(err.message, "error");
    if (err.agent?.steps?.length) appendModelTrace(article, err.agent);
    else article.append(make("p", "本次请求失败 · 未取得模型调用记录", "model-trace"));
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
    evidence: "批量提取以下论文的关键数据和图表，核对后回填。\nhttps://arxiv.org/abs/2005.11401v4\nhttps://arxiv.org/abs/1706.03762v7",
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

byId("model-select").addEventListener("change", switchModel);
window.addEventListener("pageshow", event => {
  if (event.persisted && !busy && !switchingModel) refreshState();
});
byId("refresh").addEventListener("click", () => refreshState(true));
const reviewDialog = byId("evidence-review");
byId("review-close").addEventListener("click", () => reviewDialog.close());
for (const [id, instruction] of [["review-cancel", "取消回填"], ["review-confirm", "确认回填"]]) {
  byId(id).addEventListener("click", () => {
    if (!reviewId || reviewId !== pendingId) return;
    const message = instruction + " " + reviewId;
    reviewDialog.close();
    submit(message);
  });
}
byId("close-detail").addEventListener("click", () => byId("detail").close());
byId("detail").addEventListener("click", event => {
  if (event.target === byId("detail")) byId("detail").close();
});

controls();
selectView("cards", false);
refreshState(false);
