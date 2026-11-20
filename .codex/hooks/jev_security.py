"""Codex lifecycle hooks for the repository's Jev credential policy."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[2]
_SHARED_HOOK = _REPOSITORY / '.github' / 'hooks' / 'entrypoint.py'
_KEY_NAME = 'TYPESAFE_API_KEY'
_KEY_VALUE = r'[A-Za-z0-9._+/=-]{16,}'
_NAMED_VALUE = re.compile(rf"""\b{_KEY_NAME}["']?\s*[:=]\s*["']?{_KEY_VALUE}""", re.IGNORECASE)
_VALUE = re.compile(_KEY_VALUE)
_KEY_ACCESS = re.compile(
    rf"""
    \$\{{?{_KEY_NAME}\b | %{_KEY_NAME}%
    | \benv:\s*{_KEY_NAME}\b
    | \b(?:getenv|GetEnvironmentVariable|environ\.get)\s*\(\s*["']{_KEY_NAME}["']
    | \b(?:environ|process\.env)\s*\[\s*["']{_KEY_NAME}["']
    | \bprocess\.env\.{_KEY_NAME}\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ENVIRONMENT_DUMP = re.compile(
    r"""
    (?:^|[;&|\r\n])\s*(?:printenv\b|(?:env|set)\s*(?=$|[;&|>\r\n]))
    | \b(?:Get-ChildItem|gci|dir|ls)\s+(?:-Path\s+)?["']?env:
    | \b(?:print|pprint)\s*\(\s*(?:dict\s*\(\s*)?os\.environ\b
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SESSION_GUIDANCE = (
    '実際の TYPESAFE_API_KEY は発表用の Python サーバープロセスにだけ渡してください。'
    'Codex と CI ではダミー値を使ってください。'
    '認証情報の扱いと送信先はリポジトリの AGENTS.md に従ってください。'
)


def contains_named_key_value(value: object) -> bool:
    """Find credential-like literals next to the Jev key name in text or structured values."""
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, str) and _NAMED_VALUE.search(current):
            return True
        if isinstance(current, dict):
            for name, item in current.items():
                if name.upper() == _KEY_NAME and isinstance(item, str) and _VALUE.fullmatch(item):
                    return True
                pending.append(item)
        elif isinstance(current, list):
            pending.extend(current)
    return False


def notice(message: str) -> int:
    """Provide a blocking reason or feedback without echoing the payload."""
    print(message, file=sys.stderr)
    return 2


def inspect_shared(phase: str, payload: bytes) -> int:
    """Run the existing repository checks with audit logging disabled."""
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
    except (OSError, subprocess.TimeoutExpired) as error:
        return notice(f'リポジトリのセキュリティ検査を実行できませんでした（{type(error).__name__}）。')
    if result.returncode == 2:
        return notice('リポジトリのセキュリティ検査が、機密情報や不審な操作・内容の可能性を検出しました。')
    if result.returncode != 0:
        return notice('リポジトリのセキュリティ検査が正常に完了しませんでした。')
    return 0


def main() -> int:
    """Dispatch a supported Codex hook event."""
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')
    input_bytes = sys.stdin.buffer.read()
    try:
        event = json.loads(input_bytes)
    except (ValueError, RecursionError) as error:
        return notice(f'Codex Hook の入力を読み取れませんでした（{type(error).__name__}）。')
    if not isinstance(event, dict):
        return notice('Codex Hook の入力は JSON オブジェクトである必要があります。')

    event_name = event.get('hook_event_name')
    if event_name == 'SessionStart':
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart', 'additionalContext': _SESSION_GUIDANCE}}))
        return 0
    if event_name == 'PreToolUse':
        tool_input = event.get('tool_input')
        command = tool_input.get('command') if isinstance(tool_input, dict) else None
        if isinstance(command, str) and (_KEY_ACCESS.search(command) or _ENVIRONMENT_DUMP.search(command)):
            return notice('Codex からの Jev API キーへのアクセス、または環境変数の一括表示を止めました。')
        if contains_named_key_value(tool_input):
            return notice('ツール入力に Jev API キーらしい値が見つかりました。値を伏字にしてください。')
        return inspect_shared('pre', input_bytes)
    if event_name == 'PostToolUse':
        if contains_named_key_value(event.get('tool_response')):
            return notice('ツール出力に Jev API キーらしい値が見つかりました。後続の処理では値を伏字にしてください。')
        return inspect_shared('post', input_bytes)
    return notice('Codex Hook のイベントを認識できませんでした。')


if __name__ == '__main__':
    sys.exit(main())
