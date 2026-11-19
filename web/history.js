"use strict";

function createWorkspaceHistory() {
  const panel = byId("history-dialog");
  const tabs = panel.querySelectorAll("[data-history-tab]");
  const welcome = byId("welcome").cloneNode(true);
  const statuses = {
    verified: "回读一致", partial: "部分完成", failed: "失败", unverified: "待核验",
    cancelled: "已取消", preview: "仅预览", unchanged: "无改动"
  };
  const dateFormat = new Intl.DateTimeFormat("zh-CN", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
  });
  let data = null;
  let changing = false;
  let revision = 0;
  let decision = null;
  let tabId = "chats";
  let messageCursor = null;
  let operationCursor = null;
  let extendedOperations = false;
  let renderedTail = "";
  const loadingPage = {messages: false, operations: false};

  function dateLabel(value) {
    if (!value) return "";
    const time = new Date(value);
    return Number.isFinite(time.getTime()) ? dateFormat.format(time) : "";
  }

  function actionButton(text, handler, label = "") {
    const element = make("button", text, "paper-detail-pill");
    element.type = "button";
    if (label) element.setAttribute("aria-label", label);
    element.addEventListener("click", handler);
    return element;
  }

  function setError(message = "") {
    const error = byId("history-error");
    error.textContent = message;
    error.hidden = !message;
  }

  function cancelDecision() {
    decision = null;
    byId("history-decision").hidden = true;
  }

  function confirmRemoval(message, action, payload = {}) {
    decision = { action, payload };
    byId("history-decision-text").textContent = message;
    byId("history-decision").hidden = false;
    byId("history-back").focus();
  }

  function showTab(id) {
    cancelDecision();
    tabId = id;
    for (const tab of tabs) tab.setAttribute("aria-pressed", String(tab.dataset.historyTab === id));
    for (const pane of panel.querySelectorAll("[data-history-section]")) pane.hidden = pane.dataset.historySection !== id;
    byId("history-body").scrollTop = 0;
    renderPanel();
  }

  function showContext() {
    const hasScope = Boolean(state.history_scope);
    byId("history-open").hidden = !hasScope;
    byId("context-strip").hidden = !hasScope;
    byId("context-table").textContent = "当前表 · " + (state.table_name || "未选择");
    byId("history-table-name").textContent = [state.base_name, state.table_name].filter(Boolean).join(" / ");
    const title = data?.scope === state.history_scope && data.active_id === state.history_conversation_id && data.focus?.title;
    const focus = byId("context-focus");
    focus.textContent = title ? "正在读 · " + title : "";
    focus.title = title || "";
    focus.hidden = byId("context-forget").hidden = !title;
  }

  function renderChats() {
    const list = byId("history-chats");
    list.replaceChildren();
    for (const item of data.conversations || []) {
      const row = make("div", undefined, "history-row");
      const active = item.id === data.active_id;
      const title = item.title || "新对话";
      const button = make("button", undefined, "history-chat");
      button.type = "button";
      if (active) button.setAttribute("aria-current", "true");
      button.append(make("strong", title), make("small", [active ? "当前对话" : "", dateLabel(item.updated)].filter(Boolean).join(" · ")));
      button.onclick = () => active ? panel.close() : change("open", { target_id: item.id });
      row.append(button, actionButton("删除", () => confirmRemoval("删除“" + title + "”及其中的消息？", "delete",
        { target_id: item.id }), "删除对话 " + title));
      list.append(row);
    }
    if (!list.childElementCount) list.append(make("p", "本表还没有对话。", "history-empty"));
  }

  function renderOperations() {
    const list = byId("history-operations");
    const expanded = new Set(Array.from(list.querySelectorAll("details[open]"), el => el.dataset.id));
    list.replaceChildren();
    for (const item of data.operations || []) {
      const details = make("details", undefined, "history-operation");
      details.dataset.id = item.id;
      details.open = expanded.has(item.id);
      const summary = make("summary");
      const heading = make("span", undefined, "history-operation-title");
      heading.append(make("strong", item.summary || "操作记录"), make("small", dateLabel(item.created)));
      const status = make("span", statuses[item.status] || item.status || "已记录", "history-status");
      status.dataset.status = item.status || "";
      summary.append(heading, status);
      const fields = make("dl");
      for (const field of item.details || []) {
        const label = make("dt", field.label || "详情");
        const value = make("dd", field.value || "");
        if (/^https?:\/\//i.test(field.url || "") && safeLink(field.url)) {
          value.append(link("打开链接", safeLink(field.url), "history-source"));
        }
        if (field.record_id) {
          const scope = data.scope;
          value.append(actionButton("查看当前记录", async () => {
            await refreshState();
            if (!ready || state.history_scope !== scope) return setError("数据表已切换，请重新选择操作记录。");
            const row = state.rows.find(r => r.id === field.record_id);
            if (!row) return setError("这条记录已删除或当前身份无法读取。");
            panel.close();
            showDetail(row);
          }));
        }
        fields.append(label, value);
      }
      details.append(summary, fields.childElementCount ? fields : make("p", "该操作没有额外明细。", "history-empty"));
      list.append(details);
    }
    if (!list.childElementCount) list.append(make("p", "本表还没有操作记录。", "history-empty"));
  }

  function renderSettings() {
    for (const input of panel.querySelectorAll("[data-setting]")) {
      input.checked = data.settings?.[input.dataset.setting] === true;
    }
    const identity = new Set([...(state.identity_fields?.titles || []), ...(state.identity_fields?.links || [])]);
    const options = document.createDocumentFragment();
    const columns = state.fields.filter(f => f.type === "text" && !identity.has(f.name));
    for (const kind of ["代码链接", "关键数据", "图题", "实验表格", "公式", "模型链接", "数据集链接"]) {
      for (const column of columns) options.append(new Option(kind + "优先写入「" + column.name + "」"));
    }
    byId("preference-options").replaceChildren(options);
    const list = byId("history-preferences");
    list.replaceChildren();
    for (const item of data.preferences || []) {
      const row = make("div", undefined, "history-row");
      row.append(make("span", item.text, "history-pref-text"),
        actionButton("删除", () => change("delete_preference", { target_id: item.id }), "删除偏好 " + item.text));
      list.append(row);
    }
    if (!list.childElementCount) list.append(make("p", "本表还没有已保存的偏好。", "history-empty"));
  }

  function renderPanel() {
    if (!panel.open || !data) return;
    if (tabId === "chats") renderChats();
    else if (tabId === "operations") renderOperations();
    else renderSettings();
    syncControls();
  }

  function archivedMessages(messages) {
    const fragment = document.createDocumentFragment();
    for (const message of messages) {
      if (!["user", "assistant", "error"].includes(message.role) || !message.text) continue;
      // 历史气泡仅还原文字；只有服务端当前待确认计划能够产生确认按钮。
      const {article} = createMessage(message.text, message.role);
      article.dataset.archived = "true";
      article.dataset.created = message.created || "";
      fragment.append(article);
    }
    return fragment;
  }

  function restoreMessages(messages, changed) {
    resetConversationSurface(changed);
    const chat = byId("chat");
    const fragment = archivedMessages(messages);
    if (!fragment.childElementCount) fragment.append(welcome.cloneNode(true));
    chat.replaceChildren(fragment);
    chat.scrollTop = chat.scrollHeight;
  }

  function updateTranscript(snapshot) {
    // 手动刷新同时取回同一浏览器其他标签的新增消息，保留此前翻过的旧页。
    const messages = snapshot.messages || [];
    const chat = byId("chat");
    const first = messages[0]?.created;
    const older = first ? Array.from(chat.querySelectorAll("[data-archived]")).filter(el => el.dataset.created < first) : [];
    const overlaps = messages.some(m => m.created === renderedTail);
    chat.replaceChildren(...older, archivedMessages(messages));
    if (!chat.childElementCount) chat.append(welcome.cloneNode(true));
    chat.scrollTop = chat.scrollHeight;
    if (!overlaps) messageCursor = snapshot.more_messages ?? null;
    pendingId = null;
  }

  function accept(snapshot, force = false, manual = false) {
    const changed = data?.active_id !== snapshot.active_id || data?.scope !== snapshot.scope;
    const tail = snapshot.messages?.at(-1)?.created || "";
    if (changed || force) {
      messageCursor = snapshot.more_messages ?? null;
      extendedOperations = false;
      restoreMessages(snapshot.messages || [], Boolean(data));
      byId("preference-input").value = "";
      cancelDecision();
      renderedTail = tail;
    } else if (manual && snapshot.settings?.save_history && renderedTail !== tail) {
      updateTranscript(snapshot);
      renderedTail = tail;
    }
    if (extendedOperations) {
      const ids = new Set(snapshot.operations.map(item => item.id));
      snapshot.operations = snapshot.operations.concat(data.operations.filter(item => !ids.has(item.id)));
    } else operationCursor = snapshot.more_operations ?? null;
    data = snapshot;
    showContext();
    renderPanel();
    syncControls();
  }

  async function earlier(kind) {
    const before = kind === "messages" ? messageCursor : operationCursor;
    if (!data || before == null || loadingPage[kind] || changing) return;
    const {scope, active_id} = data;
    const query = new URLSearchParams({scope, conversation_id: active_id, before: String(before), kind});
    loadingPage[kind] = true;
    syncControls();
    try {
      const result = await api("/api/workspace-history/page?" + query);
      if (data?.scope !== scope || data.active_id !== active_id || !Array.isArray(result.items)) return;
      if (kind === "messages" && messageCursor === before) {
        const chat = byId("chat");
        const height = chat.scrollHeight;
        const top = chat.scrollTop;
        const current = Array.from(chat.querySelectorAll("[data-archived]"));
        const seen = new Set(current.map(el => el.dataset.created + "/" + (el.classList.contains("user") ? "user" : "assistant")));
        const messages = result.items.filter(m => !seen.has(m.created + "/" + m.role));
        // 手动刷新可能跨越多页。按存储时间把补读的消息放回对应位置。
        const fragment = archivedMessages(messages);
        if (fragment.childElementCount) {
          const sorted = [...current, ...fragment.children].sort((a, b) =>
            a.dataset.created.localeCompare(b.dataset.created) || Number(b.classList.contains("user")) - Number(a.classList.contains("user")));
          const archive = document.createDocumentFragment();
          for (const el of sorted) archive.append(el);
          chat.prepend(archive);
        }
        chat.scrollTop = top + chat.scrollHeight - height;
        messageCursor = result.before ?? null;
      } else if (kind === "operations" && operationCursor === before) {
        const ids = new Set(data.operations.map(o => o.id));
        data.operations.push(...result.items.filter(o => !ids.has(o.id)));
        extendedOperations = true;
        operationCursor = result.before ?? null;
        renderPanel();
      }
    } catch (err) {
      if (kind === "messages") showToast(err.message, "error");
      else setError(err.message);
    } finally {
      loadingPage[kind] = false;
      syncControls();
    }
  }

  async function sync(snapshot, current = () => true, {manual = false, force = false} = {}) {
    const request = ++revision;
    if (!snapshot.history_scope || !snapshot.history_conversation_id) {
      if (data) restoreMessages([], true);
      data = null;
      showContext();
      return true;
    }
    if (!manual && !force && !panel.open && data?.scope === snapshot.history_scope &&
        data.active_id === snapshot.history_conversation_id && Object.hasOwn(snapshot, "history_focus")) {
      data.focus = snapshot.history_focus;
      showContext();
      return true;
    }
    try {
      const result = await api("/api/workspace-history");
      if (request !== revision || !current()) return true;
      if (result.scope !== snapshot.history_scope || result.active_id !== snapshot.history_conversation_id) return false;
      accept(result, false, manual);
      setError();
      return true;
    } catch (err) {
      if (request !== revision || !current()) return true;
      if (data?.scope !== snapshot.history_scope || data?.active_id !== snapshot.history_conversation_id) {
        if (data) restoreMessages([], true);
        data = null;
      }
      showContext();
      setError(err.message);
      throw err;
    }
  }

  function syncControls() {
    const locked = changing || busy || switchingModel || refreshingState || uploadingPdf || !ready;
    byId("history-open").disabled = byId("context-forget").disabled = locked;
    for (const el of panel.querySelectorAll("#history-body button, #history-body input, #history-accept")) el.disabled = locked || !data;
    byId("preference-save").disabled ||= !byId("preference-input").value.trim();
    byId("history-earlier-messages").hidden = !data || messageCursor == null;
    byId("history-earlier-messages").disabled = locked || loadingPage.messages;
    byId("history-earlier-operations").hidden = !data || operationCursor == null;
    byId("history-earlier-operations").disabled = locked || loadingPage.operations;
  }

  async function change(action, payload = {}) {
    if (changing || busy || refreshingState || !data) return;
    const previous = data;
    ++revision;
    changing = true;
    cancelDecision();
    controls();
    setError();
    try {
      const result = await api("/api/workspace-history", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({action, scope: previous.scope, conversation_id: previous.active_id, ...payload})
      });
      if (result.scope !== previous.scope) throw new Error("数据表已切换，请重新打开历史。");
      state.history_conversation_id = result.active_id;
      const changed = previous.active_id !== result.active_id || action === "clear_history";
      accept(result, action === "clear_history");
      if (action === "add_preference") byId("preference-input").value = "";
      if (action === "open" || action === "new") panel.close();
      if (changed) {
        await refreshState();
        byId("prompt").focus();
      }
    } catch (err) {
      if (data) renderPanel();
      await refreshState();
      if (panel.open) setError(err.message);
      else showToast(err.message, "error");
    } finally {
      changing = false;
      controls();
    }
  }

  byId("history-open").onclick = async () => {
    if (!panel.open) panel.showModal();
    renderPanel();
    try {
      if (!await sync(state)) await refreshState();
    } catch (err) { setError(err.message); }
  };
  byId("history-close").onclick = () => panel.close();
  panel.addEventListener("close", cancelDecision);
  panel.addEventListener("click", event => { if (event.target === panel) panel.close(); });
  for (const tab of tabs) tab.onclick = () => showTab(tab.dataset.historyTab);
  byId("history-back").onclick = cancelDecision;
  byId("history-earlier-messages").onclick = () => earlier("messages");
  byId("history-earlier-operations").onclick = () => earlier("operations");
  byId("history-accept").onclick = () => { if (decision) change(decision.action, decision.payload); };
  byId("history-new").onclick = () => change("new");
  byId("history-clear").onclick = () => confirmRemoval("清空当前表的全部对话和消息？操作记录仍可查阅。", "clear_history");
  byId("context-forget").onclick = () => change("clear_focus");
  for (const el of panel.querySelectorAll("[data-setting]")) {
    el.onchange = () => change("settings", { [el.dataset.setting]: el.checked });
  }
  byId("preference-input").oninput = syncControls;
  byId("preference-form").onsubmit = event => {
    event.preventDefault();
    const text = byId("preference-input").value.trim();
    if (text) change("add_preference", {text});
  };
  return { sync, syncControls, get changing() { return changing; } };
}
