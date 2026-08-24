# Engine Scorecard (end-to-end, real speech)

本番経路 (LID→ルーティング→デコード→ja句読点) のエンドツーエンド採点。
単発クリップのためプリロール・二段パスは含まない。metricは en=WER, 他=CER（yueはt2s正規化）。

| lang | clips | LID正解 | 主tier | mean err | mean RTF |
|---|---|---|---|---|---|
| ja | 15 | 15/15 | rz | 0.075 | 0.066 |
| en | 15 | 15/15 | v3 | 0.023 | 0.104 |
| zh | 12 | 12/12 | pz | 0.053 | 0.100 |
| ko | 12 | 12/12 | sv | 0.081 | 0.042 |
| yue | 12 | 11/12 | sv+omni | 0.074 | 0.057 |

## LID誤判定の内訳

| wav | true | detected | tier | err |
|---|---|---|---|---|
| yue_02.wav | yue | vi | omni | 0.179 |
