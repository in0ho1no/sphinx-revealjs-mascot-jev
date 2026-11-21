"""HTML へのクライアントスクリプトの注入と、注入した応答のヘッダーを検証する。"""

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from sphinx_revealjs_mascot_jev.server import ClientConfig, build_client_snippet, inject_client
from tests.serving import CLIENT_CONFIG, StartServer

_MARKER_SCRIPT = '<script id="sphinx-revealjs-mascot-config" type="application/json">{"toggleKey": "m"}</script>'
_MARKED_HTML = f'<!DOCTYPE html>\n<html><head>{_MARKER_SCRIPT}</head><body><p>スライド</p></body></html>\n'.encode()


class _ElementCollector(HTMLParser):
    """要素の開始タグと、script 要素の中身を集める。"""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.script_data: dict[str, str] = {}
        self._script_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))
        if tag == 'script':
            self._script_id = dict(attrs).get('id') or ''

    def handle_endtag(self, tag: str) -> None:
        if tag == 'script':
            self._script_id = None

    def handle_data(self, data: str) -> None:
        if self._script_id is not None:
            self.script_data[self._script_id] = self.script_data.get(self._script_id, '') + data


def _collect(html: bytes) -> _ElementCollector:
    collector = _ElementCollector()
    collector.feed(html.decode())
    collector.close()
    return collector


class TestInjectClient:
    def test_inserts_snippet_right_before_body_end_tag(self) -> None:
        injected = inject_client(_MARKED_HTML, CLIENT_CONFIG)

        assert injected == _MARKED_HTML.replace(b'</body>', build_client_snippet(CLIENT_CONFIG) + b'</body>')

    def test_inserts_before_last_body_end_tag(self) -> None:
        html = f'<html><head>{_MARKER_SCRIPT}</head><body><!-- </body> --></body></html>'.encode()

        injected = inject_client(html, CLIENT_CONFIG)

        assert injected == html.replace(b' --></body>', b' -->' + build_client_snippet(CLIENT_CONFIG) + b'</body>')

    def test_finds_uppercase_body_end_tag(self) -> None:
        html = f'<HTML><HEAD>{_MARKER_SCRIPT}</HEAD><BODY></BODY></HTML>'.encode()

        injected = inject_client(html, CLIENT_CONFIG)

        assert injected == html.replace(b'</BODY>', build_client_snippet(CLIENT_CONFIG) + b'</BODY>')

    def test_skips_html_without_marker(self) -> None:
        html = '<html><head></head><body><p>mascot なし</p></body></html>'.encode()

        assert inject_client(html, CLIENT_CONFIG) is None

    def test_skips_html_without_body_end_tag(self) -> None:
        html = f'<html><head>{_MARKER_SCRIPT}</head><body><p>閉じタグなし</p>'.encode()

        assert inject_client(html, CLIENT_CONFIG) is None


class TestBuildClientSnippet:
    def test_contains_stylesheet_config_and_deferred_script_in_order(self) -> None:
        collector = _collect(build_client_snippet(ClientConfig(lang='en-US', toggle_key='x')))

        assert collector.tags == [
            ('link', {'rel': 'stylesheet', 'href': '/__mascot_jev__/client.css'}),
            ('script', {'id': 'sphinx-revealjs-mascot-jev-config', 'type': 'application/json'}),
            ('script', {'src': '/__mascot_jev__/client.js', 'defer': None}),
        ]
        assert json.loads(collector.script_data['sphinx-revealjs-mascot-jev-config']) == {'lang': 'en-US', 'toggleKey': 'x'}

    @pytest.mark.parametrize('lang', ['</script><script>alert(1)</script>', '<!--<script>', '</SCRIPT >'])
    def test_keeps_script_end_tag_in_value_inside_config_element(self, lang: str) -> None:
        collector = _collect(build_client_snippet(ClientConfig(lang=lang, toggle_key='r')))

        assert [tag for tag, _ in collector.tags] == ['link', 'script', 'script']
        config = collector.script_data['sphinx-revealjs-mascot-jev-config']
        assert '<' not in config
        assert json.loads(config) == {'lang': lang, 'toggleKey': 'r'}


class TestInjectedResponse:
    def test_get_returns_injected_html_with_recalculated_length(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)

        response = server.request('GET', '/index.html')

        assert response.status == 200
        assert response.body == inject_client(_MARKED_HTML, CLIENT_CONFIG)
        assert response.headers['Content-Length'] == str(len(response.body))
        assert response.headers['Content-Type'] == 'text/html'

    def test_injected_response_is_not_stored_or_validated(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)

        response = server.request('GET', '/index.html')

        assert response.headers['Cache-Control'] == 'no-store'
        assert response.headers['Last-Modified'] is None

    def test_conditional_request_gets_injected_html_instead_of_304(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)

        response = server.request('GET', '/index.html', headers=[('Host', server.host), ('If-Modified-Since', 'Fri, 01 Jan 2100 00:00:00 GMT')])

        assert response.status == 200
        assert response.body == inject_client(_MARKED_HTML, CLIENT_CONFIG)

    def test_head_returns_injected_content_length(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)
        injected = inject_client(_MARKED_HTML, CLIENT_CONFIG)
        assert injected is not None

        response = server.request('HEAD', '/index.html')

        assert response.status == 200
        assert response.headers['Content-Length'] == str(len(injected))
        assert response.headers['Cache-Control'] == 'no-store'
        assert response.body == b''

    def test_directory_index_is_injected(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'slides').mkdir()
        (tmp_path / 'slides' / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)

        response = server.request('GET', '/slides/')

        assert response.status == 200
        assert response.body == inject_client(_MARKED_HTML, CLIENT_CONFIG)

    def test_unreadable_html_is_not_found(self, tmp_path: Path, start_server: StartServer, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path)

        def deny(path: Path) -> bytes:
            raise PermissionError(path)

        monkeypatch.setattr(Path, 'read_bytes', deny)

        response = server.request('GET', '/index.html')

        assert response.status == 404

    def test_injects_options_given_at_startup(self, tmp_path: Path, start_server: StartServer) -> None:
        (tmp_path / 'index.html').write_bytes(_MARKED_HTML)
        server = start_server(tmp_path, client_config=ClientConfig(lang='en-US', toggle_key='q'))

        response = server.request('GET', '/index.html')

        config = _collect(response.body).script_data['sphinx-revealjs-mascot-jev-config']
        assert json.loads(config) == {'lang': 'en-US', 'toggleKey': 'q'}


class TestUntouchedResponse:
    # 注入の目印と </body> を含めても、text/html 以外は注入の対象にならない
    _MARKED_TEXT = f'/* {_MARKER_SCRIPT} </body> */\n'.encode()

    @pytest.mark.parametrize(
        ('name', 'content'),
        [
            ('plain.html', '<html><head></head><body><p>mascot なし</p></body></html>'.encode()),
            ('unclosed.html', f'<html><head>{_MARKER_SCRIPT}</head><body><p>閉じタグなし</p>'.encode()),
            ('image.png', b'\x89PNG\r\n\x1a\n' + _MARKED_TEXT),
            ('script.js', _MARKED_TEXT),
            ('style.css', _MARKED_TEXT),
        ],
    )
    def test_serves_file_as_is(self, tmp_path: Path, start_server: StartServer, name: str, content: bytes) -> None:
        (tmp_path / name).write_bytes(content)
        server = start_server(tmp_path)

        response = server.request('GET', f'/{name}')

        assert response.status == 200
        assert response.body == content
        assert response.headers['Content-Length'] == str(len(content))
