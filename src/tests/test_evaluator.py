"""判定処理の接続設定と入出力を検証する。"""

from sphinx_revealjs_mascot_jev.evaluator import DEFAULT_BASE_URL, DEFAULT_MODEL, DecideRequest, JevSettings, decide

_DUMMY_KEY = 'dummy-key'
_REQUEST = DecideRequest(
    slide_title='10年間の歩みとこれから',
    utterance='ここまで続けてこられたのも皆さんのおかげです、本当にありがとうございます',
    available_actions=('idle', 'jump', 'wink', 'surprise', 'happy', 'tilt', 'trouble', 'proud'),
)


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


def test_decide_returns_no_action_without_api_key() -> None:
    decision = decide(_REQUEST, JevSettings(api_key=None))

    assert decision.action is None
