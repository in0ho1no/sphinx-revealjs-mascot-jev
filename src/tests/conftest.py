"""テストで共有するフィクスチャ。"""

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from sphinx_revealjs_mascot_jev.evaluator import JevSettings
from sphinx_revealjs_mascot_jev.server import ClientConfig, MascotJevServer
from tests.serving import CLIENT_CONFIG, DUMMY_KEY, RunningServer, StartServer


@pytest.fixture
def start_server() -> Iterator[StartServer]:
    servers: list[tuple[MascotJevServer, threading.Thread]] = []

    def start(html_dir: Path, *, client_config: ClientConfig = CLIENT_CONFIG, jev_settings: JevSettings | None = None) -> RunningServer:
        settings = jev_settings if jev_settings is not None else JevSettings(api_key=DUMMY_KEY)
        server = MascotJevServer(0, html_dir, client_config, settings)
        # 停止の要求に早く応じるよう、待ち受けの確認間隔を既定の 0.5 秒より短くする
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        thread.start()
        servers.append((server, thread))
        return RunningServer(server)

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join()
