# Engine Scorecard (end-to-end, real speech)

本番経路 (LID→ルーティング→デコード→ja句読点) のエンドツーエンド採点。
単発クリップのためプリロール・二段パスは含まない。metricは en=WER, 他=CER（yueはt2s正規化）。

| lang | clips | LID正解 | 主tier | mean err | mean RTF |
|---|---|---|---|---|---|
| ja | 15 | 15/15 | rz | 0.075 | 0.071 |
| en | 15 | 15/15 | v3 | 0.023 | 0.109 |
| zh | 12 | 12/12 | pz | 0.053 | 0.102 |
| ko | 12 | 12/12 | sv | 0.081 | 0.062 |
| yue | 12 | 12/12 | sv | 0.061 | 0.061 |

## LID誤判定の内訳

誤判定なし。
