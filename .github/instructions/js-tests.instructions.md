---
applyTo: "src/**/*.js"
description: "client.js とそのテストを編集・作成するときの補足ルール。"
---

- `client.js` のテストは、Node.js 標準のテストランナー（`node:test`）で書き、`src/tests/js/` に `*.test.js` の名前で置く
- npm と `package.json` は使わない
- テストの実行: `node --test "src/tests/js/*.test.js"`（Node.js は `mise.toml` で固定した版を使う）
