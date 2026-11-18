"use strict";

const byId = id => document.getElementById(id);
const make = (tag, value) => {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  return element;
};
let state = { fields: [], rows: [], sample: {} };
let busy = false;
let ready = false;
let pendingId = null;
let selectedId = null;
let latestStateRequest = 0;
let retryableRequest = null;

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

function link(label, url) {
  const a = make("a", label);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  return a;
}

function controls() {
  byId("send").disabled = busy || !ready || !byId("prompt").value.trim();
  byId("prompt").disabled = busy || !ready;
  byId("refresh").disabled = busy;
  document.querySelectorAll("[data-example]").forEach(btn => {
    btn.disabled = busy || !ready || Boolean(pendingId);
  });
  document.querySelectorAll("[data-choice]").forEach(btn => {
    btn.disabled = busy || !ready || btn.dataset.task !== pendingId;
  });
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

function showDetail(row) {
  const list = byId("detail-content");
  list.replaceChildren(make("dt", "记录 ID"), make("dd", row.id));
  for (const field of state.fields) {
    const desc = make("dd");
    renderValue(desc, field, row.fields[field.name]);
    list.append(make("dt", field.name), desc);
  }
  byId("detail").showModal();
}

function renderState() {
  const mode = byId("mode");
  mode.className = "mode connected";
  mode.lastElementChild.textContent = state.mode === "feishu" ? "飞书多维表格" : "本地演示 · SQLite";
  byId("records-heading").textContent = state.table_name || "阅读清单";
  const populatedRows = state.rows.filter(row => Object.values(row.fields || {}).some(hasCellValue));
  const blankCount = state.rows.length - populatedRows.length;
  byId("row-count").textContent = blankCount
    ? populatedRows.length + " 条有内容记录 · " + blankCount + " 条空白行"
    : state.rows.length + " 条记录";
  byId("field-count").textContent = state.fields.length + " 个字段";
  byId("sample").hidden = !state.sample.title;
  byId("sample-title").textContent = state.sample.title || "";
  const download = safeLink(state.download_url, true);
  byId("sample-pdf").hidden = !download;
  if (download) byId("sample-pdf").href = download;
  else byId("sample-pdf").removeAttribute("href");
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
    const btn = make("button", String(index + 1));
    btn.type = "button";
    btn.className = "row-detail";
    btn.title = "展开记录详情";
    btn.setAttribute("aria-label", "查看第 " + (index + 1) + " 条记录");
    btn.addEventListener("click", () => showDetail(row));
    tr.insertCell().append(btn);
    for (const field of state.fields) renderValue(tr.insertCell(), field, row.fields[field.name]);
  });
  byId("empty-sheet").hidden = Boolean(populatedRows.length);
  byId("empty-sheet").textContent = blankCount
    ? "当前显示有内容的记录。存入论文后，结果会出现在这里。"
    : "暂无记录。存入论文后，结果会出现在这里。";
}

async function refreshState() {
  const token = ++latestStateRequest;
  try {
    const snapshot = await api("/api/state");
    if (token !== latestStateRequest) return;
    state = { ...snapshot, sample: snapshot.sample || {}, fields: snapshot.fields || [], rows: snapshot.rows || [] };
    ready = true;
    renderState();
  } catch (err) {
    if (token !== latestStateRequest) return;
    ready = false;
    byId("mode").className = "mode error";
    byId("mode").lastElementChild.textContent = "连接失败";
    byId("empty-sheet").hidden = false;
    byId("empty-sheet").textContent = "刷新失败：" + err.message + " 请检查服务后点击刷新。";
    if (!state.rows.length) byId("row-count").textContent = "未取得记录";
  } finally {
    controls();
  }
}

function message(author, content, kind) {
  const article = make("article");
  article.className = "message " + (kind || "");
  const speaker = make("span", author);
  speaker.className = "speaker";
  article.append(speaker, make("p", content));
  byId("chat").append(article);
  byId("chat").scrollTop = byId("chat").scrollHeight;
  return article;
}

function showEvidence(items) {
  const container = byId("evidence");
  container.replaceChildren();
  if (!items.length) {
    const empty = make("p", "此次写入未引用论文摘录。");
    empty.className = "muted";
    container.append(empty);
    return;
  }
  items.forEach(item => {
    const block = make("div");
    block.className = "evidence-item";
    if (item.field) block.append(make("strong", item.field));
    if (item.quote) block.append(make("blockquote", item.quote));
    if (item.source) {
      const source = make("small");
      const sourceUrl = item.url || item.source;
      const address = /^https?:\/\//i.test(sourceUrl) ? safeLink(sourceUrl) : null;
      if (address) source.append(link(item.source, address));
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
  controls();
  message("你", instruction, "user");
  const progress = message("助手", "正在处理…");
  try {
    const result = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: instruction, request_id: request.id, ...(task ? { pending_id: task } : {}) })
    });
    progress.remove();
    pendingId = result.pending_id || null;
    const reply = message("助手", result.message || "请求已处理。");
    if (pendingId && Array.isArray(result.choices)) {
      const choices = make("div");
      choices.className = "choices";
      result.choices.forEach(choice => {
        const button = make("button", choice);
        button.type = "button";
        button.dataset.choice = choice;
        button.dataset.task = pendingId;
        button.addEventListener("click", () => submit(choice));
        choices.append(button);
      });
      reply.append(choices);
    }
    const download = safeLink(result.download_url, true);
    if (download) {
      reply.append(link("查看论文 PDF ↗", download));
      const savePdf = link("下载论文 PDF ↓", download);
      savePdf.removeAttribute("target");
      savePdf.download = "";
      reply.append(savePdf);
    }
    if (result.row) selectedId = result.row.id;
    if (result.verification) {
      byId("verification").className = "verify-status report";
      byId("verification").textContent = result.verification;
    }
    if (result.row || result.evidence?.length) showEvidence(Array.isArray(result.evidence) ? result.evidence : []);
    await refreshState();
  } catch (err) {
    progress.remove();
    if (err.networkFailure) {
      retryableRequest = request;
      byId("prompt").value = instruction;
    }
    message("助手", err.message, "error");
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
byId("prompt").addEventListener("input", controls);
byId("prompt").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault();
    submit(byId("prompt").value);
  }
});
document.querySelectorAll("[data-example]").forEach(button => {
  button.addEventListener("click", () => {
    const examples = {
      save: "把 " + (state.sample.url || "https://arxiv.org/abs/2005.11401") + " 存进" + (state.table_name || "阅读清单"),
      method: "给刚才那篇补一句方法概述",
      notes: "把刚才那篇的方法写进笔记"
    };
    submit(examples[button.dataset.example]);
  });
});
byId("refresh").addEventListener("click", refreshState);
byId("close-detail").addEventListener("click", () => byId("detail").close());
controls();
refreshState();
