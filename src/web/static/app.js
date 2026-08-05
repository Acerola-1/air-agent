// ============================================================
// Air Agent 前端 (ChatGPT 风格还原)
// 纯 vanilla JS, 0 框架. 依赖: marked + DOMPurify + highlight.js (CDN)
//
// 关键差异 (相比旧版本):
// - 工具调用完全嵌入 AI 消息 body 内, 不再作为独立 "T" 头像消息
// - 历史加载时, 相邻的 assistant/tool 消息合并成同一条 AI 消息
// - 用户消息无 bubble 背景 (纯文本 + 头像 + 作者小字, GPT 经典排版)
// - 顶栏: 左会话标题, 右图选择器 (GPT 布局)
// ============================================================

// ============ State ============
const state = {
  graphId: "basic-qa",
  threadId: null,
  controller: null,
  isStreaming: false,
  threads: [],
  graphs: [],
  userPinnedToTop: false,
  searchKeyword: "",
  // 当前流式 AI 消息引用 (用于嵌入 tool calls)
  _curAssistant: null,   // {body, pendingToolDetails: []}
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
  graphCurrent: el("graph-current"),
  graphCurrentName: el("graph-current-name"),
  graphDropdown: el("graph-dropdown"),
  graphOptions: el("graph-options"),
  graphPicker: el("graph-picker"),
  chatTitle: el("chat-title"),                // 顶栏会话标题 (新增)
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

/** 把时间戳格式化成相对时间. */
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

/** 更新顶栏会话标题. */
function updateChatTitle(title) {
  const t = title ? title.trim() : "";
  $.chatTitle.textContent = t || "新对话";
  document.title = t ? `${t} — Air Agent` : "Air Agent";
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
  $.sendBtn.disabled = false;
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
//
// 三级降级:
//   (1) marked@4 + hljs@11 + DOMPurify (完整能力, 首选; 静态库本地加载)
//   (2) fallbackMarkdownToHtml()   (纯 JS 正则, 0 依赖; 标题/粗体/列表/代码/链接/表格)
//   (3) escapeHtml()               (最底层; 仅 XSS 防护)
let _mdReady = false;
let _mdMethod = "pending"; // "marked" | "fallback" | "none"

/**
 * 流式 MD 保护: 如果 text 末尾的围栏代码块 ``` 是奇数(未闭合), 则临时补上 "\n```\n"
 * 避免逐字渲染时代码块标签外溢, 导致后续文字全被当成 <code>.
 * ChatGPT 官方也用类似技巧.
 */
function completeUnclosedFences(text) {
  if (!text || !text.includes("```")) return text;
  // 只统计位于行首(允许前置空白)的 ```, 避免代码内容里的 ``` 干扰
  const matches = text.match(/^[ \t]*```/gm);
  if (matches && matches.length % 2 === 1) {
    return text.replace(/\n?$/, "\n```\n");
  }
  return text;
}

/**
 * 纯 JS 轻量 MD 渲染降级 (保底, 0 外部依赖).
 * 支持: 标题 H1~H3, 段落, **粗体**, *斜体*, ~~删除线~~, `内联code`,
 *       ```围栏代码块```, 无序/有序/任务列表, >引用, [链接](url),
 *       水平分割线, 表格(最小语法), 换行.
 * 返回的是不安全 HTML, 必须配合 escapeHtml 或 DOMPurify 使用.
 */
function fallbackMarkdownToHtml(raw) {
  if (!raw) return "";
  const lines = String(raw).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;

  // 转义工具
  const h = (s) =>
    String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  // 内联元素处理 (调用后再拼回 HTML)
  const inline = (s) => {
    let t = h(s);
    // 内联 code: `xxx` (先处理, 避免其他正则干扰)
    t = t.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
    // 链接: [text](url)
    t = t.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
      (_, tx, u) => `<a href="${h(u)}" target="_blank" rel="noopener noreferrer">${tx}</a>`
    );
    // **粗体**, *斜体*, ~~删除线~~
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    t = t.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    return t;
  };

  while (i < lines.length) {
    const line = lines[i];

    // 围栏代码块 (```lang ... ```)
    const fence = line.match(/^[ \t]*```([\w+-]*)[ \t]*$/);
    if (fence) {
      const lang = fence[1] || "text";
      const buf = [];
      i += 1;
      while (i < lines.length && !/^[ \t]*```[ \t]*$/.test(lines[i])) {
        buf.push(lines[i]);
        i += 1;
      }
      i += 1; // 跳过结束 ```
      const code = buf.join("\n");
      out.push(
        `<pre><div class="code-header">` +
          `<span class="code-lang">${h(lang)}</span>` +
          `<button class="code-copy-btn" data-code="${encodeURIComponent(code)}">复制</button>` +
          `</div><code class="hljs language-${h(lang)}">${h(code)}</code></pre>`
      );
      continue;
    }

    // 水平分割线 --- / ***
    if (/^[ \t]*([-*_])[ \t]*\1[ \t]*\1[ \t\1]*$/.test(line)) {
      out.push("<hr>");
      i += 1;
      continue;
    }

    // 引用块 >
    if (/^[ \t]*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^[ \t]*>/.test(lines[i])) {
        buf.push(lines[i].replace(/^[ \t]*>[ \t]?/, ""));
        i += 1;
      }
      out.push(`<blockquote>${inline(buf.join(" "))}</blockquote>`);
      continue;
    }

    // 标题 # / ## / ### / ####
    const hx = line.match(/^(#{1,6})[ \t]+(.+)$/);
    if (hx) {
      const level = hx[1].length;
      out.push(`<h${level}>${inline(hx[2]).trim()}</h${level}>`);
      i += 1;
      continue;
    }

    // 表格 (下一行是 |---| 分隔)
    if (
      /\|/.test(line) &&
      lines[i + 1] &&
      /^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)+\|?[ \t]*$/.test(lines[i + 1])
    ) {
      const headCells = line.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      i += 2; // 跳过头 + 分隔
      const rows = [];
      while (i < lines.length && /\|/.test(lines[i]) && lines[i].trim() !== "") {
        rows.push(lines[i].replace(/^\||\|$/g, "").split("|").map((c) => c.trim()));
        i += 1;
      }
      let html = '<div class="table-wrap"><table><thead><tr>';
      headCells.forEach((c) => (html += `<th>${inline(c)}</th>`));
      html += "</tr></thead><tbody>";
      rows.forEach((r) => {
        html += "<tr>";
        for (let k = 0; k < headCells.length; k += 1) {
          html += `<td>${inline(r[k] ?? "")}</td>`;
        }
        html += "</tr>";
      });
      html += "</tbody></table></div>";
      out.push(html);
      continue;
    }

    // 无序列表 / 有序列表 / 任务列表 (支持连续多行)
    const ulMatch = line.match(/^([ \t]*)[-*+][ \t]+(.+)$/);
    const olMatch = !ulMatch && line.match(/^([ \t]*)(\d+)\.[ \t]+(.+)$/);
    if (ulMatch || olMatch) {
      const isOl = !!olMatch;
      const baseIndent = (ulMatch || olMatch)[1].length;
      const tag = isOl ? "ol" : "ul";
      out.push(`<${tag}>`);
      while (i < lines.length) {
        const mUL = lines[i].match(/^([ \t]*)[-*+][ \t]+(.+)$/);
        const mOL = !mUL && lines[i].match(/^([ \t]*)(\d+)\.[ \t]+(.+)$/);
        if (!mUL && !mOL) break;
        const m = mUL || mOL;
        if (m[1].length !== baseIndent) break;
        const wantOl = !!mOL;
        if (wantOl !== isOl) break;
        let content = m[mUL ? 2 : 3];
        // 任务列表 - [x] / - [ ]
        const task = content.match(/^\[([ xX])\][ \t]+(.+)$/);
        if (task) {
          const checked = task[1].toLowerCase() === "x" ? " checked" : "";
          out.push(
            `<li><input type="checkbox" disabled${checked}> <span>${inline(task[2])}</span></li>`
          );
        } else {
          out.push(`<li>${inline(content)}</li>`);
        }
        i += 1;
      }
      out.push(`</${tag}>`);
      continue;
    }

    // 空行
    if (line.trim() === "") {
      i += 1;
      continue;
    }

    // 段落: 连续非空行合并
    const buf = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,6})[ \t]/.test(lines[i]) &&
      !/^[ \t]*>/.test(lines[i]) &&
      !/^[ \t]*```/.test(lines[i]) &&
      !/^[ \t]*([-*+]|\d+\.)[ \t]/.test(lines[i]) &&
      !/^[ \t]*([-*_])[ \t]*\1[ \t]*\1/.test(lines[i])
    ) {
      buf.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${inline(buf.join(" "))}</p>`);
  }
  return out.join("\n");
}

/** 等 3 个库加载完成并配置 marked + hljs. 3 秒超时后降级到 fallback. */
const waitForLibs = () =>
  new Promise((resolve) => {
    const startAt = Date.now();
    const TIMEOUT_MS = 3000;

    const setupMarked = () => {
      try {
        // marked v4 setOptions API
        window.marked.setOptions({
          gfm: true,
          breaks: false,
          tables: true,
          headerIds: false,
          mangle: false,
        });
        const renderer = new window.marked.Renderer();

        // --- 表格: 包一层 .table-wrap 实现外圆角 ---
        renderer.table = (header, body) =>
          `<div class="table-wrap"><table><thead>${header}</thead><tbody>${body}</tbody></table></div>`;

        // --- 链接: 自动加 target=_blank + rel ---
        renderer.link = (href, title, text) => {
          const safe = (href || "").replace(/"/g, "%22");
          const t = title ? ` title="${title.replace(/"/g, "%22")}"` : "";
          return `<a href="${safe}"${t} target="_blank" rel="noopener noreferrer">${text}</a>`;
        };

        // --- 代码块: hljs v11 + 复制按钮 ---
        renderer.code = (code, lang) => {
          // hljs v11 API: hljs.getLanguage(lang), hljs.highlight(code, {language}), hljs.highlightAuto(code)
          const hl = window.hljs;
          let normalizedLang = "text";
          if (hl && typeof hl.getLanguage === "function") {
            const l = (lang || "").trim().split(" ")[0].toLowerCase();
            if (l && hl.getLanguage(l)) normalizedLang = l;
          }
          let highlighted = null;
          if (hl && typeof hl.highlight === "function") {
            try {
              if (normalizedLang !== "text") {
                const r = hl.highlight(code, { language: normalizedLang, ignoreIllegals: true });
                highlighted = r.value || null;
              } else if (typeof hl.highlightAuto === "function") {
                const r = hl.highlightAuto(code);
                highlighted = r.value || null;
                if (r.language) normalizedLang = r.language;
              }
            } catch (_e) {
              highlighted = null;
            }
          }
          if (!highlighted) highlighted = escapeHtml(code);
          const encoded = encodeURIComponent(code);
          return (
            `<pre><div class="code-header">` +
            `<span class="code-lang">${escapeHtml(normalizedLang)}</span>` +
            `<button class="code-copy-btn" data-code="${encoded}">复制</button>` +
            `</div><code class="hljs language-${escapeHtml(normalizedLang)}">${highlighted}</code></pre>`
          );
        };

        window.marked.use({ renderer });
        return true;
      } catch (e) {
        console.warn("[md] marked 配置失败, 降级到 fallback:", e);
        return false;
      }
    };

    const check = () => {
      if (window.marked && typeof window.marked.parse === "function" && window.DOMPurify) {
        // hljs 不存在也可以跑 marked,只是代码块没高亮 -> 自动用 escapeHtml
        const ok = setupMarked();
        _mdReady = true;
        _mdMethod = ok ? "marked" : "fallback";
        resolve();
        return;
      }
      if (Date.now() - startAt > TIMEOUT_MS) {
        // 超时, 直接用 fallback (避免永远等不到 CDN 或加载异常)
        _mdReady = true;
        _mdMethod = "fallback";
        console.warn("[md] 3 秒内 MD 库未就绪, 降级为纯 JS fallback 渲染器.");
        resolve();
        return;
      }
      setTimeout(check, 50);
    };
    check();
  });

/**
 * 把 Markdown 文本渲染成安全的 HTML 字符串.
 * - 流式时会自动补齐未闭合的 ``` 围栏代码块, 避免排版错位.
 * - 三级降级: marked -> fallbackMarkdownToHtml -> escapeHtml
 */
const renderMarkdown = (text, { streaming = false } = {}) => {
  if (text == null) return "";
  const src = streaming ? completeUnclosedFences(String(text)) : String(text);
  if (!_mdReady) return escapeHtml(src);

  let html = null;
  let needPurify = false;

  if (_mdMethod === "marked" && window.marked && typeof window.marked.parse === "function") {
    try {
      html = window.marked.parse(src);
      needPurify = true;
    } catch (e) {
      console.warn("[md] marked.parse 异常, 降级 fallback:", e);
      html = fallbackMarkdownToHtml(src);
      needPurify = true;
    }
  } else {
    html = fallbackMarkdownToHtml(src);
    needPurify = true;
  }

  if (!html) return escapeHtml(src);

  // DOMPurify 最终兜底
  if (needPurify && window.DOMPurify) {
    try {
      return window.DOMPurify.sanitize(html, {
        ADD_ATTR: ["target", "class", "data-code", "disabled", "checked", "rel"],
        ADD_TAGS: ["details", "summary"],
      });
    } catch (e) {
      console.warn("[md] DOMPurify 异常, 退化为转义:", e);
      return escapeHtml(src);
    }
  }
  // 没 Purify 就直接输出 fallback(内部已先转义)
  if (_mdMethod === "fallback") return html;
  return escapeHtml(src);
};

/** 给 root 内所有代码块的复制按钮绑定事件. */
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

// 调试/扩展接口: 把关键 MD 函数挂到 window, 方便外部调用与测试.
// 注: app.js 顶层 const 不会挂到 window, 必须显式赋值.
window.__md = {
  renderMarkdown,
  fallbackMarkdownToHtml,
  bindCodeCopyButtons,
  completeUnclosedFences,
  getStatus: () => ({ ready: _mdReady, method: _mdMethod }),
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

/** 切换图. */
async function switchGraph(graphId) {
  if (state.isStreaming) stopStream();
  state.graphId = graphId;
  state.threadId = null;
  localStorage.setItem("air-agent:graph", graphId);
  updateChatTitle("");
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

/** 渲染侧边栏 thread 列表. */
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
                    <path d="M16.5 3.5a2.121 2 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"></path>
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
      if (e.target.closest(".thread-item-actions")) return;
      if (tid && tid !== state.threadId) switchThread(tid);
    });
    const titleEl = item.querySelector(".thread-item-title");
    titleEl.addEventListener("dblclick", (e) => {
      e.stopPropagation();
      startInlineRename(item, tid);
    });
    item.querySelector(".rename-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      startInlineRename(item, tid);
    });
    item.querySelector(".delete-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("确认删除该对话?")) return;
      const ok = await apiDeleteThread(tid);
      if (ok) {
        if (tid === state.threadId) {
          state.threadId = null;
          renderHistory(null);
          updateChatTitle("");
        }
        await refreshThreadList();
        if (!state.threadId && state.threads.length) {
          await switchThread(state.threads[0].thread_id);
        } else if (!state.threads.length) {
          await startNewChat();
        }
      }
    });
  });
}

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
      input.replaceWith(titleEl);
      return;
    }
    await apiRenameThread(threadId, next);
    if (t) t.title = next;
    if (state.threadId === threadId) updateChatTitle(next);
    await refreshThreadList();
  };
  const cancel = () => {
    if (committed) return;
    committed = true;
    input.replaceWith(titleEl);
  };
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    else if (e.key === "Escape") { e.preventDefault(); cancel(); }
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
  const t = state.threads.find((x) => x.thread_id === threadId);
  updateChatTitle(t ? t.title : "");
  renderThreadList();

  const data = await apiGetHistory(state.graphId, threadId);
  renderHistory(data);
  scrollToBottom(true);
}

/** 渲染历史消息.
 *  关键逻辑: 相邻的 assistant + tool 消息合并到同一条 AI 消息中 (ChatGPT 风格).
 *  这样 tool call / tool result 作为折叠卡, 位于同一个 AI author 下, 无独立 T 头像.
 */
function renderHistory(data) {
  $.messages.innerHTML = "";
  if (!data || !data.messages || data.messages.length === 0) {
    if ($.emptyState) $.messages.appendChild($.emptyState);
    updateScrollBottomBtn();
    return;
  }

  const msgs = data.messages;
  // 第 1 轮: 分组成"回合" — 每组以 user 开头 (或以非 user 开头, 用于首条 system/assistant),
  // 连续的 assistant/tool 合并为同一条 AI 消息块
  let i = 0;
  while (i < msgs.length) {
    const m = msgs[i];
    if (m.role === "system") { i++; continue; }

    if (m.role === "user") {
      appendMessage("user", m.content || "", false);
      i++;
      continue;
    }

    // 连续 assistant/tool → 合并
    const assistantParts = [];  // {kind:'content', text} | {kind:'tool_call', name, args} | {kind:'tool_result', name, content}
    while (i < msgs.length && (msgs[i].role === "assistant" || msgs[i].role === "tool")) {
      const mm = msgs[i];
      if (mm.role === "assistant") {
        if (Array.isArray(mm.tool_calls)) {
          for (const tc of mm.tool_calls) {
            assistantParts.push({ kind: "tool_call", name: tc.name || "tool", args: tc.args || {} });
          }
        }
        if (mm.content && mm.content.trim()) {
          assistantParts.push({ kind: "content", text: mm.content });
        }
      } else if (mm.role === "tool") {
        assistantParts.push({ kind: "tool_result", name: mm.tool_name || "工具", content: mm.content });
      }
      i++;
    }
    if (assistantParts.length) {
      appendMergedAssistant(assistantParts);
    }
  }
  updateScrollBottomBtn();
}

/** 渲染合并后的 AI 消息块 (内容 + 多个工具调用/结果). */
function appendMergedAssistant(parts) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = "message assistant";

  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = "A";

  const body = document.createElement("div");
  body.className = "message-body";

  const authorEl = document.createElement("div");
  authorEl.className = "message-author";
  authorEl.textContent = "Air Agent";
  body.appendChild(authorEl);

  for (const p of parts) {
    if (p.kind === "content") {
      const contentEl = document.createElement("div");
      contentEl.className = "message-content";
      contentEl.innerHTML = renderMarkdown(p.text);
      bindCodeCopyButtons(contentEl);
      body.appendChild(contentEl);
    } else if (p.kind === "tool_call") {
      body.appendChild(makeToolCallElement(p.name, p.args, true, "已调用"));
    } else if (p.kind === "tool_result") {
      body.appendChild(makeToolResultElement(p.name, p.content));
    }
  }

  // Actions (复制 + 重试)
  const allText = parts.filter(p => p.kind === "content").map(p => p.text).join("\n\n") || "";
  const actions = document.createElement("div");
  actions.className = "message-actions";
  const copyBtn = mkIconBtn("复制", ICON_COPY, () => {
    navigator.clipboard.writeText(allText);
    flashIcon(copyBtn);
  });
  const retryBtn = mkIconBtn("重试", ICON_RETRY, () => retryLastUserMessage());
  actions.appendChild(copyBtn);
  actions.appendChild(retryBtn);
  body.appendChild(actions);

  // 包裹成 wrap -> avatar + body (GPT 标准两列布局)
  const wrapInner = document.createElement("div");
  wrapInner.className = "message-wrap";
  wrapInner.appendChild(avatar);
  wrapInner.appendChild(body);
  wrap.appendChild(wrapInner);

  $.messages.appendChild(wrap);
  scrollToBottom();
}

// ============ 工具调用元素 (纯函数, 返回 DOM) ============

/** 构造一个 <details class="tool-call"> — 用于"调用中"或"已调用". */
function makeToolCallElement(toolName, args, done, statusText) {
  const details = document.createElement("details");
  details.className = "tool-call" + (done ? " tool-done" : "");
  // 默认展开时显示: 用户能看到调用了什么, 但默认收起不占空间 (GPT 做派)
  details.open = false;
  const argsPretty = (typeof args === "string")
    ? args
    : JSON.stringify(args, null, 2);
  details.innerHTML = `
    <summary>
      <span class="tool-icon">${done ? "✓" : "⚡"}</span>
      <span class="tool-name">${escapeHtml(toolName)}</span>
      <span class="tool-status">
        ${done ? `<span>${statusText || "已调用"}</span>` : `<span class="spinner"></span><span>调用中</span>`}
      </span>
    </summary>
    <div class="tool-args">${escapeHtml(argsPretty)}</div>
  `;
  return details;
}

/** 构造工具返回折叠卡 (绿色 ✓, 展示响应体). */
function makeToolResultElement(toolName, resultText) {
  // 尝试美化 JSON
  let pretty = resultText;
  try {
    const obj = JSON.parse(resultText);
    pretty = JSON.stringify(obj, null, 2);
  } catch (_) { /* 非 JSON, 保持原样 */ }
  const details = document.createElement("details");
  details.className = "tool-call tool-done";
  details.open = false;
  details.innerHTML = `
    <summary>
      <span class="tool-icon">✓</span>
      <span class="tool-name">${escapeHtml(toolName)}</span>
      <span class="tool-status">返回结果</span>
    </summary>
    <div class="tool-args">${escapeHtml(pretty)}</div>
  `;
  return details;
}

// ============ 消息渲染 ============

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

/** 追加一条消息.
 *  返回 { wrap, body, contentEl } — 调用方拿到后可继续往 body 里插入 tool-calls.
 */
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
  body.appendChild(authorEl);

  const contentEl = document.createElement("div");
  contentEl.className = "message-content";
  if (streaming) {
    // 流式: 仍用 MD 渲染 (completeUnclosedFences 会自动补齐未闭合 ``` 围栏, 避免错位)
    contentEl.innerHTML = renderMarkdown(content, { streaming: true });
    bindCodeCopyButtons(contentEl);
  } else {
    contentEl.innerHTML = renderMarkdown(content);
    bindCodeCopyButtons(contentEl);
  }
  body.appendChild(contentEl);

  // 非流式消息: 加 actions
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

  // GPT 两列布局: wrap > wrap-inner (avatar + body)
  const wrapInner = document.createElement("div");
  wrapInner.className = "message-wrap";
  wrapInner.appendChild(avatar);
  wrapInner.appendChild(body);
  wrap.appendChild(wrapInner);

  $.messages.appendChild(wrap);
  scrollToBottom();
  return { wrap, body, contentEl };
}

function flashIcon(btn) {
  const orig = btn.innerHTML;
  btn.innerHTML = `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
  setTimeout(() => (btn.innerHTML = orig), 1200);
}

/** 追加工具调用 — 嵌入到"当前流式 AI 消息"或"最后一条 AI 消息"的 body 里. */
function appendToolCall(toolName, args) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  let bodyEl;
  if (state._curAssistant && state._curAssistant.body) {
    bodyEl = state._curAssistant.body;
  } else {
    // fallback: 取最后一条 assistant
    const last = $.messages.querySelector(".message.assistant:last-child .message-body");
    if (!last) return null;
    bodyEl = last;
  }
  const details = makeToolCallElement(toolName, args, false);
  bodyEl.appendChild(details);
  // 记录到 pending 列表, 之后转"完成"状态
  if (state._curAssistant) {
    state._curAssistant.pendingToolDetails = state._curAssistant.pendingToolDetails || [];
    state._curAssistant.pendingToolDetails.push(details);
  }
  scrollToBottom();
  return details;
}

/** 追加工具返回 — 嵌入到最后一条 AI 消息 body, 绿色 ✓, 展示响应. */
function appendToolResult(toolName, resultText) {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  let bodyEl;
  if (state._curAssistant && state._curAssistant.body) {
    bodyEl = state._curAssistant.body;
  } else {
    const last = $.messages.querySelector(".message.assistant:last-child .message-body");
    if (!last) return null;
    bodyEl = last;
  }
  const details = makeToolResultElement(toolName, resultText);
  bodyEl.appendChild(details);
  scrollToBottom();
  return details;
}

/** 把当前所有 pending tools 标为"已完成" (当一段文本或下一个 tool 到来时调用). */
function markPendingToolsDone(statusText) {
  if (!state._curAssistant || !state._curAssistant.pendingToolDetails) return;
  for (const d of state._curAssistant.pendingToolDetails) {
    d.classList.add("tool-done");
    const icon = d.querySelector(".tool-icon");
    if (icon) icon.textContent = "✓";
    const status = d.querySelector(".tool-status");
    if (status) status.innerHTML = `<span>${statusText || "已调用"}</span>`;
  }
  state._curAssistant.pendingToolDetails = [];
}

function appendSystemMessage(text, kind = "system") {
  if ($.emptyState && $.emptyState.parentNode) $.emptyState.remove();
  const wrap = document.createElement("div");
  wrap.className = `message ${kind}`;
  const avatar = document.createElement("div");
  avatar.className = "message-avatar";
  avatar.textContent = kind === "error" ? "!" : "·";
  const body = document.createElement("div");
  body.className = "message-body";
  const authorEl = document.createElement("div");
  authorEl.className = "message-author";
  authorEl.textContent = kind === "error" ? "错误" : "系统";
  body.appendChild(authorEl);
  const contentEl = document.createElement("div");
  contentEl.className = "message-content";
  contentEl.style.cssText = "color:var(--text-secondary);font-style:italic";
  contentEl.textContent = text;
  body.appendChild(contentEl);
  const wrapInner = document.createElement("div");
  wrapInner.className = "message-wrap";
  wrapInner.appendChild(avatar);
  wrapInner.appendChild(body);
  wrap.appendChild(wrapInner);
  $.messages.appendChild(wrap);
  scrollToBottom();
}

// ============ 发送 / 流式 ============

async function send() {
  const text = $.input.value.trim();
  if (!text || state.isStreaming) return;

  let isNewThread = false;
  if (!state.threadId) {
    const t = await apiCreateThread(state.graphId);
    state.threadId = t.thread_id;
    localStorage.setItem("air-agent:thread:" + state.graphId, t.thread_id);
    isNewThread = true;
  }

  if (isNewThread) {
    state.threads.unshift({
      thread_id: state.threadId,
      graph_id: state.graphId,
      title: text.slice(0, 30),
      created_at: Date.now() / 1000,
      updated_at: Date.now() / 1000,
      step_count: 0,
    });
    updateChatTitle(text.slice(0, 30));
  } else {
    const t = state.threads.find((x) => x.thread_id === state.threadId);
    if (t) {
      if (!t.title) {
        t.title = text.slice(0, 30);
        updateChatTitle(t.title);
      }
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

  const { body: assistantBody, contentEl, wrap: assistantWrap } = appendMessage("assistant", "", true);
  // 记录当前流式 AI 消息引用, tool calls 嵌入此 body
  state._curAssistant = { body: assistantBody, contentEl, wrap: assistantWrap, pendingToolDetails: [], rawText: "" };

  try {
    const r = await fetch(
      `/api/threads/${state.threadId}/runs/stream?graph_id=${encodeURIComponent(state.graphId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ graph_id: state.graphId, content: text }),
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
      const lines = buffer.split("\n\n");
      buffer = lines.pop() || "";
      for (const chunk of lines) {
        const line = chunk.replace(/^data: /, "").trim();
        if (!line) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.type === "text") {
            // 有新文本来了, 上一个 tool 必然完成了 → 标记完成
            markPendingToolsDone("已调用");
            state._curAssistant.rawText += obj.content;
            // 流式也走 MD, completeUnclosedFences 补齐未闭合的 ``` 围栏
            state._curAssistant.contentEl.innerHTML = renderMarkdown(state._curAssistant.rawText, { streaming: true });
            bindCodeCopyButtons(state._curAssistant.contentEl);
            scrollToBottom();
          } else if (obj.type === "tool_call") {
            appendToolCall(obj.name, obj.args);
          } else if (obj.type === "error") {
            markPendingToolsDone("失败");
            appendSystemMessage("流式错误: " + obj.message, "error");
          } else if (obj.type === "done") {
            markPendingToolsDone("完成");
          }
        } catch (e) {
          console.warn("SSE 解析失败:", line, e);
        }
      }
    }

    // 流式结束: 把当前 rawText → markdown 渲染; 加 actions
    markPendingToolsDone("完成");
    const finalText = state._curAssistant.rawText || "";
    state._curAssistant.contentEl.innerHTML = renderMarkdown(finalText);
    bindCodeCopyButtons(state._curAssistant.contentEl);
    state._curAssistant.wrap.classList.remove("streaming");

    // 移除旧 actions (流式时没加, 保险起见) + 加新的
    const oldActions = state._curAssistant.body.querySelector(":scope > .message-actions");
    if (oldActions) oldActions.remove();
    const actions = document.createElement("div");
    actions.className = "message-actions";
    const copyBtn = mkIconBtn("复制", ICON_COPY, () => {
      navigator.clipboard.writeText(finalText);
      flashIcon(copyBtn);
    });
    const retryBtn = mkIconBtn("重试", ICON_RETRY, () => retryLastUserMessage());
    actions.appendChild(copyBtn);
    actions.appendChild(retryBtn);
    state._curAssistant.body.appendChild(actions);
  } catch (e) {
    markPendingToolsDone("已中断");
    if (e.name === "AbortError") {
      appendSystemMessage("已停止", "system");
    } else {
      appendSystemMessage("出错: " + e.message, "error");
    }
  } finally {
    setStreaming(false);
    state.controller = null;
    state._curAssistant = null;
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
  updateChatTitle("");
  renderHistory(null);

  state.threads.unshift({
    thread_id: t.thread_id,
    graph_id: state.graphId,
    title: "",
    created_at: Date.now() / 1000,
    updated_at: Date.now() / 1000,
    step_count: 0,
  });
  renderThreadList();
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
  const collapsed = localStorage.getItem("air-agent:sidebar-collapsed") === "1";
  if (collapsed && window.innerWidth > 768) {
    $.sidebar.classList.add("collapsed");
  }

  try {
    await Promise.race([
      waitForLibs(),
      new Promise((resolve) => setTimeout(resolve, 8000)),
    ]);
  } catch (e) {
    console.warn("Markdown 库加载失败, 将以纯文本渲染:", e);
  }

  try {
    state.graphs = await apiListGraphs();
  } catch (e) {
    console.warn("加载图列表失败, 使用默认:", e);
    state.graphs = ["basic-qa"];
  }
  const savedGraph = localStorage.getItem("air-agent:graph");
  if (savedGraph && state.graphs.includes(savedGraph)) {
    state.graphId = savedGraph;
  }
  renderGraphPicker();

  try {
    await refreshThreadList();
  } catch (e) {
    console.warn("加载会话列表失败:", e);
    state.threads = [];
    renderThreadList();
  }

  const saved = localStorage.getItem("air-agent:thread:" + state.graphId);
  if (saved && state.threads.some((t) => t.thread_id === saved)) {
    await switchThread(saved);
  } else {
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

$.graphCurrent.addEventListener("click", (e) => {
  e.stopPropagation();
  $.graphDropdown.hidden = !$.graphDropdown.hidden;
});
document.addEventListener("click", (e) => {
  if (!$.graphPicker.contains(e.target)) $.graphDropdown.hidden = true;
});

$.newChatBtn.addEventListener("click", startNewChat);

$.themeBtn.addEventListener("click", toggleTheme);

$.collapseBtn.addEventListener("click", toggleSidebar);
$.sidebarToggle.addEventListener("click", toggleSidebar);
$.sidebarMask.addEventListener("click", () => setSidebarCollapsed(true));

$.searchInput.addEventListener("input", (e) => {
  state.searchKeyword = e.target.value;
  renderThreadList();
});

$.sendBtn.addEventListener("click", () => {
  if (state.isStreaming) stopStream();
  else send();
});

$.input.addEventListener("input", () => autoResize($.input));
$.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!state.isStreaming) send();
  } else if (e.key === "Escape" && state.isStreaming) {
    stopStream();
  }
});

$.scrollBottomBtn.addEventListener("click", () => {
  state.userPinnedToTop = false;
  scrollToBottom(true);
});

$.messages.addEventListener("scroll", () => {
  const atBottom = isScrolledToBottom();
  state.userPinnedToTop = !atBottom;
  updateScrollBottomBtn();
  $.mainHeader.classList.toggle("has-border", $.messages.scrollTop > 4);
});

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

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "k") {
    e.preventDefault();
    startNewChat();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "b") {
    e.preventDefault();
    toggleSidebar();
    return;
  }
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (ev) => {
  if (!localStorage.getItem("air-agent:theme")) {
    applyTheme(ev.matches ? "dark" : "light");
  }
});

init();
