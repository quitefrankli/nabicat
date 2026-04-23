const history = document.getElementById('chat-history');
const input = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const toolStatus = document.getElementById('tool-status');
const toolStatusText = document.getElementById('tool-status-text');

let messages = [];

function scrollBottom() {
  history.scrollTop = history.scrollHeight;
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function appendBubble(role, text) {
  const empty = document.getElementById('empty-hint');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = 'msg-bubble ' + (role === 'user' ? 'msg-user' : 'msg-assistant');
  el.textContent = text;
  history.appendChild(el);
  scrollBottom();
}

function appendToolBlock(command, output) {
  const collapsed = output.length > 300;
  const el = document.createElement('div');
  el.className = 'msg-tool';
  el.innerHTML = `
    <div class="tool-header" onclick="this.nextElementSibling.style.display = this.nextElementSibling.style.display === 'none' ? '' : 'none'">
      <i class="bi bi-terminal-fill text-secondary"></i>
      <code style="font-size:0.8rem">${escHtml(command.substring(0, 80))}${command.length > 80 ? '…' : ''}</code>
      <i class="bi bi-chevron-${collapsed ? 'down' : 'up'} ms-auto small"></i>
    </div>
    <div class="tool-body" style="display:${collapsed ? 'none' : ''}"><code>${escHtml(output)}</code></div>
  `;
  history.appendChild(el);
  scrollBottom();
}

function setLoading(on) {
  sendBtn.disabled = on;
  input.disabled = on;
  toolStatus.style.display = on ? '' : 'none';
  if (on) toolStatusText.textContent = 'Thinking…';
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  appendBubble('user', text);
  setLoading(true);

  try {
    const resp = await fetch('/assistant/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content,
      },
      body: JSON.stringify({ messages, message: text }),
    });

    if (!resp.ok) throw new Error('Server error ' + resp.status);
    const data = await resp.json();

    messages = data.messages;

    for (const tc of data.tool_calls || []) {
      appendToolBlock(tc.command, tc.output);
    }

    const last = messages[messages.length - 1];
    if (last && last.role === 'assistant') {
      const text = Array.isArray(last.content)
        ? last.content.filter(b => b.type === 'text').map(b => b.text).join('\n').trim()
        : (last.content || '').toString().trim();
      if (text) appendBubble('assistant', text);
    }
  } catch (e) {
    appendBubble('assistant', 'Error: ' + e.message);
  } finally {
    setLoading(false);
    input.focus();
  }
}

input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
