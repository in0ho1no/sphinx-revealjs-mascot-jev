"""判定処理の接続設定、Jev へのリクエスト、しきい値による採否、異常時の縮退を検証する。

Jev へは通信せず、ローカルに立てた偽の Jev サーバーを接続先にする。
"""

import json
import logging
import math
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sphinx_revealjs_mascot_jev import evaluator
from sphinx_revealjs_mascot_jev.evaluator import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    JEV_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    DecideRequest,
    Decision,
    JevSettings,
    decide,
)

_DUMMY_KEY = 'dummy-key'
_FULL_ACTIONS = ('idle', 'jump', 'wink', 'surprise', 'happy', 'tilt', 'trouble', 'proud')
_REQUIRED_ACTIONS = ('idle', 'jump', 'wink', 'surprise')
# 仕様書 4.5 で、クライアントがローカル API の呼び出しを中止するまでの時間
_CLIENT_ABORT_SECONDS = 0.8
_MISSING = object()


def _request(available_actions: tuple[str, ...] = _FULL_ACTIONS) -> DecideRequest:
    return DecideRequest(
        slide_title='10年間の歩みとこれから',
        utterance='ここまで続けてこられたのも皆さんのおかげです、本当にありがとうございます',
        available_actions=available_actions,
    )


def _choice_answers(choice: object = 'happy', *, noul: object = 0.9, confidence: object = 0.9) -> dict[str, dict[str, object]]:
    return {
        'should_react': {'type': 'noul', 'noul': noul},
        'reaction': {'type': 'choice', 'choice': choice, 'confidence': confidence},
    }


def _surprise_answers(*, noul: object = 0.9, surprise: object = 0.9) -> dict[str, dict[str, object]]:
    return {'should_react': {'type': 'noul', 'noul': noul}, 'surprise': {'type': 'noul', 'noul': surprise}}


def _response_body(answers: object) -> bytes:
    return json.dumps({'model': DEFAULT_MODEL, 'answers': answers, 'usage': {'input_tokens': 1300, 'output_tokens': 74}}).encode()


def _without(answers: dict[str, dict[str, object]], question: str, key: str | None = None) -> dict[str, dict[str, object]]:
    if key is None:
        del answers[question]
    else:
        del answers[question][key]
    return answers


@dataclass(frozen=True, slots=True)
class _Received:
    method: str
    path: str
    headers: Message
    body: bytes

    def payload(self) -> Any:
        return json.loads(self.body)


class _FakeJevHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self._reply()

    def do_GET(self) -> None:
        self._reply()

    def log_message(self, format: str, *args: Any) -> None:
        """テストの出力に、偽の Jev サーバーのアクセスログを出さない。"""

    def _reply(self) -> None:
        upstream = cast('_FakeJev', self.server)
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        upstream.received.append(_Received(self.command, self.path, self.headers, body))
        if upstream.before_reply is not None:
            upstream.before_reply()
        self.send_response(upstream.status)
        for name, value in upstream.extra_headers:
            self.send_header(name, value)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(upstream.body) if upstream.declared_length is None else upstream.declared_length))
        self.end_headers()
        self.wfile.write(upstream.body)


class _FakeJev(ThreadingHTTPServer):
    """受け取ったリクエストを記録し、決まった応答を返す偽の Jev サーバー。"""

    def __init__(self) -> None:
        super().__init__(('127.0.0.1', 0), _FakeJevHandler)
        self.received: list[_Received] = []
        self.status = 200
        self.extra_headers: list[tuple[str, str]] = []
        # None のときは本文の長さを Content-Length にする
        self.declared_length: int | None = None
        # 応答を返す直前に呼ぶ処理
        self.before_reply: Callable[[], None] | None = None
        self.body = _response_body(_choice_answers())

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.server_port}'

    def settings(self, **replace: str) -> JevSettings:
        return JevSettings(api_key=_DUMMY_KEY, base_url=replace.get('base_url', self.base_url), model=replace.get('model', DEFAULT_MODEL))

    def answer(self, answers: object) -> None:
        self.body = _response_body(answers)

    def only_payload(self) -> Any:
        (received,) = self.received
        return received.payload()


@pytest.fixture
def jev() -> Iterator[_FakeJev]:
    upstream = _FakeJev()
    thread = threading.Thread(target=upstream.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    yield upstream
    upstream.shutdown()
    upstream.server_close()
    thread.join()


@dataclass
class _SilentUpstream:
    """接続を1つ受け付けて要求を読み、応答しないまま、クライアントが接続を閉じるのを待つ上流。"""

    port: int
    received: bytearray = field(default_factory=bytearray)
    closed_at: float | None = None
    connected: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}'


@pytest.fixture
def silent_upstream() -> Iterator[_SilentUpstream]:
    listener = socket.create_server(('127.0.0.1', 0))
    # テストの終了時に待ち受けをやめられるよう、短い間隔で受け付けを確かめ直す
    listener.settimeout(0.05)
    upstream = _SilentUpstream(port=listener.getsockname()[1])

    def accept() -> socket.socket | None:
        while not upstream.stop.is_set():
            with suppress(TimeoutError):
                return listener.accept()[0]
        return None

    def serve() -> None:
        try:
            with suppress(OSError), listener:
                connection = accept()
                if connection is None:
                    return
                upstream.connected.set()
                with connection:
                    connection.settimeout(5)
                    while chunk := connection.recv(65536):
                        upstream.received += chunk
                    upstream.closed_at = time.perf_counter()
        finally:
            upstream.finished.set()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield upstream
    upstream.stop.set()
    thread.join()


@dataclass
class _NameResolution:
    """名前解決を、release が立つまで（最長 5 秒）終わらせない。終わると 127.0.0.1 を返す。"""

    called: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    returned: threading.Event = field(default_factory=threading.Event)


@pytest.fixture
def slow_name_resolution(monkeypatch: pytest.MonkeyPatch) -> Iterator[_NameResolution]:
    resolution = _NameResolution()
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: bytes | str | None, port: bytes | str | int | None, *args: Any, **kwargs: Any) -> list[Any]:
        resolution.called.set()
        resolution.release.wait(5)
        addresses = real_getaddrinfo('127.0.0.1', port, *args, **kwargs)
        resolution.returned.set()
        return addresses

    monkeypatch.setattr(socket, 'getaddrinfo', getaddrinfo)
    yield resolution
    resolution.release.set()


class TestJevSettingsFromEnv:
    def test_uses_defaults_when_unset(self) -> None:
        settings = JevSettings.from_env({})

        assert settings == JevSettings(api_key=None, base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL)

    def test_reads_each_variable(self) -> None:
        environ = {'TYPESAFE_API_KEY': _DUMMY_KEY, 'TYPESAFE_BASE_URL': 'http://127.0.0.1:9000', 'TYPESAFE_MODEL': 'jev-test'}

        settings = JevSettings.from_env(environ)

        assert settings == JevSettings(api_key=_DUMMY_KEY, base_url='http://127.0.0.1:9000', model='jev-test')

    def test_treats_empty_values_as_unset(self) -> None:
        environ = {'TYPESAFE_API_KEY': '', 'TYPESAFE_BASE_URL': '', 'TYPESAFE_MODEL': ''}

        settings = JevSettings.from_env(environ)

        assert settings == JevSettings(api_key=None, base_url=DEFAULT_BASE_URL, model=DEFAULT_MODEL)


def test_settings_repr_hides_api_key() -> None:
    settings = JevSettings(api_key=_DUMMY_KEY)

    assert _DUMMY_KEY not in repr(settings)


class TestJevRequest:
    def test_posts_to_systemone_with_bearer_key(self, jev: _FakeJev) -> None:
        decide(_request(), jev.settings())

        (received,) = jev.received
        assert (received.method, received.path) == ('POST', '/v1/systemone')
        assert received.headers['Authorization'] == f'Bearer {_DUMMY_KEY}'
        assert received.headers.get_content_type() == 'application/json'

    def test_joins_base_url_ending_with_slash(self, jev: _FakeJev) -> None:
        decide(_request(), jev.settings(base_url=f'{jev.base_url}/'))

        (received,) = jev.received
        assert received.path == '/v1/systemone'

    def test_keeps_path_of_base_url(self, jev: _FakeJev) -> None:
        decide(_request(), jev.settings(base_url=f'{jev.base_url}/compat/'))

        (received,) = jev.received
        assert received.path == '/compat/v1/systemone'

    def test_truncates_state_to_upper_limits(self, jev: _FakeJev) -> None:
        # 発話は末尾 120 文字、見出しは先頭 60 文字を送る
        request = DecideRequest(slide_title='見' * 60 + '出し', utterance='前置き' + '発' * 120, available_actions=_FULL_ACTIONS)

        decide(request, jev.settings())

        assert jev.only_payload()['state'] == {'slide_title': '見' * 60, 'utterance': '発' * 120}

    def test_sends_model_and_state(self, jev: _FakeJev) -> None:
        request = _request()

        decide(request, jev.settings(model='jev-test'))

        payload = jev.only_payload()
        assert payload['model'] == 'jev-test'
        assert payload['state'] == {'slide_title': request.slide_title, 'utterance': request.utterance}

    def test_sends_should_react_and_five_choices_for_all_actions(self, jev: _FakeJev) -> None:
        decide(_request(_FULL_ACTIONS), jev.settings())

        questions = jev.only_payload()['questions']
        assert questions.keys() == {'should_react', 'reaction'}
        assert questions['should_react']['type'] == 'noul'
        assert questions['reaction']['type'] == 'choice'
        assert questions['reaction']['criteria'].keys() == {'happy', 'surprise', 'tilt', 'trouble', 'proud'}

    def test_should_react_true_lists_every_reaction_cover(self, jev: _FakeJev) -> None:
        decide(_request(_FULL_ACTIONS), jev.settings())

        questions = jev.only_payload()['questions']
        should_react = questions['should_react']['criteria']['true']
        covers = [criteria['covers'] for criteria in questions['reaction']['criteria'].values()]
        assert [cover for cover in covers if cover not in should_react] == []

    def test_surprise_false_lists_covers_of_other_reactions(self, jev: _FakeJev) -> None:
        decide(_request(_FULL_ACTIONS), jev.settings())
        jev.answer(_surprise_answers())
        decide(_request(_REQUIRED_ACTIONS), jev.settings())

        full, surprise_only = (received.payload()['questions'] for received in jev.received)
        surprise_false = surprise_only['surprise']['criteria']['false']
        others = [criteria['covers'] for action, criteria in full['reaction']['criteria'].items() if action != 'surprise']
        assert [cover for cover in others if cover not in surprise_false] == []

    @pytest.mark.parametrize(
        ('available_actions', 'expected'),
        [
            pytest.param((*_REQUIRED_ACTIONS, 'happy'), {'surprise', 'happy'}, id='required-and-happy'),
            pytest.param((*_REQUIRED_ACTIONS, 'tilt', 'proud', 'doze'), {'surprise', 'tilt', 'proud'}, id='required-and-two'),
        ],
    )
    def test_narrows_choices_to_available_actions(self, jev: _FakeJev, available_actions: tuple[str, ...], expected: set[str]) -> None:
        jev.answer(_choice_answers('surprise'))

        decide(_request(available_actions), jev.settings())

        assert jev.only_payload()['questions']['reaction']['criteria'].keys() == expected

    def test_sends_surprise_noul_instead_of_choice_when_only_surprise_is_available(self, jev: _FakeJev) -> None:
        jev.answer(_surprise_answers())

        decide(_request(_REQUIRED_ACTIONS), jev.settings())

        questions = jev.only_payload()['questions']
        assert questions.keys() == {'should_react', 'surprise'}
        assert questions['surprise']['type'] == 'noul'


class TestNoRequest:
    @pytest.mark.parametrize(
        'available_actions',
        [
            pytest.param((), id='empty'),
            pytest.param(('idle', 'jump', 'wink'), id='no-emotion'),
            pytest.param(('idle', 'jump', 'wink', 'happy'), id='single-non-surprise'),
        ],
    )
    def test_returns_no_action_without_sending_when_candidates_are_unsupported(self, jev: _FakeJev, available_actions: tuple[str, ...]) -> None:
        decision = decide(_request(available_actions), jev.settings())

        assert decision == Decision(action=None)
        assert jev.received == []

    def test_returns_no_action_without_sending_when_api_key_is_unset(self, jev: _FakeJev) -> None:
        decision = decide(_request(), JevSettings(api_key=None, base_url=jev.base_url))

        assert decision == Decision(action=None)
        assert jev.received == []

    @pytest.mark.parametrize('base_url', ['ftp://127.0.0.1', 'file:///', 'jev.invalid', 'http://[::1', 'https://'])
    def test_returns_no_action_when_base_url_is_not_http(self, base_url: str) -> None:
        decision = decide(_request(), JevSettings(api_key=_DUMMY_KEY, base_url=base_url))

        assert decision == Decision(action=None)

    @pytest.mark.parametrize('suffix', ['?key=value', '#fragment'])
    def test_returns_no_action_without_sending_when_base_url_has_query_or_fragment(self, jev: _FakeJev, suffix: str) -> None:
        decision = decide(_request(), jev.settings(base_url=f'{jev.base_url}{suffix}'))

        assert decision == Decision(action=None)
        assert jev.received == []


class TestChoiceThreshold:
    @pytest.mark.parametrize(
        ('action', 'min_confidence'),
        [('tilt', 0.45), ('trouble', 0.55), ('proud', 0.55), ('happy', 0.65), ('surprise', 0.65)],
    )
    def test_adopts_choice_at_lower_bounds(self, jev: _FakeJev, action: str, min_confidence: float) -> None:
        jev.answer(_choice_answers(action, noul=0.5, confidence=min_confidence))

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=action, noul=0.5, confidence=min_confidence)

    @pytest.mark.parametrize(
        ('action', 'confidence'),
        [('tilt', 0.44), ('trouble', 0.54), ('proud', 0.54), ('happy', 0.64), ('surprise', 0.64)],
    )
    def test_rejects_confidence_below_lower_bound(self, jev: _FakeJev, action: str, confidence: float) -> None:
        jev.answer(_choice_answers(action, noul=1.0, confidence=confidence))

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)

    def test_rejects_should_react_below_gate(self, jev: _FakeJev) -> None:
        jev.answer(_choice_answers('happy', noul=0.49, confidence=1.0))

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)


class TestSurpriseOnlyThreshold:
    def test_adopts_surprise_at_lower_bounds_without_confidence(self, jev: _FakeJev) -> None:
        jev.answer(_surprise_answers(noul=0.5, surprise=0.65))

        decision = decide(_request(_REQUIRED_ACTIONS), jev.settings())

        assert decision == Decision(action='surprise', noul=0.5, confidence=None)

    @pytest.mark.parametrize(
        ('noul', 'surprise'),
        [pytest.param(0.49, 1.0, id='should-react-below'), pytest.param(1.0, 0.64, id='surprise-below')],
    )
    def test_rejects_below_lower_bounds(self, jev: _FakeJev, noul: float, surprise: float) -> None:
        jev.answer(_surprise_answers(noul=noul, surprise=surprise))

        decision = decide(_request(_REQUIRED_ACTIONS), jev.settings())

        assert decision == Decision(action=None)


class TestFailure:
    @pytest.mark.parametrize('status', [201, 400, 403, 500, 503])
    def test_returns_no_action_for_non_200_status(self, jev: _FakeJev, status: int) -> None:
        jev.status = status
        if status >= 400:
            jev.body = json.dumps({'detail': {'error_type': 'api_usage_error', 'message': 'Invalid request.'}}).encode()

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)

    @pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
    def test_does_not_follow_redirect(self, jev: _FakeJev, status: int) -> None:
        jev.status = status
        jev.extra_headers = [('Location', f'{jev.base_url}/v1/systemone')]

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)
        assert len(jev.received) == 1

    @pytest.mark.parametrize(
        'body',
        [
            pytest.param(b'not json', id='not-json'),
            pytest.param(b'[]', id='not-object'),
            pytest.param(b'{}', id='no-answers'),
            pytest.param(b'[' * 30_000 + b']' * 30_000, id='too-deep'),
            pytest.param(_response_body([]), id='answers-not-object'),
            pytest.param(_response_body(_without(_choice_answers(), 'should_react')), id='no-should-react'),
            pytest.param(_response_body(_without(_choice_answers(), 'should_react', 'noul')), id='no-noul'),
            pytest.param(_response_body(_without(_choice_answers(), 'reaction')), id='no-reaction'),
            pytest.param(_response_body(_without(_choice_answers(), 'reaction', 'choice')), id='no-choice'),
            pytest.param(_response_body(_without(_choice_answers(), 'reaction', 'confidence')), id='no-confidence'),
            pytest.param(_response_body(_choice_answers('idle')), id='choice-not-emotion'),
            pytest.param(_response_body(_choice_answers(['happy'])), id='choice-not-string'),
            pytest.param(_response_body(_choice_answers(noul='0.9')), id='noul-string'),
            pytest.param(_response_body(_choice_answers(noul=True)), id='noul-bool'),
            pytest.param(_response_body(_choice_answers(noul=math.nan)), id='noul-nan'),
            pytest.param(_response_body(_choice_answers(noul=math.inf)), id='noul-infinity'),
            pytest.param(_response_body(_choice_answers(noul=10**400)), id='noul-huge-int'),
            pytest.param(_response_body(_choice_answers(noul=-0.1)), id='noul-negative'),
            pytest.param(_response_body(_choice_answers(confidence=1.5)), id='confidence-over-one'),
            pytest.param(_response_body(_choice_answers(confidence=None)), id='confidence-null'),
        ],
    )
    def test_returns_no_action_for_unexpected_response(self, jev: _FakeJev, body: bytes) -> None:
        jev.body = body

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)

    def test_returns_no_action_when_choice_is_not_available(self, jev: _FakeJev) -> None:
        jev.answer(_choice_answers('tilt', noul=1.0, confidence=1.0))

        decision = decide(_request((*_REQUIRED_ACTIONS, 'happy')), jev.settings())

        assert decision == Decision(action=None)

    @pytest.mark.parametrize(
        'answers',
        [
            pytest.param(_without(_surprise_answers(), 'surprise'), id='no-surprise'),
            pytest.param(_surprise_answers(surprise='high'), id='surprise-string'),
            pytest.param(_choice_answers('surprise', noul=1.0, confidence=1.0), id='choice-instead'),
        ],
    )
    def test_returns_no_action_for_unexpected_surprise_only_response(self, jev: _FakeJev, answers: object) -> None:
        jev.answer(answers)

        decision = decide(_request(_REQUIRED_ACTIONS), jev.settings())

        assert decision == Decision(action=None)

    def test_returns_no_action_for_too_large_response(self, jev: _FakeJev) -> None:
        body = _response_body(_choice_answers(noul=1.0, confidence=1.0))
        jev.body = body + b' ' * (MAX_RESPONSE_BYTES + 1 - len(body))

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)

    def test_returns_no_action_when_body_ends_before_content_length(self, jev: _FakeJev) -> None:
        # 受け取れた部分だけでも、採択できる JSON になっている
        jev.answer(_choice_answers(noul=1.0, confidence=1.0))
        jev.declared_length = len(jev.body) + 10

        decision = decide(_request(), jev.settings())

        assert decision == Decision(action=None)

    def test_returns_no_action_when_connection_is_refused(self) -> None:
        with socket.create_server(('127.0.0.1', 0)) as listener:
            port = listener.getsockname()[1]

        decision = decide(_request(), JevSettings(api_key=_DUMMY_KEY, base_url=f'http://127.0.0.1:{port}'))

        assert decision == Decision(action=None)

    def test_logs_failure_without_api_key(self, jev: _FakeJev, caplog: pytest.LogCaptureFixture) -> None:
        jev.status = 403
        jev.body = json.dumps({'detail': {'error_type': 'authentication_error', 'message': _DUMMY_KEY}}).encode()
        caplog.set_level(logging.WARNING)

        decide(_request(), jev.settings())

        assert 'HTTP 403' in caplog.text
        assert _DUMMY_KEY not in caplog.text

    def test_logs_invalid_header_failure_without_api_key(self, jev: _FakeJev, caplog: pytest.LogCaptureFixture) -> None:
        # 改行を含むキーは、http.client がヘッダー値を含むメッセージの例外で拒否する
        key = f'{_DUMMY_KEY}\r\nX-Injected: 1'
        caplog.set_level(logging.WARNING)

        decision = decide(_request(), JevSettings(api_key=key, base_url=jev.base_url))

        assert decision == Decision(action=None)
        assert jev.received == []
        assert caplog.text
        assert _DUMMY_KEY not in caplog.text


class TestTimeout:
    def test_does_not_adopt_response_received_after_deadline(self, jev: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> None:
        # 判定処理が参照する時計だけを、応答を返す直前に締切の後まで進める
        offset = 0.0

        def monotonic() -> float:
            return time.monotonic() + offset

        def pass_deadline() -> None:
            nonlocal offset
            offset = JEV_TIMEOUT_SECONDS + 0.25

        monkeypatch.setattr(evaluator, 'time', SimpleNamespace(monotonic=monotonic))
        jev.before_reply = pass_deadline
        jev.answer(_choice_answers('happy', noul=1.0, confidence=1.0))

        decision = decide(_request(), jev.settings())

        assert len(jev.received) == 1
        assert decision == Decision(action=None)

    def test_closes_connection_within_timeout_when_upstream_does_not_respond(self, silent_upstream: _SilentUpstream) -> None:
        started = time.perf_counter()

        decision = decide(_request(), JevSettings(api_key=_DUMMY_KEY, base_url=silent_upstream.base_url))

        elapsed = time.perf_counter() - started
        assert decision == Decision(action=None)
        assert elapsed < _CLIENT_ABORT_SECONDS
        assert silent_upstream.finished.wait(5)
        assert silent_upstream.received
        assert silent_upstream.closed_at is not None
        assert silent_upstream.closed_at - started < _CLIENT_ABORT_SECONDS

    def test_closes_connection_at_deadline_when_connected_late(self, slow_name_resolution: _NameResolution, silent_upstream: _SilentUpstream) -> None:
        # 名前解決に 0.3 秒かかると、送受信1回ごとのタイムアウトだけでは、接続が 1.05 秒まで残る
        threading.Timer(0.3, slow_name_resolution.release.set).start()
        started = time.perf_counter()

        decision = decide(_request(), JevSettings(api_key=_DUMMY_KEY, base_url=f'http://jev.invalid:{silent_upstream.port}'))

        elapsed = time.perf_counter() - started
        assert decision == Decision(action=None)
        assert elapsed < _CLIENT_ABORT_SECONDS
        assert silent_upstream.finished.wait(5)
        assert silent_upstream.received
        assert silent_upstream.closed_at is not None
        assert silent_upstream.closed_at - started < _CLIENT_ABORT_SECONDS

    def test_does_not_connect_when_name_resolution_ends_after_deadline(
        self, slow_name_resolution: _NameResolution, silent_upstream: _SilentUpstream
    ) -> None:
        started = time.perf_counter()

        decision = decide(_request(), JevSettings(api_key=_DUMMY_KEY, base_url=f'http://jev.invalid:{silent_upstream.port}'))

        elapsed = time.perf_counter() - started
        slow_name_resolution.release.set()
        assert decision == Decision(action=None)
        # urllib のタイムアウトがかからない名前解決で止まっても、全体の上限で打ち切る
        assert slow_name_resolution.called.is_set()
        assert JEV_TIMEOUT_SECONDS - 0.05 <= elapsed < _CLIENT_ABORT_SECONDS
        # 締切の後に名前解決が終わっても、TCP の接続を始めない
        assert slow_name_resolution.returned.wait(5)
        assert not silent_upstream.connected.wait(0.5)
