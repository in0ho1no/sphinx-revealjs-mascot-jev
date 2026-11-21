"""`mascot-jev` コマンドの定義、引数の解析、起動時の警告を検証する。"""

from importlib.metadata import entry_points
from pathlib import Path
from typing import ClassVar, Self

import pytest

from sphinx_revealjs_mascot_jev import cli
from sphinx_revealjs_mascot_jev.evaluator import JevSettings
from sphinx_revealjs_mascot_jev.server import ClientConfig
from tests.serving import DUMMY_KEY


class _FakeServer:
    """待ち受けを開始せず、受け取った引数を記録するサーバー。"""

    instances: ClassVar[list[_FakeServer]] = []
    bind_error: ClassVar[OSError | None] = None

    def __init__(self, port: int, html_dir: Path, client_config: ClientConfig, jev_settings: JevSettings) -> None:
        if self.bind_error is not None:
            raise self.bind_error
        self.server_port = port
        self.html_dir = html_dir
        self.client_config = client_config
        self.jev_settings = jev_settings
        self.served = False
        self.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def serve_forever(self) -> None:
        self.served = True


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> type[_FakeServer]:
    monkeypatch.setattr(_FakeServer, 'instances', [])
    monkeypatch.setattr(_FakeServer, 'bind_error', None)
    monkeypatch.setattr(cli, 'MascotJevServer', _FakeServer)
    return _FakeServer


@pytest.fixture
def with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('TYPESAFE_API_KEY', DUMMY_KEY)


@pytest.fixture
def without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)


def _warnings(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if '警告' in line]


def test_mascot_jev_command_calls_cli_main() -> None:
    (entry_point,) = entry_points(group='console_scripts', name='mascot-jev')

    assert entry_point.load() is cli.main


@pytest.mark.usefixtures('with_api_key')
class TestStartup:
    def test_starts_server_with_defaults(self, tmp_path: Path, fake_server: type[_FakeServer]) -> None:
        assert cli.main([str(tmp_path)]) == 0

        (server,) = fake_server.instances
        assert (server.server_port, server.html_dir) == (8000, tmp_path)
        assert server.client_config == ClientConfig(lang='ja-JP', toggle_key='r')
        assert server.jev_settings.api_key == DUMMY_KEY
        assert server.served

    def test_passes_options_to_server(self, tmp_path: Path, fake_server: type[_FakeServer]) -> None:
        argv = [str(tmp_path), '--port', '8123', '--lang', 'en-US', '--toggle-key', 'q']

        assert cli.main(argv) == 0

        (server,) = fake_server.instances
        assert server.server_port == 8123
        assert server.client_config == ClientConfig(lang='en-US', toggle_key='q')

    def test_prints_no_warning_with_api_key(self, tmp_path: Path, fake_server: type[_FakeServer], capsys: pytest.CaptureFixture[str]) -> None:
        cli.main([str(tmp_path)])

        assert _warnings(capsys.readouterr().err) == []

    def test_reports_bind_failure(self, tmp_path: Path, fake_server: type[_FakeServer], capsys: pytest.CaptureFixture[str]) -> None:
        fake_server.bind_error = OSError('address already in use')

        assert cli.main([str(tmp_path)]) == 1

        assert 'address already in use' in capsys.readouterr().err


@pytest.mark.usefixtures('without_api_key')
def test_warns_once_without_api_key(tmp_path: Path, fake_server: type[_FakeServer], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(tmp_path)]) == 0

    (warning,) = _warnings(capsys.readouterr().err)
    assert 'TYPESAFE_API_KEY' in warning
    assert fake_server.instances[0].served


@pytest.mark.usefixtures('with_api_key')
class TestInvalidArguments:
    @pytest.mark.parametrize('toggle_key', ['', 'rr', '!', 'あ'])
    def test_rejects_toggle_key_other_than_single_alphanumeric(self, tmp_path: Path, fake_server: type[_FakeServer], toggle_key: str) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.main([str(tmp_path), '--toggle-key', toggle_key])

        assert exc_info.value.code == 2
        assert fake_server.instances == []

    @pytest.mark.parametrize('port', ['-1', '65536', 'http'])
    def test_rejects_port_out_of_range(self, tmp_path: Path, fake_server: type[_FakeServer], port: str) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.main([str(tmp_path), '--port', port])

        assert exc_info.value.code == 2
        assert fake_server.instances == []

    @pytest.mark.parametrize('host', ['0.0.0.0', '127.0.0.1'])
    def test_has_no_option_to_change_listening_address(self, tmp_path: Path, fake_server: type[_FakeServer], host: str) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.main([str(tmp_path), '--host', host])

        assert exc_info.value.code == 2
        assert fake_server.instances == []

    def test_rejects_missing_html_dir(self, tmp_path: Path, fake_server: type[_FakeServer]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            cli.main([str(tmp_path / 'missing')])

        assert exc_info.value.code == 2
        assert fake_server.instances == []
