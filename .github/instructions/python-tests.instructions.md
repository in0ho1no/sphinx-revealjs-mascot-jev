---
applyTo: "src/tests/**/*.py"
description: "Python の pytest ベースのテストファイルを編集・作成するときの補足ルール。"
---

- テストは pytest を使用する
- テストファイルは `src/tests/` に `test_*.py` の名前で置く
- テストは読みやすさを優先し、必要な前提だけを明示する
- テスト追加・変更後は、必要最小限の範囲を `uv run pytest` で実行して確認する
