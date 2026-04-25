const history = document.getElementById('chat-history');
const input = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const newChatBtn = document.getElementById('new-chat-btn');
const toolStatus = document.getElementById('tool-status');
const toolStatusText = document.getElementById('tool-status-text');
const chatList = document.getElementById('chat-list');
const chatTitleEl = document.getElementById('chat-title');

const CSRF = document.querySelector('meta[name="csrf-token"]').content;

let messages = [];
let toolCalls = [];
let chatId = null;

function scrollBottom() {
  history.scrollTop = history.scrollHeight;
}

function escHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function clearHistory() {
  history.innerHTML = '';
}

function showEmptyHint() {
  clearHistory();
  const el = document.createElement('div');
  el.className = 'text-muted text-center small fst-italic mt-auto pt-3';
  el.id = 'empty-hint';
  el.textContent = 'Send a message to start chatting with Claude.';
  history.appendChild(el);
}

function appendBubble(role, text) {
  const empty = document.getElementById('empty-hint');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = 'msg-bubble ' + (role === 'user' ? 'msg-user' : 'msg-assistant');
  el.textContent = text;
  history.appendChild(el);
}

function appendToolBlock(command, output) {
  const empty = document.getElementById('empty-hint');
  if (empty) empty.remove();

  const out = String(output || '');
  const collapsed = out.length > 300;
  const el = document.createElement('div');
  el.className = 'msg-tool';
  el.innerHTML = `
    <div class="tool-header">
      <i class="bi bi-terminal-fill text-secondary"></i>
      <code style="font-size:0.8rem">${escHtml(command.substring(0, 80))}${command.length > 80 ? '…' : ''}</code>
      <i class="bi bi-chevron-${collapsed ? 'down' : 'up'} ms-auto small"></i>
    </div>
    <div class="tool-body" style="display:${collapsed ? 'none' : ''}"><code>${escHtml(out)}</code></div>
  `;
  el.querySelector('.tool-header').addEventListener('click', () => {
    const body = el.querySelector('.tool-body');
    body.style.display = body.style.display === 'none' ? '' : 'none';
  });
  history.appendChild(el);
}

function renderTimeline() {
  clearHistory();
  if (!messages || messages.length === 0) {
    showEmptyHint();
    return;
  }

  // Map tool_use_id -> output by walking tool_result blocks
  const toolOutputs = {};
  for (const m of messages) {
    if (m.role === 'user' && Array.isArray(m.content)) {
      for (const b of m.content) {
        if (b && b.type === 'tool_result') {
          toolOutputs[b.tool_use_id] = typeof b.content === 'string'
            ? b.content
            : JSON.stringify(b.content);
        }
      }
    }
  }

  for (const m of messages) {
    if (m.role === 'user') {
      if (typeof m.content === 'string') {
        appendBubble('user', m.content);
      }
      // tool_result-only user messages are folded into the matching tool block
    } else if (m.role === 'assistant') {
      const blocks = Array.isArray(m.content) ? m.content : [];
      for (const b of blocks) {
        if (b.type === 'text') {
          if (b.text && b.text.trim()) appendBubble('assistant', b.text);
        } else if (b.type === 'tool_use') {
          const cmd = (b.input && b.input.command) || '';
          const out = toolOutputs[b.id] || '';
          appendToolBlock(cmd, out);
        }
      }
    }
  }
  scrollBottom();
}

function setLoading(on) {
  sendBtn.disabled = on;
  input.disabled = on;
  toolStatus.style.display = on ? '' : 'none';
  if (on) toolStatusText.textContent = 'Thinking…';
}

function setActiveChat(id) {
  chatId = id;
  for (const el of chatList.querySelectorAll('.chat-item')) {
    el.classList.toggle('active', el.dataset.chatId === id);
  }
}

function setChatTitle(title) {
  chatTitleEl.textContent = title || 'New chat';
}

function startNewChat() {
  chatId = null;
  messages = [];
  toolCalls = [];
  setChatTitle('New chat');
  for (const el of chatList.querySelectorAll('.chat-item')) {
    el.classList.remove('active');
  }
  showEmptyHint();
  input.focus();
}

async function fetchChats() {
  try {
    const resp = await fetch('/assistant/chats', { headers: { 'X-CSRFToken': CSRF } });
    if (!resp.ok) return;
    const data = await resp.json();
    renderChatList(data.chats || []);
  } catch (e) {
    // best-effort
  }
}

function renderChatList(chats) {
  chatList.innerHTML = '';
  if (!chats.length) {
    const empty = document.createElement('div');
    empty.id = 'chat-list-empty';
    empty.className = 'text-muted small fst-italic p-3 text-center';
    empty.textContent = 'No past chats yet.';
    chatList.appendChild(empty);
    return;
  }

  for (const c of chats) {
    const row = document.createElement('div');
    row.className = 'chat-item';
    row.dataset.chatId = c.id;
    if (c.id === chatId) row.classList.add('active');

    const title = document.createElement('span');
    title.className = 'chat-item-title';
    title.textContent = c.title || '(untitled)';
    title.title = c.title || '';

    const del = document.createElement('button');
    del.className = 'chat-item-delete';
    del.type = 'button';
    del.title = 'Delete chat';
    del.innerHTML = '<i class="bi bi-trash"></i>';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteChat(c.id);
    });

    row.addEventListener('click', () => loadChat(c.id));

    row.appendChild(title);
    row.appendChild(del);
    chatList.appendChild(row);
  }
}

async function loadChat(id) {
  try {
    const resp = await fetch(`/assistant/chats/${encodeURIComponent(id)}`, {
      headers: { 'X-CSRFToken': CSRF },
    });
    if (!resp.ok) throw new Error('Failed to load chat');
    const data = await resp.json();
    chatId = data.id;
    messages = data.messages || [];
    toolCalls = data.tool_calls || [];
    setChatTitle(data.title);
    setActiveChat(chatId);
    renderTimeline();
  } catch (e) {
    appendBubble('assistant', 'Error loading chat: ' + e.message);
  }
}

async function deleteChat(id) {
  if (!confirm('Delete this chat?')) return;
  try {
    const resp = await fetch(`/assistant/chats/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      headers: { 'X-CSRFToken': CSRF },
    });
    if (!resp.ok) throw new Error('Failed to delete');
    if (id === chatId) startNewChat();
    await fetchChats();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

sendBtn.addEventListener('click', sendMessage);
newChatBtn.addEventListener('click', startNewChat);
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

fetchChats();

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  setLoading(true);

  // Optimistic: show user bubble immediately while the server runs the loop
  appendBubble('user', text);
  scrollBottom();

  try {
    const resp = await fetch('/assistant/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CSRF,
      },
      body: JSON.stringify({ chat_id: chatId, message: text }),
    });

    if (!resp.ok) throw new Error('Server error ' + resp.status);
    const data = await resp.json();

    chatId = data.chat_id;
    messages = data.messages || [];
    toolCalls = data.tool_calls || [];
    setChatTitle(data.title);
    renderTimeline();

    if (data.error) {
      appendBubble('assistant', 'Error: ' + data.error);
      scrollBottom();
    }

    await fetchChats();
    setActiveChat(chatId);
  } catch (e) {
    appendBubble('assistant', 'Error: ' + e.message);
    scrollBottom();
  } finally {
    setLoading(false);
    input.focus();
  }
}
