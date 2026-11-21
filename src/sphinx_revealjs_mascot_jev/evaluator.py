"""Jev へのリクエストの組み立て、しきい値による採否、Jev との通信。

`server.py` から呼ぶ判定処理の入出力の型と `decide` を定める。
"""

import functools
import http.client
import json
import logging
import queue
import socket
import ssl
import threading
import time
import urllib.request
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import IO, Any, Self
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit

DEFAULT_BASE_URL = 'https://api.typesafe.ai'
DEFAULT_MODEL = 'jev-1.13.0'

SYSTEMONE_PATH = '/v1/systemone'
JEV_TIMEOUT_SECONDS = 0.75
"""Jev との通信全体（名前解決・接続・送受信）にかける時間の上限（秒）。"""
MAX_RESPONSE_BYTES = 64 * 1024
"""Jev の応答本文として読み込む上限（バイト）。"""
MAX_UTTERANCE_CHARS = 120
"""Jev に送る発話の上限（文字）。超える場合は末尾を残す。"""
MAX_SLIDE_TITLE_CHARS = 60
"""Jev に送るスライド見出しの上限（文字）。超える場合は先頭を残す。"""

EMOTION_ACTIONS = ('happy', 'surprise', 'tilt', 'trouble', 'proud')
"""音声感情の対象となるアクション。"""
SHOULD_REACT_THRESHOLD = 0.65
"""`should_react` の `noul` の下限（共通ゲート条件）。"""
CONFIDENCE_THRESHOLDS: Mapping[str, float] = {'happy': 0.65, 'surprise': 0.65, 'tilt': 0.45, 'trouble': 0.55, 'proud': 0.55}
"""`reaction` を送る場合の、アクション別の `confidence` の下限。"""
SURPRISE_NOUL_THRESHOLD = 0.65
"""候補が `surprise` だけの構成で使う、`surprise` の `noul` の下限（驚き判定条件）。"""

_SURPRISE = 'surprise'
_SUPPORTED_SCHEMES = frozenset({'http', 'https'})

_REACTION_INSTRUCTIONS = '発表者の発話（utterance）に対して、小鳥のマスコットが返すのに最もふさわしいリアクションを1つ選んでください。'
_REACTION_CRITERIA: Mapping[str, dict[str, object]] = {
    'happy': {
        'covers': '感謝、お祝い、節目の喜び、好評だった報告、純粋に嬉しい出来事',
        'not_for': '技術的な工夫や解決策の自慢は proud を選ぶ',
        'examples': ['10周年を迎えられました', '本当にありがとうございます', 'とても好評でした'],
    },
    'surprise': {
        'covers': '意外な事実、直感に反するデータ、極端な規模や速度のギャップ、ポジティブまたは中立な驚き',
        'not_for': '障害や失敗で困った話は trouble を選ぶ',
        'examples': ['実はこれ、わずか0.1秒で終わります', 'なんと従来の10倍に増えました', '予想外の結果が出ました'],
    },
    'tilt': {
        'covers': '聴衆への問いかけ、疑問の提示、なぜだろうという思考の導入',
        'not_for': 'すでに失敗して困っている苦労話は trouble を選ぶ',
        'examples': ['なぜこうなると思いますか', 'ここで一つ疑問が浮かびます', '本当にそれだけで十分でしょうか'],
    },
    'trouble': {
        'covers': '過去の失敗談、苦労話、ハマりポイント、想定外のトラブルや反省',
        'not_for': '問題を見事に解決した自慢は proud を選ぶ',
        'examples': ['最初は全然動かなくて苦労しました', 'ここで痛い目を見ました', '設定の沼にハマってしまいました'],
    },
    'proud': {
        'covers': '独自の工夫や解決策のアピール、達成への自信、強みの紹介、今後の力強い決意表明',
        'not_for': '聴衆への感謝や単なるお祝いは happy を選ぶ',
        'examples': ['ここを自動化したのが一番の工夫です', '無事にやり切ることができました', '次はさらにここを目指していきます'],
    },
}
# 反応の対象が should_react と reaction で食い違わないよう、should_react の true の基準と surprise の false の基準は covers から組み立てる
_SHOULD_REACT_QUESTION: dict[str, object] = {
    'type': 'noul',
    'instructions': (
        'プレゼン発表者の直近の発話（utterance）に、聞き手のマスコットが感情リアクションを返すべき明確な山場や感情表現が含まれているか？'
        '（slide_title は話題の背景参考にとどめ、判定は utterance を主軸に行うこと）'
    ),
    'criteria': {
        'true': '次のいずれかが utterance に明確に含まれている。' + ''.join(f'{criteria["covers"]}。' for criteria in _REACTION_CRITERIA.values()),
        'false': (
            '次のスライドへの繋ぎ言葉、構成や手順の淡々とした事実説明、次回やこの後の予定の説明、'
            '理解の確認や質問の受付などの定型の問いかけ、定型挨拶、言い淀みのみで感情の山場がない。'
        ),
    },
}
_SURPRISE_QUESTION: dict[str, object] = {
    'type': 'noul',
    'instructions': (
        'プレゼン発表者の直近の発話（utterance）は、聞き手のマスコットが驚きのリアクションを返すのにふさわしい内容か？'
        '（slide_title は話題の背景参考にとどめ、判定は utterance を主軸に行うこと）'
    ),
    'criteria': {
        'true': '意外な事実、直感に反するデータ、極端な規模や速度のギャップなど、ポジティブまたは中立な驚きが utterance に明確に含まれている。',
        'false': (
            '驚きの要素がない。次のいずれかが中心の発話もこちらに含める。'
            + ''.join(f'{criteria["covers"]}。' for action, criteria in _REACTION_CRITERIA.items() if action != _SURPRISE)
            + '障害や失敗で困った話。'
        ),
    },
}

_logger = logging.getLogger(__name__)
# 判定のたびに証明書を読み込み直さないよう、TLS の設定は使い回す
_SSL_CONTEXT = ssl.create_default_context()


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
        confidence: `reaction` の `confidence`。action が None のとき、および候補が `surprise` だけの構成では None。
    """

    action: str | None
    noul: float | None = None
    confidence: float | None = None


class _JevError(Exception):
    """Jev との通信か応答の解釈に失敗したことを表す。メッセージには API キーや応答本文を含めない。"""


class _DeadlinePassedError(TimeoutError):
    """通信全体の締切を過ぎたため、接続を始めない・続けないことを表す。"""


class _Deadline:
    """通信全体の締切。締切を過ぎた接続を、別のスレッドから打ち切る。"""

    def __init__(self, seconds: float) -> None:
        self._expires_at = time.monotonic() + seconds
        self._lock = threading.Lock()
        self._expired = False
        self._connection: socket.socket | None = None

    def seconds_left(self) -> float:
        """締切までの残り時間を返す。

        Returns:
            残り時間（秒）。締切を過ぎていれば 0 以下。
        """
        return self._expires_at - time.monotonic()

    def timeout(self) -> float:
        """締切までの残り時間を、送受信のタイムアウトとして返す。

        Returns:
            残り時間（秒）。

        Raises:
            _DeadlinePassedError: 締切を過ぎている場合。
        """
        left = self.seconds_left()
        if self._expired or left <= 0:
            raise _DeadlinePassedError('the deadline passed')
        return left

    def watch(self, sock: socket.socket) -> None:
        """接続済みのソケットを、締切で打ち切る対象にする。

        Args:
            sock: 接続済みのソケット。

        Raises:
            _DeadlinePassedError: 締切を過ぎている場合。
        """
        # TLS のソケットは元のソケットを引き継いで作られ、元のソケットは使えなくなる。
        # ハンドシェイク中も止められるよう、同じ接続を指す複製を持つ
        duplicate = sock.dup()
        with self._lock:
            if not self._expired and self._connection is None:
                self._connection = duplicate
                return
        duplicate.close()
        raise _DeadlinePassedError('the deadline passed')

    def expire(self) -> None:
        """以後の接続を断り、監視中の接続の送受信を止める。"""
        with self._lock:
            self._expired = True
            if self._connection is not None:
                # 別のスレッドで待っている送受信は、close では戻らない OS があるため、shutdown で止める
                with suppress(OSError):
                    self._connection.shutdown(socket.SHUT_RDWR)

    def release(self) -> None:
        """監視に使った複製のソケットを閉じる。"""
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, /, *, deadline: _Deadline, **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._deadline = deadline
        # 名前解決の後、接続を始める前に締切を確かめるため、http.client が接続に使う関数を差し替える
        self._create_connection = self._connect_before_deadline

    def _connect_before_deadline(
        self, address: tuple[str, int], timeout: object = None, source_address: tuple[str, int] | None = None
    ) -> socket.socket:
        host, port = address
        # 名前解決は途中で止められないため、締切を過ぎても終わるまで待つ。その後は、締切を過ぎていれば接続を始めない
        addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        error: OSError = OSError('getaddrinfo returned no address')
        for family, kind, proto, _, sockaddr in addresses:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(self._deadline.timeout())
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                self._deadline.watch(sock)
            except _DeadlinePassedError:
                sock.close()
                raise
            except OSError as exc:
                sock.close()
                error = exc
                continue
            return sock
        raise error


class _DeadlineHTTPSConnection(_DeadlineHTTPConnection, http.client.HTTPSConnection):
    pass


class _DeadlineHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: _Deadline) -> None:
        super().__init__()
        self._deadline = deadline

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(functools.partial(_DeadlineHTTPConnection, deadline=self._deadline), req)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline: _Deadline) -> None:
        super().__init__(context=_SSL_CONTEXT)
        self._deadline = deadline

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        return self.do_open(functools.partial(_DeadlineHTTPSConnection, deadline=self._deadline), req, context=_SSL_CONTEXT)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: http.client.HTTPMessage, newurl: str
    ) -> urllib.request.Request | None:
        # urllib はリダイレクト先にも Authorization ヘッダーを送るため、追わずに 3xx を HTTPError にする
        return None


def decide(request: DecideRequest, settings: JevSettings) -> Decision:
    """発話に対してマスコットが返すアクションを判定する。

    候補が2種以上なら `reaction`（`Choice`）で、`surprise` だけなら驚き判定用の `surprise`（`Noul`）で評価する。
    通信の失敗、HTTP エラー、応答の形式の不正、タイムアウトのときも、例外を投げずに `action` なしを返す。

    Args:
        request: 判定の依頼。
        settings: Jev への接続設定。

    Returns:
        判定の結果。
    """
    candidates = tuple(action for action in EMOTION_ACTIONS if action in request.available_actions)
    # 選択肢が1つの Choice は常に確信度が最大になり判定にならない。mascot は surprise の画像を必須とするため、
    # surprise 以外の1種だけが残る構成は起こらず、無反応とする
    if settings.api_key is None or not (len(candidates) >= 2 or candidates == (_SURPRISE,)):
        return Decision(action=None)
    try:
        url = _systemone_url(settings.base_url)
        response = _post_json(url, _build_payload(request, settings.model, candidates), settings.api_key)
        return _judge(response, candidates)
    except _JevError as exc:
        _logger.warning('Jev request failed: %s', exc)
        return Decision(action=None)


def _systemone_url(base_url: str) -> str:
    try:
        parts = urlsplit(base_url)
    except ValueError:
        raise _JevError('TYPESAFE_BASE_URL is not a valid URL') from None
    # urllib は file: などにも対応するため、意図しない読み込みや送信をしないよう http と https に限る。
    # クエリやフラグメントがあると、パスを足しても /v1/systemone 以外へ送ることになるため受け付けない
    if parts.scheme not in _SUPPORTED_SCHEMES or not parts.hostname or parts.query or parts.fragment:
        raise _JevError('TYPESAFE_BASE_URL is not an http or https URL without query and fragment')
    return urlunsplit(parts._replace(path=parts.path.rstrip('/') + SYSTEMONE_PATH))


def _build_payload(request: DecideRequest, model: str, candidates: tuple[str, ...]) -> dict[str, object]:
    questions: dict[str, object] = {'should_react': _SHOULD_REACT_QUESTION}
    if candidates == (_SURPRISE,):
        questions[_SURPRISE] = _SURPRISE_QUESTION
    else:
        criteria = {action: _REACTION_CRITERIA[action] for action in candidates}
        questions['reaction'] = {'type': 'choice', 'instructions': _REACTION_INSTRUCTIONS, 'criteria': criteria}
    # クライアントの切り詰めを経ない呼び出しでも、外部に送る長さの上限を守る
    state = {'slide_title': request.slide_title[:MAX_SLIDE_TITLE_CHARS], 'utterance': request.utterance[-MAX_UTTERANCE_CHARS:]}
    return {'model': model, 'state': state, 'questions': questions}


def _post_json(url: str, payload: Mapping[str, object], api_key: str) -> object:
    deadline = _Deadline(JEV_TIMEOUT_SECONDS)
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        method='POST',
    )
    results: queue.SimpleQueue[bytes | _JevError] = queue.SimpleQueue()
    # urllib のタイムアウトは送受信1回ごとにかかり、名前解決にはかからないため、通信を別スレッドで行い全体の上限まで待つ
    threading.Thread(target=_send, args=(request, deadline, results), daemon=True).start()
    try:
        result = results.get(timeout=max(deadline.seconds_left(), 0.0))
    except queue.Empty:
        deadline.expire()
        raise _JevError(f'no response within {JEV_TIMEOUT_SECONDS}s') from None
    if isinstance(result, _JevError):
        raise result
    try:
        return json.loads(result)
    except ValueError, RecursionError:
        raise _JevError('response is not JSON') from None


def _send(request: urllib.request.Request, deadline: _Deadline, results: queue.SimpleQueue[bytes | _JevError]) -> None:
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler, _DeadlineHTTPHandler(deadline), _DeadlineHTTPSHandler(deadline))
        with opener.open(request, timeout=JEV_TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
            # 大きさを指定した read は、Content-Length に届く前に接続が切れても例外を出さず、届いた分を返す
            unread: int | None = response.length
            # 呼び出し側の待ちが遅れると、締切の後に届いた結果もキューから受け取れるため、受け取り終えた時刻で判断する
            received_in_time = deadline.seconds_left() > 0
    except HTTPError as exc:
        exc.close()
        results.put(_JevError(f'HTTP {exc.code}'))
        return
    except Exception as exc:
        # 名前解決・接続・送受信で起きる例外は種類が多いため、まとめて失敗とする。
        # 不正なヘッダー値の例外などは値（API キー）をメッセージに含むため、型名だけを残す
        results.put(_JevError(type(exc).__name__))
        return
    finally:
        deadline.release()
    if not received_in_time:
        results.put(_JevError('response arrived after the deadline'))
    elif status != HTTPStatus.OK:
        results.put(_JevError(f'HTTP {status}'))
    elif len(body) > MAX_RESPONSE_BYTES:
        results.put(_JevError('response is too large'))
    elif unread:
        results.put(_JevError('response ended before Content-Length'))
    else:
        results.put(body)


def _judge(response: object, candidates: tuple[str, ...]) -> Decision:
    answers = _member(response, 'answers')
    noul = _score(_member(answers, 'should_react'), 'noul')
    if candidates == (_SURPRISE,):
        surprise = _score(_member(answers, _SURPRISE), 'noul')
        if noul >= SHOULD_REACT_THRESHOLD and surprise >= SURPRISE_NOUL_THRESHOLD:
            return Decision(action=_SURPRISE, noul=noul)
        return Decision(action=None)
    reaction = _member(answers, 'reaction')
    choice = reaction.get('choice')
    if not isinstance(choice, str) or choice not in candidates:
        raise _JevError('reaction.choice is not one of the candidates')
    confidence = _score(reaction, 'confidence')
    if noul >= SHOULD_REACT_THRESHOLD and confidence >= CONFIDENCE_THRESHOLDS[choice]:
        return Decision(action=choice, noul=noul, confidence=confidence)
    return Decision(action=None)


def _member(container: object, key: str) -> Mapping[str, object]:
    value = container.get(key) if isinstance(container, dict) else None
    if not isinstance(value, dict):
        raise _JevError(f'{key} is not an object')
    return value


def _score(container: Mapping[str, object], key: str) -> float:
    value = container.get(key)
    # bool は int のサブクラスのため除く。範囲の比較で、NaN と無限大も除く
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
        raise _JevError(f'{key} is not a number between 0 and 1')
    return float(value)
