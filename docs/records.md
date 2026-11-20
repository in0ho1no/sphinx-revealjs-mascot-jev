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
