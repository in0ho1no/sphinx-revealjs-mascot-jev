"""`mascot-jev` コマンドのエントリポイント。

引数の解析とサーバーの起動は未実装である（Issue #4 で実装する）。
"""

import sys


def main() -> int:
    """`mascot-jev` コマンドを実行する。

    Returns:
        終了コード。サーバーの起動が未実装のため、常に 1 を返す。
    """
    print('mascot-jev: サーバーの起動は未実装です。', file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
