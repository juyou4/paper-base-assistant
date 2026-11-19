"use strict";

function createWorkspaceHistory() {
  const panel = byId("history-dialog");
  const tabs = panel.querySelectorAll("[data-history-tab]");
  const welcome = byId("welcome").cloneNode(true);
  const statuses = {
    verified: "已核验", partial: "部分完成", failed: "失败", unverified: "待核验",
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
  const loadingPage = {messages: false, operations: false, pending_batches: false};

  function dateLabel(value) {
    if (!value) return "";
    const time = new Date(value);
    return Number.isFinite(time.getTime()) ? dateFormat.format(time) : "";
  }

  function actionButton(text, handler, label = "", extraClass = "") {
    const element = make("button", text, "paper-detail-pill" + (extraClass ? " " + extraClass : ""));
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
        { target_id: item.id }), "删除对话 " + title, "history-danger-btn"));
      list.append(row);
    }
    if (!list.childElementCount) list.append(make("p", "本表还没有对话。", "history-empty"));
  }

  function recordButton(recordId) {
    const scope = data.scope;
    const view = state.table_view_id;
    return actionButton("查看当前记录", async () => {
      try {
        const row = await currentRow(recordId, view);
        if (!ready || state.history_scope !== scope) return setError("数据表已切换，请重新选择操作记录。");
        panel.close();
        showDetail(row);
      } catch (error) { setError(error.message); }
    });
  }

  function renderOperations() {
    const list = byId("history-operations");
    const expanded = new Set(Array.from(list.querySelectorAll("details[open]"), el => el.dataset.id));
    list.replaceChildren();
    const pendingOnly = byId("history-pending-only").checked;
    for (const item of (pendingOnly ? data.pending_batches : data.operations) || []) {
      const details = make("details", undefined, "history-operation");
      details.dataset.id = item.id;
      details.open = expanded.has(item.id);
      const summary = make("summary");
      const heading = make("span", undefined, "history-operation-title");
      heading.append(make("strong", item.summary || "操作记录"), make("small", dateLabel(item.created)));
      const status = make("span", statuses[item.status] || item.status || "已记录", "history-status");
      status.dataset.status = item.status || "";
      const chev = make("span", undefined, "disclosure-chevron");
      chev.setAttribute("aria-hidden", "true");
      chev.innerHTML = ICONS.chevronDown;
      summary.append(heading, status, chev);
      const fields = make("dl");
      if (item.papers?.length) {
        const states = {queued: "尚未提交", sent: "已提交，待核对结果", verified: "已核验",
          failed: "目标校验未通过", unverified: "结果待核验", excluded: "已跳过"};
        for (const paper of item.papers) {
          const entry = make("dd", undefined, "batch-paper-status");
          const stateBadge = make("span", states[paper.state] || paper.state, "batch-state-badge");
          stateBadge.dataset.paperState = paper.state || "";
          entry.append(stateBadge);
          const noteText = paper.observed ? dateLabel(paper.checked) + " · " + paper.observed : paper.note || "";
          if (noteText) entry.append(make("small", noteText, "batch-paper-note"));
          for (const selection of paper.selected || []) {
            const evidence = make("span", selection.label + " · " + selection.value, "batch-paper-evidence");
            if (safeLink(selection.url || "")) evidence.append(iconLink("出处", safeLink(selection.url), "history-source"));
            entry.append(evidence);
          }
          if (paper.record_id) entry.append(make("small", "记录 ID · " + paper.record_id, "batch-paper-record"));
          const actions = make("div", undefined, "batch-paper-actions");
          if (paper.record_id) actions.append(recordButton(paper.record_id));
          if (paper.state !== "excluded" && paper.verdict !== "match" &&
                (paper.state !== "verified" || paper.verdict) && paper.retry) {
            actions.append(actionButton("重新预览此篇", () => {
              panel.close();
              const input = byId("prompt");
              input.value = paper.retry;
              input.dispatchEvent(new Event("input", {bubbles: true}));
              input.focus();
            }));
          }
          if (actions.childElementCount) entry.append(actions);
          fields.append(make("dt", paper.title), entry);
        }
        const recheckCell = make("dd", undefined, "batch-recheck-cell");
        recheckCell.append(actionButton("重新核验（只读）", () => change("recheck_batch", {target_id: item.id})));
        fields.append(make("dt", "当前表"), recheckCell);
      }
      for (const field of item.papers?.length ? [] : item.details || []) {
        const label = make("dt", field.label || "详情");
        const value = make("dd", field.value || "");
        if (/^https?:\/\//i.test(field.url || "") && safeLink(field.url)) {
          value.append(iconLink("打开链接", safeLink(field.url), "history-source"));
        }
        if (field.record_id) value.append(recordButton(field.record_id));
        fields.append(label, value);
      }
      details.append(summary, fields.childElementCount ? fields : make("p", "该操作没有额外明细。", "history-empty"));
      list.append(details);
    }
    if (!list.childElementCount) list.append(make("p", pendingOnly ? "本表没有待处理批次。" : "本表还没有操作记录。", "history-empty"));
  }

  const prefKindSelect = byId("pref-kind-select");
  const prefColSelect = byId("pref-col-select");
  for (const [val, label] of [
    ["", "选择数据类型"], ["关键数据", "关键数据"], ["图片", "图片"], ["表格", "表格"],
    ["公式", "公式"], ["代码链接", "代码链接"], ["模型链接", "模型链接"], ["数据集链接", "数据集链接"]
  ]) prefKindSelect.add(new Option(label, val));
  prefColSelect.add(new Option("选择写入列", ""));
  const prefKindCs = createCustomSelect(prefKindSelect, {
    variant: "pill", placement: "bottom", minWidth: 118, maxHeight: 210, fullWidth: true
  });
  const prefColCs = createCustomSelect(prefColSelect, {
    variant: "pill", placement: "bottom", minWidth: 118, maxHeight: 210, fullWidth: true
  });
  function syncPrefBuilderInput() {
    if (prefKindSelect.value && prefColSelect.value) {
      byId("preference-input").value = prefKindSelect.value + "优先写入「" + prefColSelect.value + "」";
      syncControls();
    }
  }
  prefKindSelect.addEventListener("change", syncPrefBuilderInput);
  prefColSelect.addEventListener("change", syncPrefBuilderInput);

  function renderSettings() {
    for (const input of panel.querySelectorAll("[data-setting]")) {
      input.checked = data.settings?.[input.dataset.setting] === true;
    }
    const identity = new Set([...(state.identity_fields?.titles || []), ...(state.identity_fields?.links || [])]);
    const options = document.createDocumentFragment();
    const columns = state.fields.filter(f => f.type === "text" && !identity.has(f.name));
    const prevCol = prefColSelect.value;
    prefColSelect.replaceChildren(new Option("选择写入列", ""));
    for (const column of columns) prefColSelect.add(new Option(column.name, column.name));
    if (columns.some(column => column.name === prevCol)) prefColSelect.value = prevCol;
    for (const kind of ["代码链接", "关键数据", "图片", "表格", "公式", "模型链接", "数据集链接"]) {
      for (const column of columns) options.append(new Option(kind + "优先写入「" + column.name + "」"));
    }
    byId("preference-options").replaceChildren(options);
    prefKindCs?.sync(false);
    prefColCs?.sync(false);
    const list = byId("history-preferences");
    list.replaceChildren();
    for (const item of data.preferences || []) {
      const row = make("div", undefined, "history-row");
      row.append(make("span", item.text, "history-pref-text"),
        actionButton("删除", () => change("delete_preference", { target_id: item.id }), "删除偏好 " + item.text, "history-danger-btn"));
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
      prefKindSelect.value = prefColSelect.value = "";
      prefKindCs?.sync(false);
      prefColCs?.sync(false);
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
    const before = kind === "messages" ? messageCursor : kind === "pending_batches" ? data.more_pending_batches : operationCursor;
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
      } else if (kind === "pending_batches" && data.more_pending_batches === before) {
        const ids = new Set(data.pending_batches.map(o => o.id));
        data.pending_batches.push(...result.items.filter(o => !ids.has(o.id)));
        data.more_pending_batches = result.before;
        renderOperations();
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
    prefKindSelect.disabled = prefColSelect.disabled = locked || !data;
    prefKindCs?.sync(false);
    prefColCs?.sync(false);
    byId("preference-save").disabled ||= !byId("preference-input").value.trim();
    byId("history-earlier-messages").hidden = !data || messageCursor == null;
    byId("history-earlier-messages").disabled = locked || loadingPage.messages;
    byId("history-earlier-operations").hidden = !data || (byId("history-pending-only").checked ? data.more_pending_batches : operationCursor) == null;
    byId("history-earlier-operations").disabled = locked || loadingPage.operations || loadingPage.pending_batches;
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
      if (action === "add_preference") {
        byId("preference-input").value = "";
        prefKindSelect.value = prefColSelect.value = "";
        prefKindCs?.sync(false);
        prefColCs?.sync(false);
      }
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
  byId("history-earlier-operations").onclick = () => earlier(byId("history-pending-only").checked ? "pending_batches" : "operations");
  byId("history-pending-only").onchange = () => { renderOperations(); syncControls(); };
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
  async function savePreferences(texts) {
    if (!data?.scope || !data?.active_id || !Array.isArray(texts) || !texts.length) return 0;
    let saved = 0;
    for (const raw of [...new Set(texts)]) {
      const text = String(raw || "").trim();
      if (!text) continue;
      try {
        const result = await api("/api/workspace-history", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "add_preference", scope: data.scope, conversation_id: data.active_id, text })
        });
        if (result.scope === data.scope) accept(result);
        saved++;
      } catch {}
    }
    return saved;
  }

  return { sync, syncControls, savePreferences, get changing() { return changing; } };
}
