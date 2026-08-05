// Air Agent 前端逻辑
// 极简实现: 不引第三方框架, fetch + ReadableStream 消费 SSE

const state = {
  threadId: null,
  graphId: null,
  busy: false,
  controller: null,
  currentAssistantMsg: null,
};

// ============ DOM ============
const el = (id) => document.getElementById(id);
const messagesEl = el("messages");
const inputEl = el("input");
const sendBtn = el("send-btn");
const stopBtn = el("stop-btn");
const newChatBtn = el("new-chat-btn");
const graphSelect = el("graph-select");
const threadIdDisplay = el("thread-id-display");
const emptyState = el("empty-state");

// ============ Init ============
async function init() {
  await loadGraphs();
  // 默认开启新对话
  await startNewChat();
}

async function loadGraphs() {
  try {
    const res = await fetch("/api/graphs");
    const { graphs } = await res.json();
    graphSelect.innerHTML = "";
    for (const gid of graphs) {
      const opt = document.createElement("option");
      opt.value = gid;
      opt.textContent = gid;
      graphSelect.appendChild(opt);
    }
    if (graphs.length > 0) {
      state.graphId = graphs[0];
      graphSelect.value = state.graphId;
    }
  } catch (err) {
    appendSystemMessage("加载图列表失败: " + err);
  }
  graphSelect.addEventListener("change", () => {
    state.graphId = graphSelect.value;
  });
}

// ============ Thread ============
async function startNewChat() {
  if (state.busy) return;
  const res = await fetch("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph_id: state.graphId }),
  });
  const data = await res.json();
  state.threadId = data.thread_id;
  threadIdDisplay.textContent = state.threadId;
  messagesEl.innerHTML = "";
  emptyState.style.display = "block";
  messagesEl.appendChild(emptyState);
  inputEl.focus();
}

async function loadHistory() {
  if (!state.threadId || !state.graphId) return;
  try {
    const res = await fetch(
      `/api/threads/${state.threadId}/history?graph_id=${state.graphId}`
    );
    if (!res.ok) return;
    const data = await res.json();
    messagesEl.innerHTML = "";
    for (const m of data.messages || []) {
      appendMessage(m.role, m.content, false);
    }
  } catch (err) {
    // 首次对话时 404 是正常的
    console.debug("history empty or error:", err);
  }
}

// ============ Message UI ============
function appendMessage(role, content, streaming = false) {
  if (emptyState.parentNode) emptyState.remove();
  const div = document.createElement("div");
  div.className = `message ${role}${streaming ? " streaming" : ""}`;
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role === "user" ? "你" : "AI";
  const contentEl = document.createElement("div");
  contentEl.className = "content";
  contentEl.textContent = content;
  div.appendChild(roleEl);
  div.appendChild(contentEl);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return contentEl;
}

function appendSystemMessage(text) {
  if (emptyState.parentNode) emptyState.remove();
  const div = document.createElement("div");
  div.className = "message system";
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// ============ Send & Stream ============
async function send() {
  const content = inputEl.value.trim();
  if (!content || state.busy) return;
  if (!state.threadId) await startNewChat();

  // 显示用户消息
  appendMessage("user", content);
  inputEl.value = "";

  // 准备 assistant 流式消息
  const assistantContent = appendMessage("assistant", "", true);
  state.currentAssistantMsg = assistantContent;
  state.busy = true;
  sendBtn.style.display = "none";
  stopBtn.style.display = "inline-block";

  state.controller = new AbortController();
  try {
    const res = await fetch(
      `/api/threads/${state.threadId}/runs/stream`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          graph_id: state.graphId,
          content: content,
        }),
        signal: state.controller.signal,
      }
    );

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // 按行处理 SSE
      let nl;
      while ((nl = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 2);
        const line = chunk.trim();
        if (line.startsWith("data:")) {
          const payload = line.slice(5).trim();
          if (payload === "[DONE]" || payload === "") continue;
          try {
            const obj = JSON.parse(payload);
            handleEvent(obj, assistantContent);
          } catch (e) {
            console.warn("解析 SSE payload 失败:", e, payload);
          }
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      assistantContent.textContent += "\n\n[已停止]";
    } else {
      appendSystemMessage("错误: " + err.message);
    }
  } finally {
    if (assistantContent) assistantContent.classList.remove("streaming");
    state.busy = false;
    state.currentAssistantMsg = null;
    sendBtn.style.display = "inline-block";
    stopBtn.style.display = "none";
    inputEl.focus();
  }
}

function handleEvent(obj, contentEl) {
  if (obj.type === "message" && obj.content) {
    contentEl.textContent += obj.content;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  } else if (obj.type === "text" && obj.content) {
    // v3 events: 逐 token 流式追加
    contentEl.textContent += obj.content;
    messagesEl.scrollTop = messagesEl.scrollHeight;
  } else if (obj.type === "tool_call") {
    appendSystemMessage(`[tool] ${obj.name}(${JSON.stringify(obj.args)})`);
  } else if (obj.type === "node_start") {
    appendSystemMessage(`▶ ${obj.name || "节点"}`);
  } else if (obj.type === "node_end") {
    // skip noise
  } else if (obj.type === "done") {
    // finished
  } else if (obj.type === "error") {
    appendSystemMessage("流式错误: " + obj.message);
  }
}

function stopStream() {
  if (state.controller) state.controller.abort();
}

// ============ Event Bindings ============
newChatBtn.addEventListener("click", startNewChat);
sendBtn.addEventListener("click", send);
stopBtn.addEventListener("click", stopStream);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

init();
