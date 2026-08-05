// ============================================================
// Air Agent 前端 (ChatGPT 风格)
// 纯 vanilla JS, 0 框架. 依赖: marked + DOMPurify + highlight.js (CDN)
//
// 功能:
// - 侧边栏: 时间分组 (今天/昨天/前7天/更早), inline 重命名, 删除, 搜索
// - 顶栏: ChatGPT model picker 风格的图选择器
// - 主区: 消息流 (Markdown + 代码高亮 + 代码复制按钮 + 工具调用折叠)
// - 流式: SSE, 脉冲光标, 滚动跟随策略
// - 主题: light / dark, 跟随系统 + 手动切换 + 持久化
// - 键盘: Enter 发送, Shift+Enter 换行, Ctrl+K 新对话, Ctrl+B 折叠侧栏, Esc 停止
// ============================================================

// ============ State ============
const state = {
  graphId: "basic-qa",
  threadId: null,
  controller: null,
  isStreaming: false,
  threads: [],
  graphs: [],
  // 用户是否手动滚离底部 (用于决定流式时是否自动跟随)
  userPinnedToTop: false,
  // 搜索关键词
  searchKeyword: "",
};

// 图描述 (用于下拉菜单显示, key 与后端 _GRAPH_SPECS 对齐)
const GRAPH_DESCRIPTIONS = {
  "basic-qa": "基础问答 — 快速空气质量查询",
  "intelligent-analysis": "智能分析 — 多维度数据洞察",
  "data-analysis": "数据分析 — 图表与统计计算",
  "intelligent-report": "智能报告 — 自动生成分析报告",
  "deep-research": "深度研究 — 多步检索与综合",
  "intelligent-tracing": "智能溯源 — 污染来源追踪",
};

// ============ DOM ============
const el = (id) => document.getElementById(id);
const $ = {
  messages: el("messages"),
  emptyState: el("empty-state"),
  input: el("input"),
  sendBtn: el("send-btn"),
  newChatBtn: el("new-chat-btn"),
  newChatTop: el("new-chat-top"),
  graphCurrent: el("graph-current"),
  graphCurrentName: el("graph-current-name"),
  graphDropdown: el("graph-dropdown"),
  graphOptions: el("graph-options"),
  graphPicker: el("graph-picker"),
  threadIdDisplay: el("thread-id-display"),
  threadList: el("thread-list"),
  themeBtn: el("theme-btn"),
  collapseBtn: el("collapse-btn"),
  sidebarToggle: el("sidebar-toggle"),
  sidebar: el("sidebar"),
  sidebarMask: el("sidebar-mask"),
  searchInput: el("search-input"),
  scrollBottomBtn: el("scroll-bottom-btn"),
  mainHeader: document.querySelector(".main-header"),
};

// ============ Util ============

/** HTML 转义, 防止 XSS. */
const escapeHtml = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

/** 把时间戳格式化成相对时间 (刚刚 / N 分钟前 / N 小时前 / N 天前 / 日期). */
const formatTime = (ts) => {
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 604800) return `${Math.floor(diff / 86400)} 天前`;
  return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
};

/** 按 updated_at 把 threads 分到 4 个时间桶. */
function groupThreadsByTime(threads) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const startOfYesterday = startOfToday - 86400;
  const startOf7Days = startOfToday - 7 * 86400;

  const groups = { today: [], yesterday: [], week: [], older: [] };
  for (const t of threads) {
    const ts = t.updated_at || t.created_at || 0;
    if (ts >= startOfToday) groups.today.push(t);
    else if (ts >= startOfYesterday) groups.yesterday.push(t);
    else if (ts >= startOf7Days) groups.week.push(t);
    else groups.older.push(t);
  }
  return [
    { label: "今天", items: groups.today },
    { label: "昨天", items: groups.yesterday },
    { label: "前 7 天", items: groups.week },
    { label: "更早", items: groups.older },
  ].filter((g) => g.items.length > 0);
}

/** 自适应 textarea 高度. */
const autoResize = (ta) => {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 200) + "px";
};

/** 切换流式状态: 改按钮形态 / 输入禁用. */
const setStreaming = (on) => {
  state.isStreaming = on;
  $.sendBtn.classList.toggle("streaming", on);
  $.sendBtn.disabled = false; // 流式时也可点 (变停止按钮)
  $.input.disabled = on;
};

/** 判断 messages 容器是否已滚动到底部 (允许 80px 容差). */
const isScrolledToBottom = () => {
  const m = $.messages;
  return m.scrollHeight - m.scrollTop - m.clientHeight < 80;
};

/** 滚动 messages 到底部. */
const scrollToBottom = (force = false) => {
  if (!force && state.userPinnedToTop) return;
  requestAnimationFrame(() => {
    $.messages.scrollTop = $.messages.scrollHeight;
    updateScrollBottomBtn();
  });
};

/** 根据滚动位置更新"滚到底部"按钮的显隐. */
const updateScrollBottomBtn = () => {
  const show = !isScrolledToBottom();
  $.scrollBottomBtn.hidden = !show;
};

// ============ Markdown 渲染 ============

let _mdReady = false;

/** 等 CDN 库加载完成, 配置 marked + hljs. */
const waitForLibs = () =>
  new Promise((resolve) => {
    const check = () => {
      if (window.marked && window.DOMPurify && window.hljs) {
        _mdReady = true;
        window.marked.setOptions({ gfm: true, breaks: false });
        // 接管代码块: 输出带 header + 复制按钮的结构
        const renderer = new window.marked.Renderer();
        const origCode = renderer.code.bind(renderer);
        renderer.code = (code, lang) => {
          const langLabel = lang && window.hljs.getLanguage(lang) ? lang : "text";
          let highlighted;
          try {
            if (lang && window.hljs.getLanguage(lang)) {
              highlighted = window.hljs.highlight(code, { language: lang }).value;
            } else {
              highlighted = window.hljs.highlightAuto(code).value;
            }
          } catch {
            highlighted = escapeHtml(code);
          }
          const encoded = encodeURIComponent(code);
          return (
            `<pre><div class="code-header">` +
            `<span class="code-lang">${escapeHtml(langLabel)}</span>` +
            `<button class="code-copy-btn" data-code="${encoded}">复制</button>` +
            `</div><code class="hljs language-${escapeHtml(langLabel)}">${highlighted}</code></pre>`
          );
        };
        window.marked.use({ renderer });
        resolve();
      } else {
        setTimeout(check, 50);
      }
    };
    check();
  });

/** 把 markdown 文本渲染成 sanitized HTML. */
const renderMarkdown = (text) => {
  if (!_mdReady || !window.marked) return escapeHtml(text);
  const html = window.marked.parse(text);
  return window.DOMPurify.sanitize(html, {
    ADD_ATTR: ["target", "class", "data-code"],
    ADD_TAGS: ["details", "summary"],
  });
};

/** 给 root 内所有代码块的复制按钮绑定事件 (避免重复绑定). */
function bindCodeCopyButtons(root) {
  root.querySelectorAll("pre .code-copy-btn").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const code = decodeURIComponent(btn.dataset.code || "");
      navigator.clipboard.writeText(code).then(() => {
        const orig = btn.textContent;
        btn.textContent = "已复制 ✓";
        setTimeout(() => (btn.textContent = orig), 1200);
      });
    });
  });
}

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

async function apiDeleteThread(threadId) {
  const r = await fetch(`/api/threads/${threadId}`, { method: "DELETE" });
  return r.ok;
}

async function apiGetHistory(graphId, threadId) {
  const r = await fetch(
    `/api/threads/${threadId}/history?graph_id=${encodeURIComponent(graphId)}`,
  );
  if (!r.ok) return null;
  return await r.json();
}

// ============ 图选择器 ============

/** 渲染顶栏图下拉. */
function renderGraphPicker() {
  $.graphCurrentName.textContent = state.graphId;
  $.graphOptions.innerHTML = state.graphs
    .map((g) => {
      const active = g === state.graphId ? " active" : "";
      const desc = GRAPH_DESCRIPTIONS[g] || "";
      const check = active ? '<span class="check">✓</span>' : "";
      return `
        <li class="graph-option${active}" data-graph="${escapeHtml(g)}">
          <div class="graph-option-name">
            <span>${escapeHtml(g)}</span>
            ${check}
          </div>
          ${desc ? `<div class="graph-option-desc">${escapeHtml(desc)}</div>` : ""}
        </li>`;
    })
    .join("");
  $.graphOptions.querySelectorAll(".graph-option").forEach((opt) => {
    opt.addEventListener("click", () => {
      const g = opt.getAttribute("data-graph");
      $.graphDropdown.hidden = true;
      if (g && g !== state.graphId) switchGraph(g);
    });
  });
}

/** 切换图: 清空主区, 重新加载该图的 thread 列表, 选中最近 thread. */
async function switchGraph(graphId) {
  if (state.isStreaming) stopStream();
  state.graphId = graphId;
  state.threadId = null;
  localStorage.setItem("air-agent:graph", graphId);
  $.threadIdDisplay.textContent = "—";
  $.threadIdDisplay.title = "";
  renderGraphPicker();
  renderThreadList();
  renderHistory(null);

  await refreshThreadList();
  const saved = localStorage.getItem("air-agent:thread:" + state.graphId);
  if (saved && state.threads.some((t) => t.thread_id === saved)) {
    await switchThread(saved);
  } else if (state.threads.length) {
    await switchThread(state.threads[0].thread_id);
  } else {
    await startNewChat();
  }
}

// ============ ThreadList ============

/** 渲染侧边栏 thread 列表, 按时间分组 + 关键词过滤. */
function renderThreadList() {
  let threads = state.threads;
  const kw = state.searchKeyword.trim().toLowerCase();
  if (kw) {
    threads = threads.filter((t) =>
      (t.title || "").toLowerCase().includes(kw),
    );
  }

  if (!threads.length) {
    $.threadList.innerHTML = `<li class="thread-empty">${kw ? "无匹配对话" : "暂无历史"}</li>`;
    return;
  }

  const groups = groupThreadsByTime(threads);
  $.threadList.innerHTML = groups
    .map((g) => {
      const items = g.items
        .map((t) => {
          const active = t.thread_id === state.threadId ? " active" : "";
          const title = t.title || "(新对话)";
          return `
            <li class="thread-item${active}" data-thread-id="${escapeHtml(t.thread_id)}">
              <span class="thread-item-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
              <div class="thread-item-actions">
                <button class="icon-btn rename-btn" title="重命名">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 20h9"></path>
                    <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
                  </svg>
                </button>
                <button class="icon-btn danger delete-btn" title="删除">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6"></path>
                    <path d="M10 11v6M14 11v6"></path>
                  </svg>
                </button>
              </div>
            </li>`;
        })
        .join("");
      return `
        <li class="thread-group">
          <div class="thread-group-label">${g.label}</div>
          <ul class="thread-group-items">${items}</ul>
        </li>`;
    })
    .join("");

  // 绑定事件
  $.threadList.querySelectorAll(".thread-item").forEach((item) => {
    const tid = item.getAttribute("data-thread-id");
    item.addEventListener("click", (e) => {
      // 点了内部按钮就不切换
      if (e.target.closest(".thread-item-actions")) return;
      if (tid && tid !== state.threadId) switchThread(tid);
    });
    // 双击标题进入 inline 编辑
    const titleEl = item.querySelector(".thread-item-title");
    titleEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startInlineRename(item, tid);
    });
    // 重命名按钮
    item.querySelector(".rename-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      startInlineRename(item, tid);
    });
    // 删除
    item.querySelector(".delete-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("确认删除该对话? 历史消息将被清空.")) return;
      const ok = await apiDeleteThread(tid);
      if (ok) {
        if (tid === state.threadId) {
          state.threadId = null;
          renderHistory(null);
        }
        await refreshThreadList();
        // 如果当前 thread 被删了, 切到第一个
        if (!state.threadId && state.threads.length) {
          await switchThread(state.threads[0].thread_id);
        } else if (!state.threads.length) {
          await startNewChat();
        }
      }
    });
  });
}

/** 把某个 thread 项的标题变成可编辑 input. */
function startInlineRename(itemEl, threadId) {
  const titleEl = itemEl.querySelector(".thread-item-title");
  const t = state.threads.find((x) => x.thread_id === threadId);
  const current = (t && t.title) || "";
  const input = document.createElement("input");
  input.className = "thread-item-title-input";
  input.value = current;
  input.placeholder = "输入对话标题";
  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let committed = false;
  const commit = async () => {
    if (committed) return;
    committed = true;
    const next = input.value.trim();
    if (!next || next === current) {
      // 取消, 还原
      input.replaceWith(titleEl);
      return;
    }
    await apiRenameThread(threadId, next);
    if (t) t.title = next;
    await refreshThreadList();
  };
  const cancel = () => {
    if (committed) return;
    committed = true;
    input.replaceWith(titleEl);
  };
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    } else if (e.key === "Escape") {
      e.preventDefault();
      cancel();
    }
  });
}

async function refreshThreadList() {
  state.threads = await apiListThreads(state.graphId);
  renderThreadList();
}

// ============ Thread 切换 / 历史加载 ============

async function switchThread(threadId) {
  if (state.isStreaming) stopStream();
  state.threadId = threadId;
  localStorage.setItem("air-agent:thread:" + state.graphId, threadId);
  $.threadIdDisplay.textContent = threadId.slice(0, 8) + "…";
  $.threadIdDisplay.title = threadId;
  renderThreadList();

  const data = await apiGetHistory(state.graphId, threadId);
  renderHistory(data);
  scrollToBottom(true);
}

/** 渲染历史消息.
 * data = { messages: [{role, content, tool_calls, tool_name, ...}, ...] }
 * - user: 用户消息 (bubble)
 * - assistant: AI 回复 (markdown); 若带 tool_calls, 额外渲染工具调用卡片
 * - tool: 工具返回 (折叠 details, 默认收起, 避免原始 JSON 刷屏)
 * - system: 系统消息 (灰显, 通常不展示)
 */
function renderHistory(data) {
  $.messages.innerHTML = "";
  if (!data || !data.messages || data.messages.length === 0) {
    if ($.emptyState) $.messages.appendChild($.emptyState);
    updateScrollBottomBtn();
    return;
  }
  for (const m of data.messages) {
    // 系统消息: 历史加载时直接跳过 (不展示给用户)
    if (m.role === "system") continue;

    // 工具返回: 折叠卡片
    if (m.role === "tool") {
      appendToolResult(m.tool_name || "工具", m.content);
      continue;
    }

    // AI 消息: 先渲染 tool_calls (如果有), 再渲染 content
    if (m.role === "assistant") {
      if (Array.isArray(m.tool_calls) && m.tool_calls.length) {
        for (const tc of m.tool_calls) {
          appendToolCall(tc.name || "tool", tc.args || {});
        }
      }
      // content 为空且没 tool_calls → 跳过 (避免空 bubble)
      if (m.content && m.content.trim()) {
        appendMessage("assistant", m.content, false);
      } else if (!Array.isArray(m.tool_calls) || !m.tool_calls.length) {
        // 完全空的 AI 消息也跳过
        continue;
      }
      continue;
    }

    // 用户消息
    if (m.role === "user" && m.content) {
      appendMessage("user", m.content, false);
    }
  }
  updateScrollBottomBtn();
}

/** 追加工具返回结果 (折叠卡片, 默认收起).
 *  与 appendToolCall (调用中) 区分: 这里是已完成的工具返回.
 */
function appendToolResult(toolName, resultText) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = "message tool";
  // 尝试把 JSON 字符串美化, 否则原样展示
  let pretty = resultText;
  try {
    const obj = JSON.parse(resultText);
    pretty = JSON.stringify(obj, null, 2);
  } catch (_) {
    // 非 JSON, 保持原样
  }
  wrap.innerHTML = `
    <div class="message-avatar">T</div>
    <div class="message-body">
      <details class="tool-call">
        <summary>
          <span class="tool-icon">✓</span>
          <span class="tool-name">${escapeHtml(toolName)}</span>
          <span class="tool-status" style="color:var(--text-tertiary)">返回结果</span>
        </summary>
        <div class="tool-args">${escapeHtml(pretty)}</div>
      </details>
    </div>
  `;
  $.messages.appendChild(wrap);
}

// ============ 消息渲染 ============

/** 创建图标按钮 (用于消息底部 actions). */
function mkIconBtn(label, svgPath, onClick, extraClass = "") {
  const b = document.createElement("button");
  b.className = `message-action ${extraClass}`.trim();
  b.title = label;
  b.innerHTML = svgPath;
  b.addEventListener("click", onClick);
  return b;
}

const ICON_COPY = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
</svg>`;
const ICON_RETRY = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="23 4 23 10 17 10"></polyline>
  <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
</svg>`;

/** 追加一条消息到消息流. 返回 contentEl (供流式更新). */
function appendMessage(role, content, streaming) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = `message ${role}${streaming ? " streaming" : ""}`;

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent =
    role === "user" ? "U" : role === "assistant" ? "A" : role === "error" ? "!" : "·";

  const body = document.createElement("div");
  body.className = "message-body";

  const authorEl = document.createElement("div");
  authorEl.className = "message-author";
  authorEl.textContent =
    role === "user" ? "你" :
    role === "assistant" ? "Air Agent" :
    role === "error" ? "错误" : "系统";
  if (streaming) body.appendChild(authorEl);

  const contentEl = document.createElement("div");
  contentEl.className = "message-content";
  if (streaming) {
    contentEl.textContent = content;
  } else {
    contentEl.innerHTML = renderMarkdown(content);
    bindCodeCopyButtons(contentEl);
  }

  body.appendChild(contentEl);

  // 非流式消息: 加底部 actions
  if (!streaming) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copyBtn = mkIconBtn("复制", ICON_COPY, () => {
      navigator.clipboard.writeText(content);
      flashIcon(copyBtn);
    });
    actions.appendChild(copyBtn);
    if (role === "assistant") {
      const retryBtn = mkIconBtn("重试", ICON_RETRY, () => retryLastUserMessage());
      actions.appendChild(retryBtn);
    }
    body.appendChild(actions);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  $.messages.appendChild(wrap);
  scrollToBottom();
  return contentEl;
}

/** 让按钮短暂变成"已复制"反馈. */
function flashIcon(btn) {
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
  setTimeout(() => (btn.innerHTML = orig), 1200);
}

/** 追加工具调用 (折叠的 details 块). */
function appendToolCall(toolName, args) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = "message tool";
  wrap.innerHTML = `
    <div class="message-avatar">T</div>
    <div class="message-body">
      <details class="tool-call">
        <summary>
          <span class="tool-icon">⚡</span>
          <span class="tool-name">${escapeHtml(toolName)}</span>
          <span class="tool-status">
            <span class="spinner"></span>
            调用中
          </span>
        </summary>
        <div class="tool-args">${escapeHtml(JSON.stringify(args, null, 2))}</div>
      </details>
    </div>
  `;
  $.messages.appendChild(wrap);
  scrollToBottom();
}

/** 追加系统/错误消息. */
function appendSystemMessage(text, kind = "system") {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = `message ${kind}`;
  wrap.innerHTML = `
    <div class="message-avatar">${kind === "error" ? "!" : "·"}</div>
    <div class="message-body">
      <div class="message-content" style="color:var(--text-secondary);font-style:italic">${escapeHtml(text)}</div>
    </div>
  `;
  $.messages.appendChild(wrap);
  scrollToBottom();
}

// ============ 发送 / 流式 ============

async function send() {
  const text = $.input.value.trim();
  if (!text || state.isStreaming) return;

  // 没 thread 就先建一个
  let isNewThread = false;
  if (!state.threadId) {
    const t = await apiCreateThread(state.graphId);
    state.threadId = t.thread_id;
    localStorage.setItem("air-agent:thread:" + state.graphId, t.thread_id);
    $.threadIdDisplay.textContent = t.thread_id.slice(0, 8) + "…";
    $.threadIdDisplay.title = t.thread_id;
    isNewThread = true;
  }

  // 乐观更新: 新对话立即插入列表 + 用首条消息作标题
  if (isNewThread) {
    state.threads.unshift({
      thread_id: state.threadId,
      graph_id: state.graphId,
      title: text.slice(0, 30),
      created_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
      step_count: 0,
    });
  } else {
    // 已有对话: 更新标题 (若为空) + 移到列表顶部
    const t = state.threads.find((x) => x.thread_id === state.threadId);
    if (t) {
      if (!t.title) t.title = text.slice(0, 30);
      t.updated_at = Date.now() / 1000;
      state.threads = [t, ...state.threads.filter((x) => x.thread_id !== state.threadId)];
    }
  }
  renderThreadList();

  appendMessage("user", text, false);
  $.input.value = "";
  autoResize($.input);

  setStreaming(true);
  state.userPinnedToTop = false;
  state.controller = new AbortController();

  const contentEl = appendMessage("assistant", "", true);

  let rawText = "";
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

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE: data: {...}\n\n
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";
      for (const chunk of lines) {
        const line = chunk.replace(/^data: /, "").trim();
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.type === "text") {
            rawText += obj.content;
            // 流式过程中用 textContent 更新 (避免每次重渲染 markdown 卡顿)
            contentEl.textContent = rawText;
            scrollToBottom();
          } else if (obj.type === "tool_call") {
            appendToolCall(obj.name, obj.args);
          } else if (obj.type === "error") {
            appendSystemMessage("流式错误: " + obj.message, "error");
          } else if (obj.type === "done") {
            // 流式结束
          }
        } catch (e) {
          console.warn("SSE 解析失败:", line, e);
        }
      }
    }

    // 流式结束: 渲染 markdown + 加 actions
    contentEl.innerHTML = renderMarkdown(rawText);
    bindCodeCopyButtons(contentEl);
    const wrap = contentEl.closest(".message");
    wrap.classList.remove("streaming");
    const body = wrap.querySelector(".message-body");
    // 移除旧的 (流式时未加), 创建新的 actions
    const oldActions = body.querySelector(".message-actions");
    if (oldActions) oldActions.remove();
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copyBtn = mkIconBtn("复制", ICON_COPY, () => {
      navigator.clipboard.writeText(rawText);
      flashIcon(copyBtn);
    });
    const retryBtn = mkIconBtn("重试", ICON_RETRY, () => retryLastUserMessage());
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
    // 后台静默刷新会话列表 (不阻塞 UI, 拿后端最新 updated_at / title)
    refreshThreadList();
  }
}

function stopStream() {
  if (state.controller) state.controller.abort();
}

async function retryLastUserMessage() {
  const userMsgs = $.messages.querySelectorAll(".message.user .message-content");
  if (!userMsgs.length) return;
  const last = userMsgs[userMsgs.length - 1].textContent;
  // 删掉 last user 之后所有消息
  const all = Array.from($.messages.children);
  const lastUserIdx = all.length - 1 - Array.from(all).reverse().findIndex((m) =>
    m.classList.contains("user"),
  );
  while ($.messages.children.length > lastUserIdx + 1) {
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
  $.threadIdDisplay.title = t.thread_id;
  renderHistory(null);

  // 乐观更新: 立即把新 thread 插入列表头部, 不等网络刷新
  state.threads.unshift({
    thread_id: t.thread_id,
    graph_id: state.graphId,
    title: "",
    created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000,
    step_count: 0,
  });
  renderThreadList();

  // 后台静默刷新 (不阻塞 UI, 拿到后端最新状态)
  refreshThreadList();

  $.input.focus();
}

// ============ 主题切换 ============

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("air-agent:theme", theme);
}

function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme") || "light";
  applyTheme(cur === "light" ? "dark" : "light");
}

// ============ 侧边栏折叠 ============

function setSidebarCollapsed(collapsed) {
  $.sidebar.classList.toggle("collapsed", collapsed);
  $.sidebarMask.classList.toggle("show", collapsed && window.innerWidth <= 768);
  localStorage.setItem("air-agent:sidebar-collapsed", collapsed ? "1" : "0");
}

function toggleSidebar() {
  setSidebarCollapsed(!$.sidebar.classList.contains("collapsed"));
}

// ============ Init ============

async function init() {
  // 主题: 已在 <head> 内联脚本中预设, 这里只同步一下按钮图标状态
  // (CSS 用 [data-theme] 自动切换图标, 无需 JS 处理)

  // 侧边栏折叠状态
  const collapsed = localStorage.getItem("air-agent:sidebar-collapsed") === "1";
  if (collapsed && window.innerWidth > 768) {
    $.sidebar.classList.add("collapsed");
  }

  // 等待 CDN 库 (marked / DOMPurify / hljs) 加载完成
  // 超时 8s 后放行, 避免 CDN 不可达时整个 init 卡死
  try {
    await Promise.race([
      waitForLibs(),
      new Promise((resolve) => setTimeout(resolve, 8000)),
    ]);
  } catch (e) {
    console.warn("Markdown 库加载失败, 将以纯文本渲染:", e);
  }

  // 加载图列表 (失败时用默认 basic-qa)
  try {
    state.graphs = await apiListGraphs();
  } catch (e) {
    console.warn("加载图列表失败, 使用默认:", e);
    state.graphs = ["basic-qa"];
  }
  // 恢复上次选中的图
  const savedGraph = localStorage.getItem("air-agent:graph");
  if (savedGraph && state.graphs.includes(savedGraph)) {
    state.graphId = savedGraph;
  }
  renderGraphPicker();

  // 加载会话历史列表 (失败时降级为空, 不阻塞后续)
  try {
    await refreshThreadList();
  } catch (e) {
    console.warn("加载会话列表失败:", e);
    state.threads = [];
    renderThreadList();
  }

  // 恢复上次 thread
  const saved = localStorage.getItem("air-agent:thread:" + state.graphId);
  if (saved && state.threads.some((t) => t.thread_id === saved)) {
    await switchThread(saved);
  } else {
    // 选第一个有内容的 thread (跳过空对话), 否则新建
    const firstMeaningful = state.threads.find(
      (t) => t.title && t.title.trim(),
    );
    if (firstMeaningful) {
      await switchThread(firstMeaningful.thread_id);
    } else if (state.threads.length) {
      await switchThread(state.threads[0].thread_id);
    } else {
      await startNewChat();
    }
  }
}

// ============ Events ============

// 图选择器下拉显隐
$.graphCurrent.addEventListener("click", (e) => {
  e.stopPropagation();
  $.graphDropdown.hidden = !$.graphDropdown.hidden;
});
document.addEventListener("click", (e) => {
  if (!$.graphPicker.contains(e.target)) {
    $.graphDropdown.hidden = true;
  }
});

// 新对话按钮 (侧栏 + 顶栏)
$.newChatBtn.addEventListener("click", startNewChat);
$.newChatTop.addEventListener("click", startNewChat);

// 主题切换
$.themeBtn.addEventListener("click", toggleTheme);

// 侧栏折叠
$.collapseBtn.addEventListener("click", toggleSidebar);
$.sidebarToggle.addEventListener("click", toggleSidebar);
$.sidebarMask.addEventListener("click", () => setSidebarCollapsed(true));

// 搜索
$.searchInput.addEventListener("input", (e) => {
  state.searchKeyword = e.target.value;
  renderThreadList();
});

// 发送 / 停止 (同一个按钮)
$.sendBtn.addEventListener("click", () => {
  if (state.isStreaming) stopStream();
  else send();
});

// 输入框
$.input.addEventListener("input", () => {
  autoResize($.input);
});
$.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!state.isStreaming) send();
  } else if (e.key === "Escape" && state.isStreaming) {
    stopStream();
  }
});

// 滚动到底部按钮
$.scrollBottomBtn.addEventListener("click", () => {
  state.userPinnedToTop = false;
  scrollToBottom(true);
});

// 消息区滚动: 检测用户是否主动上滚
$.messages.addEventListener("scroll", () => {
  const atBottom = isScrolledToBottom();
  state.userPinnedToTop = !atBottom;
  updateScrollBottomBtn();
  // 顶部加边框, 视觉层次
  $.mainHeader.classList.toggle("has-border", $.messages.scrollTop > 4);
});

// 空状态建议卡片
document.querySelectorAll(".suggestion-card").forEach((card) => {
  card.addEventListener("click", () => {
    const q = card.getAttribute("data-q");
    if (q) {
      $.input.value = q;
      autoResize($.input);
      $.input.focus();
    }
  });
});

// 全局快捷键
document.addEventListener("keydown", (e) => {
  // Ctrl/Cmd + K: 新对话
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    startNewChat();
    return;
  }
  // Ctrl/Cmd + B: 折叠侧栏
  if ((e.ctrlKey || e.metaKey) && e.key === "b") {
    e.preventDefault();
    toggleSidebar();
    return;
  }
});

// 跟随系统主题变化 (用户未手动选过时)
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (ev) => {
  if (!localStorage.getItem("air-agent:theme")) {
    applyTheme(ev.matches ? "dark" : "light");
  }
});

init();
