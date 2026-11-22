"""ビルド済みスライドの静的配信、HTML へのクライアントスクリプトの注入、`/__mascot_jev__/decide` の中継。

`/__mascot_jev__/decide` は、リクエストを検証してから `evaluator.decide` に渡す。
"""

import io
import json
import os
import socketserver
import sys
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from sphinx_revealjs_mascot_jev import evaluator

LISTEN_HOST = '127.0.0.1'
ASSET_PREFIX = '/__mascot_jev__/'
DECIDE_PATH = '/__mascot_jev__/decide'
MAX_DECIDE_BODY_BYTES = 16 * 1024

_INJECTION_MARKER = b'id="sphinx-revealjs-mascot-config"'
_BODY_END_TAG = b'</body>'
_ASSET_TYPES = {
    'client.js': 'text/javascript; charset=utf-8',
    'client.css': 'text/css; charset=utf-8',
}
_LOOPBACK_HOST_NAMES = (LISTEN_HOST, 'localhost')


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """注入する `client.js` に渡す設定。

    Attributes:
        lang: `SpeechRecognition` に設定する音声認識言語コード。
        toggle_key: マイク認識の待機を切り替える単一キー。
    """

    lang: str
    toggle_key: str


@dataclass(frozen=True, slots=True)
class ServeContext:
    """リクエストの処理に使う、起動時に決まる値。

    Attributes:
        html_dir: 配信するディレクトリ（解決済みの絶対パス）。
        client_config: 注入する `client.js` に渡す設定。
        jev_settings: Jev への接続設定。
        allowed_hosts: `/__mascot_jev__/decide` が受け付ける `Host` ヘッダーの値（小文字）。
    """

    html_dir: Path
    client_config: ClientConfig
    jev_settings: evaluator.JevSettings
    allowed_hosts: frozenset[str]


def allowed_host_headers(port: int) -> frozenset[str]:
    """`/__mascot_jev__/decide` が受け付ける `Host` ヘッダーの値を求める。

    Args:
        port: 待ち受けポート番号。

    Returns:
        受け付ける `Host` ヘッダーの値（小文字）。
    """
    values = {f'{name}:{port}' for name in _LOOPBACK_HOST_NAMES}
    if port == 80:
        # ブラウザは既定のポートを Host ヘッダーから省く
        values.update(_LOOPBACK_HOST_NAMES)
    return frozenset(values)


def build_client_snippet(config: ClientConfig) -> bytes:
    """`</body>` の直前に挿入する要素を組み立てる。

    Args:
        config: `client.js` に渡す設定。

    Returns:
        挿入する要素（UTF-8）。
    """
    # 値に `</script>` や `<!--` が含まれても要素の外へ出ないよう、`<` を JSON の文字列エスケープに置き換える
    data = json.dumps({'lang': config.lang, 'toggleKey': config.toggle_key}).replace('<', '\\u003c')
    return (
        f'<link rel="stylesheet" href="{ASSET_PREFIX}client.css">\n'
        f'<script id="sphinx-revealjs-mascot-jev-config" type="application/json">{data}</script>\n'
        f'<script src="{ASSET_PREFIX}client.js" defer></script>\n'
    ).encode()


def inject_client(html: bytes, config: ClientConfig) -> bytes | None:
    """注入の目印を持つ HTML に、クライアントスクリプトを注入する。

    Args:
        html: 配信する HTML。
        config: `client.js` に渡す設定。

    Returns:
        注入後の HTML。注入の目印か `</body>` がない場合は None。
    """
    if _INJECTION_MARKER not in html:
        return None
    # bytes.lower() は ASCII だけを変換するため、位置は元の HTML と一致する
    position = html.lower().rfind(_BODY_END_TAG)
    if position < 0:
        return None
    return html[:position] + build_client_snippet(config) + html[position:]


def parse_decide_request(body: bytes) -> evaluator.DecideRequest | None:
    """`/__mascot_jev__/decide` の本文を判定の依頼に変換する。

    Args:
        body: リクエストの本文。

    Returns:
        判定の依頼。JSON として読めない場合や、項目の型が合わない場合は None。
    """
    try:
        data = json.loads(body)
    except ValueError, RecursionError:
        return None
    if not isinstance(data, dict):
        return None
    slide_title = data.get('slide_title')
    utterance = data.get('utterance')
    actions = data.get('available_actions')
    if not isinstance(slide_title, str) or not isinstance(utterance, str):
        return None
    if not isinstance(actions, list) or not all(isinstance(action, str) for action in actions):
        return None
    return evaluator.DecideRequest(slide_title=slide_title, utterance=utterance, available_actions=tuple(actions))


def is_allowed_host(headers: Message, allowed_hosts: frozenset[str]) -> bool:
    """`Host` ヘッダーが1つだけあり、受け付ける値と一致するかを判定する。

    Args:
        headers: リクエストヘッダー。
        allowed_hosts: 受け付ける `Host` ヘッダーの値（小文字）。

    Returns:
        受け付ける場合は True。
    """
    hosts = headers.get_all('Host') or []
    return len(hosts) == 1 and hosts[0].lower() in allowed_hosts


def check_decide_headers(headers: Message, allowed_hosts: frozenset[str]) -> tuple[HTTPStatus, str] | None:
    """`/__mascot_jev__/decide` のリクエストヘッダーを検証する。

    Args:
        headers: リクエストヘッダー。
        allowed_hosts: 受け付ける `Host` ヘッダーの値（小文字）。

    Returns:
        拒否する場合は、応答のステータスと理由。受け付ける場合は None。
    """
    if not is_allowed_host(headers, allowed_hosts):
        return HTTPStatus.FORBIDDEN, 'Host is not allowed'
    host = str(headers['Host']).lower()
    origins = headers.get_all('Origin') or []
    if len(origins) > 1 or (origins and origins[0].lower() != f'http://{host}'):
        return HTTPStatus.FORBIDDEN, 'Origin is not allowed'
    content_types = headers.get_all('Content-Type') or []
    if len(content_types) != 1 or headers.get_content_type() != 'application/json':
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE, 'Content-Type must be application/json'
    return None


def _decision_payload(decision: evaluator.Decision) -> dict[str, object]:
    if decision.action is None:
        return {'action': None}
    return {'action': decision.action, 'noul': decision.noul, 'confidence': decision.confidence}


class MascotJevRequestHandler(SimpleHTTPRequestHandler):
    """静的配信・HTML 注入・`/__mascot_jev__/` 配下の配信と判定の中継を行うハンドラー。"""

    def __init__(self, request: Any, client_address: Any, server: socketserver.BaseServer, *, context: ServeContext) -> None:
        """リクエストを処理する。

        Args:
            request: 受け付けたソケット。
            client_address: 接続元のアドレス。
            server: 受け付けたサーバー。
            context: 起動時に決まる値。
        """
        # 親クラスの __init__ がリクエストを処理するため、先に設定する
        self.context = context
        super().__init__(request, client_address, server, directory=context.html_dir)

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        """`GET` と `HEAD` の応答ヘッダーを送り、本文を返す。

        Returns:
            応答の本文。ヘッダーだけで応答が終わる場合は None。
        """
        # DNS rebinding で配信ファイルを読まれないよう、判定 API と同じ Host の検証を先に行う
        if not is_allowed_host(self.headers, self.context.allowed_hosts):
            self.send_error(HTTPStatus.FORBIDDEN, 'Host is not allowed')
            return None
        url_path = urlsplit(self.path).path
        if url_path.startswith(ASSET_PREFIX):
            return self._send_asset(url_path.removeprefix(ASSET_PREFIX))
        file_path = self.translate_path(self.path)
        served_file = self._served_file(file_path)
        # 検査の後に親クラスがファイルを開き直すまでの間の差し替えは防がない。差し替えには <html_dir> への書き込みが要り、
        # Sphinx のビルドはリンクを作らずにファイルを複製する。標準ライブラリだけでは、リンクを辿らずに開く手段が全 OS で揃わない
        if not self._is_within_html_dir(file_path if served_file is None else served_file):
            self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
            return None
        if served_file is not None and self.guess_type(served_file) == 'text/html':
            try:
                html = served_file.read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
                return None
            injected = inject_client(html, self.context.client_config)
            if injected is not None:
                self._send_ok_headers(self.guess_type(served_file), len(injected))
                return io.BytesIO(injected)
        return super().send_head()

    def list_directory(self, path: str | os.PathLike[str]) -> io.BytesIO | None:
        """ディレクトリ一覧を表示せず、404 を返す。

        Args:
            path: 一覧を求められたディレクトリ。

        Returns:
            常に None。
        """
        self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
        return None

    def do_POST(self) -> None:
        """`/__mascot_jev__/decide` のリクエストを検証し、判定の結果を返す。"""
        body = self._read_body()
        if body is None:
            return
        if urlsplit(self.path).path != DECIDE_PATH:
            self.send_error(HTTPStatus.NOT_FOUND, 'Not found')
            return
        rejection = check_decide_headers(self.headers, self.context.allowed_hosts)
        if rejection is not None:
            self.send_error(*rejection)
            return
        request = parse_decide_request(body)
        if request is None:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Invalid request body')
            return
        try:
            decision = evaluator.decide(request, self.context.jev_settings)
        except Exception as exc:
            # 判定処理の異常でスライドの進行を止めないよう、無反応として返す。例外の内容は API キーを含みうるため、型名だけを残す
            self.log_error('decide failed: %s', type(exc).__name__)
            decision = evaluator.Decision(action=None)
        payload = json.dumps(_decision_payload(decision)).encode()
        self._send_ok_headers('application/json', len(payload))
        self.wfile.write(payload)

    def _send_asset(self, name: str) -> io.BytesIO | None:
        content_type = _ASSET_TYPES.get(name)
        if content_type is None:
            self.send_error(HTTPStatus.NOT_FOUND, 'File not found')
            return None
        body = resources.files('sphinx_revealjs_mascot_jev').joinpath('static', name).read_bytes()
        self._send_ok_headers(content_type, len(body))
        return io.BytesIO(body)

    def _send_ok_headers(self, content_type: str, content_length: int) -> None:
        # 起動オプションで内容が変わるため、条件付きリクエストの 304 で古い内容を使わせないよう Last-Modified を付けない
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(content_length))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def _is_within_html_dir(self, file_path: str | Path) -> bool:
        # translate_path は `..` を取り除くが、シンボリックリンクやジャンクションの先は確かめない
        try:
            return Path(file_path).resolve().is_relative_to(self.context.html_dir)
        except OSError, ValueError:
            return False

    def _served_file(self, file_path: str) -> Path | None:
        # 親クラスの send_head はヘッダーを送ってから本文を返す。配信範囲の検査と注入の要否を先に決めるよう、開くファイルの解決を同じ規則でなぞる。
        # リダイレクト・ディレクトリ一覧・ファイルなしで終わる場合は None を返す
        path = Path(file_path)
        if path.is_dir():
            if not file_path.endswith('/'):
                return None
            return next((path / name for name in self.index_pages if (path / name).is_file()), None)
        if file_path.endswith('/') or not path.is_file():
            return None
        return path

    def _read_body(self) -> bytes | None:
        # 未読のデータを残したまま接続を閉じると、接続のリセットで応答が失われうるため、拒否する場合も先に本文を読み切る
        if 'Transfer-Encoding' in self.headers:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Transfer-Encoding is not supported')
            return None
        lengths = self.headers.get_all('Content-Length') or []
        if not lengths:
            self.send_error(HTTPStatus.LENGTH_REQUIRED, 'Content-Length is required')
            return None
        if len(lengths) != 1 or not (lengths[0].isascii() and lengths[0].isdigit()):
            self.send_error(HTTPStatus.BAD_REQUEST, 'Invalid Content-Length')
            return None
        # int() は先頭の 0 も数えた桁数が上限（既定 4300 桁）を超えると ValueError になる。
        # 先頭の 0 を除いた値だけを変換し、上限値より桁の多い値は変換せずに上限超過とする
        digits = lengths[0].lstrip('0')
        length = int(digits or '0') if len(digits) <= len(str(MAX_DECIDE_BODY_BYTES)) else MAX_DECIDE_BODY_BYTES + 1
        if length > MAX_DECIDE_BODY_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Request body is too large')
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self.send_error(HTTPStatus.BAD_REQUEST, 'Request body is shorter than Content-Length')
            return None
        return body


class MascotJevServer(ThreadingHTTPServer):
    """`mascot-jev` の HTTP サーバー。"""

    # Windows では、SO_REUSEADDR を付けると、SO_REUSEADDR 付きで待ち受けている別のサーバーと同じポートに bind できてしまうため付けない。
    # Linux・macOS では、付けないと TIME_WAIT の接続が残る間は再起動できないため付ける（根拠は docs/records.md）
    allow_reuse_address = sys.platform != 'win32'

    def __init__(self, port: int, html_dir: Path, client_config: ClientConfig, jev_settings: evaluator.JevSettings) -> None:
        """`127.0.0.1` で待ち受けを開始する。

        Args:
            port: 待ち受けポート番号。0 を指定すると空いているポートを使う。
            html_dir: 配信するディレクトリ。
            client_config: 注入する `client.js` に渡す設定。
            jev_settings: Jev への接続設定。

        Raises:
            OSError: 待ち受けを開始できない場合。
        """
        super().__init__((LISTEN_HOST, port), self._create_handler)
        self.context = ServeContext(
            html_dir=html_dir.resolve(),
            client_config=client_config,
            jev_settings=jev_settings,
            allowed_hosts=allowed_host_headers(self.server_port),
        )

    def _create_handler(self, request: Any, client_address: Any, server: socketserver.BaseServer) -> MascotJevRequestHandler:
        # 受け付けるポートが待ち受けの開始後に決まるため、context は受け付けのたびに参照する
        return MascotJevRequestHandler(request, client_address, server, context=self.context)
