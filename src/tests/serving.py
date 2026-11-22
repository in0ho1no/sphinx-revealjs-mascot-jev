"""配信サーバーを起動して HTTP で呼び出すための補助。"""

import socket
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.client import HTTPMessage, HTTPResponse

from sphinx_revealjs_mascot_jev.server import ClientConfig, MascotJevServer

DUMMY_KEY = 'dummy-key'
CLIENT_CONFIG = ClientConfig(lang='ja-JP', toggle_key='r')


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: HTTPMessage
    body: bytes


@dataclass(frozen=True, slots=True)
class RunningServer:
    server: MascotJevServer

    @property
    def port(self) -> int:
        return self.server.server_port

    @property
    def host(self) -> str:
        return f'127.0.0.1:{self.port}'

    def request(
        self, method: str, path: str, *, headers: Sequence[tuple[str, str]] | None = None, body: bytes = b'', close_write: bool = False
    ) -> Response:
        """ヘッダーを補わずに、指定した順序と重複のまま送る。

        headers が None のときは Host だけを送る。close_write が True のときは、送信後に送信側を閉じて本文の終わりを伝える。
        """
        # http.client は重複した Host ヘッダーを1つにまとめて送るため、リクエストを組み立てて直接送る
        header_lines = [f'{name}: {value}' for name, value in (headers if headers is not None else [('Host', self.host)])]
        head = '\r\n'.join([f'{method} {path} HTTP/1.1', *header_lines, '', ''])
        with socket.create_connection(('127.0.0.1', self.port), timeout=5) as connection:
            connection.sendall(head.encode() + body)
            if close_write:
                connection.shutdown(socket.SHUT_WR)
            response = HTTPResponse(connection, method=method)
            response.begin()
            return Response(status=response.status, headers=response.headers, body=response.read())


type StartServer = Callable[..., RunningServer]
