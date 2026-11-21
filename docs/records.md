# 判断と検証の記録

設計を進める中で得た、再利用価値のある判断と検証結果を記録する。仕様そのものは [jev_mascot_spec.md](jev_mascot_spec.md) に記述する。

## ローカルサーバーを実行時依存なしで実装する（2026-11-21）

### 背景

ローカルサーバーを動かすことで依存パッケージが増え、サプライチェーンリスクが高まるのではないかという懸念があった。

### 判断

- サーバーは Python 標準ライブラリで実装し、実行時の依存パッケージを持たない。
- `http.server` は公式ドキュメントで本番用途に推奨されていないが、発表者1人がループバックで使うローカルツールとして許容する。その代わり、防御は仕様書の「セキュリティと外部送信データ」に要件として定め、自前で実装する。

### 理由

- 静的配信（`http.server`）、Jev への HTTPS 通信（`urllib.request`）、JSON、CLI 引数、同梱ファイルの読み込み（`importlib.resources`）は、いずれも標準ライブラリで実現できる。クライアントも素の JavaScript で、npm やビルド工程は不要である。
- 依存が入るのは開発用（ruff・mypy・pyright・pytest と、それらの推移的依存）だけで、配布物には含まれない。2026-11-21 時点の `uv.lock` では、本体を除いて14件である。
- 開発用依存には、次の対策が既に入っている。
  - `uv.lock` へのハッシュ記録
  - `pyproject.toml` の `exclude-newer = "7 days"` と、Dependabot の `cooldown`（7日）による公開直後の版の回避
  - GitHub Actions の SHA 固定
  - CI での semgrep と gitleaks の実行

### 依存より注意が必要と判断した点

依存パッケージよりも、ローカルサーバーが外部から呼び出されることの方がリスクが大きいと判断した。

- `--host` でループバック以外を指定した場合に、同じネットワークの第三者が `/__mascot_jev__/decide` を呼び出し、API キーの利用枠を消費できる。
- 発表中に同じブラウザで開いている別サイトから、`/__mascot_jev__/decide` へ POST を送れる。CORS により応答は読まれないが、Jev への問い合わせは発生する。DNS rebinding を使われると、応答も読まれうる。
- 配信範囲外のファイルやディレクトリ一覧が露出しうる。

これらへの対策は、仕様書に要件と検証範囲として記載した。

## 音声認識に Web Speech API を採用する（2026-11-21）

### 判断

- 音声認識は、ブラウザ標準の Web Speech API（`SpeechRecognition`）を使う。
- `processLocally` は指定せず、認識を端末内で行うか外部で行うかはブラウザに任せる。音声がブラウザの音声認識サービスへ送信されることは許容する。

### 理由

- 実行時の依存を増やさずに済み、サプライチェーンリスクを増やさない。
- 認識途中の結果（`interimResults`）が得られるため、`1500ms` の無音デバウンスによる区切り検出とそのまま組み合わせられる。
- Jev が判定に使うのは直近最大120文字の発話の大意であり、認識精度の差が反応の良し悪しに効きにくい。
- 動作が必要なのは発表者の端末のブラウザだけである。MDN では「Limited availability」（主要ブラウザの一部で動かない）とされているが、問題になりにくい。
- 発話テキストは Jev を利用する時点で外部へ送信されるため、音声認識での外部送信も許容範囲と判断した。

### 検討して採用しなかった選択肢

| 選択肢 | 採用しなかった理由 |
| :--- | :--- |
| ローカルで動かす Whisper 系（faster-whisper、whisper.cpp など） | ノート PC では推論の負荷が大きい。推論ライブラリと大きなモデルの準備が必要で、依存も増える。区切った音声をまとめて処理する方式のため、区切り検出の設計を作り直す必要がある |
| OpenAI の音声認識 API（音声ファイル送信、Realtime API） | 音声の録音・中継・無音判定を自前で作る必要がある。WebSocket や `multipart/form-data` の処理で依存が増えやすい。区切り後の文字起こしの分だけ反応が遅れる。音声の長さに応じた課金と、別の API キーが必要になる。送信先が変わるだけで、外部送信は解消しない。精度向上の利点が判定に効きにくい |
| その他のクラウド音声認識 API（Google Cloud、Azure、AWS など） | OpenAI と同様に、API キー・費用・SDK または通信処理が増える |
| ブラウザ内で動く音声認識モデル（Transformers.js など） | JS ライブラリとモデルファイルの配信が必要で、フロントエンドのビルド工程を設けない方針と合わない |
| OpenAI の LLM を Jev の代わりに判定に使う | 仕様は Jev が返す `noul` と `confidence` をしきい値と比べる前提であり、確信度の扱いを設計し直すことになる |

### 外部送信の範囲についての注意

Jev に送るのは、関門を通った発話テキスト（最大120文字）とスライド見出しだけである。一方、音声認識サービスには、マイク認識 ON の間の音声がすべて送られる。8文字未満の発話やクールタイム中の発話も含むため、送信範囲は Jev より広い。

このため、マイクは発表中だけ ON にする運用とし、仕様書の「外部に送信されるデータ」に記載した。

### 検証結果

- 2026-11-21、利用者の Chrome で `await SpeechRecognition.available({ langs: ['ja-JP'], processLocally: true })` を実行し、`downloadable` を得た。
  - 接頭辞なしの `SpeechRecognition` と、端末内での認識に対応していることを示す。
  - 日本語の言語データを入れれば、音声を外部に出さずに認識できる。音声の外部送信を避けたくなった場合の移行先として使える。

### 未確認事項

- 端末内で認識した場合の日本語の認識精度。
- Chrome の音声認識サービスが Google のものであること。一般的な理解に基づく前提で、Google の公式資料では確認できていない。旧 Chrome Privacy Whitepaper の URL は「Chrome のプライバシーについて」のヘルプページへ転送され、そこには音声認識の記載がなかった。
- MDN の互換性表に基づく、ブラウザごとの対応状況。

### 参考

- [SpeechRecognition - MDN](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)
- [Using the Web Speech API - MDN](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API/Using_the_Web_Speech_API)
- [SpeechRecognition: processLocally property - MDN](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/processLocally)

## mascot 本体 v0.1.3 の前提を確認する（2026-11-21）

### 確認方法

Issue #2 の事前確認として、次のソースを GitHub のタグから取得して読んだ。あわせて、同じ版で実際にビルドし、生成した HTML でも確かめた。ブラウザでの動作は確かめていない。

| 対象 | 版 | 読んだ箇所 |
| :--- | :--- | :--- |
| sphinx-revealjs-mascot | `v0.1.3`（2026-11-21 時点の最新リリース） | `__init__.py`、`config.py`、`mascot.js`、`docs/SPEC.md`、`src/tests/test_mascot_build.py` |
| Sphinx | `9.2.0`（mascot の `uv.lock` で固定） | `sphinx/builders/html/__init__.py` の `js_tag`、`themes/basic/layout.html` |
| sphinx-revealjs | `3.2.1`（mascot の `uv.lock` で固定） | `themes/revealjs-basic/page.html`、`npm-shrinkwrap.json` |
| reveal.js | `5.2.1`（sphinx-revealjs の `npm-shrinkwrap.json` で固定） | `js/reveal.js`、`js/controllers/keyboard.js`、標準プラグイン |

ビルドの条件は次のとおりである（2026-11-21）。

- リポジトリの外に、uv の専用の仮想環境を作り、上表の版を入れた。mascot は GitHub Release の wheel を使った。
- `revealjs` ビルダーで、`-W`（警告をエラー扱い）を付けてビルドした。
- 画像は、mascot 同梱の標準画像8枚と、そこから必須4枚だけを取り出したものの2通りとした。どちらも警告なしでビルドできた。

### 結果

注入の目印となる要素：

- mascot はページごとの設定を `app.add_js_file(None, body=..., type='application/json', id='sphinx-revealjs-mascot-config')` で埋め込む。
- Sphinx 9.2.0 の `js_tag` は、属性を `f'{key}="{html.escape(value, quote=True)}"'`（二重引用符）の形で名前順に出力し、`script_files` を `<head>` に置く。このため、`<head>` に `<script id="sphinx-revealjs-mascot-config" type="application/json">` が出力され、仕様書 3.3 の文字列一致で判定できる。
- 出力されるのは `revealjs` と `dirrevealjs` ビルダーのときだけである。
- mascot の `test_mascot_build.py` は、ビルド結果をパースしてこの `id` の要素があることを確かめているが、属性の書き方（二重引用符）までは確かめていない。そこで、ビルド済み HTML に仕様書 3.3 と同じ文字列一致を当てたところ、8枚と必須4枚のどちらでも目印が見つかった。

`window.RevealMascot` の API：

- `Object.freeze` したオブジェクトで、`getAvailableActions()`、`trigger(action)`、`isVisible()` の3つを持つ。
- `isVisible()` は、`m` キー等で非表示にしている間に加え、画像の読み込みと配置が終わる前も偽を返す。
- `trigger(action)` は、表示前や非表示中の呼び出し、明示指定できないアクション（`doze`、`wake`）、画像のないアクションを、状態を変えずに無視する。ウトウト中は `wake`（`500ms`）だけを再生する。

`window.RevealMascot` が使えるようになるタイミング：

- スクリプトの実行順は次のとおりである。`defer` のスクリプトは文書順に実行されるため、注入する `client.js` は `mascot.js` より後に実行される。
  1. reveal.js 本体と `Reveal.initialize()`（`<body>` 内、同期実行）
  2. `mascot.js`（`<head>`、`defer`）
  3. `client.js`（`</body>` の直前、`defer`）
- ビルド済み HTML でも、`mascot.js`（`defer`）と設定の JSON は `<head>` に、reveal.js 本体と `Reveal.initialize()` は `<body>` の中にあった。実行順そのものは、ブラウザで確かめていない。
- `mascot.js` は、実行時に `Reveal.isReady()` が真ならその場で、偽なら `ready` イベントでキャラクターを生成し、最後に `window.RevealMascot` を定義する。
- reveal.js 5.2.1 は、`start()` の中で `isReady()` を真にし、`ready` イベントは `setTimeout(..., 1)` で後から発火する。`isReady()` が真なのに `ready` が未発火の時間がある。
- このため、`client.js` が `Reveal.isReady()` で判定すると、`mascot.js` が `ready` を待っている間に `RevealMascot` がないと誤って判断する場合がある。仕様書 4.1 では、`client.js` の実行時点になければ `ready` イベントで再確認するよう定めた。
- `Reveal.on()` は `.reveal` 要素への `addEventListener()` であり、リスナーは登録順に呼ばれる。`mascot.js` が `ready` を待つ場合は `client.js` より先にリスナーを登録しているため、`client.js` のリスナーが呼ばれる時点では `RevealMascot` が定義済みになる。

`available_actions` の取得元：

- `getAvailableActions()` が、配置した画像で明示指定できるアクション名を返す。仕様書 5.1 では、この戻り値をそのまま送ると定めた。
- 8枚すべて配置した場合は `["idle", "jump", "wink", "surprise", "happy", "tilt", "trouble", "proud"]` となり、仕様書 5.1 の例と一致する。必須4枚だけの場合は `["idle", "jump", "wink", "surprise"]` となる。音声感情対象の5種のうち残るのは `surprise` だけで、仕様書 5.2 の「有効候補が `surprise` の1種のみ」と一致する。
- ビルド済み HTML に埋め込まれた設定から、同じ規則（`explicit` が真のアクション）で求めた値も、8枚と必須4枚のどちらでも上と一致した。

### マイク切り替えの既定キーを `r` に変更する

- reveal.js 5.2.1 は `V` キーを一時停止（黒画面）に割り当てている（`keyboard.js` の `keyCode` 86）。仕様書の既定値だった `v` では、マイクを切り替えるたびに画面が暗転する。
- reveal.js 本体が使う英字キー（N、P、H、L、K、J、B、V、F、A、G、O、オーバーレイ表示中の C）、標準プラグインのキー（スピーカーノートの S）、mascot の既定キー（M）のいずれとも重ならない `r` を、既定値とした。
- 残る注意点：`--toggle-key` と mascot の `revealjs_mascot_toggle_key` に同じキーを指定すると、マイクの切り替えとマスコットの表示切り替えが両立しない。仕様書 3.2 で、同じキーを指定しないよう定めた。ソースからの推測で、動作は確かめていない。
  - mascot は `showMascot()` の中で、`document` に capture 段階の `keydown` リスナーを登録し、対象のキーで `stopImmediatePropagation()` を呼ぶ。`showMascot()` が呼ばれるのは、`mascot.js` の実行時（`Reveal.isReady()` が真の場合）か、`ready` イベントの時点である。
  - `client.js` のリスナーが mascot より後に呼ばれる場合（`document` に後から登録した場合など）は、キー操作が `client.js` に届かない。先に呼ばれる場合（`window` に capture 段階で登録した場合など）は、マイクとマスコットの表示が同時に切り替わる。

## Jev の応答時間と応答形式を確認する（2026-11-21）

### 測定条件

- 利用者の環境（Windows 11、Python 3.14.3）から、2026-11-21 02:55（JST）に `https://api.typesafe.ai/v1/systemone` へ送った。応答ヘッダーの `CF-RAY` によると、Cloudflare の東京拠点（`NRT`）を経由している。
- リクエスト本文は仕様書 5.3 の JSON そのもので、`model` は `jev-1.13.0`、`state` だけを差し替えた。発話は10種類（反応すべきもの7件、反応不要なもの3件）を2巡させ、計20回送った。
- 1秒間隔で順に送り、本番の中継サーバー（`urllib.request`）と同じく、リクエストごとに新しい接続を張った。接続と応答待ちを分けて測るため、`http.client` を直接使った。
- 計測用のスクリプトは使い捨てとし、リポジトリには置いていない。

### 結果：応答時間

20回すべてが `200` を返した。サーバー側タイムアウト（`750ms`）を超えた回はなく、仕様の見直しは不要と判断した。

| 区間 | 中央値 | 最小 | 最大 |
| :--- | :--- | :--- | :--- |
| 合計（新規接続を含む、本番相当） | `195.1ms` | `174.4ms` | `244.7ms` |
| 接続後の応答待ち | `144.0ms` | `136.3ms` | `173.3ms` |
| 接続（DNS・TCP・TLS） | `50.0ms` | `36.0ms` | `100.6ms` |

- 最大値は1回目の `244.7ms` で、接続（`100.6ms`）が他の回より遅かった。
- 1回の入力は1,288〜1,315トークン、出力は74〜75トークンだった（応答の `usage`）。

### 結果：応答の構造

正常時の応答は次の形式である（1回目の応答）。

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "should_react": {"type": "noul", "noul": 0.89},
    "reaction": {
      "type": "choice",
      "choice": "happy",
      "confidence": 1.0,
      "probabilities": {"happy": 1.0, "surprise": 0.0, "tilt": 0.0, "trouble": 0.0, "proud": 0.0}
    }
  },
  "usage": {"input_tokens": 1315, "output_tokens": 74}
}
```

- 仕様書 5.4 が前提とする `answers.should_react.noul` と `answers.reaction.confidence` は、20回すべての応答に含まれていた。
- 選ばれたアクションは `answers.reaction.choice` に入る。仕様書 5.4 に記載がなかったため、追記した。
- `Noul` の応答（`answers.should_react`）には `type` と `noul` だけが入り、`confidence` と `choice` は含まれない。このため、仕様書 5.2 の `Choice` を送らない1択の構成（候補が `surprise` だけ）には、5.4 の採択条件をそのまま当てはめられない。仕様書 5.4 では、採択条件の適用を `Choice` を送る場合に限った。1択の構成の質問文と採択条件は未定とし、判定処理の実装前に定めることにした。
- `answers.reaction.probabilities` には候補ごとの確率が入り、合計は毎回 `1.0` だった。仕様書 4.4 の注記（候補を除外すると、残りの選択肢へ確率が配分される）と矛盾しない。
- 応答ヘッダーには、`x-typesafe-request-id` でリクエスト ID が付く。

エラー時の応答は、`{"detail": {"error_type": "...", "message": "..."}}` の形式だった。

| 送ったもの | ステータス | `error_type` | `message` |
| :--- | :--- | :--- | :--- |
| `Authorization` ヘッダーなし | `403` | `authentication_error` | `Must supply an API key! Check your request and try again.` |
| `reaction` の `type` を不正な値にしたもの | `400` | `api_usage_error` | `Invalid request.` |

### 判定値の傾向（参考）

10種類の発話を2回ずつ送っただけであり、しきい値の妥当性を判断できる量ではない。同じ発話に対する `noul` の差は、2回で `0.01` 以内だった。

| 発話 | `noul` | `choice` | `confidence` | 採択 |
| :--- | :--- | :--- | :--- | :--- |
| ここまで続けてこられたのも皆さんのおかげです、本当にありがとうございます | `0.88〜0.89` | `happy` | `1.0` | する |
| 実はこれ、わずか0.1秒で終わります | `0.71` | `surprise` | `1.0` | する |
| なぜこうなると思いますか、ここで一つ疑問が浮かびます | `0.62` | `tilt` | `1.0` | しない（`noul` 不足） |
| 最初は全然動かなくて、設定の沼にハマってしまいました | `0.90〜0.91` | `trouble` | `1.0` | する |
| ここを自動化したのが一番の工夫で、無事にやり切ることができました | `0.84` | `proud` | `1.0` | する |
| なんと利用者は従来の10倍に増えました | `0.93` | `surprise` | `1.0` | する |
| 次はさらにここを目指していきます | `0.08` | `proud` | `1.0` | しない（`noul` 不足） |
| このスライドでは全体の構成を順番に説明していきます | `0.03` | `tilt` | `0.26〜0.29` | しない |
| まず設定ファイルを開いて、次にビルドコマンドを実行します | `0.03` | `tilt` | `0.26〜0.29` | しない |
| それでは次のスライドに進みたいと思います | `0.03` | `happy` | `0.39〜0.46` | しない |

- 反応不要な3件は `noul` が `0.03` で、共通ゲートで除外された。
- 問いかけの発話は `noul` が `0.62` で、共通ゲートの `0.65` をわずかに下回った。
- 「次はさらにここを目指していきます」は、`reaction` の `proud` の例に挙げた文だが、`noul` は `0.08` だった。`should_react` の `true` の基準に「今後の決意表明」が含まれていないためと推測する。
- 反応すべき発話では `confidence` が `1.0` になり、アクション別の下限は採否に影響しなかった。

### 未確認事項

- 時間帯や回線による応答時間の変動。計測は1回だけである。
- 仕様書 5.2 の2択の構成でのリクエスト。1択の構成は、驚き判定用の `Noul` の質問文が未定のため、送っていない。
