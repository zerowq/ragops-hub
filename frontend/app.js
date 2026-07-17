const identity = {
  tenant: "demo-company",
  user: "agent-chenyu",
  department: "customer-service",
  roles: "support_agent,knowledge_admin",
};

const state = {
  cases: [],
  activeCaseId: null,
  context: null,
  filter: "all",
  streaming: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const authHeaders = (json = true) => ({
  ...(json ? { "Content-Type": "application/json" } : {}),
  "X-Tenant-ID": identity.tenant,
  "X-User-ID": identity.user,
  "X-Department-ID": identity.department,
  "X-Roles": identity.roles,
});

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const cleanAnswer = (value) => String(value ?? "")
  .replace(/#{1,6}\s*/g, "")
  .replace(/\*\*/g, "")
  .trim();

const formatTime = (value) => {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
};

const formatMoney = (cents) => `¥${new Intl.NumberFormat("zh-CN").format((Number(cents) || 0) / 100)}`;
const priorityText = { high: "高优先级", medium: "中优先级", low: "普通" };
const statusText = { open: "处理中", waiting: "待跟进", escalated: "已升级" };

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : detail;
    } catch (_) {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(detail);
  }
  return response;
}

function switchView(viewName) {
  document.documentElement.dataset.view = viewName;
  document.body.dataset.view = viewName;
  $$(".nav-item").forEach((button) => button.classList.toggle("is-active", button.dataset.view === viewName));
  $$(".view").forEach((view) => view.classList.remove("is-active"));
  $(`#${viewName}-view`).classList.add("is-active");
  history.replaceState(null, "", `#${viewName}`);
  if (viewName === "knowledge") loadDocuments();
  if (viewName === "operations") loadOperations();
}

function renderCaseList() {
  const query = $("#case-search").value.trim().toLowerCase();
  const filtered = state.cases.filter((item) => {
    const matchesText = [item.customer_name, item.company_name, item.subject, item.preview]
      .some((value) => String(value).toLowerCase().includes(query));
    const matchesFilter = state.filter === "all"
      || item.priority === state.filter
      || item.status === state.filter;
    return matchesText && matchesFilter;
  });
  $("#case-count").textContent = state.cases.filter((item) => ["open", "waiting"].includes(item.status)).length;
  $("#case-list").innerHTML = filtered.map((item) => `
    <button class="case-card ${item.id === state.activeCaseId ? "is-active" : ""}" data-case-id="${escapeHtml(item.id)}">
      <span class="case-avatar">${escapeHtml(item.customer_name.slice(0, 1))}</span>
      <span class="case-main">
        <span class="case-top"><strong>${escapeHtml(item.customer_name)}</strong><time>${escapeHtml(formatTime(item.updated_at))}</time></span>
        <span class="case-company">${escapeHtml(item.company_name)} · ${escapeHtml(item.channel)}</span>
        <span class="case-preview">${escapeHtml(item.preview)}</span>
        <span class="case-footer"><span class="tag ${escapeHtml(item.priority)}">${escapeHtml(priorityText[item.priority] || item.priority)}</span><span class="sla">${escapeHtml(statusText[item.status] || item.status)}</span></span>
      </span>
    </button>
  `).join("") || `<p class="empty-state">没有符合条件的会话</p>`;
  $$("[data-case-id]").forEach((button) => button.addEventListener("click", () => selectCase(button.dataset.caseId)));
}

async function loadCases(preferredCaseId = state.activeCaseId) {
  try {
    const response = await request("/api/v1/support/cases", { headers: authHeaders(false) });
    state.cases = await response.json();
    const nextId = state.cases.some((item) => item.id === preferredCaseId) ? preferredCaseId : state.cases[0]?.id;
    state.activeCaseId = nextId || null;
    renderCaseList();
    if (state.activeCaseId) await selectCase(state.activeCaseId, false);
  } catch (error) {
    $("#case-list").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
  }
}

async function selectCase(caseId, rerender = true) {
  state.activeCaseId = caseId;
  if (rerender) renderCaseList();
  $("#conversation-title").textContent = "正在加载会话…";
  try {
    const response = await request(`/api/v1/support/cases/${encodeURIComponent(caseId)}`, { headers: authHeaders(false) });
    state.context = await response.json();
    renderContext();
    renderInitialMessages();
  } catch (error) {
    $("#conversation-title").textContent = "会话加载失败";
    setStatus(error.message, true);
  }
}

function renderContext() {
  const { case: supportCase, customer, order, pending_action: pendingAction } = state.context;
  $("#conversation-title").textContent = supportCase.subject;
  $("#conversation-meta").textContent = `${supportCase.id} · ${supportCase.channel} · ${supportCase.customer_name} / ${supportCase.company_name}`;
  const priority = $("#priority-badge");
  priority.textContent = priorityText[supportCase.priority] || supportCase.priority;
  priority.className = `priority-badge ${supportCase.priority}`;

  $("#customer-level").textContent = customer?.level || "未分级";
  $("#customer-avatar").textContent = (customer?.name || supportCase.customer_name).slice(0, 1);
  $("#customer-name").textContent = customer?.name || supportCase.customer_name;
  $("#customer-company").textContent = customer?.company_name || supportCase.company_name;
  $("#customer-industry").textContent = customer?.industry || "待同步";
  $("#customer-size").textContent = customer?.company_size || "待同步";
  $("#customer-phone").textContent = customer?.phone_masked || "待同步";
  $("#customer-email").textContent = customer?.email_masked || "待同步";

  $("#order-status").textContent = order?.status || "未关联";
  $("#order-product").textContent = order?.product_name || "未关联订单";
  $("#order-version").textContent = order?.product_version || "—";
  $("#order-id").textContent = order?.id || "—";
  $("#order-period").textContent = order ? `${order.purchased_at} 至 ${order.valid_until}` : "—";
  $("#order-amount").textContent = order ? formatMoney(order.amount_cents) : "—";
  renderEntitlements(order);
  renderPendingAction(pendingAction, supportCase);
}

function renderEntitlements(order) {
  if (!order) {
    $("#entitlements").innerHTML = "";
    return;
  }
  const items = [
    ["账号席位", order.seats_used, order.seats_total, "个"],
    ["知识容量", order.knowledge_used_gb, order.knowledge_quota_gb, " GB"],
    ["API 调用", order.api_used, order.api_quota, " 次"],
  ];
  $("#entitlements").innerHTML = items.map(([label, used, total, unit]) => {
    const percent = Math.min(100, Math.round((Number(used) / Math.max(1, Number(total))) * 100));
    return `<div><div class="entitlement-head"><span>${label}</span><span>${Number(used).toLocaleString()} / ${Number(total).toLocaleString()}${unit}</span></div><div class="progress-track"><div class="progress-value" style="width:${percent}%"></div></div></div>`;
  }).join("");
}

function renderPendingAction(action, supportCase) {
  const details = $("#pending-details");
  const prepare = $("#prepare-ticket");
  const cancel = $("#cancel-ticket");
  const confirm = $("#confirm-ticket");
  if (supportCase.ticket_id) {
    $("#action-description").textContent = `该会话已升级为技术工单 ${supportCase.ticket_id}，关联关系已写入审计。`;
    details.hidden = true;
    prepare.hidden = true;
    cancel.hidden = true;
    confirm.hidden = true;
    return;
  }
  prepare.hidden = Boolean(action);
  cancel.hidden = !action;
  confirm.hidden = !action;
  details.hidden = !action;
  $("#action-description").textContent = action
    ? "待提交内容已经生成。请客服确认客户、订单和问题描述后再创建。"
    : "确认问题需要升级后，Copilot 会先生成待提交内容，只有人工确认才会写入工单系统。";
  details.innerHTML = action ? `<strong>${escapeHtml(action.subject)}</strong><br>${escapeHtml(action.description)}<br>关联：${escapeHtml(action.case_id || "—")} / ${escapeHtml(action.order_id || "—")}` : "";
}

function renderInitialMessages() {
  const { case: supportCase } = state.context;
  $("#messages").innerHTML = "";
  addMessage(supportCase.preview, "customer", `${supportCase.customer_name} · ${formatTime(supportCase.updated_at)}`);
  const starter = supportCase.ticket_id
    ? `这个问题已升级为技术工单 ${supportCase.ticket_id}。我可以继续查询知识或订单信息。`
    : "我已加载当前客户、订单和套餐权益。可以先检索故障知识并核对服务期；如需升级，我会生成待确认工单。";
  addMessage(starter, "assistant", "Copilot · 业务上下文已加载");
}

function addMessage(content, role, meta = "") {
  const row = document.createElement("article");
  row.className = `message-row ${role}`;
  row.innerHTML = `
    <span class="message-avatar">${role === "customer" ? "客" : "AI"}</span>
    <div class="message-block">
      <p class="message-author">${escapeHtml(meta || (role === "customer" ? "客户" : "RAGOps Copilot"))}</p>
      <div class="message-bubble"></div>
      <p class="message-status"></p>
      <div class="citation-strip"></div>
    </div>`;
  row.querySelector(".message-bubble").textContent = content;
  $("#messages").append(row);
  $("#messages").scrollTop = $("#messages").scrollHeight;
  return row;
}

function setStatus(message, isError = false) {
  const status = $("#composer-status");
  status.textContent = message;
  status.style.color = isError ? "var(--danger)" : "";
}

async function streamMessage(message) {
  if (!message.trim() || state.streaming || !state.activeCaseId) return;
  state.streaming = true;
  $("#send-button").disabled = true;
  addMessage(message.trim(), "customer", "陈雨 · 代客户发起");
  const output = addMessage("", "assistant", "Copilot · 正在处理");
  const bubble = output.querySelector(".message-bubble");
  const messageStatus = output.querySelector(".message-status");
  const citationStrip = output.querySelector(".citation-strip");
  const citations = [];
  setStatus("Copilot 正在识别意图并处理…");
  try {
    const response = await request("/api/v1/chat/stream", {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message: message.trim(), conversation_id: state.activeCaseId, case_id: state.activeCaseId }),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop();
      for (const block of blocks) {
        const lines = block.split("\n");
        const event = (lines.find((line) => line.startsWith("event:")) || "").slice(6).trim();
        const raw = (lines.find((line) => line.startsWith("data:")) || "").slice(5).trim();
        if (!raw) continue;
        const data = JSON.parse(raw);
        if (event === "intent_classified") messageStatus.textContent = `意图：${data.intent}`;
        if (event === "retrieval_start") messageStatus.textContent = "正在执行权限过滤后的混合检索…";
        if (event === "retrieval_finished") {
          messageStatus.textContent = `已召回 ${data.count} 条证据 · ${data.latency_ms} ms`;
          citations.push(...(data.citations || []));
        }
        if (event === "tool_start") messageStatus.textContent = `正在调用受控工具：${data.tool}`;
        if (event === "text_delta") bubble.textContent += data.content || "";
        if (event === "human_confirmation_required") {
          state.context.pending_action = data.action;
          renderPendingAction(data.action, state.context.case);
          messageStatus.textContent = "工单已准备，等待人工确认";
        }
        if (event === "tool_finished" && data.result?.ok) messageStatus.textContent = "工具调用完成，已写入审计";
        if (event === "error") throw new Error(data.message || "Agent 处理失败");
        if (event === "message_end") messageStatus.textContent = `处理完成 · ${data.latency_ms || 0} ms`;
      }
    }
    bubble.textContent = cleanAnswer(bubble.textContent) || "处理完成。";
    citationStrip.innerHTML = citations.map((item) => `<button class="citation-button" data-chunk-id="${escapeHtml(item.chunk_id)}">[${item.index}] ${escapeHtml(item.title)}</button>`).join("");
    citationStrip.querySelectorAll("button").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.chunkId)));
    if (message.trim().toLowerCase() === "确认" || message.includes("确认创建")) await refreshActiveCase();
    setStatus("处理完成，回答与操作均可审计。", false);
  } catch (error) {
    bubble.textContent = `处理失败：${error.message}`;
    messageStatus.textContent = "请稍后重试";
    setStatus(error.message, true);
  } finally {
    state.streaming = false;
    $("#send-button").disabled = false;
    $("#messages").scrollTop = $("#messages").scrollHeight;
  }
}

async function refreshActiveCase() {
  const response = await request(`/api/v1/support/cases/${encodeURIComponent(state.activeCaseId)}`, { headers: authHeaders(false) });
  state.context = await response.json();
  renderContext();
  const casesResponse = await request("/api/v1/support/cases", { headers: authHeaders(false) });
  state.cases = await casesResponse.json();
  renderCaseList();
}

async function openSource(chunkId) {
  try {
    const response = await request(`/api/v1/chunks/${encodeURIComponent(chunkId)}`, { headers: authHeaders(false) });
    const source = await response.json();
    $("#source-title").textContent = source.title;
    $("#source-meta").textContent = `${source.source} · Chunk ${source.position}`;
    $("#source-content").textContent = source.content;
    $("#source-dialog").showModal();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function cancelPendingTicket() {
  try {
    await request(`/api/v1/support/cases/${encodeURIComponent(state.activeCaseId)}/pending-action/cancel`, { method: "POST", headers: authHeaders(false) });
    state.context.pending_action = null;
    renderPendingAction(null, state.context.case);
    setStatus("已取消待提交工单。", false);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadDocuments() {
  const list = $("#document-list");
  try {
    const response = await request("/api/v1/documents", { headers: authHeaders(false) });
    const documents = await response.json();
    $("#document-count").textContent = documents.length;
    list.innerHTML = documents.length ? `<table><thead><tr><th>文档</th><th>可见范围</th><th>版本</th><th>状态</th><th>入库时间</th></tr></thead><tbody>${documents.map((document) => `<tr><td><strong>${escapeHtml(document.title)}</strong><br><span>${escapeHtml(document.source)}</span></td><td>${escapeHtml(document.visibility)}</td><td>v${escapeHtml(document.version)}</td><td>${escapeHtml(document.status)}</td><td>${escapeHtml(new Date(document.created_at).toLocaleString("zh-CN"))}</td></tr>`).join("")}</tbody></table>` : `<p class="empty-state">暂时没有可访问文档</p>`;
  } catch (error) {
    list.innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
  }
}

async function uploadDocument(event) {
  event.preventDefault();
  const file = $("#document-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  form.append("visibility", $("#document-visibility").value);
  form.append("version", "1");
  $("#upload-status").textContent = "正在解析、切分并入库…";
  try {
    const response = await request("/api/v1/documents", { method: "POST", headers: authHeaders(false), body: form });
    const result = await response.json();
    $("#upload-status").textContent = `入库完成：${result.chunks || 0} 个 Chunk`;
    event.target.reset();
    await loadDocuments();
  } catch (error) {
    $("#upload-status").textContent = error.message;
  }
}

async function runSearch(event) {
  event.preventDefault();
  const query = $("#search-query").value.trim();
  if (!query) return;
  $("#search-results").innerHTML = `<p class="empty-state">正在执行混合检索…</p>`;
  try {
    const response = await request("/api/v1/search", { method: "POST", headers: authHeaders(), body: JSON.stringify({ query }) });
    const result = await response.json();
    $("#search-results").innerHTML = result.hits.map((hit, index) => `<article class="search-hit"><button data-chunk-id="${escapeHtml(hit.chunk_id)}"><span class="search-hit-header"><strong>${index + 1}. ${escapeHtml(hit.title)}</strong><span>Rerank ${Number(hit.rerank_score || 0).toFixed(3)}</span></span><p>${escapeHtml(hit.content)}</p></button></article>`).join("") || `<p class="empty-state">没有找到可用证据</p>`;
    $$(".search-hit button").forEach((button) => button.addEventListener("click", () => openSource(button.dataset.chunkId)));
  } catch (error) {
    $("#search-results").innerHTML = `<p class="empty-state">${escapeHtml(error.message)}</p>`;
  }
}

async function loadOperations() {
  try {
    const [healthResponse, summaryResponse] = await Promise.all([
      request("/api/v1/health"),
      request("/api/v1/ops/summary", { headers: authHeaders(false) }),
    ]);
    const health = await healthResponse.json();
    const summary = await summaryResponse.json();
    const banner = $("#health-banner");
    banner.className = "health-banner ok";
    banner.textContent = `服务正常 · 数据库 ready · 向量后端 ${health.vector_backend} · Embedding ${health.embedding_provider} · LLM ${health.llm_enabled ? "enabled" : "disabled"}`;
    const labels = {
      documents: "可用文档",
      chunks: "知识切片",
      open_cases: "待处理会话",
      open_tickets: "开放工单",
      conversations: "Agent 会话",
      audit_events: "审计事件",
    };
    $("#metric-grid").innerHTML = Object.entries(summary.metrics).map(([key, value]) => `<article class="metric-card"><span>${escapeHtml(labels[key] || key)}</span><strong>${Number(value).toLocaleString()}</strong></article>`).join("");
    $("#audit-list").innerHTML = summary.recent_audits.map((item) => `<div class="audit-item"><strong>${escapeHtml(item.action)} · ${escapeHtml(item.resource_type)} ${escapeHtml(item.resource_id)}</strong><span>${escapeHtml(new Date(item.created_at).toLocaleString("zh-CN"))}</span></div>`).join("") || `<p class="empty-state">暂无审计事件，完成一次工具调用后会显示在这里。</p>`;
  } catch (error) {
    $("#health-banner").className = "health-banner";
    $("#health-banner").textContent = error.message;
  }
}

function bindEvents() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  $$(".filter-chip").forEach((button) => button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    $$(".filter-chip").forEach((chip) => chip.classList.toggle("is-active", chip === button));
    renderCaseList();
  }));
  $("#case-search").addEventListener("input", renderCaseList);
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#message-input");
    const message = input.value;
    input.value = "";
    streamMessage(message);
  });
  $("#message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  $$("[data-prompt]").forEach((button) => button.addEventListener("click", () => streamMessage(button.dataset.prompt)));
  $("#quick-order").addEventListener("click", () => streamMessage(`查询订单 ${state.context?.case?.order_id || "ORD-1001"} 的当前状态和服务有效期`));
  $("#quick-ticket").addEventListener("click", () => streamMessage("请为当前客户创建工单，类型为技术支持"));
  $("#prepare-ticket").addEventListener("click", () => streamMessage("请为当前客户创建工单，类型为技术支持"));
  $("#confirm-ticket").addEventListener("click", () => streamMessage("确认"));
  $("#cancel-ticket").addEventListener("click", cancelPendingTicket);
  $("#upload-form").addEventListener("submit", uploadDocument);
  $("#search-form").addEventListener("submit", runSearch);
  $("#refresh-documents").addEventListener("click", loadDocuments);
  $("#refresh-operations").addEventListener("click", loadOperations);
}

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  const requested = location.hash.replace("#", "");
  if (["workbench", "knowledge", "operations"].includes(requested)) switchView(requested);
  await loadCases();
});
