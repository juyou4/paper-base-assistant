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
  checkSm: '<svg class="ico-check-draw" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  checkPlain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 4.5 4.5L19 7"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v4.5M12 16h.01"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2.5"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>',
  arrowUpRight: '<svg class="ico-ext" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M9 7h8v8"/></svg>',
  download: '<svg class="ico-dl" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11m0 0-4-4m4 4 4-4M5 19h14"/></svg>',
  chevronDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>',
  chevronRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>',
  statusRun: '<svg class="ico-spin-arc" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.22-8.56"/></svg>',
  statusFail: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M15 9 9 15M9 9l6 6"/></svg>',
  statusSkip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 12h12"/></svg>',
  spinner8: '<svg class="thinking-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><line x1="12" y1="2.5" x2="12" y2="6" opacity="1"/><line x1="18.7" y1="5.3" x2="16.2" y2="7.8" opacity="0.85"/><line x1="21.5" y1="12" x2="18" y2="12" opacity="0.7"/><line x1="18.7" y1="18.7" x2="16.2" y2="16.2" opacity="0.55"/><line x1="12" y1="21.5" x2="12" y2="18" opacity="0.4"/><line x1="5.3" y1="18.7" x2="7.8" y2="16.2" opacity="0.28"/><line x1="2.5" y1="12" x2="6" y2="12" opacity="0.2"/><line x1="5.3" y1="5.3" x2="7.8" y2="7.8" opacity="0.14"/></svg>'
};

const customSelects = [];
function closeAllCustomSelects(except = null) {
  for (const inst of customSelects) {
    if (inst !== except) inst.close(false);
  }
}

function createCustomSelect(selectEl, opts = {}) {
  if (!selectEl || selectEl._cselect) return selectEl?._cselect;
  const {
    variant = "pill",
    placement = "bottom",
    minWidth = 170,
    maxHeight = 250,
    matchTriggerWidth = false,
    fullWidth = false
  } = opts;

  const wrapper = make("div", undefined, "cselect" + (fullWidth ? " cselect-full" : ""));
  const trigger = make("button", undefined, "cselect-trigger cselect-" + variant);
  trigger.type = "button";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  const labelAttr = selectEl.getAttribute("aria-label");
  if (labelAttr) trigger.setAttribute("aria-label", labelAttr);

  const labelSpan = make("span", "", "cselect-label");
  const busySpin = make("span", undefined, "cselect-busy-spin");
  busySpin.setAttribute("aria-hidden", "true");
  const chev = make("span", undefined, "cselect-chevron");
  chev.setAttribute("aria-hidden", "true");
  chev.innerHTML = ICONS.chevronDown;
  trigger.append(labelSpan, busySpin, chev);

  selectEl.before(wrapper);
  wrapper.append(trigger, selectEl);
  selectEl.classList.add("sr-select");
  selectEl.tabIndex = -1;
  selectEl.setAttribute("aria-hidden", "true");

  const menu = make("div", undefined, "cselect-menu");
  menu.setAttribute("role", "listbox");
  menu.inert = true;
  menu.setAttribute("aria-hidden", "true");
  const scrollBox = make("div", undefined, "cselect-scroll");
  const glide = make("div", undefined, "cselect-glide");
  glide.setAttribute("aria-hidden", "true");
  scrollBox.append(glide);
  menu.append(scrollBox);

  let open = false;
  let optionBtns = [];
  let lastLabel = "";

  function ensureMounted() {
    const host = selectEl.closest("dialog") || document.body;
    if (menu.parentElement !== host) host.append(menu);
  }

  function moveGlide(btn) {
    if (!btn || btn.disabled) {
      glide.classList.remove("visible");
      return;
    }
    glide.style.height = btn.offsetHeight + "px";
    glide.style.transform = `translate3d(0, ${btn.offsetTop}px, 0)`;
    glide.classList.add("visible");
  }

  function positionMenu() {
    ensureMounted();
    const rect = trigger.getBoundingClientRect();
    let width = matchTriggerWidth ? Math.max(rect.width, minWidth || 0) : Math.max(rect.width, minWidth);
    width = Math.min(width, window.innerWidth - 24);
    menu.style.width = width + "px";

    const spaceAbove = rect.top - 14;
    const spaceBelow = window.innerHeight - rect.bottom - 14;
    let chosen = placement;
    if (chosen === "top" && spaceAbove < 140 && spaceBelow > spaceAbove) chosen = "bottom";
    else if (chosen === "bottom" && spaceBelow < 140 && spaceAbove > spaceBelow) chosen = "top";

    const avail = chosen === "top" ? spaceAbove : spaceBelow;
    scrollBox.style.maxHeight = Math.max(100, Math.min(maxHeight, avail - 10)) + "px";

    const menuH = menu.offsetHeight;
    const top = chosen === "top"
      ? Math.max(10, rect.top - menuH - 6)
      : Math.min(window.innerHeight - menuH - 10, rect.bottom + 6);
    let left = (placement === "top" && !matchTriggerWidth) ? rect.right - width : rect.left;
    left = Math.max(12, Math.min(left, window.innerWidth - width - 12));

    menu.dataset.placement = chosen;
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function syncOptions() {
    const optsList = [...selectEl.options];
    const sig = optsList.map(o => o.value + "\0" + o.textContent + "\0" + (o.disabled ? "1" : "0")).join("\n");
    if (menu.dataset.sig !== sig) {
      menu.dataset.sig = sig;
      scrollBox.replaceChildren(glide);
      optionBtns = optsList.map((opt, idx) => {
        const btn = make("button", undefined, "cselect-option");
        btn.type = "button";
        btn.setAttribute("role", "option");
        btn.dataset.index = String(idx);
        btn.disabled = opt.disabled;
        const txt = make("span", opt.textContent, "cselect-option-text");
        const chk = make("span", undefined, "cselect-check");
        chk.setAttribute("aria-hidden", "true");
        chk.innerHTML = ICONS.checkPlain;
        btn.append(txt, chk);
        btn.addEventListener("pointerenter", () => moveGlide(btn));
        btn.addEventListener("focus", () => moveGlide(btn));
        btn.addEventListener("click", () => pick(idx));
        scrollBox.append(btn);
        return btn;
      });
    }
    const selIdx = selectEl.selectedIndex;
    optionBtns.forEach((btn, idx) => {
      btn.setAttribute("aria-selected", String(idx === selIdx));
    });
  }

  function sync(animateText = false) {
    syncOptions();
    const sel = selectEl.options[selectEl.selectedIndex];
    const text = sel ? sel.textContent : "";
    if (text !== lastLabel) {
      if (animateText && lastLabel) swapInlineText(labelSpan, text);
      else labelSpan.textContent = text;
      lastLabel = text;
    }
    trigger.title = text;
    wrapper.hidden = selectEl.hidden;
    trigger.disabled = selectEl.disabled || !selectEl.options.length;
    if ((trigger.disabled || wrapper.hidden) && open) close(false);
  }

  function setOpen(next, focusOpt = true) {
    if (next && trigger.disabled) return;
    if (open === next) return;
    if (next) closeAllCustomSelects(inst);
    open = next;
    trigger.classList.toggle("is-open", open);
    trigger.setAttribute("aria-expanded", String(open));
    menu.inert = !open;
    menu.setAttribute("aria-hidden", String(!open));
    if (open) {
      sync(false);
      positionMenu();
      menu.classList.add("is-open");
      const active = optionBtns[Math.max(0, selectEl.selectedIndex)] || optionBtns[0];
      if (active) {
        active.scrollIntoView({ block: "nearest" });
        requestAnimationFrame(() => moveGlide(active));
        if (focusOpt) requestAnimationFrame(() => active.focus({ preventScroll: true }));
      }
    } else {
      menu.classList.remove("is-open");
      glide.classList.remove("visible");
    }
  }

  function close(restoreFocus = false) {
    const wasOpen = open;
    setOpen(false, false);
    if (wasOpen && restoreFocus && trigger.isConnected && !trigger.disabled) {
      trigger.focus({ preventScroll: true });
    }
  }

  function pick(idx) {
    const changed = selectEl.selectedIndex !== idx;
    selectEl.selectedIndex = idx;
    sync(true);
    close(true);
    if (changed) selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  trigger.addEventListener("click", () => setOpen(!open, true));
  trigger.addEventListener("keydown", e => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      if (!open) setOpen(true, true);
    }
  });

  menu.addEventListener("pointerleave", () => {
    const focused = optionBtns.find(b => b === document.activeElement);
    if (focused) moveGlide(focused);
    else glide.classList.remove("visible");
  });

  menu.addEventListener("keydown", e => {
    const enabled = optionBtns.filter(b => !b.disabled);
    if (!enabled.length) return;
    const cur = enabled.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      enabled[(cur + 1) % enabled.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      enabled[(cur - 1 + enabled.length) % enabled.length].focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      enabled[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      enabled[enabled.length - 1].focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close(true);
    } else if (e.key === "Tab") {
      close(false);
    }
  });

  const dialog = selectEl.closest("dialog");
  const onDialogClose = () => close(false);
  if (dialog) dialog.addEventListener("close", onDialogClose);

  function destroy() {
    close(false);
    if (dialog) dialog.removeEventListener("close", onDialogClose);
    menu.remove();
    const idx = customSelects.indexOf(inst);
    if (idx >= 0) customSelects.splice(idx, 1);
    delete selectEl._cselect;
  }

  const inst = {
    sync,
    close,
    destroy,
    trigger,
    containsTarget: t => wrapper.contains(t) || menu.contains(t),
    isOpen: () => open
  };
  customSelects.push(inst);
  selectEl._cselect = inst;
  sync(false);
  return inst;
}

document.addEventListener("pointerdown", e => {
  for (const inst of customSelects) {
    if (inst.isOpen() && !inst.containsTarget(e.target)) inst.close(false);
  }
});
window.addEventListener("resize", () => closeAllCustomSelects());
window.addEventListener("scroll", e => {
  for (const inst of customSelects) {
    if (inst.isOpen() && !inst.containsTarget(e.target)) inst.close(false);
  }
}, true);

function animateDetailsToggle(detailsEl, bodySelector) {
  const summary = detailsEl.querySelector("summary");
  const body = detailsEl.querySelector(bodySelector);
  if (!summary || !body) return;
  let anim = null;
  summary.addEventListener("click", e => {
    if (!motionOk()) return;
    e.preventDefault();
    if (anim) {
      anim.cancel();
      anim = null;
    }
    if (detailsEl.open) {
      const h = body.scrollHeight;
      anim = body.animate(
        [
          { height: h + "px", opacity: 1 },
          { height: "0px", opacity: 0 }
        ],
        { duration: 210, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );
      anim.finished.then(() => {
        detailsEl.open = false;
        anim = null;
      }, () => { anim = null; });
    } else {
      detailsEl.open = true;
      const h = body.scrollHeight;
      anim = body.animate(
        [
          { height: "0px", opacity: 0 },
          { height: h + "px", opacity: 1 }
        ],
        { duration: 260, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
      );
      anim.finished.then(() => { anim = null; }, () => { anim = null; });
    }
  });
}

function makeSummaryWithChevron(labelText) {
  const sum = make("summary");
  sum.append(make("span", labelText));
  const chev = make("span", undefined, "disclosure-chevron");
  chev.setAttribute("aria-hidden", "true");
  chev.innerHTML = ICONS.chevronDown;
  sum.append(chev);
  return sum;
}

let state = { fields: [], rows: [], sample: {}, capabilities: {} };
let busy = false;
let ready = false;
let switchingModel = false;
let refreshingState = false;
let pendingId = null;
let reviewId = null;
const reviewSelections = new Map();
const reviewFieldOverrides = new Map();
const reviewColSelects = [];
let selectedId = null;
let choosingComparison = false;
const comparedRecords = new Map();
let hasVisibleRows = false;
let viewMode = "cards";
let latestStateRequest = 0;
let retryableRequest = null;
let sendResetTimer = null;
let uploadingPdf = false;

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

function iconLink(label, url, className, iconSvg = ICONS.arrowUpRight) {
  const a = link(undefined, url, className);
  const span = make("span", label);
  const wrap = make("span", undefined, "ico-wrap");
  wrap.setAttribute("aria-hidden", "true");
  wrap.innerHTML = iconSvg;
  a.append(span, wrap);
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
    status.textContent = enabled ? (name || "模型已就绪") : "规则模式";
  }
  const select = byId("model-select");
  const models = Array.isArray(agent?.models)
    ? [...new Set(agent.models.filter(model => typeof model === "string" && model.trim()))] : [];
  byId("model-picker").hidden = !ready || agent?.selectable !== true || !models.includes(agent.model);
  if (models.length !== select.length || models.some((model, index) => model !== select.options[index].value)) {
    select.replaceChildren(...models.map(model => new Option(model, model)));
  }
  select.value = agent?.model || "";
  select._cselect?.sync();
  const target = ready && state.mode === "feishu" ? feishuTableLink(state.feishu_url) : null;
  const external = byId("feishu-link");
  external.hidden = !target;
  if (target) external.href = target;
  else external.removeAttribute("href");
}

function appendModelTrace(article, agent) {
  const steps = Array.isArray(agent?.steps)
    ? agent.steps.filter(step => typeof step === "string" && step.trim()) : [];
  const trace = make("div", undefined, "model-trace");
  if (!steps.length) {
    trace.append(make("span", "规则处理", "model-trace-badge"));
    article.append(trace);
    return;
  }
  trace.append(make("span", modelName(agent) || "模型推理", "model-trace-badge"));
  const row = make("div", undefined, "model-steps-row");
  steps.forEach((step, idx) => {
    if (idx > 0) {
      const sep = make("span", undefined, "model-step-sep");
      sep.setAttribute("aria-hidden", "true");
      sep.innerHTML = ICONS.chevronRight;
      row.append(sep);
    }
    row.append(make("span", step, "model-step-chip"));
  });
  trace.append(row);
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
    showToast("复制失败，请手动复制", "error");
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
        { opacity: 0.3, filter: "blur(3px)", transform: "scale(0.94)" },
        { opacity: 1, filter: "blur(0)", transform: "scale(1)" }
      ],
      { duration: 200, easing: "cubic-bezier(0.22, 1, 0.36, 1)" }
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
  const cardsEl = byId("cards-view");
  const tableEl = byId("table-view");
  const emptyEl = byId("empty-sheet");

  if (!hasVisibleRows) {
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
  byId("pdf-add").disabled = locked || uploadingPdf;
  byId("pdf-bind").disabled = locked || uploadingPdf;
  byId("pdf-save").disabled = byId("pdf-extract").disabled =
    locked || Boolean(pendingId) || uploadingPdf || state.target_bound === false;
  byId("send").disabled = locked || !input.value.trim();
  input.disabled = busy || !ready;
  byId("refresh").disabled = busy || switchingModel || refreshingState;
  byId("model-select").disabled = locked;
  byId("model-picker").setAttribute("aria-busy", String(switchingModel));
  byId("model-picker-label").textContent = switchingModel ? "切换中" : "模型";
  byId("pending-badge").hidden = !pendingId;
  document.querySelectorAll("[data-example]").forEach(btn => {
    btn.disabled = locked || Boolean(pendingId) || (state.target_bound === false && btn.dataset.example !== "base");
  });
  document.querySelectorAll("[data-choice]").forEach(btn => {
    btn.disabled = locked || btn.dataset.task !== pendingId;
  });
  const reviewLocked = locked || !reviewId || reviewId !== pendingId;
  byId("review-cancel").disabled = reviewLocked;
  byId("review-confirm").disabled = reviewLocked || !reviewSelections.get(reviewId)?.some(items => items.some(Boolean));
  const emptyCta = byId("empty-try-btn");
  if (emptyCta) emptyCta.disabled = locked || Boolean(pendingId);
  for (const cs of customSelects) cs.sync(false);
}

async function switchModel() {
  const select = byId("model-select");
  const requested = select.value;
  const current = state.capabilities.agent;
  if (!ready || busy || switchingModel || refreshingState || !current?.selectable ||
      !current.models?.includes(requested) || current.model === requested) return;

  // 发送前以会话回执为准。网络中断时，再读一次服务端状态。
  const restoreFocus = document.activeElement === select || document.activeElement === select._cselect?.trigger;
  switchingModel = true;
  controls();
  try {
    const result = await api("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: requested })
    });
    if (result.agent?.model !== requested || !Array.isArray(result.agent.models) ||
        !result.agent.models.includes(requested)) throw new Error("模型切换失败，请重试");
    state.capabilities.agent = result.agent;
    renderConnectionDetails();
    showToast(result.message || "已切换至 " + requested);
  } catch (err) {
    select.value = current.model;
    select._cselect?.sync(false);
    await refreshState();
    showToast(err.message, "error");
  } finally {
    switchingModel = false;
    controls();
    if (restoreFocus && document.activeElement === document.body && !byId("model-picker").hidden) {
      (select._cselect?.trigger || select).focus({ preventScroll: true });
    }
  }
}

async function api(path, options) {
  let res;
  try {
    res = await fetch(path, { cache: "no-store", ...options });
  } catch {
    const error = new Error("连接中断，请稍后重试");
    error.networkFailure = true;
    throw error;
  }
  let data;
  try {
    data = await res.json();
  } catch (cause) {
    const error = new Error("响应格式异常，请重试");
    error.networkFailure = cause instanceof TypeError;
    throw error;
  }
  if (!res.ok || data.error) {
    const error = new Error(data.error || data.message || "服务暂不可用");
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

function matchesProgress(row) {
  const filter = byId("reading-filter").value;
  return !state.reading_progress || !filter || (state.reading_progress[row.id] || "待读") === filter;
}

function searchText(value) {
  return cellValue(value).normalize("NFKC").toLowerCase().replace(/\s+/g, " ").trim();
}

function renderLibraryRows() {
  const populated = state.rows.filter(row => Object.values(row.fields || {}).some(hasCellValue));
  const query = searchText(byId("library-query").value);
  let wholeTerm = null;
  if (/^[a-z0-9]{1,5}$/.test(query)) {
    wholeTerm = new RegExp("(^|[^a-z0-9])" + query + "(?![a-z0-9])");
  } else if (/^[a-z][a-z0-9-]*[-.]\d+(?:\.\d+)*$/.test(query)) {
    wholeTerm = new RegExp("(^|[^a-z0-9_-])" + query.replaceAll(".", "\\.") + "(?![a-z0-9_-]|\\.\\d)");
  }
  const visible = populated.filter(row => {
    if (!matchesProgress(row)) return false;
    if (!query || state.title_aliases?.[row.id]?.includes(query)) return true;
    return Object.values(row.fields || {}).some(value => {
      const text = searchText(value);
      return wholeTerm ? wholeTerm.test(text) : text.includes(query);
    });
  });
  hasVisibleRows = visible.length > 0;
  const filtered = Boolean(query || (state.reading_progress && byId("reading-filter").value));
  byId("filter-count").textContent = filtered ? visible.length + " / " + populated.length + " 条" : "";
  byId("reset-library-filter").hidden = !filtered;
  renderSelection();
  renderCardsView(visible);
  renderTableView(visible);
  byId("empty-sheet-text").textContent = state.target_bound === false ? "请先新建多维表格"
    : populated.length && filtered ? "没有符合当前筛选条件的论文" : "暂无论文记录";
  byId("empty-try-btn").hidden = filtered || state.target_bound === false || Boolean(populated.length);
  syncViewVisibility(false);
}

function resetLibraryFilter() {
  byId("library-query").value = "";
  byId("reading-filter").value = "";
}

function paperRef(row) {
  const names = state.identity_fields || {};
  const value = keys => (keys || []).map(key => cellValue(row.fields[key])).find(Boolean) || "";
  return { id: row.id, title: value(names.titles), url: value(names.links) };
}

function toggleCompared(ref) {
  if (comparedRecords.has(ref.id)) comparedRecords.delete(ref.id);
  else if (comparedRecords.size === 4) return showToast("一次最多对照四篇论文");
  else comparedRecords.set(ref.id, ref);
  renderLibraryRows();
}

function comparisonPick(row) {
  const ref = paperRef(row);
  const index = [...comparedRecords.keys()].indexOf(ref.id);
  const button = make("button", index < 0 ? "选择" : "已选 " + (index + 1), "paper-detail-pill compare-pick");
  button.type = "button";
  button.setAttribute("aria-pressed", String(index >= 0));
  button.setAttribute("aria-label", (index < 0 ? "选择对照：" : "移出对照：") + (ref.title || ref.url));
  button.disabled = (!ref.title && !ref.url) || (index < 0 && comparedRecords.size === 4);
  button.onclick = () => toggleCompared(ref);
  return button;
}

function renderSelection() {
  byId("compare-mode").setAttribute("aria-pressed", String(choosingComparison));
  byId("compare-mode").textContent = choosingComparison ? "结束选择" : "选择对照";
  byId("selection-bar").hidden = !choosingComparison;
  byId("selection-count").textContent = comparedRecords.size ? "已选 " + comparedRecords.size + " / 4 篇" : "请选择 2 至 4 篇";
  byId("compare-selected").disabled = comparedRecords.size < 2;
  const chips = byId("selection-chips");
  chips.replaceChildren();
  for (const [i, ref] of [...comparedRecords.values()].entries()) {
    const chip = make("button", (i + 1) + ". " + (ref.title || ref.url) + " ×", "paper-detail-pill selection-chip");
    chip.type = "button";
    chip.title = "移出对照：" + (ref.title || ref.url);
    chip.onclick = () => toggleCompared(ref);
    chips.append(chip);
  }
}

function readingTools(row) {
  const wrap = make("div", undefined, "reading-tools");
  const current = state.reading_progress[row.id] || "待读";
  const select = make("select", undefined, "paper-detail-pill");
  select.setAttribute("aria-label", "阅读进度");
  for (const label of ["待读", "在读", "已读"]) select.append(new Option(label, label));
  select.value = current;
  select.onchange = async () => {
    select.disabled = true;
    try {
      await api("/api/reading-progress", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ record_id: row.id, expected: current, status: select.value }) });
      showToast("阅读进度已保存到本机");
    } catch (err) { showToast(err.message, "error"); }
    await refreshState();
    const fresh = state.rows.find(item => item.id === row.id);
    if (byId("detail").open && fresh && ready) showDetail(fresh);
    else if (byId("detail").open) byId("detail").close();
  };
  wrap.append(select);
  const { urlField } = detectPrimaryFields(state.fields);
  const rawUrl = urlField && cellValue(row.fields[urlField.name]);
  const url = /^https?:\/\//i.test(rawUrl || "") && safeLink(rawUrl);
  if (url && /^https?:\/\//i.test(url)) {
    for (const [label, prompt] of [["追加笔记", "给 " + url + " 记一句："], ["补齐缺项", "补齐缺失的摘要和方法 " + url],
                                   ["预览数据和图表", "提取数据和图表 " + url]]) {
      const button = make("button", label, "paper-detail-pill");
      button.type = "button";
      button.onclick = () => {
        if (pendingId || busy) return showToast("请先完成或取消当前操作");
        if (byId("prompt").value.trim()) return showToast("输入框里还有草稿，请先处理草稿");
        byId("detail").close();
        byId("prompt").value = prompt;
        autoResizePrompt();
        byId("prompt").focus();
        controls();
      };
      wrap.append(button);
    }
  }
  return wrap;
}

function showDetail(row) {
  const list = byId("detail-content");
  list.replaceChildren();
  const ref = paperRef(row);
  const viewId = state.table_view_id;
  const cite = byId("copy-citation");
  cite.disabled = !(ref.title || ref.url);
  cite.onclick = async () => {
    try {
      const result = await recordsRequest("export", { format: "bib", records: [ref] }, viewId);
      await copyWithFeedback(cite, result.content, "复制 BibTeX");
      if (result.notice) showToast(result.notice);
    } catch (err) { showToast(err.message, "error"); }
  };
  if (state.reading_progress) {
    list.append(makeDetailRow("本地阅读进度", "", dd => dd.append(readingTools(row))));
  }
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
  if (!byId("detail").open) byId("detail").showModal();
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
    (state.identity_fields?.links || []).map(name => fields.find(f => f.name === name)).find(Boolean) ||
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
    if (state.reading_progress) heading.append(make("small", state.reading_progress[row.id] || "待读", "reading-label"));

    const actions = make("div", undefined, "paper-card-actions");
    if (choosingComparison) actions.append(comparisonPick(row));
    const rawUrl = urlField ? cellValue(row.fields[urlField.name]) : "";
    const validUrl = /^https?:\/\//i.test(rawUrl) ? safeLink(rawUrl) : null;
    if (validUrl) {
      const urlPill = iconLink(shortHost(validUrl), validUrl, "paper-url-pill");
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
  ["#", ...(choosingComparison ? ["对照"] : []), ...(state.reading_progress ? ["阅读进度"] : []), ...state.fields.map(field => field.name)].forEach(name => {
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
    if (choosingComparison) tr.insertCell().append(comparisonPick(row));
    if (state.reading_progress) tr.insertCell().textContent = state.reading_progress[row.id] || "待读";
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

  byId("records-heading").textContent = [state.base_name, state.table_name].filter(Boolean).join(" / ") || "阅读清单";
  const populatedRows = state.rows.filter(row => Object.values(row.fields || {}).some(hasCellValue));
  const blankCount = state.rows.length - populatedRows.length;

  const rowSummary = blankCount
    ? populatedRows.length + " 条（含 " + blankCount + " 空行）"
    : populatedRows.length + " 条";
  swapInlineText(byId("row-count"), rowSummary);
  swapInlineText(byId("field-count"), state.fields.length + " 列");

  byId("sample").hidden = !state.sample.title;
  byId("sample-title").textContent = state.sample.title || "";
  const download = safeLink(state.download_url, true);
  byId("sample-pdf").hidden = !download;
  if (download) byId("sample-pdf").href = download;
  else byId("sample-pdf").removeAttribute("href");

  renderSchemaStrip();
  byId("reading-filter").hidden = !state.reading_progress;
  byId("export-format").querySelector('[value="json"]').disabled = !state.reading_progress;
  renderLibraryRows();
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
    if (state.table_view_id !== snapshot.table_view_id) {
      choosingComparison = false;
      comparedRecords.clear();
    }
    state = {
      ...snapshot,
      sample: snapshot.sample || {},
      fields: snapshot.fields || [],
      rows: snapshot.rows || [],
      capabilities: snapshot.capabilities || {}
    };
    ready = true;
    if (comparedRecords.size) {
      const current = new Map(state.rows.map(row => [row.id, row]));
      for (const ref of comparedRecords.values()) {
        const row = current.get(ref.id);
        const live = row && paperRef(row);
        if (!live || live.title !== ref.title || live.url !== ref.url) comparedRecords.delete(ref.id);
      }
    }
    renderPdfTray();
    renderState();
    const plan = state.pending_dialog || state.workspace_pending;
    if (plan && plan.id !== pendingId) {
      pendingId = plan.id;
      const { article } = appendMessage(plan.message, "assistant");
      if (plan.evidence_preview) showReviewButton(article, plan.evidence_preview, true);
      else if (plan.choices.length) appendChoices(article, plan.choices, plan.id);
    } else if (!plan && "pending_dialog" in state) {
      pendingId = null;
    }
    if (manual) showToast("已刷新");
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
    if (!state.rows.length) byId("row-count").textContent = "读取失败";
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
  animateDetailsToggle(card, ".activity-body");
  const headIcon = card.querySelector(".activity-head-icon");
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
  let lastPhase = "";
  const controller = new AbortController();
  const statusNames = { running: "进行中", done: "已完成", failed: "失败" };

  function setPhaseIcon(phase) {
    if (lastPhase === phase) return;
    lastPhase = phase;
    headIcon.innerHTML = phase === "done" ? ICONS.checkPlain
      : (phase === "failed" || phase === "partial") ? ICONS.statusFail
      : ICONS.statusRun;
  }

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
        const marker = make("span", undefined, "activity-marker");
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
      const [marker, description, statusEl, timeEl] = row.children;
      const [nameEl, toolEl] = description.children;
      if (nameEl.textContent !== label) nameEl.textContent = label;
      const tool = typeof event.tool === "string" && /^[\p{L}\p{N}][\p{L}\p{N} ·+_-]{0,31}$/u.test(event.tool)
        ? event.tool : "";
      if (toolEl.textContent !== tool) toolEl.textContent = tool;
      toolEl.hidden = !tool;
      if (row.dataset.status !== event.status) {
        row.dataset.status = event.status;
        statusEl.textContent = statusNames[event.status];
        marker.innerHTML = event.status === "done" ? ICONS.checkSm
          : event.status === "failed" ? ICONS.statusFail : ICONS.statusRun;
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
    setPhaseIcon(phase);
    const summary = (complete ? (failed ? "部分失败" : "已完成") : "执行中") + " · " + rows.size + " 步";
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
      const failPhase = complete ? "failed" : "partial";
      card.dataset.state = failPhase;
      setPhaseIcon(failPhase);
      count.textContent = (complete ? "执行失败" : "连接中断") + " · " + rows.size + " 步";
      card.open = false;
    }
  };
}

function showEvidence(items) {
  const container = byId("evidence");
  container.replaceChildren();
  if (!items.length) {
    container.append(make("p", "暂无原文出处", "muted"));
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
      if (address) source.append(iconLink(item.source, address));
      else source.textContent = item.source;
      block.append(source);
    }
    container.append(block);
  });
}

// 原文引用和原图使用提取器支持的站点。
function paperSourceLink(address) {
  if (typeof address === "string" && /^\/api\/attachments\/[0-9a-f]{64}#page=[1-9]\d*$/.test(address)) {
    return new URL(address, location.origin).href;
  }
  try {
    const url = new URL(address);
    const hosts = ["arxiv.org", "aclanthology.org", "openaccess.thecvf.com", "openreview.net", "doi.org", "ieeexplore.ieee.org"];
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.port ||
        !hosts.some(host => url.hostname === host || url.hostname.endsWith("." + host))) return null;
    return url.href;
  } catch {
    return null;
  }
}

const evidenceKinds = { metric: "数据", figure: "图片", table: "表格", formula: "公式",
  code: "代码仓库", model: "模型资源", dataset: "数据集" };
const resourceKinds = new Set(["code", "model", "dataset"]);

function reviewItem(item, paperUrl, checkbox, activeField, pageEndpoint) {
  const block = make("section", undefined, "review-item");
  block.dataset.kind = item.kind;
  const kind = evidenceKinds[item.kind] || "摘录";
  const meta = make("div", undefined, "review-meta");
  if (checkbox) meta.append(checkbox);
  meta.append(make("span", kind, "evidence-tag"));
  const fieldName = activeField || item.field;
  if (fieldName) meta.append(make("span", "写入「" + fieldName + "」", "review-item-field"));
  block.append(meta, PaperRichText.render(make("h4"), item.label, true));
  if (item.review_required) {
    const notice = make("p", undefined, "review-uncertain");
    notice.append(make("strong", "需核对原页"), "　" + item.review_required);
    checkbox?.setAttribute("aria-description", notice.textContent);
    block.append(notice);
  }
  const latex = item.kind === "formula" && (item.latex || item.value);
  if (latex) {
    const copy = make("button", undefined, "mini-copy-btn formula-copy");
    copy.type = "button";
    copy.innerHTML = ICONS.copy + '<span class="copy-txt">复制 LaTeX</span>';
    copy.addEventListener("click", () => copyWithFeedback(copy, latex, "复制 LaTeX"));
    meta.append(copy);
    const raw = make("details", undefined, "review-quote formula-source");
    raw.append(makeSummaryWithChevron("LaTeX 源码"), make("pre", latex));
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
    const figure = link(undefined, imageUrl, "review-image");
    const img = make("img");
    img.alt = item.label || "论文原图";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.addEventListener("error", () => img.remove(), { once: true });
    img.src = imageUrl;
    const caption = make("span");
    caption.append(make("span", "打开原图"));
    const ico = make("span", undefined, "ico-wrap");
    ico.setAttribute("aria-hidden", "true");
    ico.innerHTML = ICONS.arrowUpRight;
    caption.append(ico);
    figure.append(img, caption);
    block.append(figure);
  }
  if (item.quote && item.quote !== item.value) {
    const quote = make("details", undefined, "review-quote");
    quote.append(makeSummaryWithChevron("原文片段"), PaperRichText.render(make("blockquote"), item.quote));
    block.append(quote);
  }
  const sourceUrl = paperSourceLink(item.source_url) || paperSourceLink(item.source) || paperUrl;
  const label = item.source && !/^https?:/i.test(item.source) ? item.source : "论文原文";
  if (sourceUrl) block.append(iconLink(label, sourceUrl, "review-source"));
  else if (item.source) block.append(make("small", label));
  if (resourceKinds.has(item.kind)) {
    const resource = safeLink(item.resource_url);
    if (resource && ["github.com", "gitlab.com", "huggingface.co"].includes(new URL(resource).hostname)) {
      block.append(iconLink("打开" + kind, resource, "review-source"));
    }
  }
  if (pageEndpoint && Number.isInteger(item.pdf_page)) {
    const page = make("details", undefined, "review-quote pdf-page");
    page.append(makeSummaryWithChevron("核对 PDF 第 " + item.pdf_page + " 页"));
    page.addEventListener("toggle", () => {
      if (!page.open || page.querySelector("img")) return;
      const image = make("img");
      image.alt = "证据所在 PDF 第 " + item.pdf_page + " 页";
      const status = make("span", "正在读取原页…");
      image.onload = () => status.remove();
      image.onerror = () => { status.textContent = "原页预览暂不可用，可打开上方原文核对。"; image.hidden = true; };
      image.src = pageEndpoint;
      page.append(status, image);
    });
    block.append(page);
  }
  return block;
}

function showReviewButton(article, preview, restored = false) {
  const review = make("button", "核对并回填", "icon-pill review-reopen");
  review.type = "button";
  review.addEventListener("click", () => openEvidenceReview(preview, restored));
  article.append(review);
  review.focus({preventScroll: true});
  openEvidenceReview(preview, restored);
}

function openEvidenceReview(preview, restored = false) {
  const dialog = byId("evidence-review");
  while (reviewColSelects.length) reviewColSelects.pop().destroy();
  const container = byId("review-papers");
  container.replaceChildren();
  const tabsEl = byId("review-paper-tabs");
  tabsEl.replaceChildren();
  const paperSelect = byId("review-paper-select");
  paperSelect.replaceChildren();
  const kindSelect = byId("review-kind");
  reviewId = preview.id;
  const readyPapers = preview.papers.filter(paper => paper.status === "ready");
  const actionable = Boolean(reviewId && reviewId === pendingId && readyPapers.length);
  if (actionable && !reviewSelections.has(reviewId)) {
    let saved;
    if (restored) {
      try { saved = JSON.parse(sessionStorage.getItem("paper-review-choices")); } catch {}
    }
    const choices = preview.papers.map(paper => paper.status === "ready"
      ? paper.items.map(item => !restored && !resourceKinds.has(item.kind) && !item.review_required)
      : []);
    const matches = saved?.id === reviewId && Array.isArray(saved.choices) &&
      saved.choices.length === choices.length && choices.every((items, i) =>
        Array.isArray(saved.choices[i]) && saved.choices[i].length === items.length &&
        saved.choices[i].every(value => typeof value === "boolean"));
    reviewSelections.set(reviewId, matches ? saved.choices : choices);
    const defaultOverrides = preview.papers.map(() => ({}));
    const fieldsMatch = matches && Array.isArray(saved.fields) &&
      saved.fields.length === preview.papers.length &&
      saved.fields.every((entry, i) => {
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) return false;
        const allowed = new Set(preview.papers[i].target_columns || []);
        const count = (preview.papers[i].items || []).length;
        return Object.entries(entry).every(([k, v]) => /^\d+$/.test(k) && Number(k) < count && allowed.has(v));
      });
    reviewFieldOverrides.set(reviewId, fieldsMatch ? saved.fields.map(m => ({ ...m })) : defaultOverrides);
  } else if (actionable && !reviewFieldOverrides.has(reviewId)) {
    reviewFieldOverrides.set(reviewId, preview.papers.map(() => ({})));
  }
  const selections = reviewSelections.get(reviewId);
  const fieldOverrides = reviewFieldOverrides.get(reviewId);
  function updateCount() {
    if (actionable) {
      try {
        sessionStorage.setItem("paper-review-choices", JSON.stringify({
          id: preview.id, choices: selections, fields: fieldOverrides
        }));
      } catch {}
    }
    const counts = (selections || []).map(items => items.filter(Boolean).length);
    const papers = counts.filter(Boolean).length;
    const items = counts.reduce((total, count) => total + count, 0);
    byId("review-confirm").textContent = "确认写入 " + papers + " 篇 · " + items + " 项";
    controls();
  }
  const target = (state.mode === "feishu" ? "飞书" : "本地") + "「" + (state.table_name || "阅读清单") + "」";
  byId("review-summary").textContent = readyPapers.length + "/" + preview.papers.length + " 篇可写入 · " + target;
  tabsEl.hidden = !preview.papers.length;
  const tabRefs = [];
  const shortActions = { create: "新增", update: "更新", unchanged: "无变化" };
  preview.papers.forEach((paper, index) => {
    const paperUrl = paperSourceLink(paper.url);
    const section = make("section", undefined, "review-paper");
    const title = paper.title || paper.url || "论文";
    paperSelect.add(new Option((index + 1) + ". " + title, index));
    const action = { create: "新增", update: "更新", unchanged: "无变化" }[paper.action];
    const items = Array.isArray(paper.items) ? paper.items : [];
    const tabBtn = make("button", undefined, "review-tab");
    tabBtn.type = "button";
    tabBtn.setAttribute("role", "tab");
    tabBtn.setAttribute("aria-selected", String(index === 0));
    tabBtn.title = title;
    tabBtn.dataset.state = paper.status === "ready" ? "ready" : "skipped";
    const tabTop = make("span", undefined, "review-tab-top");
    tabTop.append(make("span", String(index + 1), "review-tab-idx"), make("span", title, "review-tab-title"));
    const tabFoot = make("span", undefined, "review-tab-foot");
    const tabCount = make("span", paper.status === "ready" ? items.length + " 项" : "跳过", "review-tab-count");
    const tabAction = make("span", paper.status === "ready" ? (shortActions[paper.action] || "") : "需核对", "review-tab-action");
    tabFoot.append(tabCount, tabAction);
    tabBtn.append(tabTop, tabFoot);
    tabBtn.addEventListener("click", () => {
      if (paperSelect.selectedIndex === index) return;
      paperSelect.selectedIndex = index;
      paperSelect.onchange();
      paperSelect._cselect?.sync(false);
    });
    tabsEl.append(tabBtn);
    tabRefs.push(tabBtn);

    const paperMeta = make("span", paper.status === "ready"
      ? [items.length + " 项", action].filter(Boolean).join(" · ") : "暂不写入", "review-paper-meta");
    const mapping = make("p", undefined, "review-mapping");
    section.append(make("h3", title), paperMeta);
    const selected = selections?.[index];
    const paperOverrides = fieldOverrides?.[index] || {};
    const group = make("input");
    const inputs = [];
    const itemFieldNodes = [];
    function effectiveField(i) {
      return paperOverrides[i] || items[i]?.field || "";
    }
    function syncGroup() {
      group.checked = selected.every(Boolean);
      group.indeterminate = !group.checked && selected.some(Boolean);
      const count = selected.filter(Boolean).length;
      paperMeta.textContent = "已选 " + count + "/" + items.length + " 项" +
        (group.checked && action ? " · " + action : "");
      tabCount.textContent = count + "/" + items.length + " 项";
      tabBtn.dataset.state = count ? "ready" : "empty";
      const hasOverride = Object.keys(paperOverrides).length > 0;
      const evidenceFields = new Set(items.map(item => item.field));
      const activeEvidenceCols = [...new Set(items.flatMap((item, i) => selected[i] ? [effectiveField(i)] : []).filter(Boolean))];
      const idFields = (paper.fields || []).map(field => field.name).filter(name => !evidenceFields.has(name));
      const allTargetCols = [...new Set([...activeEvidenceCols, ...idFields])];
      const modes = { set: "写入", append: "追加", unchanged: "不变" };
      mapping.textContent = group.checked && !hasOverride
        ? (paper.fields || []).map(field => (modes[field.mode] || "写入") + "「" + field.name + "」").join(" · ")
        : count && allTargetCols.length ? "写入列 · " + allTargetCols.join("、") : "";
      mapping.hidden = !mapping.textContent;
    }
    if (selected?.length) {
      const toggle = make("label", undefined, "review-toggle");
      group.type = "checkbox";
      group.disabled = !actionable;
      group.setAttribute("aria-label", "全选本篇提取项：" + title);
      toggle.append(group, make("span", "全选本篇"));
      section.append(toggle);
      syncGroup();
      group.addEventListener("change", () => {
        selected.fill(group.checked);
        inputs.forEach(input => { input.checked = group.checked; });
        syncGroup();
        updateCount();
      });
    }
    if (paperUrl) section.append(iconLink("查看原文", paperUrl, "review-source"));
    if (paper.source_version) section.append(make("p", "来源 · " + paper.source_version, "review-mapping"));
    const targetUrl = paperSourceLink(paper.target_url);
    if (targetUrl) section.append(iconLink("查看表中原链接", targetUrl, "review-source"));
    if (paper.error) section.append(make("p", paper.error, "review-error"));
    if (paper.notice) section.append(make("p", paper.notice, "review-mapping"));
    if (paper.fields?.length) {
      if (!selected?.length) mapping.textContent = "写入列 · " + paper.fields.map(field => field.name).join("、");
      section.append(mapping);
    }

    const pendingColSelects = [];
    if (actionable && paper.status === "ready" && Array.isArray(paper.target_columns) && paper.target_columns.length > 1 && items.length) {
      const kindOrder = Object.keys(evidenceKinds);
      const byKind = kindOrder
        .map(kind => ({ kind, indices: items.flatMap((it, i) => it.kind === kind ? [i] : []) }))
        .filter(g => g.indices.length > 0);
      if (byKind.length) {
        const colBar = make("div", undefined, "review-col-bar");
        colBar.append(make("span", "写入列", "review-col-bar-title"));
        for (const { kind, indices } of byKind) {
          const chip = make("label", undefined, "review-col-chip");
          chip.append(make("span", evidenceKinds[kind] || kind));
          const colSelect = make("select");
          colSelect.setAttribute("aria-label", (evidenceKinds[kind] || kind) + "写入列");
          const defaultFields = new Set(indices.map(i => items[i].field));
          const multiDefault = defaultFields.size > 1;
          if (multiDefault) colSelect.add(new Option("自动分列", "__auto__"));
          for (const col of paper.target_columns) colSelect.add(new Option(col, col));
          const currentFields = new Set(indices.map(i => effectiveField(i)));
          colSelect.value = currentFields.size === 1 ? [...currentFields][0] : "__auto__";
          colSelect.addEventListener("change", () => {
            const val = colSelect.value;
            for (const i of indices) {
              if (val === "__auto__" || val === items[i].field) delete paperOverrides[i];
              else paperOverrides[i] = val;
              if (itemFieldNodes[i]) itemFieldNodes[i].textContent = "写入「" + effectiveField(i) + "」";
            }
            if (selected?.length) syncGroup();
            updateCount();
          });
          chip.append(colSelect);
          colBar.append(chip);
          pendingColSelects.push(colSelect);
        }
        section.append(colBar);
      }
    }

    items.forEach((item, itemIndex) => {
      let checkbox;
      if (selected?.length) {
        checkbox = make("input", undefined, "review-check");
        checkbox.type = "checkbox";
        checkbox.checked = selected[itemIndex];
        checkbox.disabled = !actionable;
        checkbox.setAttribute("aria-label", "回填" + (evidenceKinds[item.kind] || "摘录") + "：" + item.label);
        checkbox.addEventListener("change", () => {
          selected[itemIndex] = checkbox.checked;
          syncGroup();
          updateCount();
        });
        inputs.push(checkbox);
      }
      const pageEndpoint = actionable ? "/api/evidence-page/" + preview.id + "/" + index + "/" + itemIndex + ".png" : null;
      const card = reviewItem(item, paperUrl, checkbox, effectiveField(itemIndex), pageEndpoint);
      itemFieldNodes[itemIndex] = card.querySelector(".review-item-field");
      section.append(card);
    });
    container.append(section);
    for (const colSelect of pendingColSelects) {
      reviewColSelects.push(createCustomSelect(colSelect, {
        variant: "pill", placement: "bottom", minWidth: 136, maxHeight: 200
      }));
    }
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
    tabRefs.forEach((btn, i) => {
      btn.setAttribute("aria-selected", String(i === index));
      if (i === index) btn.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    const items = preview.papers[index].items || [];
    kindSelect.replaceChildren(new Option("全部（" + items.length + "）", ""));
    for (const [kind, name] of Object.entries(evidenceKinds)) {
      const count = items.filter(item => item.kind === kind).length;
      if (count) kindSelect.add(new Option(name + "（" + count + "）", kind));
    }
    kindSelect.onchange();
    kindSelect._cselect?.sync(false);
  };
  paperSelect.onchange();
  paperSelect._cselect?.sync(false);
  dialog.querySelector("footer").hidden = !actionable;
  updateCount();
  dialog.showModal();
  byId("review-title").focus();
  container.scrollTop = 0;
}

function appendEvidenceResults(article, results) {
  const list = make("ul", undefined, "review-results");
  const statuses = { verified: "已核验", excluded: "未选中", skipped: "已跳过", failed: "失败", unverified: "待核对" };
  for (const result of results) {
    const row = make("li");
    row.dataset.status = result.status;
    const badge = make("span", undefined, "review-res-badge");
    badge.innerHTML = result.status === "verified" ? ICONS.checkSm
      : result.status === "failed" ? ICONS.statusFail : ICONS.statusSkip;
    badge.append(make("span", statuses[result.status] || "待核验"));
    row.append(badge, make("span", result.title || result.url));
    if (result.message) row.append(make("small", result.message));
    list.append(row);
  }
  article.append(list);
}

function comparisonTable(comparison) {
  const region = make("div", undefined, "saved-comparison");
  region.tabIndex = 0;
  region.setAttribute("role", "region");
  region.setAttribute("aria-label", comparison.papers.length + " 篇收藏的逐项对照");
  const table = document.createElement("table");
  if (comparison.papers.length > 2) table.style.minWidth = (100 + comparison.papers.length * 175) + "px";
  const header = table.createTHead().insertRow();
  const heading = text => {
    const cell = make("th", text);
    cell.scope = "col";
    header.append(cell);
    return cell;
  };
  heading("比较项");
  for (const paper of comparison.papers) {
    const cell = heading("");
    const url = safeLink(paper.url);
    if (url) cell.append(link(paper.title || paper.url, url));
    else cell.textContent = paper.title;
  }
  const body = table.createTBody();
  for (const field of comparison.fields) {
    const row = body.insertRow();
    const label = make("th", field.label);
    label.scope = "row";
    row.append(label);
    for (const values of field.values) {
      const col = row.insertCell();
      if (!values.length) col.textContent = "未保存";
      for (const item of values) {
        if (item.field !== field.label) col.append(make("small", item.field));
        const content = make("div");
        PaperRichText.render(content, item.value);
        for (const anchor of content.querySelectorAll("a[href]")) {
          if (anchor.textContent !== anchor.getAttribute("href")) continue;
          anchor.title = anchor.href;
          anchor.textContent = /\.(?:png|jpe?g|svg|webp)(?:$|[?#])/i.test(anchor.href) ? "打开原图" : "打开原文";
        }
        col.append(content);
      }
    }
  }
  region.append(table);
  return region;
}

function openComparison(comparison) {
  byId("comparison-title").textContent = comparison.table_name + " · " + comparison.papers.length + " 篇论文对照";
  byId("comparison-body").replaceChildren(comparisonTable(comparison));
  const copy = byId("copy-comparison");
  copy.onclick = () => copyWithFeedback(copy, comparison.markdown, "复制 Markdown");
  if (!byId("comparison-detail").open) byId("comparison-detail").showModal();
}

function appendSavedComparison(article, comparison) {
  const region = comparisonTable(comparison);
  const expand = make("button", "展开对照", "paper-detail-pill expand-comparison");
  expand.type = "button";
  expand.addEventListener("click", () => openComparison(comparison));
  article.append(region, expand);
}

function appendCollection(article, cards) {
  const list = make("div", undefined, "collection-list");
  const records = new Map();
  for (const card of cards) {
    // 同一条记录只显示一次，保留本批较晚回读的完整来源。
    records.set(card.record_id || card.url, card);
  }
  for (const card of records.values()) {
    const block = make("section", undefined, "collection-card");
    block.append(make("strong", card.title));
    if (card.error) {
      block.append(make("small", card.error));
    } else {
      const authors = card.authors.slice(0, 3).join("、") + (card.authors.length > 3 ? " 等" : "");
      block.append(make("small", [authors, card.year, card.reading_status, card.version].filter(Boolean).join(" · ")));
      if (card.notice) block.append(make("small", card.notice));
      if (card.saved_summary) block.append(make("small", card.saved_summary));
      const links = make("div", undefined, "collection-links");
      for (const raw of card.sources) {
        const address = safeLink(raw);
        if (address) links.append(iconLink(shortHost(address) || "来源", address));
      }
      if (card.fulltext_url && safeLink(card.fulltext_url)) {
        links.append(iconLink("打开全文", safeLink(card.fulltext_url)));
      }
      block.append(links);
    }
    list.append(block);
  }
  article.append(list);
}

function appendChoices(article, labels, taskId) {
  const choices = make("div", undefined, "choices");
  labels.forEach((choice, idx) => {
    const isCancel = /^(取消|算了|不用了)$/.test(choice);
    const button = make("button", choice, isCancel ? "choice-cancel" : "");
    button.type = "button";
    button.style.animationDelay = Math.min(idx * 45, 200) + "ms";
    button.dataset.choice = choice;
    button.dataset.task = taskId;
    button.addEventListener("click", () => {
      button.classList.add("choice-picked");
      submit(choice);
    });
    choices.append(button);
  });
  article.append(choices);
}

async function submit(text) {
  const instruction = text.trim();
  if (!instruction || busy || switchingModel || refreshingState || !ready) return;
  const task = pendingId;
  const confirm = /^确认(?:回填|写入|提交)(?:\s+[0-9a-f]{32})?[。！!]?$/.test(instruction);
  const selection = confirm && reviewSelections.has(task)
    ? reviewSelections.get(task).map(items => items.flatMap((checked, index) => checked ? [index] : []))
    : undefined;
  const rawFields = confirm && reviewFieldOverrides.has(task) ? reviewFieldOverrides.get(task) : undefined;
  const fields = rawFields && rawFields.some(map => map && Object.keys(map).length)
    ? rawFields.map(map => (map && Object.keys(map).length ? { ...map } : null))
    : undefined;
  const matchingRetry = retryableRequest?.message === instruction && retryableRequest.task === task &&
    JSON.stringify(retryableRequest.selection) === JSON.stringify(selection) &&
    JSON.stringify(retryableRequest.fields) === JSON.stringify(fields);
  const request = matchingRetry ? retryableRequest : { id: crypto.randomUUID(), message: instruction, task, selection, fields };
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
        evidence_selection: request.selection,
        ...(request.fields ? { evidence_fields: request.fields } : {}),
        ...(task ? { pending_id: task } : {})
      })
    });
    finishActivity(result.activity);
    pendingId = result.pending_id || null;

    const comparison = result.saved_comparison;
    const { article } = appendMessage(comparison ? "已从「" + comparison.table_name + "」读取 " + comparison.papers.length + " 篇收藏的对应字段。"
      : result.message || "已完成。", "assistant");
    if (comparison) appendSavedComparison(article, comparison);
    appendModelTrace(article, result.agent);
    if (result.target_changed) {
      selectedId = reviewId = null;
      resetLibraryFilter();
      reviewSelections.clear();
      reviewFieldOverrides.clear();
      document.querySelectorAll(".review-reopen, .open-saved-row").forEach(button => { button.disabled = true; });
      showEvidence([]);
      const target = feishuTableLink(result.workspace_url);
      if (target) article.append(iconLink("打开飞书数据表", target, "icon-pill"));
    }
    if (result.collection?.length) appendCollection(article, result.collection);

    if (result.evidence_preview?.papers?.length) {
      showReviewButton(article, result.evidence_preview);
    } else if (pendingId && Array.isArray(result.choices) && result.choices.length) {
      appendChoices(article, result.choices, pendingId);
    }
    if (result.evidence_results?.length) appendEvidenceResults(article, result.evidence_results);

    const download = safeLink(result.download_url, true);
    if (download) {
      const pdfWrap = make("div", undefined, "pdf-actions");
      pdfWrap.append(iconLink("打开 PDF", download, "pdf-pill"));
      const savePdf = iconLink("下载 PDF", download, "pdf-pill", ICONS.download);
      savePdf.removeAttribute("target");
      savePdf.download = "";
      pdfWrap.append(savePdf);
      article.append(pdfWrap);
    }

    if (result.saved_record_id) {
      selectedId = result.saved_record_id;
      const open = make("button", "查看表中记录", "paper-detail-pill open-saved-row");
      open.type = "button";
      open.onclick = async () => {
        if (open.getAttribute("aria-busy") === "true") return;
        open.setAttribute("aria-busy", "true");
        try {
          await refreshState();
          if (!ready || open.disabled) return;
          const row = state.rows.find(item => item.id === result.saved_record_id);
          if (row) showDetail(row);
          else showToast("当前表已找不到这条记录，请重新查询。", "error");
        } finally {
          open.removeAttribute("aria-busy");
        }
      };
      article.append(open);
    }
    if (result.row) selectedId = result.row.id;
    const incomplete = result.evidence_results?.some(item => !["verified", "excluded"].includes(item.status));

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
    else article.append(make("p", "请求失败", "model-trace"));
    showToast(err.message, "error");
  } finally {
    busy = false;
    controls();
    byId("chat").scrollTop = byId("chat").scrollHeight;
  }
}

function renderPdfTray(selected = byId("pdf-select").value) {
  const items = state.attachments || [];
  byId("pdf-tray").hidden = !items.length;
  if (!items.length) return;
  const select = byId("pdf-select");
  const unchanged = select.dataset.active === selected && items.some(item => item.id === selected);
  select.replaceChildren(...items.map(item => new Option(item.filename +
    (item.paper_url ? " · 已关联" : " · 待关联"), item.id)));
  const item = items.find(entry => entry.id === selected) || items[0];
  select.value = select.dataset.active = item.id;
  byId("pdf-count").textContent = "PDF 附件 · " + items.length;
  byId("pdf-open").href = item.viewer;
  byId("pdf-associate").hidden = Boolean(item.paper_url);
  byId("pdf-bound").hidden = !item.paper_url;
  byId("pdf-paper").textContent = item.title;
  const target = byId("pdf-target");
  const previous = unchanged ? target.value : "";
  const {titleField, urlField} = detectPrimaryFields(state.fields || []);
  target.replaceChildren(new Option("手动输入链接或 DOI", ""));
  for (const row of state.rows || []) {
    const url = cellValue(row.fields[urlField?.name]);
    if (url && /^https:\/\//i.test(url)) {
      const option = new Option(cellValue(row.fields[titleField?.name]) || url, row.id);
      option.dataset.url = url;
      target.add(option);
    }
  }
  target.value = previous;
  byId("pdf-manual").hidden = Boolean(target.value);
  byId("pdf-identifiers").replaceChildren(...item.candidates.map(value => new Option(value, value)));
  if (!unchanged) {
    byId("pdf-ref").value = item.candidates.length === 1 ? item.candidates[0] : "";
    byId("pdf-title").value = item.title;
  }
  byId("pdf-status").textContent = item.paper_url ? "" : item.candidates.length
    ? "已识别候选链接，请核对后关联。"
    : "已暂存 PDF，请补充论文链接或从清单关联。";
  select._cselect?.sync(false);
  target._cselect?.sync(false);
}

async function receivePdfs(files) {
  if (busy || uploadingPdf || !ready || !files.length) return;
  uploadingPdf = true;
  controls();
  const pdfAddLabel = byId("pdf-add").querySelector(".pdf-add-label") || byId("pdf-add");
  pdfAddLabel.textContent = "解析中…";
  try {
    for (const file of files) {
      try {
        if (!/\.pdf$/i.test(file.name) || file.size > 50 * 1024 * 1024) {
          throw new Error("请选择 50 MB 以内的 PDF 文件。");
        }
        const item = await api("/api/attachments", {method: "POST", body: file,
          headers: {"Content-Type": "application/pdf", "X-Pdf-Name": encodeURIComponent(file.name)}});
        state.attachments = [item, ...(state.attachments || []).filter(other => other.id !== item.id)];
        renderPdfTray(item.id);
        byId("pdf-tray").open = true;
      } catch (error) {
        showToast(file.name + "：" + error.message, "error");
      }
    }
  } finally {
    uploadingPdf = false;
    pdfAddLabel.textContent = "添加 PDF";
    byId("pdf-files").value = "";
    controls();
  }
}

byId("pdf-add").addEventListener("click", () => byId("pdf-files").click());
byId("pdf-files").addEventListener("change", event => receivePdfs(Array.from(event.target.files)));
byId("pdf-select").addEventListener("change", event => renderPdfTray(event.target.value));
byId("pdf-target").addEventListener("change", event => { byId("pdf-manual").hidden = Boolean(event.target.value); });
const composer = byId("composer");
composer.addEventListener("dragover", event => {
  if (event.dataTransfer.types.includes("Files")) {
    event.preventDefault();
    composer.classList.add("pdf-drop");
  }
});
composer.addEventListener("dragleave", event => {
  if (!composer.contains(event.relatedTarget)) composer.classList.remove("pdf-drop");
});
composer.addEventListener("drop", event => {
  if (!event.dataTransfer.files.length) return;
  event.preventDefault();
  composer.classList.remove("pdf-drop");
  receivePdfs(Array.from(event.dataTransfer.files));
});
byId("pdf-bind").addEventListener("click", async () => {
  if (uploadingPdf || busy) return;
  const id = byId("pdf-select").value;
  const target = byId("pdf-target").value;
  const body = target ? {record_id: target, expected_url: byId("pdf-target").selectedOptions[0].dataset.url}
    : {paper_ref: byId("pdf-ref").value.trim(), title: byId("pdf-title").value.trim()};
  if (!target && !body.paper_ref) {
    byId("pdf-ref").focus();
    return;
  }
  uploadingPdf = true;
  controls();
  byId("pdf-status").textContent = "关联中…";
  try {
    const item = await api("/api/attachments/" + id + "/bind", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    state.attachments = state.attachments.map(other => other.id === item.id ? item : other);
    renderPdfTray(item.id);
    showToast("已关联，可提取数据");
  } catch (error) {
    byId("pdf-status").textContent = error.message;
  } finally {
    uploadingPdf = false;
    controls();
  }
});
for (const [id, instruction] of [["pdf-save", "把这篇论文收藏到表中 "], ["pdf-extract", "提取这篇论文的关键数据和图表 "]]) {
  byId(id).addEventListener("click", () => {
    const item = state.attachments?.find(entry => entry.id === byId("pdf-select").value);
    if (item?.paper_url) submit(instruction + item.paper_url);
  });
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
    schema: "新建文本列 审阅意见",
    table: "在当前多维表格里新建数据表「论文精读」",
    base: "新建多维表格「论文资料库」，首张数据表叫「论文清单」"
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
byId("reading-filter").addEventListener("change", renderLibraryRows);
byId("library-query").addEventListener("input", renderLibraryRows);
byId("reset-library-filter").addEventListener("click", () => {
  resetLibraryFilter();
  renderLibraryRows();
  byId("library-query").focus();
});
function recordsRequest(action, body, viewId = state.table_view_id) {
  return api("/api/records/" + action, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, table_view_id: viewId }) });
}

byId("compare-mode").addEventListener("click", () => {
  choosingComparison = !choosingComparison;
  if (!choosingComparison) comparedRecords.clear();
  renderLibraryRows();
});
byId("compare-selected").addEventListener("click", async () => {
  const button = byId("compare-selected");
  button.disabled = true;
  const viewId = state.table_view_id;
  try {
    const data = await recordsRequest("compare", {records: [...comparedRecords.values()]}, viewId);
    if (state.table_view_id === viewId && choosingComparison) openComparison(data);
  } catch (err) { showToast(err.message, "error"); await refreshState(); }
  finally { renderSelection(); }
});

byId("export-format").addEventListener("change", async event => {
  const format = event.target.value;
  event.target.value = "";
  if (!format) return;
  const viewId = state.table_view_id;
  const filename = (state.table_name || "阅读清单").replace(/[<>:"/\\|?*]/g, "_");
  try {
    let data;
    if (format === "json") {
      const snapshot = await api("/api/state");
      if (snapshot.table_view_id !== viewId || !snapshot.reading_progress) throw new Error("请在当前本地清单重新导出。");
      const rows = snapshot.rows.map(row => ({...row, reading_progress: snapshot.reading_progress[row.id] || "待读"}));
      const file = { table: snapshot.table_name, exported_at: new Date().toISOString(), fields: snapshot.fields, rows };
      data = {content: JSON.stringify(file, null, 2), extension: "json", mime: "application/json", count: rows.length};
    } else {
      data = await recordsRequest("export", {format}, viewId);
    }
    const url = URL.createObjectURL(new Blob([data.content], {type: data.mime}));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename + "." + data.extension.replace(/^\./, "");
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    showToast(data.notice || "已导出 " + data.count + " 条记录");
  } catch (err) { showToast(err.message, "error"); }
});
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
byId("close-comparison").addEventListener("click", () => byId("comparison-detail").close());
byId("comparison-detail").addEventListener("click", event => {
  if (event.target === byId("comparison-detail")) byId("comparison-detail").close();
});
byId("detail").addEventListener("click", event => {
  if (event.target === byId("detail")) byId("detail").close();
});

animateDetailsToggle(byId("pdf-tray"), ".pdf-tray-body");
createCustomSelect(byId("model-select"), { variant: "pill", placement: "top", minWidth: 216, maxHeight: 248 });
createCustomSelect(byId("pdf-select"), { variant: "field", placement: "top", matchTriggerWidth: true, fullWidth: true });
createCustomSelect(byId("pdf-target"), { variant: "field", placement: "top", matchTriggerWidth: true, fullWidth: true });
createCustomSelect(byId("review-paper-select"), { variant: "pill", placement: "bottom", matchTriggerWidth: true, fullWidth: true });
createCustomSelect(byId("review-kind"), { variant: "pill", placement: "bottom", matchTriggerWidth: true, fullWidth: true });

controls();
selectView("cards", false);
refreshState(false);
