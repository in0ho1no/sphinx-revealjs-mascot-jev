"""`mascot-jev` コマンドのエントリポイント。"""

import argparse
import contextlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sphinx_revealjs_mascot_jev.evaluator import JevSettings
from sphinx_revealjs_mascot_jev.server import LISTEN_HOST, ClientConfig, MascotJevServer

_PROG = 'mascot-jev'


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError('0〜65535 の整数を指定してください')
    return port


def _toggle_key(value: str) -> str:
    if len(value) != 1 or not (value.isascii() and value.isalnum()):
        raise argparse.ArgumentTypeError('英数字1文字を指定してください')
    return value


def build_parser() -> argparse.ArgumentParser:
    """`mascot-jev` の引数の定義を作る。

    Returns:
        引数のパーサー。
    """
    parser = argparse.ArgumentParser(
        prog=_PROG,
        description=f'ビルド済みの Reveal.js スライドを {LISTEN_HOST} で配信し、発表者の発話に応じてマスコットにリアクションさせる。',
    )
    parser.add_argument('html_dir', type=Path, help='sphinx-revealjs のビルド出力ディレクトリ（例：_build/revealjs）')
    parser.add_argument('--port', type=_port, default=8000, help='待ち受けポート番号（既定：%(default)s）')
    parser.add_argument('--lang', default='ja-JP', help='SpeechRecognition に設定する音声認識言語コード（既定：%(default)s）')
    parser.add_argument(
        '--toggle-key',
        type=_toggle_key,
        default='r',
        help='マイク認識の待機を切り替える英数字1文字のキー。mascot の表示切り替えキー（既定 m）とは別のキーにする（既定：%(default)s）',
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`mascot-jev` コマンドを実行する。

    Args:
        argv: コマンドライン引数。None のときは `sys.argv[1:]` を使う。

    Returns:
        終了コード。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    html_dir: Path = args.html_dir
    if not html_dir.is_dir():
        parser.error(f'ディレクトリが見つかりません: {html_dir}')
    settings = JevSettings.from_env(os.environ)
    try:
        server = MascotJevServer(args.port, html_dir, ClientConfig(lang=args.lang, toggle_key=args.toggle_key), settings)
    except OSError as exc:
        print(f'{_PROG}: {LISTEN_HOST}:{args.port} で待ち受けを開始できません: {exc}', file=sys.stderr)
        return 1
    if settings.api_key is None:
        print(f'{_PROG}: 警告: TYPESAFE_API_KEY が未設定のため、判定 API は常に無反応（action: null）を返します。', file=sys.stderr)
    with server:
        # 出力先がファイルやパイプの場合も、待ち受けの開始をすぐに伝える
        print(f'{_PROG}: http://{LISTEN_HOST}:{server.server_port}/ で {html_dir} を配信しています（Ctrl+C で停止）', flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
    return 0


if __name__ == '__main__':
    sys.exit(main())
