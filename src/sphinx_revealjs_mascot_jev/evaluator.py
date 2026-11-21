"""Jev へのリクエストの組み立て、しきい値による採否、Jev との通信。

`server.py` から呼ぶ判定処理の入出力の型と `decide` を定める。
`decide` は仮実装で、常に `action` なしを返す。判定の中身は Issue #5 で実装する。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Self

DEFAULT_BASE_URL = 'https://api.typesafe.ai'
DEFAULT_MODEL = 'jev-1.13.0'


@dataclass(frozen=True, slots=True)
class JevSettings:
    """Jev（または `/v1/systemone` 互換サーバー）への接続設定。

    Attributes:
        api_key: Bearer 認証のトークン。未設定のときは None。ログに出ないよう repr には含めない。
        base_url: `/v1/systemone` の送信先のベース URL。
        model: 判定に使うモデルの識別子。
    """

    api_key: str | None = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Self:
        """環境変数から接続設定を読み込む。

        空文字列の環境変数は、未設定として扱う。

        Args:
            environ: 環境変数（通常は `os.environ`）。

        Returns:
            接続設定。
        """
        return cls(
            api_key=environ.get('TYPESAFE_API_KEY') or None,
            base_url=environ.get('TYPESAFE_BASE_URL') or DEFAULT_BASE_URL,
            model=environ.get('TYPESAFE_MODEL') or DEFAULT_MODEL,
        )


@dataclass(frozen=True, slots=True)
class DecideRequest:
    """`/__mascot_jev__/decide` が受け取った判定の依頼。

    Attributes:
        slide_title: 現在のスライドの見出し。
        utterance: 直近の発話テキスト。
        available_actions: `window.RevealMascot.getAvailableActions()` の戻り値。
    """

    slide_title: str
    utterance: str
    available_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Decision:
    """判定の結果。

    Attributes:
        action: 発火させるアクション名。反応不要・しきい値未達・エラーのときは None。
        noul: `should_react` の `noul`。action が None のときは None。
        confidence: `reaction` の `confidence`。action が None のときは None。
            候補が `surprise` だけの構成で入れる値は未定である。
    """

    action: str | None
    noul: float | None = None
    confidence: float | None = None


def decide(request: DecideRequest, settings: JevSettings) -> Decision:
    """発話に対してマスコットが返すアクションを判定する。

    仮実装であり、常に `action` なしを返す。

    Args:
        request: 判定の依頼。
        settings: Jev への接続設定。

    Returns:
        判定の結果。
    """
    return Decision(action=None)
