import subprocess
import requests as http_requests
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from web_app.config import ConfigManager
from web_app.assistant.data_interface import DataInterface


SYSTEM_PROMPT = (
    f"You are a coding assistant for the nabicat project at {ConfigManager().project_dir}. "
    "Use the bash tool freely to read/write files, run tests, and execute git commands "
    "(including commits and pushes). Act directly — do not describe what you would do. "
    "Do not begin responses with 'CLAUDE.md read!'."
)

TOOLS = [{
    "name": "bash",
    "description": (
        f"Execute a bash command. Working directory is {ConfigManager().project_dir}. "
        "Use for reading/writing files, running tests, git operations, etc."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run"}
        },
        "required": ["command"]
    }
}]

HEADERS = {
    "Content-Type": "application/json",
    "x-meridian-agent": "opencode",
}

assistant_api = Blueprint(
    'assistant',
    __name__,
    template_folder='templates',
    static_folder='static',
    url_prefix='/assistant'
)


@assistant_api.before_request
@login_required
def before_request():
    if not current_user.is_admin:
        abort(403)


@assistant_api.context_processor
def inject_app_name():
    return dict(app_name='Assistant')


@assistant_api.route('/')
def index():
    return render_template('assistant_index.html')


def _run(command: str) -> str:
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True,
            text=True, timeout=60, cwd=str(ConfigManager().project_dir)
        )
        out = proc.stdout
        if proc.stderr:
            out += '\n' + proc.stderr
        return out.strip() or '(no output)'
    except subprocess.TimeoutExpired:
        return 'Command timed out after 60s'


def _call_meridian(messages: list) -> dict:
    config = ConfigManager()
    resp = http_requests.post(config.assistant_meridian_url, headers=HEADERS, json={
        "model": config.assistant_model,
        "max_tokens": config.assistant_max_tokens,
        "system": SYSTEM_PROMPT,
        "tools": TOOLS,
        "messages": messages,
    }, timeout=120)
    if not resp.ok:
        raise RuntimeError(f"meridian {resp.status_code}: {resp.text}")
    return resp.json()


@assistant_api.route('/chats', methods=['GET'])
def list_chats():
    di = DataInterface()
    chats = di.list_chats(current_user)
    return jsonify({"chats": [c.model_dump() for c in chats]})


@assistant_api.route('/chats/<chat_id>', methods=['GET'])
def get_chat(chat_id: str):
    di = DataInterface()
    chat = di.load_chat(current_user, chat_id)
    if chat is None:
        abort(404)
    return jsonify(chat.model_dump())


@assistant_api.route('/chats/<chat_id>', methods=['DELETE'])
def delete_chat(chat_id: str):
    DataInterface().delete_chat(current_user, chat_id)
    return jsonify({"ok": True})


@assistant_api.route('/chat', methods=['POST'])
def chat():
    data = request.get_json()
    chat_id = data.get('chat_id')
    user_message = data.get('message', '').strip()

    di = DataInterface()
    if chat_id:
        stored = di.load_chat(current_user, chat_id)
        if stored is None:
            abort(404)
    else:
        stored = di.create_chat(current_user, title=user_message or "New chat")

    messages = stored.messages
    tool_call_log = stored.tool_calls

    if user_message:
        messages.append({"role": "user", "content": user_message})

    # Agentic loop: keep calling until stop_reason != "tool_use"
    while True:
        try:
            result = _call_meridian(messages)
        except Exception as e:
            stored.messages = messages
            stored.tool_calls = tool_call_log
            di.save_chat(current_user, stored)
            return jsonify({
                "chat_id": stored.id,
                "title": stored.title,
                "messages": messages,
                "tool_calls": tool_call_log,
                "error": str(e),
            }), 200

        content_blocks = result.get('content', [])
        stop_reason = result.get('stop_reason')

        messages.append({"role": "assistant", "content": content_blocks})

        if stop_reason != 'tool_use':
            break

        tool_results = []
        for block in content_blocks:
            if block.get('type') != 'tool_use':
                continue
            name = block.get('name')
            tool_input = block.get('input', {})
            tool_use_id = block.get('id')

            if name == 'bash':
                output = _run(tool_input.get('command', ''))
            else:
                output = f"Unknown tool: {name}"

            tool_call_log.append({
                "command": tool_input.get('command', ''),
                "output": output,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": output,
            })

        messages.append({"role": "user", "content": tool_results})

    stored.messages = messages
    stored.tool_calls = tool_call_log
    di.save_chat(current_user, stored)

    return jsonify({
        "chat_id": stored.id,
        "title": stored.title,
        "messages": messages,
        "tool_calls": tool_call_log,
    })
