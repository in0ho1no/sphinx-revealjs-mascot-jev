"""Verify the Codex hook protocol with synthetic inputs."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_HOOK = Path(__file__).with_name('jev_security.py')
_DUMMY_VALUE = 'synthetic_' + '0' * 24


def invoke(event: str, **fields: Any) -> subprocess.CompletedProcess[str]:
    """Send a sample event to the hook."""
    return subprocess.run(
        [sys.executable, '-IB', str(_HOOK)],
        input=json.dumps({'hook_event_name': event, **fields}),
        capture_output=True,
        encoding='utf-8',
        timeout=10,
        check=False,
    )


def test_session_start_delivers_credential_guidance() -> None:
    """SessionStart supplies guidance in the Codex response envelope."""
    result = invoke('SessionStart')
    assert result.returncode == 0
    context = json.loads(result.stdout)['hookSpecificOutput']
    assert context['hookEventName'] == 'SessionStart'
    assert 'TYPESAFE_API_KEY' in context['additionalContext']
    assert 'AGENTS.md' in context['additionalContext']


@pytest.mark.parametrize(
    'command',
    [
        'Get-Item Env:TYPESAFE_API_KEY',
        'Get-ChildItem Env:',
        'printenv TYPESAFE_API_KEY',
        'echo "$TYPESAFE_API_KEY"',
        'echo %TYPESAFE_API_KEY%',
        'node -p process.env.TYPESAFE_API_KEY',
    ],
)
def test_shell_credential_access_is_denied(command: str) -> None:
    """Common direct access and environment listing commands are denied."""
    result = invoke('PreToolUse', tool_name='Bash', tool_input={'command': command})
    assert result.returncode == 2
    assert result.stderr.strip()


def test_key_name_can_be_used_in_source_and_search() -> None:
    """Searches and app code can refer to the variable by name."""
    result = invoke('PreToolUse', tool_name='Bash', tool_input={'command': 'rg -n TYPESAFE_API_KEY docs'})
    assert result.returncode == 0
    patch = '*** Begin Patch\n*** Add File: example.py\n+key = os.getenv("TYPESAFE_API_KEY")\n*** End Patch\n'
    result = invoke('PreToolUse', tool_name='apply_patch', tool_input=patch)
    assert result.returncode == 0


def test_shared_sensitive_file_rule_is_applied() -> None:
    """The shared inspector rejects a patch that targets a secret file."""
    patch = '*** Begin Patch\n*** Add File: .env\n+example=dummy\n*** End Patch\n'
    result = invoke('PreToolUse', tool_name='apply_patch', tool_input=patch)
    assert result.returncode == 2


@pytest.mark.parametrize(
    ('event', 'fields'),
    [
        ('PreToolUse', {'tool_input': {'command': f'TYPESAFE_API_KEY={_DUMMY_VALUE}'}}),
        ('PostToolUse', {'tool_response': {'TYPESAFE_API_KEY': _DUMMY_VALUE}}),
        ('PostToolUse', {'tool_response': f'TYPESAFE_API_KEY={_DUMMY_VALUE}'}),
    ],
)
def test_named_key_values_are_flagged_without_echoing_data(event: str, fields: dict[str, Any]) -> None:
    """Credential-like values are omitted from tool feedback."""
    result = invoke(event, tool_name='Bash', **fields)
    assert result.returncode == 2
    assert result.stderr.strip()
    assert _DUMMY_VALUE not in result.stdout + result.stderr
