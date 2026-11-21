"""`mascot-jev` コマンドの定義を検証する。"""

from importlib.metadata import entry_points

from sphinx_revealjs_mascot_jev import cli


def test_mascot_jev_command_calls_cli_main() -> None:
    (entry_point,) = entry_points(group='console_scripts', name='mascot-jev')

    assert entry_point.load() is cli.main
