"""Codex lifecycle hooks for the repository's Jev credential policy."""

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[2]
_SHARED_HOOK = _REPOSITORY / '.github' / 'hooks' / 'entrypoint.py'
_KEY_NAME = 'TYPESAFE_API_KEY'
_KEY_VALUE = r'[A-Za-z0-9._+/=-]{16,}'
_NAMED_VALUE = re.compile(rf"""\b{_KEY_NAME}["']?\s*[:=]\s*["']?{_KEY_VALUE}""", re.IGNORECASE)
_VALUE = re.compile(_KEY_VALUE)

_WORD = r"""(?:"[^"]*"|'[^']*'|[^\s;&|<>()"'])+"""
_ASSIGNMENT = rf'[A-Za-z_]\w*=(?:{_WORD})?'
_PATH_PREFIX = r'(?:[\w.~:-]*[\\/])*'
_ARGUMENTS_END = r"""\s*(?=$|[;&|<>()\r\n"'])"""
_ENV_ARGUMENT = rf"""
    (?:-[uCS]|--(?:unset|chdir|split-string))\s+{_WORD}
    | -[^\s;&|<>()]*
    | {_ASSIGNMENT}
    | \d*[<>]+&?\s*{_WORD}
"""
_WRAPPER = rf"""
    {_PATH_PREFIX}(?:command|builtin|exec|sudo|doas|nohup|nice|time|stdbuf|xargs)(?:\.exe)?(?:\s+-[^\s;&|<>()]*)*\s+
    | {_PATH_PREFIX}env(?:\.exe)?(?:\s+(?:{_ENV_ARGUMENT}))*\s+
    | {_PATH_PREFIX}(?:cmd(?:\.exe)?\s+/[ck]|(?:ba|z|da|k)?sh(?:\.exe)?\s+-c|(?:pwsh|powershell)(?:\.exe)?\s+-(?:c|command))\s+["']?
"""
_COMMAND_START = rf"""(?:^|[;&|(\r\n])\s*(?:{_ASSIGNMENT}\s+)*(?:{_WRAPPER})*{_PATH_PREFIX}"""

# cmd.exe は引用符の中でも %NAME% を展開し、PowerShell の Env: パスは引用されていてもコマンドレットが読み取るため、引用符を除かずに検査する
_KEY_ACCESS_IN_ANY_QUOTING = re.compile(
    rf"""
    %{_KEY_NAME}%
    | \benv:[\\/]?\s*{_KEY_NAME}\b
    | {_COMMAND_START}set\s+{_KEY_NAME}{_ARGUMENTS_END}
    """,
    re.IGNORECASE | re.VERBOSE,
)
_ENVIRONMENT_DUMP = re.compile(
    rf"""
    {_COMMAND_START}(?:
        printenv(?:\.exe)?(?=$|[\s;&|<>()"'])
        | env(?:\.exe)?(?:\s+(?:{_ENV_ARGUMENT}))*{_ARGUMENTS_END}
        | (?:set|export(?:\s+-p)?|(?:declare|typeset)\s+-[a-z]*[px][a-z]*){_ARGUMENTS_END}
    )
    | \b(?:Get-ChildItem|gci|dir|ls|Get-Item|gi)\s+(?:-Path\s+)?["']?env:
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SHELL_EXPANSION = re.compile(rf'\$\{{?{_KEY_NAME}\b', re.IGNORECASE)
_CODE_ACCESS = re.compile(
    rf"""
    \b(?:getenv|GetEnvironmentVariable|environ\.get)\s*\(\s*["']{_KEY_NAME}["']
    | \b(?:environ|process\.env)\s*\[\s*["']{_KEY_NAME}["']
    | \bprocess\.env\.{_KEY_NAME}\b
    | \b(?:print|pprint)\s*\(\s*(?:dict\s*\(\s*)?os\.environ\b
    | \bconsole\.\w+\s*\(\s*process\.env\s*\)
    | \[(?:System\.)?Environment\]::GetEnvironmentVariables\s*\(
    """,
    re.IGNORECASE | re.VERBOSE,
)
_CODE_RUNNER = re.compile(
    rf"""
    (?:^|[\s;&|(])["']?{_PATH_PREFIX}
    (?:python[\d.]*|py|pypy[\d.]*|node|nodejs|deno|bun|perl|ruby|php|pwsh|powershell|cmd|(?:ba|z|da|k)?sh|fish|eval|iex|invoke-expression)
    (?:\.exe)?(?=$|[\s;&|<>()"'])
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


def _is_quoted_literal(word: str, quotes: str) -> bool:
    """Tell whether a shell word is entirely wrapped in one of the given quote characters."""
    return len(word) >= 2 and word[0] in quotes and word[-1] == word[0]


def _quoted_literal_views(command: str) -> tuple[str, str] | None:
    """Return the command without single-quoted words and without any quoted words."""
    # 括弧などで語を区切ると [Environment]::GetEnvironmentVariable('NAME') の引数だけが引用語として除かれるため、空白でだけ区切る
    lexer = shlex.shlex(command, posix=False)
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        words = list(lexer)
    except ValueError:
        return None
    without_single = ' '.join(word for word in words if not _is_quoted_literal(word, "'"))
    without_quoted = ' '.join(word for word in words if not _is_quoted_literal(word, '\'"'))
    return without_single, without_quoted


def is_restricted_command(command: str) -> bool:
    """Decide whether a shell command reads the Jev key or prints environment variables."""
    if _KEY_ACCESS_IN_ANY_QUOTING.search(command) or _ENVIRONMENT_DUMP.search(command):
        return True
    # コード実行コマンドは引用符の中身を実行し、引用符を解析できない場合は範囲を判断できないため、引用符を除かずに検査する
    views = None if _CODE_RUNNER.search(command) else _quoted_literal_views(command)
    expansion_target, code_target = views if views is not None else (command, command)
    return bool(_SHELL_EXPANSION.search(expansion_target) or _CODE_ACCESS.search(code_target))


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
        if isinstance(command, str) and is_restricted_command(command):
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
