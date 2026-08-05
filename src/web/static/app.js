// Air Agent 前端 (ChatGPT 风格: ThreadList + Markdown + Tool Call + 消息操作)
// 纯 vanilla JS, 0 框架. 依赖: marked + DOMPurify + highlight.js (CDN)

// ============ State ============
const state = {
  graphId: "basic-qa",
  threadId: null,
  controller: null,
  isStreaming: false,
  threads: [],
  graphs: [],
};

// ============ DOM ============
const el = (id) => document.getElementById(id);
const $ = {
  messages: el("messages"),
  emptyState: el("empty-state"),
  input: el("input"),
  sendBtn: el("send-btn"),
  stopBtn: el("stop-btn"),
  newChatBtn: el("new-chat-btn"),
  graphSelect: el("graph-select"),
  threadIdDisplay: el("thread-id-display"),
  threadList: el("thread-list"),
  threadTitle: el("thread-title"),
  renameBtn: el("rename-btn"),
};

// ============ Util ============
const escapeHtml = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const formatTime = (ts) => {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 604800) return `${Math.floor(diff / 86400)} 天前`;
  return d.toLocaleDateString("zh-CN");
};

const autoResize = (ta) => {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
};

const setStreaming = (on) => {
  state.isStreaming = on;
  $.sendBtn.style.display = on ? "none" : "block";
  $.stopBtn.style.display = on ? "block" : "none";
  $.input.disabled = on;
  $.sendBtn.disabled = on || !$.input.value.trim();
};

// ============ Markdown 渲染 ============
let _mdReady = false;
const waitForLibs = () =>
  new Promise((resolve) => {
    const check = () => {
      if (window.marked && window.DOMPurify && window.hljs) {
        _mdReady = true;
        // 配置 marked: 启用 GFM + 代码高亮
        window.marked.setOptions({
          gfm: true,
          breaks: false,
        });
        // 接管代码块高亮
        const renderer = new window.marked.Renderer();
        const origCode = renderer.code.bind(renderer);
        renderer.code = (code, lang) => {
          if (lang && window.hljs.getLanguage(lang)) {
            try {
              return `<pre><code class="hljs language-${escapeHtml(lang)}">${window.hljs.highlight(code, { language: lang }).value}</code></pre>`;
            } catch {}
          }
          return `<pre><code class="hljs">${window.hljs.highlightAuto(code).value}</code></pre>`;
        };
        window.marked.use({ renderer });
        resolve();
      } else {
        setTimeout(check, 50);
      }
    };
    check();
  });

const renderMarkdown = (text) => {
  if (!_mdReady || !window.marked) return escapeHtml(text);
  const html = window.marked.parse(text);
  return window.DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "class"],
    ADD_TAGS: ["details", "summary"],
  });
};

// ============ API 调用 ============
async function apiListGraphs() {
  const r = await fetch("/api/graphs");
  return (await r.json()).graphs;
}

async function apiListThreads(graphId) {
  const r = await fetch(`/api/threads?graph_id=${encodeURIComponent(graphId)}`);
  if (!r.ok) return [];
  const d = await r.json();
  return d.threads || [];
}

async function apiCreateThread(graphId) {
  const r = await fetch("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph_id: graphId }),
  });
  return await r.json();
}

async function apiRenameThread(threadId, title) {
  await fetch(`/api/threads/${threadId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

async function apiGetHistory(graphId, threadId) {
  const r = await fetch(
    `/api/threads/${threadId}/history?graph_id=${encodeURIComponent(graphId)}`,
  );
  if (!r.ok) return null;
  return await r.json();
}

// ============ ThreadList ============
function renderThreadList() {
  if (!state.threads.length) {
    $.threadList.innerHTML = '<li class="thread-empty">暂无历史</li>';
    return;
  }
  $.threadList.innerHTML = state.threads
    .map((t) => {
      const active = t.thread_id === state.threadId ? " active" : "";
      const title = t.title || "(新对话)";
      const time = formatTime(t.updated_at);
      return `
        <li class="thread-item${active}" data-thread-id="${t.thread_id}">
          <div class="thread-item-title">${escapeHtml(title)}</div>
          <div class="thread-item-meta">
            <span>${t.step_count} 步</span>
            <span>${time}</span>
          </div>
        </li>`;
    })
    .join("");
  // 点击切换
  $.threadList.querySelectorAll(".thread-item").forEach((el) => {
    el.addEventListener("click", () => {
      const tid = el.getAttribute("data-thread-id");
      if (tid && tid !== state.threadId) switchThread(tid);
    });
  });
}

async function refreshThreadList() {
  state.threads = await apiListThreads(state.graphId);
  renderThreadList();
}

// ============ Thread 切换 / 加载 ============
async function switchThread(threadId) {
  if (state.isStreaming) stopStream();
  state.threadId = threadId;
  localStorage.setItem("air-agent:thread:" + state.graphId, threadId);
  $.threadIdDisplay.textContent = threadId.slice(0, 8) + "…";
  renderThreadList(); // 高亮 active

  const data = await apiGetHistory(state.graphId, threadId);
  renderThreadTitle(data && data.messages && data.messages.length ? data : null);
  renderHistory(data);
}

function renderThreadTitle(historyData) {
  if (!historyData || !historyData.messages) {
    $.threadTitle.textContent = "新对话";
    return;
  }
  const t = state.threads.find((x) => x.thread_id === state.threadId);
  $.threadTitle.textContent = (t && t.title) || "新对话";
}

function renderHistory(data) {
  $.messages.innerHTML = "";
  if (!data || !data.messages || data.messages.length === 0) {
    if ($.emptyState) $.messages.appendChild($.emptyState);
    return;
  }
  for (const m of data.messages) {
    appendMessage(m.role, m.content, false);
  }
  scrollToBottom();
}

// ============ 消息渲染 ============
function appendMessage(role, content, streaming) {
  if ($.emptyState) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = `message ${role}${streaming ? " streaming" : ""}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent =
    role === "user" ? "U" : role === "assistant" ? "A" : role === "error" ? "!" : "·";

  const body = document.createElement("div");
  body.className = "message-body";

  const contentEl = document.createElement("div");
  contentEl.className = "message-content";
  if (streaming) {
    contentEl.textContent = content; // 流式先纯文本
  } else {
    contentEl.innerHTML = renderMarkdown(content);
    highlightCodeIn(contentEl);
  }

  body.appendChild(contentEl);

  if (!streaming) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    if (role === "assistant") {
      const copyBtn = mkBtn("复制", () => {
        navigator.clipboard.writeText(content);
        copyBtn.textContent = "✓";
        setTimeout(() => (copyBtn.textContent = "复制"), 1200);
      });
      const retryBtn = mkBtn("重试", () => retryLastUserMessage());
      actions.appendChild(copyBtn);
      actions.appendChild(retryBtn);
    } else if (role === "user") {
      const copyBtn = mkBtn("复制", () => {
        navigator.clipboard.writeText(content);
        copyBtn.textContent = "✓";
        setTimeout(() => (copyBtn.textContent = "复制"), 1200);
      });
      actions.appendChild(copyBtn);
    }
    body.appendChild(actions);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  $.messages.appendChild(wrap);
  scrollToBottom();
  return contentEl;
}

function mkBtn(text, onClick) {
  const b = document.createElement("button");
  b.className = "message-action";
  b.textContent = text;
  b.addEventListener("click", onClick);
  return b;
}

function appendToolCall(toolName, args) {
  if ($.emptyState) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = "message assistant";
  wrap.innerHTML = `
    <div class="message-avatar">T</div>
    <div class="message-body">
      <details class="tool-call">
        <summary>
          <span class="tool-name">${escapeHtml(toolName)}</span>
          <span style="color:#6b7280;font-size:11px;margin-left:auto">点击展开参数</span>
        </summary>
        <div class="tool-args">${escapeHtml(JSON.stringify(args, null, 2))}</div>
      </details>
    </div>
  `;
  $.messages.appendChild(wrap);
  scrollToBottom();
}

function appendSystemMessage(text, kind = "system") {
  if ($.emptyState) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = `message ${kind}`;
  wrap.innerHTML = `
    <div class="message-avatar">${kind === "error" ? "!" : "·"}</div>
    <div class="message-body">
      <div class="message-content" style="color:#6b7280;font-size:13px;font-style:italic">${escapeHtml(text)}</div>
    </div>
  `;
  $.messages.appendChild(wrap);
  scrollToBottom();
}

function highlightCodeIn(root) {
  root.querySelectorAll("pre code").forEach((block) => {
    if (!block.dataset.highlighted && window.hljs) {
      try {
        window.hljs.highlightElement(block);
        block.dataset.highlighted = "1";
      } catch {}
    }
  });
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    $.messages.scrollTop = $.messages.scrollHeight;
  });
}

// ============ 发送 / 流式 ============
async function send() {
  const text = $.input.value.trim();
  if (!text || state.isStreaming) return;
  if (!state.threadId) {
    const t = await apiCreateThread(state.graphId);
    state.threadId = t.thread_id;
    localStorage.setItem("air-agent:thread:" + state.graphId, t.thread_id);
  }

  appendMessage("user", text, false);
  $.input.value = "";
  autoResize($.input);
  $.sendBtn.disabled = true;

  setStreaming(true);
  state.controller = new AbortController();

  const contentEl = appendMessage("assistant", "", true);

  try {
    const r = await fetch(
      `/api/threads/${state.threadId}/runs/stream?graph_id=${encodeURIComponent(state.graphId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          graph_id: state.graphId,
          content: text,
        }),
        signal: state.controller.signal,
      },
    );
    if (!r.ok) {
      const e = await r.text();
      throw new Error(`HTTP ${r.status}: ${e}`);
    }

    let rawText = "";
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 格式: data: {...}\n\n
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";
      for (const chunk of lines) {
        const line = chunk.replace(/^data: /, "").trim();
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.type === "text") {
            rawText += obj.content;
            contentEl.textContent = rawText;
            scrollToBottom();
          } else if (obj.type === "tool_call") {
            appendToolCall(obj.name, obj.args);
          } else if (obj.type === "error") {
            appendSystemMessage("流式错误: " + obj.message, "error");
          }
        } catch (e) {
          console.warn("SSE 解析失败:", line, e);
        }
      }
    }
    // 流式结束: 渲染 Markdown
    contentEl.innerHTML = renderMarkdown(rawText);
    highlightCodeIn(contentEl);
    // 流式 message 加操作按钮
    const wrap = contentEl.closest(".message");
    wrap.classList.remove("streaming");
    const body = wrap.querySelector(".message-body");
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copyBtn = mkBtn("复制", () => {
      navigator.clipboard.writeText(rawText);
      copyBtn.textContent = "✓";
      setTimeout(() => (copyBtn.textContent = "复制"), 1200);
    });
    const retryBtn = mkBtn("重试", () => retryLastUserMessage());
    actions.appendChild(copyBtn);
    actions.appendChild(retryBtn);
    body.appendChild(actions);
  } catch (e) {
    if (e.name === "AbortError") {
      appendSystemMessage("已停止", "system");
    } else {
      appendSystemMessage("出错: " + e.message, "error");
    }
  } finally {
    setStreaming(false);
    state.controller = null;
    await refreshThreadList();
    renderThreadTitle(null);
  }
}

function stopStream() {
  if (state.controller) state.controller.abort();
}

async function retryLastUserMessage() {
  // 找最后一个 user message, 重新发送
  const userMsgs = $.messages.querySelectorAll(".message.user .message-content");
  if (!userMsgs.length) return;
  const last = userMsgs[userMsgs.length - 1].textContent;
  // 删掉 last user 之后所有 assistant 消息
  const all = Array.from($.messages.children);
  const lastUserIdx = all.length - 1 - Array.from(all).reverse().findIndex((m) =>
    m.classList.contains("user"),
  );
  while ($.messages.children.length > lastUserIdx) {
    $.messages.removeChild($.messages.lastChild);
  }
  $.input.value = last;
  autoResize($.input);
  await send();
}

// ============ 新对话 ============
async function startNewChat() {
  if (state.isStreaming) stopStream();
  const t = await apiCreateThread(state.graphId);
  state.threadId = t.thread_id;
  localStorage.setItem("air-agent:thread:" + state.graphId, t.thread_id);
  $.threadIdDisplay.textContent = t.thread_id.slice(0, 8) + "…";
  $.threadTitle.textContent = "新对话";
  renderHistory(null);
  await refreshThreadList();
}

// ============ 重命名 ============
function startRename() {
  const t = state.threads.find((x) => x.thread_id === state.threadId);
  const current = (t && t.title) || "";
  const next = prompt("重命名对话:", current);
  if (next == null) return;
  const title = next.trim();
  if (!title) return;
  if (!state.threadId) return;
  apiRenameThread(state.threadId, title).then(() => {
    $.threadTitle.textContent = title;
    refreshThreadList();
  });
}

// ============ Init ============
async function init() {
  await waitForLibs();
  state.graphs = await apiListGraphs();
  $.graphSelect.innerHTML = state.graphs
    .map((g) => `<option value="${g}">${g}</option>`)
    .join("");
  $.graphSelect.value = state.graphId;
  await refreshThreadList();
  // 恢复上次的 thread
  const saved = localStorage.getItem("air-agent:thread:" + state.graphId);
  if (saved && state.threads.some((t) => t.thread_id === saved)) {
    await switchThread(saved);
  } else if (state.threads.length) {
    await switchThread(state.threads[0].thread_id);
  } else {
    await startNewChat();
  }
}

// ============ Events ============
$.graphSelect.addEventListener("change", () => {
  state.graphId = $.graphSelect.value;
  state.threadId = null;
  $.threadIdDisplay.textContent = "—";
  refreshThreadList().then(async () => {
    const saved = localStorage.getItem("air-agent:thread:" + state.graphId);
    if (saved && state.threads.some((t) => t.thread_id === saved)) {
      await switchThread(saved);
    } else if (state.threads.length) {
      await switchThread(state.threads[0].thread_id);
    } else {
      renderHistory(null);
      $.threadTitle.textContent = "新对话";
    }
  });
});
$.newChatBtn.addEventListener("click", startNewChat);
$.renameBtn.addEventListener("click", startRename);
$.sendBtn.addEventListener("click", send);
$.stopBtn.addEventListener("click", stopStream);
$.input.addEventListener("input", () => {
  autoResize($.input);
  $.sendBtn.disabled = !$.input.value.trim() || state.isStreaming;
});
$.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  } else if (e.key === "Escape" && state.isStreaming) {
    stopStream();
  }
});

init();
