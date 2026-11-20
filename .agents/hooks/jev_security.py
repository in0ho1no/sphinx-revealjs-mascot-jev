"""Antigravity lifecycle hooks adapter for the repository's Jev credential policy."""

import json
import os
import re
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[2]
_CODEX_HOOK = _REPOSITORY / '.codex' / 'hooks' / 'jev_security.py'
_SHARED_HOOK = _REPOSITORY / '.github' / 'hooks' / 'entrypoint.py'

_codex_namespace = runpy.run_path(str(_CODEX_HOOK))
_KEY_NAME: str = _codex_namespace['_KEY_NAME']
_KEY_ACCESS: re.Pattern[str] = _codex_namespace['_KEY_ACCESS']
_ENVIRONMENT_DUMP: re.Pattern[str] = _codex_namespace['_ENVIRONMENT_DUMP']
_contains_named_key_value: Callable[[object], bool] = _codex_namespace['contains_named_key_value']

# PowerShell の Env:\ パス記法および cmd.exe の引数付き set による直接参照も補完する
_EXTRA_KEY_ACCESS = re.compile(
    rf"""
    \benv:[\\/]\s*{_KEY_NAME}\b
    | (?:^|[;&|\r\n])\s*set\s+{_KEY_NAME}\s*(?=$|[;&|>\r\n])
    """,
    re.IGNORECASE | re.VERBOSE,
)

_COMMAND_KEYS = frozenset({'command', 'commandline', 'cmd'})
_EXTRA_CONTENT_KEYS = frozenset({'codecontent', 'replacementcontent'})
_POST_RESULT_KEYS = (
    'toolResponse',
    'tool_response',
    'toolResult',
    'tool_result',
    'output',
    'result',
    'response',
    'stdout',
    'stderr',
    'error',
)
_NESTED_RESULT_KEYS = ('output', 'result', 'response', 'toolResponse', 'toolResult', 'error')

_SESSION_GUIDANCE = (
    '実際の TYPESAFE_API_KEY は発表用の Python サーバープロセスにだけ渡してください。'
    '開発・テスト（Antigravity・CI など）ではダミー値を使ってください。'
    '認証情報の扱いと送信先はリポジトリの AGENTS.md に従ってください。'
)
_REASON_INVALID_PRE_INPUT = 'Antigravity Hook のツール入力形式が不正なため実行を止めました。'
_REASON_KEY_OR_ENV_ACCESS = 'Antigravity からの Jev API キーへの直接参照、または環境変数の一括表示を止めました。'
_REASON_NAMED_KEY_IN_INPUT = 'ツール入力に Jev API キーらしい値が見つかりました。値を伏字にするかダミー値を使ってください。'
_REASON_SHARED_PRE_BLOCKED = 'リポジトリのセキュリティ検査が、機密ファイルへのアクセスまたは不審な操作・内容を検出しました。'
_REASON_SHARED_PRE_ERROR = 'リポジトリのセキュリティ検査を実行できなかったため、安全のため操作を止めました。'
_REASON_NAMED_KEY_IN_OUTPUT = 'ツール出力に Jev API キーらしい値が見つかりました。後続の処理では値を伏字にしてください。'
_REASON_SHARED_POST_WARNED = 'リポジトリのセキュリティ検査が、ツール出力に機密情報または不審な内容を検出しました。'
_REASON_SHARED_POST_ERROR = 'リポジトリのセキュリティ検査でツール出力の確認を完了できませんでした。'


def emit_json(payload: dict[str, object]) -> None:
    """Write a JSON response object to stdout for Antigravity."""
    print(json.dumps(payload, ensure_ascii=False))


def fail_stderr(reason: str) -> int:
    """Write a fixed warning message to stderr and return exit code 2."""
    print(reason, file=sys.stderr)
    return 2


def deny_pre_tool(reason: str) -> int:
    """Return a PreToolUse deny response with a fixed reason."""
    emit_json({'decision': 'deny', 'reason': reason})
    return 0


def collect_commands(value: object) -> tuple[list[str], bool]:
    """Extract shell command strings and flag non-string command values."""
    commands: list[str] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, nested in current.items():
                normalized = key.replace('_', '').lower()
                if normalized in _COMMAND_KEYS:
                    if not isinstance(nested, str):
                        return [], True
                    commands.append(nested)
                else:
                    pending.append(nested)
        elif isinstance(current, list):
            pending.extend(current)
    return commands, False


def collect_extra_contents(value: object) -> list[str]:
    """Collect written text under Antigravity-specific content argument keys."""
    contents: list[str] = []
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, nested in current.items():
                if key.replace('_', '').lower() in _EXTRA_CONTENT_KEYS and isinstance(nested, str):
                    contents.append(nested)
                else:
                    pending.append(nested)
        elif isinstance(current, list):
            pending.extend(current)
    return contents


def build_shared_pre_payload(tool_name: str, args: dict[str, object], commands: list[str]) -> bytes:
    """Adapt Antigravity toolCall fields to the shared pre-tool inspector schema."""
    normalized_input: dict[str, object] = dict(args)
    if commands and 'command' not in normalized_input:
        normalized_input['command'] = '\n'.join(commands)
    extra_contents = collect_extra_contents(args)
    if extra_contents and 'newcontent' not in normalized_input:
        normalized_input['newcontent'] = '\n'.join(extra_contents)
    if 'Includes' in args and '_include_paths' not in normalized_input:
        normalized_input['_include_paths'] = args['Includes']
    return json.dumps({'tool_name': tool_name, 'tool_input': normalized_input}, ensure_ascii=False).encode('utf-8')


def run_shared_inspector(phase: str, payload: bytes) -> int | None:
    """Invoke the shared repository hook with audit logging and secret env disabled."""
    environment = os.environ.copy()
    environment.pop(_KEY_NAME, None)
    environment['HOOK_NO_LOG'] = '1'
    try:
        result = subprocess.run(
            [sys.executable, '-IB', str(_SHARED_HOOK), phase],
            input=payload,
            cwd=_REPOSITORY,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    return result.returncode


def extract_post_output(event: dict[str, object]) -> dict[str, object]:
    """Collect only tool execution output fields present in the PostToolUse event."""
    extracted: dict[str, object] = {}
    for key in _POST_RESULT_KEYS:
        value = event.get(key)
        if value not in (None, '', {}, []):
            extracted[key] = value
    tool_call = event.get('toolCall')
    if isinstance(tool_call, dict):
        for key in _NESTED_RESULT_KEYS:
            value = tool_call.get(key)
            if value not in (None, '', {}, []):
                extracted[f'toolCall.{key}'] = value
    return extracted


def handle_pre_tool_use(event: dict[str, object]) -> int:
    """Inspect a PreToolUse event and output an allow/deny JSON decision."""
    tool_call = event.get('toolCall')
    if not isinstance(tool_call, dict):
        return deny_pre_tool(_REASON_INVALID_PRE_INPUT)
    tool_name = tool_call.get('name')
    if not isinstance(tool_name, str) or not tool_name:
        return deny_pre_tool(_REASON_INVALID_PRE_INPUT)
    raw_args = tool_call.get('args', {})
    if not isinstance(raw_args, dict):
        return deny_pre_tool(_REASON_INVALID_PRE_INPUT)

    commands, invalid_command = collect_commands(raw_args)
    if invalid_command:
        return deny_pre_tool(_REASON_INVALID_PRE_INPUT)
    for command in commands:
        if _KEY_ACCESS.search(command) or _EXTRA_KEY_ACCESS.search(command) or _ENVIRONMENT_DUMP.search(command):
            return deny_pre_tool(_REASON_KEY_OR_ENV_ACCESS)

    if _contains_named_key_value(raw_args):
        return deny_pre_tool(_REASON_NAMED_KEY_IN_INPUT)

    shared_code = run_shared_inspector('pre', build_shared_pre_payload(tool_name, raw_args, commands))
    if shared_code == 2:
        return deny_pre_tool(_REASON_SHARED_PRE_BLOCKED)
    if shared_code != 0:
        return deny_pre_tool(_REASON_SHARED_PRE_ERROR)

    emit_json({'decision': 'allow'})
    return 0


def handle_post_tool_use(event: dict[str, object]) -> int:
    """Inspect PostToolUse output fields when present in the hook payload."""
    tool_call = event.get('toolCall')
    tool_name = tool_call.get('name') if isinstance(tool_call, dict) else ''
    if not isinstance(tool_name, str):
        return fail_stderr(_REASON_SHARED_POST_ERROR)

    extracted_output = extract_post_output(event)
    if not extracted_output:
        emit_json({})
        return 0

    if _contains_named_key_value(extracted_output):
        return fail_stderr(_REASON_NAMED_KEY_IN_OUTPUT)

    shared_payload = json.dumps(
        {'tool_name': tool_name, 'tool_response': extracted_output},
        ensure_ascii=False,
    ).encode('utf-8')
    shared_code = run_shared_inspector('post', shared_payload)
    if shared_code == 2:
        return fail_stderr(_REASON_SHARED_POST_WARNED)
    if shared_code != 0:
        return fail_stderr(_REASON_SHARED_POST_ERROR)

    emit_json({})
    return 0


def main() -> int:
    """Dispatch a supported Antigravity lifecycle hook event."""
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')  # type: ignore[union-attr]
    input_bytes = sys.stdin.buffer.read()
    try:
        event = json.loads(input_bytes)
    except ValueError, RecursionError:
        return fail_stderr('Antigravity Hook の入力を読み取れませんでした。')
    if not isinstance(event, dict):
        return fail_stderr('Antigravity Hook の入力は JSON オブジェクトである必要があります。')

    event_name = event.get('hookEventName')
    if event_name == 'SessionStart':
        emit_json({'injectSteps': [{'systemMessage': {'systemMessage': _SESSION_GUIDANCE}}]})
        return 0
    if event_name == 'PreToolUse':
        return handle_pre_tool_use(event)
    if event_name == 'PostToolUse':
        return handle_post_tool_use(event)
    return fail_stderr('Antigravity Hook のイベントを認識できませんでした。')


if __name__ == '__main__':
    sys.exit(main())
