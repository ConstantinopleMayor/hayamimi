# ASR Accuracy Evaluation

Comparison of **Parakeet** (sherpa-onnx NeMo models) vs **faster-whisper large-v3-turbo** (INT8, CPU) on a small TTS-generated test set (8 Japanese + 8 English sentences, `testdata/eval/`).

- Japanese Parakeet model: `sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8` (NeMo CTC)
- English/multilingual Parakeet model: `sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` (NeMo transducer)
- Whisper: `large-v3-turbo`, `device=cpu`, `compute_type=int8`, `beam_size=1`, language forced per entry


## Per-file results

| file | lang | ref | Parakeet hyp | Parakeet metric | Parakeet RTF | Whisper hyp | Whisper metric | Whisper RTF |
|---|---|---|---|---|---|---|---|---|
| ja_01.wav | ja | 来週の火曜日に開催される定例会議の資料を、明日の夕方までに共有フォルダへアップロードしておいてください。 | 来週の火曜日に開催される定例会議の資料を明日の夕方までに共有フォルダーへアップロードしておいてください | CER=0.020 | 0.077 | 来週の火曜日に開催される定例会議の資料を明日の夕方までに共有フォルダーへアップロードしておいてください | CER=0.020 | 1.126 |
| ja_02.wav | ja | 昨日の夜、友達と久しぶりに近所の居酒屋で飲んだんだけど、すごく楽しかったよ。 | 昨日の夜友達と久しぶりに近所の居酒屋で飲んだんだけどすごく楽しかったよ | CER=0.000 | 0.082 | 昨日の夜、友達と久しぶりに近所の居酒屋で飲んだんだけど、すごく楽しかったよ。 | CER=0.000 | 1.582 |
| ja_03.wav | ja | 本日の売上は合計で三百四十二万円となり、先月の同じ日と比べて約十五パーセント増加しました。 | 本日の売り上げは合計で342万円となり先月の同じ日と比べて約15%増加しました。 | CER=0.326 | 0.071 | 本日の売上は合計で342万円となり 先月の同じ日と比べて約15%増加しました | CER=0.279 | 1.148 |
| ja_04.wav | ja | 次の打ち合わせは二千二十六年九月三日の午後三時から、四階の会議室で行う予定です。 | 次の打ち合わせは2026年9月3日の午後3時から4階の会議室で行う予定です | CER=0.237 | 0.095 | 次の打ち合わせは、2026年9月3日の午後3時から、4回の会議室で行う予定です。 | CER=0.263 | 1.536 |
| ja_05.wav | ja | このアプリケーションはクラウド上のサーバーレス関数を利用して、大量のデータをリアルタイムで処理しています。 | このアプリケーションはクラウド上のサーバーレス関数を利用して大量のデータをリアルタイムで処理しています | CER=0.000 | 0.055 | このアプリケーションはクラウド上のサーバーレス関数を利用して大量のデータをリアルタイムで処理しています | CER=0.000 | 1.298 |
| ja_06.wav | ja | 駅前にできた新しいカフェのラテがすごく美味しくて、もう三回も通っちゃった。 | 駅前にできた新しいカフェのラテがすごくおいしくてもう3回も通っちゃった | CER=0.086 | 0.076 | 駅前にできた新しいカフェのラテがすごく美味しくて、もう3階も通っちゃった。 | CER=0.057 | 1.778 |
| ja_07.wav | ja | 契約書の第三条には、解約する場合は少なくとも三十日前までに書面で通知することと明記されています。 | 契約書の第3条には解約する場合は少なくとも30日前までに書面で通知することと明記されています。 | CER=0.065 | 0.068 | 契約書の第3条には解約する場合は少なくとも30日前までに書面で通知することと明記されています | CER=0.065 | 1.335 |
| ja_08.wav | ja | 新しく導入したデータベースのインデックス設計のおかげで、検索速度が約五倍になりました。 | 新しく導入したデータベースのインデックス設計のおかげで検索速度が約5倍になりました | CER=0.024 | 0.079 | 新しく導入したデータベースのインデックス設計のおかげで、検索速度が約5倍になりました。 | CER=0.024 | 1.687 |
| en_01.wav | en | Could you please send me the quarterly budget report before the end of business tomorrow? | Could you please send me the quarterly budget report before the end of business tomorrow? | WER=0.000 | 0.094 | Could you please send me the quarterly budget report before the end of business tomorrow? | WER=0.000 | 2.137 |
| en_02.wav | en | Hey, are we still on for coffee this weekend, or did something come up on your end? | Hey, are we still on for coffee this weekend, or did something come up on your end? | WER=0.000 | 0.080 | Hey, are we still on for coffee this weekend, or did something come up on your end? | WER=0.000 | 2.025 |
| en_03.wav | en | Total revenue for the third quarter reached four point six million dollars, up twelve percent from last year. | Total revenue for the third quarter reached$4.6 million, up 12% from last year. | WER=0.333 | 0.075 | Total revenue for the third quarter reached $4.6 million, up 12% from last year. | WER=0.333 | 1.544 |
| en_04.wav | en | The project deadline has been moved to September twenty third, so we need to finalize the design by next Friday. | The project deadline has been moved to September 23, so we need to finalize the design by next Friday. | WER=0.100 | 0.086 | The project deadline has been moved to September 23rd, so we need to finalize the design by next Friday. | WER=0.100 | 1.547 |
| en_05.wav | en | The new microservice architecture uses containerized deployments and an event driven message queue for scalability. | The new microservice architecture uses containerized deployments and an event driven message queue for scalability. | WER=0.000 | 0.062 | The new microservice architecture uses containerized deployments and an event-driven message queue for scalability. | WER=0.000 | 1.041 |
| en_06.wav | en | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | WER=0.000 | 0.065 | I tried that new ramen place downtown last night, and honestly the broth was even better than I expected. | WER=0.000 | 1.135 |
| en_07.wav | en | Please make sure the invoice number twelve thousand and forty five is paid within thirty days of receipt. | Please the invoice number 12045 is paid within 30 days of receipt. | WER=0.444 | 0.056 | please make sure the invoice number 12,045 is paid within 30 days of receipt | WER=0.333 | 1.148 |
| en_08.wav | en | We upgraded the database indexing strategy, and query latency dropped from two hundred milliseconds to about forty. | We upgraded the database indexing strategy, and query latency dropped from 200 milliseconds to about 40. | WER=0.176 | 0.056 | We upgraded the database indexing strategy, and query latency dropped from 200 milliseconds to about 40. | WER=0.176 | 0.972 |

## Aggregate

| system | WER (en, micro-avg) | CER (ja, micro-avg) | mean RTF |
|---|---|---|---|
| Parakeet | 0.1367 | 0.0914 | 0.0736 |
| Whisper-turbo | 0.1223 | 0.0855 | 1.4400 |

## Caveats

- **TTS audio is easier than real speech.** These references were synthesized with edge-tts and are clean, single-speaker, noise-free recordings with no disfluencies, overlaps, accents, or background noise. Real-world microphone input will show meaningfully higher error rates than reported here.

- **Small sample.** Only 8 Japanese and 8 English sentences were evaluated. These numbers are indicative of relative system behavior, not statistically robust estimates — a single unusual sentence can swing the aggregate noticeably.

- **Residual normalization noise from number/date formatting.** TTS references were written with a mix of kanji numerals, half-width digits, and spelled-out forms (e.g. 三時 vs 3時, "twenty three" vs "23"), while ASR systems often output digits regardless of how the reference was written. NFKC normalization plus punctuation/space stripping only fixes full-width vs half-width digit differences; it does not reconcile spelled-out numbers/dates against digit forms or vice versa. Some of the measured CER/WER is this formatting mismatch rather than an actual mistranscription.

