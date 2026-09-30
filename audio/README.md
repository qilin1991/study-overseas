# 新闻听力音频生成（GitHub Actions）

在 GitHub 境外机房把采集到的 `news_overseas.json` 里的文章用 **Edge-TTS（免费）**
合成为英音 mp3 + 句级时间轴，上传到腾讯 COS，供 study 分站『听力精听』页使用。

## 输出
- `cos://qilin-1449368663/static/study_audio/<id>.mp3` 音频
- `cos://qilin-1449368663/static/study_audio/<id>.json` 时间轴（段级 start/end）

## 特点
- 增量生成：已存在的音频自动跳过
- 每段单独合成并记录 SentenceBoundary，时间轴精确到段
- 零成本（Edge-TTS 免费音色）

## 手动触发
Actions → gen-audio → Run workflow
