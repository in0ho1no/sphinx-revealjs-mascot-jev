# sphinx-revealjs-mascot-jev

ビルド済みの Reveal.js スライド（[sphinx-revealjs-mascot](https://github.com/in0ho1no/sphinx-revealjs-mascot) 付き）を配信するローカル CLI サーバーです。発表者の発話をブラウザの音声認識でテキストにし、[TypeSafe AI Jev](https://docs.typesafe.ai/introduction/quickstart) の判定に応じてマスコットにリアクションさせます。

sphinx-revealjs-mascot でスライドを作っていて、発表中に話した内容にもマスコットを反応させたい人向けです。スライドの原稿と `conf.py` は、変えなくても使えます。

## 必要なもの

- [uv](https://docs.astral.sh/uv/)。Python 3.14 以上を使います。手元にない場合は、uv が用意します。
- sphinx-revealjs-mascot `v0.1.3` 以降でビルドしたスライド
- 音声認識（Web Speech API の `SpeechRecognition`）に対応したブラウザと、マイク。動作を確かめたのは、Windows 11 の Chrome です。
- TypeSafe AI の API キー。`POST /v1/systemone` 互換のサーバー（Kev など）も使えます。

## 導入

リポジトリを取得して wheel を作り、`uv tool install` で `mascot-jev` コマンドを入れます。wheel のファイル名の版は、`pyproject.toml` の `version` です。

```powershell
git clone https://github.com/in0ho1no/sphinx-revealjs-mascot-jev.git
cd sphinx-revealjs-mascot-jev
uv build
uv tool install --python 3.14 dist/sphinx_revealjs_mascot_jev-0.1.0-py3-none-any.whl
```

`mascot-jev --help` で引数の説明が出れば、導入できています。コマンドが見つからない場合は、`uv tool update-shell` を実行してから、ターミナルを開き直します。実行時に使う依存パッケージはありません。

アンインストールは `uv tool uninstall sphinx-revealjs-mascot-jev` です。

## スライドを用意する

`mascot-jev` は、sphinx-revealjs のビルド出力のディレクトリ（`revealjs` ビルダーの出力先）を配信します。sphinx-revealjs-mascot の導入とビルドの方法は、[sphinx-revealjs-mascot の README](https://github.com/in0ho1no/sphinx-revealjs-mascot#extension-usage) を参照してください。

`mascot-jev` と組み合わせるときは、スライドの `conf.py` で、マスコットが居眠りするまでの時間を延ばすことを勧めます。

```python
revealjs_mascot_idle_timeout = 75
```

マスコットは、スライドの操作とリアクションがないまま `revealjs_mascot_idle_timeout`（既定は 45 秒）たつと居眠りします。居眠り中に音声で反応すると、目覚めるだけで、感情のリアクションは出ません。話していても無操作の時間は延びないため、1枚のスライドで話し続けると、既定の 45 秒では反応の多くが目覚めだけになります。75 秒にした根拠は、[判断と検証の記録](docs/records.md#readme-guidance-recheck) にあります。

この設定は任意です。`mascot-jev` は、スライドの設定を変えません。

## 発表する

1. API キーを、環境変数 `TYPESAFE_API_KEY` に設定します。PowerShell 7.1 以降では、次のように入力すると、キーを画面に表示せずに設定できます。設定は、そのターミナルの中だけで有効です。

    ```powershell
    $env:TYPESAFE_API_KEY = Read-Host -MaskInput 'TYPESAFE_API_KEY'
    ```

2. 同じターミナルで、ビルド出力のディレクトリを指定して `mascot-jev` を起動します。

    ```powershell
    mascot-jev _build/revealjs
    ```

    `mascot-jev: http://127.0.0.1:8000/ で _build/revealjs を配信しています（Ctrl+C で停止）` と出れば起動しています。`TYPESAFE_API_KEY が未設定` の警告が出た場合は、マスコットが音声に反応しません。`Ctrl+C` で止めて、1 からやり直します。

    ポート、音声認識の言語、マイクを切り替えるキー、接続先とモデルは、引数と環境変数で変えられます。一覧は仕様書の [CLI インターフェースと環境変数](docs/jev_mascot_spec.md#cli-options) を参照してください。

3. 表示された URL（既定では `http://127.0.0.1:8000/`）を Chrome で開きます。

4. `r` を押して、マイクを ON にします。初回は、ブラウザがマイクの使用許可を求めるので許可します。ON の間は、マスコットの右上に小さな点が出ます。

5. そのまま発表します。発話の区切りごとに Jev が判定し、反応すべき発話であれば、マスコットが喜ぶ・驚く・首をかしげる・困る・得意げにする、のいずれかで反応します。

    - 使える反応は、マスコットに置いた画像で決まります（仕様書の [有効アクションの動的フィルタリング](docs/jev_mascot_spec.md#action-filtering)）。
    - 反応した直後と、スライドを切り替えた直後は、しばらく反応しません（仕様書の [クールタイム制御](docs/jev_mascot_spec.md#cooldown)）。
    - スピーカービュー（`s`）も使えます。音声認識は、メイン画面だけで動きます。
    - `m` でマスコットを隠している間は、発話を判定に送りません。

6. 発表が終わったら、`r` でマイクを OFF にし、ターミナルで `Ctrl+C` を押して `mascot-jev` を止めます。

## 外部に送られるデータ

マイクが ON の間の音声と、判定に回した発話・スライドの見出しは、外部のサービスに送られます。送信先と範囲は、仕様書の [外部に送信されるデータ](docs/jev_mascot_spec.md#external-data) を確認してから使ってください。

ローカルサーバーの待ち受けと、API キーの扱いについての要件は、仕様書の [セキュリティと外部送信データ](docs/jev_mascot_spec.md#security) にあります。

## うまく動かないとき

- 起動時に `127.0.0.1:<port> で待ち受けを開始できません` と出て終了する：そのポートを、ほかのプログラムが使っています。`--port` で別のポートを指定します。
- マスコットの右上に点が出ない：マイクが OFF です。`r` で ON にします。ブラウザでマイクの使用を許可していないと、ON になりません。
- 点は出ているのに反応しない：判定に送った発話の多くは、反応すべき内容ではないと判定されます。Jev に接続できない場合は、画面には何も出ず、ターミナルに `Jev request failed: ...` の警告が出ます。
- 別のサーバーのページが開く：`mascot-jev` が表示した URL 以外（`localhost` など）で開くと、同じポートをすべてのアドレスで待ち受けている別のサーバーにつながる場合があります。表示された URL を開きます（[判断と検証の記録](docs/records.md#readme-guidance-recheck)）。

## ドキュメント

- [仕様書](docs/jev_mascot_spec.md)
- [判断と検証の記録](docs/records.md)
