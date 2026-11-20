# sphinx-revealjs-mascot-jev 仕様書（独立リポジトリ用）

## 1. 背景と目的

[`sphinx-revealjs-mascot`](https://github.com/in0ho1no/sphinx-revealjs-mascot)（[`v0.1.3`](https://github.com/in0ho1no/sphinx-revealjs-mascot/releases/tag/v0.1.3) 以降）は、単一キャラクターの状態画像差し替えと外部操作 API（`window.RevealMascot`）を備えた静的 HTML 完結の Sphinx 拡張である。

本ツール（仮称：`sphinx-revealjs-mascot-jev`）は、本体リポジトリとは独立したパッケージとして提供し、ビルド済みの Reveal.js スライドディレクトリを配信するローカル CLI サーバーとして動作する。スライド側の `conf.py` や原稿に一切手を加えることなく、[Web Speech API（`SpeechRecognition`）](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition) と判断特化型 AI モデル [TypeSafe AI Jev](https://docs.typesafe.ai/introduction/quickstart) を組み合わせて「発表者の発話の一区切りに応じた自然な感情リアクション」を付与することを目的とする。

## 2. 前提・基本方針

1. **本体無改造・設定ファイル非侵襲**: `sphinx-revealjs-mascot` 本体およびスライド側の `conf.py`・原稿ファイルの変更を必要としない。ローカル配信時に HTML へクライアントスクリプトを動的注入する方式を採る。
2. **技術スタックの最小化**: Python 3.14 以上を対象とし、Node.js やフロントエンドのビルド工程を設けない（クライアント資産は単一のプレーン JavaScript・CSS で構成する）。
3. **API キーの秘匿と互換接続**: `TYPESAFE_API_KEY` はローカル Python サーバー側でのみ保持し、ブラウザ配信データには一切含めない。接続先 URL は環境変数等で差し替え可能とし、[Kev](https://dev.classmethod.jp/articles/kev-oss-jev-compatible-docker/) 等の [`POST /v1/systemone`](https://docs.typesafe.ai/api/system-one) 互換ローカルサーバーへも切り替えられるようにする。
4. **メイン投影画面限定の認識処理**: [Reveal.js](https://revealjs.com/) のスピーカービュー（`S` キー）内の `iframe` 等では音声認識を起動せず、メイン画面のみで音声取得・判定・描画を行う。
5. **キャラクターの世界観に沿った居眠り復帰**: 居眠り（`doze`）中の発話は「眠っていて内容を聞けていない」ものとして扱い、音声判定ヒット時は目覚め（`wake`）のみを行い、感情アクションの遅延再生は行わない。
6. **厳格なタイムアウトと静かな縮退運転**: 応答遅延（`800ms` 超）や通信異常時は即座に破棄し、画面上にエラー表示を出さず通常のスライド連動マスコット動作を継続する。

## 3. システム構成と CLI 仕様

### 3.1 全体構成

本パッケージは以下の2要素で構成する。

- **ローカル配信・中継サーバー（Python）**: ビルド済み HTML ディレクトリの静的ファイル配信、対象 HTML へのクライアントスクリプト自動注入、および `/__mascot_jev__/decide` から Jev API への判定中継を担う。
- **注入クライアント（`client.js` / `client.css`）**: ブラウザ上で音声テキストの蓄積・無音検知・クールタイム管理を行い、判定結果を受け取って `window.RevealMascot.trigger(action)` を呼び出す。

### 3.2 CLI インターフェースと環境変数

起動コマンドの形式：`mascot-jev <html_dir> [options]`

| 引数・環境変数 | 既定値 | 内容 |
| :--- | :--- | :--- |
| `<html_dir>` | （必須） | `sphinx-revealjs` のビルド出力ディレクトリ（例：`_build/revealjs`） |
| `--host` | `127.0.0.1` | 待ち受けホスト（マイク権限維持のためループバックを既定とする） |
| `--port` | `8000` | 待ち受けポート番号 |
| `--lang` | `ja-JP` | `SpeechRecognition` に設定する音声認識言語コード |
| `--toggle-key` | `r` | マイク認識の待機 ON / OFF を切り替える単一キー（英数字1文字） |
| `TYPESAFE_API_KEY` | 未設定 | TypeSafe AI（または互換サーバー）の Bearer 認証トークン |
| `TYPESAFE_BASE_URL` | `https://api.typesafe.ai` | `/v1/systemone` の送信先ベース URL |
| `TYPESAFE_MODEL` | `jev-1.13.0` | 評価に使用するモデル識別子 |

- `TYPESAFE_API_KEY` が未設定のまま起動された場合（かつローカル無認証互換サーバーでもない場合）は、サーバー起動自体は妨げずターミナル上に警告を1行出力し、判定 API は常に無反応（`action: null`）を返す。

### 3.3 HTML へのスクリプト自動注入ルール

- 静的ファイル配信時、レスポンスが `text/html` であり、かつ本文中に `id="sphinx-revealjs-mascot-config"` を含むページにのみ注入を行う。
- `</body>` タグの直前に以下の要素を挿入し、挿入後の UTF-8 バイト長で `Content-Length` ヘッダを再計算して返却する。
  - `<link rel="stylesheet" href="/__mascot_jev__/client.css">`
  - `<script id="sphinx-revealjs-mascot-jev-config" type="application/json">{"lang": "...", "toggleKey": "..."}</script>`
  - `<script src="/__mascot_jev__/client.js" defer></script>`

## 4. ブラウザ側クライアント仕様（`client.js` / `client.css`）

### 4.1 起動ガード条件（スピーカービュー対策）

以下のいずれかに該当する場合は、イベント登録や `SpeechRecognition` の生成を行わず即座に処理を終了する。

1. `window.self !== window.parent`（`iframe` 内での実行）
2. `new URLSearchParams(window.location.search).has("receiver")`（Reveal.js スピーカービューのプレビュー画面）
3. `window.SpeechRecognition` および `window.webkitSpeechRecognition` が未定義（非対応ブラウザ）
4. `client.js` の実行時点で `window.RevealMascot` が存在せず、その後に発火する Reveal.js の `ready` イベントの時点でも存在しない

4 の判定に `Reveal.isReady()` は使わない。reveal.js は `ready` イベントを発火する前に `isReady()` を真にするため、mascot 本体が `ready` を待っている間に判定すると、`RevealMascot` を見落とす（根拠は [records.md](records.md) に記録する）。

### 4.2 マイク待機切り替えと最小インジケータ

- 初期状態はマイク認識 **OFF** とする（ブラウザのマイク許可プロンプトをページ読込直後に突然出さず、発表開始前のキー操作を起点にするため）。
- `toggleKey`（既定 `r` キー）押下時に認識 ON / OFF をトグルする。ただし、`altKey`・`ctrlKey`・`metaKey` 押下時、または `input`・`textarea`・`select`・`isContentEditable` 要素にフォーカスがある場合は反応しない。
- OFF に切り替えたら認識を停止し、発話バッファと無音タイマーをクリアする。送信済みの判定を無効化し、進行中のローカル API リクエストを中止する。
- マイク認識 ON 中は `#chara` 要素に `data-speech-listening="true"` を付与し、`client.css` によりキャラクター領域の端へ視覚的に邪魔にならない極小ドット（例：`6px` 円形、不透明度 `0.7`）を表示する。
- 無音区間による `SpeechRecognition` の自動終了（`end` イベント）に対しては、マイク認識フラグが ON の間は自動で `start()` を再実行して待機を継続する。
- `window.RevealMascot.isVisible()` が偽の間は、発話バッファの蓄積および API 送信を行わない。`isVisible()` は、`m` キー等でマスコットを非表示にしている間に加え、画像の読み込みと配置が終わる前も偽を返す。

### 4.3 発話テキスト蓄積・無音デバウンス・文脈抽出

1. **無音デバウンス（`1500ms`）**:
   - `continuous = true`、`interimResults = true` で認識結果を受け取り、未確定・確定テキストを現在区間のバッファへ反映する。
   - 最後の `result` イベント受信から **`1500ms`** 経過しても次の更新が来なかった時点で「発話の一区切り」とみなして評価処理を起動し、バッファをクリアする。
2. **最小文字数フィルタ（`8文字`）と最大長切り詰め（`120文字`）**:
   - 空白を除いた文字数が **`8文字未満`**（「はい」「では次です」等の短い繋ぎ言葉）の場合は API を呼ばず破棄する。
   - 過去の話題への引きずりを防ぐため、送信する発話文字列（`utterance`）は直近区間の末尾最大 **`120文字`** のみとする。
3. **現在スライド見出しの抽出（`slide_title`）**:
   - `Reveal.getCurrentSlide()` から最初に見つかる見出し要素（`h1, h2, h3`）の `textContent` を前後の空白を除去して最大 **`60文字`** まで取得し、`slide_title` とする（該当要素がない場合は空文字列 `""`）。

### 4.4 クールタイム制御（クライアント側）

「連続で動きすぎる鬱陶しさ」と「スライド操作演出との衝突」を防ぐため、以下の3種の時間制御を行う。

1. **スライド遷移直後ガード（`4秒`）**:
   - `Reveal` の `slidechanged` または `fragmentshown` が発火した時点で蓄積中の音声バッファと無音タイマーをリセットし、その後 **`4000ms`** 間は音声判定を休止する。
2. **全体クールタイム（`12〜15秒`）**:
   - 音声判定によってアクションを発火した時点から、`tilt` の場合は **`12000ms`**、その他のアクション（`happy`, `surprise`, `trouble`, `proud`）の場合は **`15000ms`** の間、新たな API 送信を行わずバッファを破棄する。
3. **同種アクション連続抑制（`25〜35秒`）**:
   - 各アクションの最終発火時刻を記録し、規定秒数（`tilt: 25秒`, `trouble: 30秒`, `proud: 30秒`, `happy: 30秒`, `surprise: 35秒`）が経過していないアクションが返却された場合は発火を見送る。
   - ※リクエスト時点で `Choice` 候補から一時除外すると残りの選択肢へ確率が強制配分されて誤判定の原因になるため、候補自体は維持したまま応答後にクライアント（またはサーバー）側で抑制する。

### 4.5 アクション発火とタイムアウト制御

- ローカル API 呼び出し（`POST /__mascot_jev__/decide`）には `AbortController` で **`800ms`** のタイムアウトを設定する。
- 判定結果は、送信時からマイクの ON 状態が継続している場合に使用する。OFF を挟んだ応答は、再び ON に切り替えた後に届いた場合も破棄する。Jev に到達済みのリクエストは、ブラウザの中止後も上流側で処理される場合がある。
- 応答で有効な `action` 文字列が返り、かつその時点でもクールタイム外かつ `window.RevealMascot.isVisible()` が真であれば、[`mascot.js`](https://github.com/in0ho1no/sphinx-revealjs-mascot/blob/develop/src/sphinx_revealjs_mascot/static/sphinx-revealjs-mascot/mascot.js) が公開する **`window.RevealMascot.trigger(action)` を1回だけ呼び出す**。
  - 通常待機中（`idle`）であれば指定された感情アクションが再生される。
  - 居眠り中（`doze`）であれば `v0.1.3` 本体の `activity(action)` 仕様により `wake`（`500ms`）のみが再生されて `idle` へ復帰する。追加の遅延再呼び出しは行わない。
- タイムアウト、ネットワークエラー、非 200 応答時は例外を握りつぶして `console.debug` のみに出力し、画面上のスライド進行を一切妨げない。

## 5. ローカル中継 API と Jev 判定仕様（サーバー側）

### 5.1 エンドポイント仕様（`POST /__mascot_jev__/decide`）

- **リクエスト JSON**:

  ```json
  {
    "slide_title": "10年間の歩みとこれから",
    "utterance": "ここまで続けてこられたのも皆さんのおかげです、本当にありがとうございます",
    "available_actions": ["idle", "jump", "wink", "surprise", "happy", "tilt", "trouble", "proud"]
  }
  ```

  `available_actions` には、クライアントが `window.RevealMascot.getAvailableActions()` の戻り値をそのまま入れる。戻り値は、配置した画像で明示指定できるアクション名の配列であり、`doze` と `wake` を含まない。

- **レスポンス JSON**:

  ```json
  {
    "action": "happy",
    "noul": 0.91,
    "confidence": 0.78
  }
  ```

  （反応不要・閾値未達・エラー時は `{"action": null}` をステータス `200` で返す）

### 5.2 有効アクションの動的フィルタリング

サーバーは `available_actions` のうち、音声感情対象の5種（`happy`, `surprise`, `tilt`, `trouble`, `proud`）に含まれるものだけを抽出して `Choice` の `criteria` を構築する。

- 標準8枚または全追加画像配置時：`5択` で評価する。
- カスタム4枚＋一部追加画像（例：`happy` のみ）配置時：`surprise` と `happy` の `2択` で評価する。
- カスタム必須4枚のみ配置時：有効候補が `surprise` の1種のみとなるため、`Choice` は送らず `should_react` に加えて驚き判定専用の `Noul` 1問のみで評価する（または候補0件時は即座に `{"action": null}` を返す）。

### 5.3 Jev（`/v1/systemone`）リクエスト定義

[TypeSafe 公式の質問設計指針（`Primitives`）](https://docs.typesafe.ai/primitives)および[構造化 `Choice` ガイド](https://docs.typesafe.ai/primitives/choice)に従い、`should_react`（`Noul`）と `reaction`（`Choice`）を1回の [`POST /v1/systemone`](https://docs.typesafe.ai/api/system-one) リクエストで並列評価する（サーバー側 HTTP タイムアウトは `750ms` とする）。

```json
{
  "model": "jev-1.13.0",
  "state": {
    "slide_title": "（現在スライドの見出し、最大60文字）",
    "utterance": "（直近の発話テキスト、最大120文字）"
  },
  "questions": {
    "should_react": {
      "type": "noul",
      "instructions": "プレゼン発表者の直近の発話（utterance）に、聞き手のマスコットが感情リアクションを返すべき明確な山場や感情表現が含まれているか？（slide_title は話題の背景参考にとどめ、判定は utterance を主軸に行うこと）",
      "criteria": {
        "true": "感謝・祝福、驚きの事実や数値、聴衆への問いかけ、失敗や苦労の吐露、工夫や達成のアピールのいずれかが utterance に明確に含まれている。",
        "false": "次のスライドへの繋ぎ言葉、構成や手順の淡々とした事実説明、定型挨拶、言い淀みのみで感情の山場がない。"
      }
    },
    "reaction": {
      "type": "choice",
      "instructions": "発表者の発話（utterance）に対して、小鳥のマスコットが返すのに最もふさわしいリアクションを1つ選んでください。",
      "criteria": {
        "happy": {
          "covers": "感謝、お祝い、節目の喜び、好評だった報告、純粋に嬉しい出来事",
          "not_for": "技術的な工夫や解決策の自慢は proud を選ぶ",
          "examples": ["10周年を迎えられました", "本当にありがとうございます", "とても好評でした"]
        },
        "surprise": {
          "covers": "意外な事実、直感に反するデータ、極端な規模や速度のギャップ、ポジティブまたは中立な驚き",
          "not_for": "障害や失敗で困った話は trouble を選ぶ",
          "examples": ["実はこれ、わずか0.1秒で終わります", "なんと従来の10倍に増えました", "予想外の結果が出ました"]
        },
        "tilt": {
          "covers": "聴衆への問いかけ、疑問の提示、なぜだろうという思考の導入",
          "not_for": "すでに失敗して困っている苦労話は trouble を選ぶ",
          "examples": ["なぜこうなると思いますか", "ここで一つ疑問が浮かびます", "本当にそれだけで十分でしょうか"]
        },
        "trouble": {
          "covers": "過去の失敗談、苦労話、ハマりポイント、想定外のトラブルや反省",
          "not_for": "問題を見事に解決した自慢は proud を選ぶ",
          "examples": ["最初は全然動かなくて苦労しました", "ここで痛い目を見ました", "設定の沼にハマってしまいました"]
        },
        "proud": {
          "covers": "独自の工夫や解決策のアピール、達成への自信、強みの紹介、今後の力強い決意表明",
          "not_for": "聴衆への感謝や単なるお祝いは happy を選ぶ",
          "examples": ["ここを自動化したのが一番の工夫です", "無事にやり切ることができました", "次はさらにここを目指していきます"]
        }
      }
    }
  }
}
```

### 5.4 確信度（`confidence`）しきい値テーブル

Jev が返す [`confidence`（確信度）](https://docs.typesafe.ai/confidence)と `should_react.noul` に対し、以下の両条件を満たした場合のみアクションを採択する。

1. **共通ゲート条件**: `answers.should_react.noul >= 0.65`
2. **アクション別確信度条件**: `answers.reaction.confidence` が下表の下限値以上であること

採択するアクションは、`answers.reaction.choice` の値とする。

| アクション名 | `confidence` 下限 | 発火後全体クールタイム | 同種リピート抑制時間 |
| :--- | :--- | :--- | :--- |
| `tilt` | `0.45` | `12秒` | `25秒` |
| `trouble` | `0.55` | `15秒` | `30秒` |
| `proud` | `0.55` | `15秒` | `30秒` |
| `happy` | `0.65` | `15秒` | `30秒` |
| `surprise` | `0.65` | `15秒` | `35秒` |

## セキュリティと外部送信データ

ローカルサーバーを安全に動かすための要件と、外部に送信されるデータの範囲を定める。採用判断の経緯は [records.md](records.md) に記録する。

### ローカルサーバーの防御

Python 標準ライブラリの `http.server` は、公式ドキュメントで本番用途に推奨されていない。本ツールは発表者1人がループバックで使う前提で `http.server` を用い、次の防御を実装する。

- 待ち受けアドレス：`--host` にループバック以外（例：`0.0.0.0`）が指定された場合は、起動は妨げずにターミナルへ警告を1行出力する。同じネットワーク上の第三者が `/__mascot_jev__/decide` を呼び出し、API キーの利用枠を消費できる状態になるため。
- `/__mascot_jev__/decide` のリクエスト検証：次のいずれかに該当するリクエストは Jev へ中継せずに拒否し、4xx を返す。正規の `client.js` からは発生しないため、通信異常時の `{"action": null}` 応答とは区別する。
  - `Host` ヘッダーが待ち受けアドレスとポート（ループバック時は `127.0.0.1:<port>` と `localhost:<port>`）と一致しない（DNS rebinding 対策）。
  - `Origin` ヘッダーがあり、配信元のオリジンと一致しない（閲覧中の別サイトからの呼び出し対策）。
  - `Content-Type` が `application/json` ではない（CORS のプリフライトを経ない単純リクエストを受け付けないため）。
  - 本文が上限サイズ（目安 `16KiB`）を超える。
- 配信範囲：`<html_dir>` 配下のファイルだけを配信し、ディレクトリ一覧は表示しない。
- API キー：`TYPESAFE_API_KEY` は環境変数からのみ読み込み、HTTP 応答・ログ・注入データのいずれにも含めない。
- 実行時依存：サーバーは Python 標準ライブラリ（`http.server`、`urllib.request`、`json`、`argparse`、`importlib.resources` など）で実装し、実行時の依存パッケージを持たない。追加が必要になった場合は、サプライチェーンリスクを踏まえて事前に検討する。

### HTML 注入の実装上の注意

3.3 の注入ルールに加え、次を満たす。

- 設定用 JSON を `<script type="application/json">` に埋め込む際は `<` を `\u003c` にエスケープし、値に `</script>` が含まれても要素の外へ出ないようにする。
- 挿入位置は、最後に現れる `</body>` の直前とする。`</body>` が見つからない HTML には注入せず、そのまま返す。
- 注入した応答には `Cache-Control: no-store` を付ける。起動オプションを変えて再起動した後に、ブラウザが条件付きリクエストへの `304` 応答を受けて、古い設定入りの HTML を使い続けるのを防ぐため。
- `HEAD` リクエストにも、`GET` と同じ注入後の `Content-Length` を返す。

### 外部に送信されるデータ

| 送信先 | 送信される内容 | 送信される期間・条件 |
| :--- | :--- | :--- |
| ブラウザが利用する音声認識サービス | マイク音声 | マイク認識 ON の間すべて（8文字未満の発話やクールタイム中の発話も含む） |
| TypeSafe AI Jev（互換サーバーに切り替えた場合はその送信先） | `utterance`（最大120文字）と `slide_title`（最大60文字） | 4.3・4.4 の条件を満たした発話の区切りのみ |

- 音声認識には Web Speech API（`SpeechRecognition`）を使う。`processLocally` は指定せず、認識を端末内で行うか外部で行うかはブラウザに任せる。
- 音声の送信範囲は、Jev への送信範囲より広い。マイクは発表中だけ ON にする運用とする（初期状態を OFF とする 4.2 の仕様も、この前提に沿う）。

## 6. ディレクトリ構成案と検証範囲

### 6.1 推奨ディレクトリ構成

```text
sphinx-revealjs-mascot-jev/
├── pyproject.toml
├── README.md
├── docs/
│   ├── jev_mascot_spec.md
│   └── records.md
└── src/
    ├── sphinx_revealjs_mascot_jev/
    │   ├── __init__.py
    │   ├── cli.py              # 引数解析・サーバー起動エントリポイント
    │   ├── server.py           # 静的配信・HTMLスクリプト注入・/__mascot_jev__/decide
    │   ├── evaluator.py        # Jev リクエスト構築・閾値判定・HTTP クライアント
    │   └── static/
    │       ├── client.js       # SpeechRecognition・デバウンス・クールタイム・trigger呼び出し
    │       └── client.css      # マイク待機インジケータ最小スタイル
    └── tests/
        ├── test_injector.py    # HTML注入・Content-Length再計算・非対象HTML非改変テスト
        ├── test_evaluator.py   # 動的criteria生成・Noul/Choice閾値判定・タイムアウト処理テスト
        └── test_server.py      # /__mascot_jev__/decide エンドポイント結合テスト
```

### 6.2 検証範囲

- `id="sphinx-revealjs-mascot-config"` を持つ HTML にのみ `client.js` / `client.css` が注入され、持たない HTML や静的アセット（PNG・JS・CSS）は無改変で配信されること。
- `available_actions` の組み合わせ（8枚フル構成・部分配置・必須4枚のみ）に応じて `Choice` の選択肢が動的に絞られること。
- `should_react.noul` 未達時、`confidence` 下限未達時、API キー未設定時、および上流タイムアウト（`750ms` 超）時にすべて HTTP `200` かつ `{"action": null}` が返ること。
- クライアント側で `?receiver` 付き URL および `iframe` 内実行時に `SpeechRecognition` が初期化されないこと。
- 判定リクエストの送信後にマイクを OFF にするとリクエストが中止され、再び ON に切り替えた後に届く応答も破棄されること。
- スライド遷移後 `4秒`・発火後 `12〜15秒`・同種 `25〜35秒` の各クールタイムおよび `8文字未満` フィルタが機能し、発火時は `window.RevealMascot.trigger(action)` が1回のみ呼ばれること。
- `--host` にループバック以外を指定して起動すると、ターミナルに警告が1行出力されること。
- `/__mascot_jev__/decide` への `Host` 不一致・`Origin` 不一致・`Content-Type` 不一致・上限超過のリクエストが、Jev へ中継されずに 4xx で拒否されること。
- `<html_dir>` の外のファイルが配信されず、ディレクトリ一覧も表示されないこと。
- 注入した HTML の応答に `Cache-Control: no-store` が付き、`HEAD` でも注入後の `Content-Length` が返ること。
- 設定値に `</script>` を含めても設定用 `<script>` 要素の外へ出ないこと、および `</body>` を持たない HTML が無改変で配信されること。
