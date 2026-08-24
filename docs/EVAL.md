# ASR Accuracy Evaluation

Comparison of **Parakeet** (sherpa-onnx NeMo models) vs **faster-whisper large-v3-turbo** (INT8, CPU) on a small TTS-generated test set (8 Japanese + 8 English sentences, `testdata/eval/`).

- Japanese Parakeet model: `sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8` (NeMo CTC)
- English/multilingual Parakeet model: `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` (NeMo transducer)
- SenseVoice: `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` (non-autoregressive, use_itn=True, language forced per entry; the model Mojicast uses)
- Whisper: `large-v3-turbo`, `device=cpu`, `compute_type=int8`, `beam_size=1`, language forced per entry


## Per-file results

| file | lang | ref | Parakeet hyp | Parakeet metric | Parakeet RTF | SenseVoice hyp | SenseVoice metric | SenseVoice RTF | Whisper-turbo hyp | Whisper-turbo metric | Whisper-turbo RTF |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ja_01.wav | ja | 来週の火曜日に開催される定例会議の資料を、明日の夕方までに共有フォルダへアップロードしておいてください。 | 来週の火曜日に開催される定例会議の資料を明日の夕方までに共有フォルダーへアップロードしておいてください | CER=0.020 | 0.058 | 来週 の 火 曜日 に 開催 される 定例 会議 の資料 を、明日 の 夕方 まで に 共有 フォルダー へ アップロード して おい て ください。 | CER=0.020 | 0.025 | 来週の火曜日に開催される定例会議の資料を明日の夕方までに共有フォルダーへアップロードしておいてください | CER=0.020 | 0.780 |
| ja_02.wav | ja | 昨日の夜、友達と久しぶりに近所の居酒屋で飲んだんだけど、すごく楽しかったよ。 | 昨日の夜友達と久しぶりに近所の居酒屋で飲んだんだけどすごく楽しかったよ | CER=0.000 | 0.054 | 昨日 の 夜、友達 と 久しぶり に 近所 の 居酒屋 で 飲ん だん だ けど、すごく 楽しかっ た よ。 | CER=0.000 | 0.024 | 昨日の夜、友達と久しぶりに近所の居酒屋で飲んだんだけど、すごく楽しかったよ。 | CER=0.000 | 1.101 |
| ja_03.wav | ja | 本日の売上は合計で三百四十二万円となり、先月の同じ日と比べて約十五パーセント増加しました。 | 本日の売り上げは合計で342万円となり先月の同じ日と比べて約15%増加しました。 | CER=0.326 | 0.051 | 本日の 売上 は 合計 で 342万 円 となり、先月 の 同じ 日 と 比べ て 約 15%増加 しま した。 | CER=0.279 | 0.024 | 本日の売上は合計で342万円となり 先月の同じ日と比べて約15%増加しました | CER=0.279 | 0.761 |
| ja_04.wav | ja | 次の打ち合わせは二千二十六年九月三日の午後三時から、四階の会議室で行う予定です。 | 次の打ち合わせは2026年9月3日の午後3時から4階の会議室で行う予定です | CER=0.237 | 0.053 | 次 の 打ち合わせ は、2026 年 9 月 3 日 の 午後 3 時 から 4 回 の 会議 室 で 行う 予定 です。 | CER=0.263 | 0.024 | 次の打ち合わせは、2026年9月3日の午後3時から、4回の会議室で行う予定です。 | CER=0.263 | 0.945 |
| ja_05.wav | ja | このアプリケーションはクラウド上のサーバーレス関数を利用して、大量のデータをリアルタイムで処理しています。 | このアプリケーションはクラウド上のサーバーレス関数を利用して大量のデータをリアルタイムで処理しています | CER=0.000 | 0.052 | このアプリケーションはクラウド上のサーバーレス関数を利用して、大量のデータをリアルタイムで処理しています。 | CER=0.000 | 0.024 | このアプリケーションはクラウド上のサーバーレス関数を利用して大量のデータをリアルタイムで処理しています | CER=0.000 | 0.883 |
| ja_06.wav | ja | 駅前にできた新しいカフェのラテがすごく美味しくて、もう三回も通っちゃった。 | 駅前にできた新しいカフェのラテがすごくおいしくてもう3回も通っちゃった | CER=0.086 | 0.054 | 駅前 に で きた 新しい カフェ の ラテ が すごく お味しく て、もう 3 回 も 通 っちゃっ た。 | CER=0.057 | 0.025 | 駅前にできた新しいカフェのラテがすごく美味しくて、もう3階も通っちゃった。 | CER=0.057 | 1.206 |
| ja_07.wav | ja | 契約書の第三条には、解約する場合は少なくとも三十日前までに書面で通知することと明記されています。 | 契約書の第3条には解約する場合は少なくとも30日前までに書面で通知することと明記されています。 | CER=0.065 | 0.052 | 契約書の第三条には解約する場合は少なくとも30日前までに書面で通知することと明記されています。 | CER=0.043 | 0.026 | 契約書の第3条には解約する場合は少なくとも30日前までに書面で通知することと明記されています | CER=0.065 | 0.846 |
| ja_08.wav | ja | 新しく導入したデータベースのインデックス設計のおかげで、検索速度が約五倍になりました。 | 新しく導入したデータベースのインデックス設計のおかげで検索速度が約5倍になりました | CER=0.024 | 0.052 | 新しく 導入 した データベース の インデックス 設計 の おかげ で、検索 速度 が 約 5 倍 に なり ま した。 | CER=0.024 | 0.024 | 新しく導入したデータベースのインデックス設計のおかげで、検索速度が約5倍になりました。 | CER=0.024 | 1.048 |
| en_01.wav | en | Could you please send me the quarterly budget report before the end of business tomorrow? | Could you please send me the quarterly budget report before the end of business tomorrow? | WER=0.000 | 0.062 | Could you please send me the quarterly budget report before the end of business tomorrow? | WER=0.000 | 0.025 | Could you please send me the quarterly budget report before the end of business tomorrow? | WER=0.000 | 1.424 |
| en_02.wav | en | Hey, are we still on for coffee this weekend, or did something come up on your end? | Hey, are we still on for coffee this weekend, or did something come up on your end? | WER=0.000 | 0.065 | Hey, are we still on for coffee this weekend or did something come up on your end? | WER=0.000 | 0.032 | Hey, are we still on for coffee this weekend, or did something come up on your end? | WER=0.000 | 1.368 |
| en_03.wav | en | Total revenue for the third quarter reached four point six million dollars, up twelve percent from last year. | Total revenue for the third quarter reached$4.6 million, up 12% from last year. | WER=0.333 | 0.061 | Total revenue for the third quarter reached $4.6 million, up 12% from last year. | WER=0.333 | 0.030 | Total revenue for the third quarter reached $4.6 million, up 12% from last year. | WER=0.333 | 0.989 |
| en_04.wav | en | The project deadline has been moved to September twenty third, so we need to finalize the design by next Friday. | The project deadline has been moved to September 23, so we need to finalize the design by next Friday. | WER=0.100 | 0.061 | The project deadline has been moved to September 23r, so we need to finalize the design by next Friday. | WER=0.100 | 0.026 | The project deadline has been moved to September 23rd, so we need to finalize the design by next Friday. | WER=0.100 | 1.067 |
| en_05.wav | en | The new microservice architecture uses containerized deployments and an event driven message queue for scalability. | The new microservice architecture uses containerized deployments and an event driven message queue for scalability. | WER=0.000 | 0.058 | The new microservice architecture uses containerized deployments and an event driven message queue for scalability. | WER=0.000 | 0.027 | The new microservice architecture uses containerized deployments and an event-driven message queue for scalability. | WER=0.000 | 1.004 |
| en_06.wav | en | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | WER=0.000 | 0.066 | I tried that new Raman place downtown last night, and honestly the broth was even better than I expected. | WER=0.053 | 0.029 | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | WER=0.000 | 1.146 |
| en_07.wav | en | Please make sure the invoice number twelve thousand and forty five is paid within thirty days of receipt. | Please the invoice number 12045 is paid within 30 days of receipt. | WER=0.444 | 0.057 | Please make sure the invoice number 12045 is paid within 30 days of receipt. | WER=0.333 | 0.031 | please make sure the invoice number 12,045 is paid within 30 days of receipt | WER=0.333 | 1.145 |
| en_08.wav | en | We upgraded the database indexing strategy, and query latency dropped from two hundred milliseconds to about forty. | We upgraded the database indexing strategy, and query latency dropped from 200 milliseconds to about 40. | WER=0.176 | 0.061 | We upgraded the database indexing strategy, and query latency dropped from 200 milliseconds to about 40. | WER=0.176 | 0.031 | We upgraded the database indexing strategy, and query latency dropped from 200 milliseconds to about 40. | WER=0.176 | 0.961 |

## Aggregate

| system | WER (en, micro-avg) | CER (ja, micro-avg) | mean RTF |
|---|---|---|---|
| Parakeet | 0.1367 | 0.0914 | 0.0573 |
| SenseVoice | 0.1295 | 0.0826 | 0.0267 |
| Whisper-turbo | 0.1223 | 0.0855 | 1.0422 |

## Caveats

- **TTS audio is easier than real speech.** These references were synthesized with edge-tts and are clean, single-speaker, noise-free recordings with no disfluencies, overlaps, accents, or background noise. Real-world microphone input will show meaningfully higher error rates than reported here.

- **Small sample.** Only 8 Japanese and 8 English sentences were evaluated. These numbers are indicative of relative system behavior, not statistically robust estimates — a single unusual sentence can swing the aggregate noticeably.

- **Residual normalization noise from number/date formatting.** TTS references were written with a mix of kanji numerals, half-width digits, and spelled-out forms (e.g. 三時 vs 3時, "twenty three" vs "23"), while ASR systems often output digits regardless of how the reference was written. NFKC normalization plus punctuation/space stripping only fixes full-width vs half-width digit differences; it does not reconcile spelled-out numbers/dates against digit forms or vice versa. Some of the measured CER/WER is this formatting mismatch rather than an actual mistranscription.

