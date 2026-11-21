"""`/__mascot_jev__/` 配下の配信、`/__mascot_jev__/decide` の検証と中継、配信範囲を検証する。"""

import io
import json
import os
from http.client import parse_headers
from importlib import resources
from pathlib import Path

import pytest

from sphinx_revealjs_mascot_jev import evaluator
from sphinx_revealjs_mascot_jev.server import MAX_DECIDE_BODY_BYTES, allowed_host_headers, check_decide_headers
from tests.serving import DUMMY_KEY, RunningServer, StartServer

_REQUEST = evaluator.DecideRequest(
    slide_title='10年間の歩みとこれから',
    utterance='ここまで続けてこられたのも皆さんのおかげです、本当にありがとうございます',
    available_actions=('idle', 'jump', 'wink', 'surprise', 'happy', 'tilt', 'trouble', 'proud'),
)
_REQUEST_BODY: dict[str, object] = {
    'slide_title': _REQUEST.slide_title,
    'utterance': _REQUEST.utterance,
    'available_actions': list(_REQUEST.available_actions),
}
_BODY = json.dumps(_REQUEST_BODY).encode()
_DECIDE = '/__mascot_jev__/decide'


class _FakeDecide:
    """`evaluator.decide` の代わりに、呼び出しを記録して決まった結果を返す。"""

    def __init__(self, decision: evaluator.Decision | Exception) -> None:
        self.decision = decision
        self.calls: list[tuple[evaluator.DecideRequest, evaluator.JevSettings]] = []

    def __call__(self, request: evaluator.DecideRequest, settings: evaluator.JevSettings) -> evaluator.Decision:
        self.calls.append((request, settings))
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision


@pytest.fixture
def fake_decide(monkeypatch: pytest.MonkeyPatch) -> _FakeDecide:
    fake = _FakeDecide(evaluator.Decision(action='happy', noul=0.91, confidence=0.78))
    monkeypatch.setattr(evaluator, 'decide', fake)
    return fake


@pytest.fixture
def server(tmp_path: Path, start_server: StartServer) -> RunningServer:
    return start_server(tmp_path)


def _decide_headers(server: RunningServer, body: bytes = _BODY, /, **replace: str | None) -> list[tuple[str, str]]:
    """正規の client.js が送るヘッダー。replace で値を差し替え、None を渡すと除く。"""
    headers = {
        'Host': server.host,
        'Origin': f'http://{server.host}',
        'Content-Type': 'application/json',
        'Content-Length': str(len(body)),
    }
    for name, value in replace.items():
        key = name.replace('_', '-').title()
        if value is None:
            del headers[key]
        else:
            headers[key] = value
    return list(headers.items())


class TestDecideRelay:
    def test_passes_request_to_decide_and_returns_decision(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        response = server.request('POST', _DECIDE, headers=_decide_headers(server), body=_BODY)

        assert response.status == 200
        assert response.headers['Content-Type'] == 'application/json'
        assert json.loads(response.body) == {'action': 'happy', 'noul': 0.91, 'confidence': 0.78}
        ((request, settings),) = fake_decide.calls
        assert request == _REQUEST
        assert settings.api_key == DUMMY_KEY

    def test_returns_only_null_action_when_no_reaction(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        fake_decide.decision = evaluator.Decision(action=None)

        response = server.request('POST', _DECIDE, headers=_decide_headers(server), body=_BODY)

        assert response.status == 200
        assert json.loads(response.body) == {'action': None}

    def test_returns_null_action_when_decide_raises(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        fake_decide.decision = RuntimeError('upstream failure')

        response = server.request('POST', _DECIDE, headers=_decide_headers(server), body=_BODY)

        assert response.status == 200
        assert json.loads(response.body) == {'action': None}

    def test_returns_null_action_without_api_key(self, tmp_path: Path, start_server: StartServer) -> None:
        # 判定処理は差し替えず、API キーが未設定のときの応答を確かめる
        server = start_server(tmp_path, jev_settings=evaluator.JevSettings(api_key=None))

        response = server.request('POST', _DECIDE, headers=_decide_headers(server), body=_BODY)

        assert response.status == 200
        assert json.loads(response.body) == {'action': None}

    @pytest.mark.parametrize('host_name', ['127.0.0.1', 'localhost', 'LOCALHOST'])
    def test_accepts_loopback_host_names(self, server: RunningServer, fake_decide: _FakeDecide, host_name: str) -> None:
        host = f'{host_name}:{server.port}'

        response = server.request('POST', _DECIDE, headers=_decide_headers(server, host=host, origin=f'http://{host}'), body=_BODY)

        assert response.status == 200

    def test_accepts_request_without_origin(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        response = server.request('POST', _DECIDE, headers=_decide_headers(server, origin=None), body=_BODY)

        assert response.status == 200

    def test_accepts_content_type_with_charset(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        headers = _decide_headers(server, content_type='application/json; charset=utf-8')

        response = server.request('POST', _DECIDE, headers=headers, body=_BODY)

        assert response.status == 200

    def test_accepts_body_at_size_limit(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        body = _BODY.ljust(MAX_DECIDE_BODY_BYTES)

        response = server.request('POST', _DECIDE, headers=_decide_headers(server, body), body=body)

        assert response.status == 200


class TestDecideRejection:
    @pytest.mark.parametrize(
        'replace',
        [
            pytest.param({'host': 'attacker.example:{port}', 'origin': 'http://attacker.example:{port}'}, id='rebinding-host'),
            pytest.param({'host': '127.0.0.1:1', 'origin': 'http://127.0.0.1:1'}, id='other-port'),
            pytest.param({'host': '127.0.0.1', 'origin': 'http://127.0.0.1'}, id='host-without-port'),
            pytest.param({'host': None, 'origin': None}, id='no-host'),
            pytest.param({'origin': 'http://attacker.example'}, id='cross-site-origin'),
            pytest.param({'origin': 'null'}, id='null-origin'),
            pytest.param({'origin': 'http://localhost:{port}'}, id='origin-of-other-host-name'),
            pytest.param({'origin': 'https://127.0.0.1:{port}'}, id='origin-of-other-scheme'),
            pytest.param({'content_type': 'text/plain'}, id='text-plain'),
            pytest.param({'content_type': 'application/x-www-form-urlencoded'}, id='form'),
            pytest.param({'content_type': None}, id='no-content-type'),
        ],
    )
    def test_rejects_invalid_headers(self, server: RunningServer, fake_decide: _FakeDecide, replace: dict[str, str | None]) -> None:
        values = {name: None if value is None else value.format(port=server.port) for name, value in replace.items()}

        response = server.request('POST', _DECIDE, headers=_decide_headers(server, **values), body=_BODY)

        assert 400 <= response.status < 500
        assert fake_decide.calls == []

    @pytest.mark.parametrize('name', ['Host', 'Origin', 'Content-Type'])
    def test_rejects_repeated_header(self, name: str) -> None:
        # 開発端末のループバック通信で重複した Host ヘッダーが除かれる事例があったため、サーバーを介さずに検証する
        lines = ['Host: 127.0.0.1:8000', 'Origin: http://127.0.0.1:8000', 'Content-Type: application/json']
        lines.append(next(line for line in lines if line.startswith(f'{name}:')))
        headers = parse_headers(io.BytesIO('\r\n'.join([*lines, '', '']).encode()))

        assert check_decide_headers(headers, allowed_host_headers(8000)) is not None

    def test_accepts_valid_headers(self) -> None:
        lines = ['Host: 127.0.0.1:8000', 'Origin: http://127.0.0.1:8000', 'Content-Type: application/json']
        headers = parse_headers(io.BytesIO('\r\n'.join([*lines, '', '']).encode()))

        assert check_decide_headers(headers, allowed_host_headers(8000)) is None

    @pytest.mark.parametrize(
        'content_length',
        [str(MAX_DECIDE_BODY_BYTES + 1), '1' * 5000, '0' * 5000 + str(MAX_DECIDE_BODY_BYTES + 1)],
        ids=['limit-plus-one', 'beyond-int-digits', 'leading-zeros-over-limit'],
    )
    def test_rejects_body_over_size_limit(self, server: RunningServer, fake_decide: _FakeDecide, content_length: str) -> None:
        # 上限を超える本文は読まずに拒否するため、ヘッダーだけを送る
        headers = _decide_headers(server, content_length=content_length)

        response = server.request('POST', _DECIDE, headers=headers)

        assert response.status == 413
        assert fake_decide.calls == []

    @pytest.mark.parametrize('content_length', [None, '-1', '+10', '1_0', 'abc'])
    def test_rejects_missing_or_invalid_content_length(self, server: RunningServer, fake_decide: _FakeDecide, content_length: str | None) -> None:
        response = server.request('POST', _DECIDE, headers=_decide_headers(server, content_length=content_length))

        assert 400 <= response.status < 500
        assert fake_decide.calls == []

    def test_accepts_content_length_with_many_leading_zeros(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        # 先頭の 0 だけで int() の桁数の上限を超える値
        headers = _decide_headers(server, content_length='0' * 5000 + str(len(_BODY)))

        response = server.request('POST', _DECIDE, headers=headers, body=_BODY)

        assert response.status == 200
        assert len(fake_decide.calls) == 1

    def test_rejects_body_shorter_than_content_length(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        headers = _decide_headers(server, content_length=str(len(_BODY) + 10))

        response = server.request('POST', _DECIDE, headers=headers, body=_BODY, close_write=True)

        assert response.status == 400
        assert fake_decide.calls == []

    def test_rejects_chunked_body(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        headers = [*_decide_headers(server, content_length=None), ('Transfer-Encoding', 'chunked')]

        # 分割された本文は読まずに拒否するため、ヘッダーだけを送る
        response = server.request('POST', _DECIDE, headers=headers)

        assert 400 <= response.status < 500
        assert fake_decide.calls == []

    @pytest.mark.parametrize(
        'body',
        [
            b'{',
            b'[]',
            json.dumps({**_REQUEST_BODY, 'utterance': None}).encode(),
            json.dumps({**_REQUEST_BODY, 'available_actions': 'happy'}).encode(),
            json.dumps({**_REQUEST_BODY, 'available_actions': ['happy', 1]}).encode(),
            json.dumps({key: value for key, value in _REQUEST_BODY.items() if key != 'slide_title'}).encode(),
            b'[' * MAX_DECIDE_BODY_BYTES,
        ],
    )
    def test_rejects_malformed_body(self, server: RunningServer, fake_decide: _FakeDecide, body: bytes) -> None:
        response = server.request('POST', _DECIDE, headers=_decide_headers(server, body), body=body)

        assert response.status == 400
        assert fake_decide.calls == []

    def test_post_to_other_path_is_not_relayed(self, server: RunningServer, fake_decide: _FakeDecide) -> None:
        response = server.request('POST', '/index.html', headers=_decide_headers(server), body=_BODY)

        assert response.status == 404
        assert fake_decide.calls == []


class TestListening:
    def test_listens_only_on_ipv4_loopback(self, server: RunningServer) -> None:
        assert server.server.server_address[0] == '127.0.0.1'

    def test_allows_loopback_ip_and_localhost(self) -> None:
        assert allowed_host_headers(8000) == {'127.0.0.1:8000', 'localhost:8000'}

    def test_default_port_allows_host_without_port(self) -> None:
        assert allowed_host_headers(80) == {'127.0.0.1:80', 'localhost:80', '127.0.0.1', 'localhost'}


class TestStaticHost:
    @pytest.mark.parametrize('path', ['/page.txt', '/', '/__mascot_jev__/client.js'])
    @pytest.mark.parametrize('method', ['GET', 'HEAD'])
    def test_rejects_other_host(self, tmp_path: Path, start_server: StartServer, method: str, path: str) -> None:
        (tmp_path / 'page.txt').write_text('inside', encoding='utf-8')
        (tmp_path / 'index.html').write_text('<html><body>index</body></html>', encoding='utf-8')
        server = start_server(tmp_path)

        response = server.request(method, path, headers=[('Host', f'attacker.example:{server.port}')])

        assert response.status == 403
        assert b'inside' not in response.body
        assert b'index' not in response.body

    def test_rejects_missing_host(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'page.txt').write_text('inside', encoding='utf-8')
        server = start_server(tmp_path)

        response = server.request('GET', '/page.txt', headers=[])

        assert response.status == 403

    @pytest.mark.parametrize('host_name', ['127.0.0.1', 'localhost'])
    def test_serves_to_loopback_host_names(self, tmp_path: Path, start_server: StartServer, host_name: str) -> None:
        (tmp_path / 'page.txt').write_text('inside', encoding='utf-8')
        server = start_server(tmp_path)

        response = server.request('GET', '/page.txt', headers=[('Host', f'{host_name}:{server.port}')])

        assert response.status == 200
        assert response.body == b'inside'


class TestClientAssets:
    @pytest.mark.parametrize(('name', 'content_type'), [('client.js', 'text/javascript; charset=utf-8'), ('client.css', 'text/css; charset=utf-8')])
    def test_serves_packaged_asset(self, server: RunningServer, name: str, content_type: str) -> None:
        expected = resources.files('sphinx_revealjs_mascot_jev').joinpath('static', name).read_bytes()

        response = server.request('GET', f'/__mascot_jev__/{name}')

        assert response.status == 200
        assert response.body == expected
        assert response.headers['Content-Type'] == content_type
        assert response.headers['Content-Length'] == str(len(expected))

    @pytest.mark.parametrize('path', ['/__mascot_jev__/decide', '/__mascot_jev__/unknown.js', '/__mascot_jev__/'])
    def test_other_paths_are_not_found(self, server: RunningServer, path: str) -> None:
        response = server.request('GET', path)

        assert response.status == 404


def _symlink_or_skip(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f'シンボリックリンクを作成できない環境: {exc}')


class TestServingScope:
    @pytest.fixture
    def html_dir(self, tmp_path: Path) -> Path:
        (tmp_path / 'secret.txt').write_text('secret', encoding='utf-8')
        html_dir = tmp_path / 'html'
        (html_dir / 'images').mkdir(parents=True)
        (html_dir / 'images' / 'normal.png').write_bytes(b'\x89PNG')
        return html_dir

    @pytest.mark.parametrize(
        'path', ['/../secret.txt', '/%2e%2e/secret.txt', '/images/..%2f..%2fsecret.txt', '/images/..%5c..%5csecret.txt', '/images/../../secret.txt']
    )
    def test_does_not_serve_files_outside_html_dir(self, html_dir: Path, start_server: StartServer, path: str) -> None:
        server = start_server(html_dir)

        response = server.request('GET', path)

        # `..\` を含む要素は取り除かれるため、親ディレクトリへのリダイレクトになる場合がある
        assert response.status != 200
        assert b'secret' not in response.body

    def test_does_not_follow_link_outside_html_dir(self, html_dir: Path, start_server: StartServer) -> None:
        _symlink_or_skip(html_dir.parent / 'secret.txt', html_dir / 'link.txt')
        server = start_server(html_dir)

        response = server.request('GET', '/link.txt')

        assert response.status == 404
        assert b'secret' not in response.body

    @pytest.mark.parametrize(('path', 'index'), [('/', 'index.html'), ('/images/', 'index.htm')])
    @pytest.mark.parametrize('target', ['secret.txt', 'secret.html'])
    def test_does_not_follow_directory_index_linked_outside(
        self, html_dir: Path, start_server: StartServer, path: str, index: str, target: str
    ) -> None:
        # secret.html は注入の目印を持ち、注入する経路を通る
        marker = '<script id="sphinx-revealjs-mascot-config" type="application/json">{}</script>'
        (html_dir.parent / 'secret.html').write_text(f'<html><head>{marker}</head><body>secret</body></html>', encoding='utf-8')
        _symlink_or_skip(html_dir.parent / target, html_dir / path.strip('/') / index)
        server = start_server(html_dir)

        response = server.request('GET', path)

        assert response.status == 404
        assert b'secret' not in response.body

    @pytest.mark.parametrize('path', ['/', '/images/'])
    def test_does_not_list_directory(self, html_dir: Path, start_server: StartServer, path: str) -> None:
        server = start_server(html_dir)

        response = server.request('GET', path)

        assert response.status == 404
        assert b'normal.png' not in response.body

    def test_serves_file_inside_html_dir(self, html_dir: Path, start_server: StartServer) -> None:
        server = start_server(html_dir)

        response = server.request('GET', '/images/normal.png')

        assert response.status == 200
        assert response.body == b'\x89PNG'
