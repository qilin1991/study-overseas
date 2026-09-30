# 境外英文阅读采集器（GitHub Actions）

在 GitHub 机房（出口在境外）运行，抓取**被墙的欧美主流媒体**文章，做免费英中对照翻译后
把结果文件 `news_overseas.json` 提交回本仓库，由 VPS 定时拉取合并进学习站。

## 覆盖源
The New York Times / BBC / The Guardian / Washington Post / CNN / Reuters /
Bloomberg / The Economist / WSJ / Fox News / AP News / National Geographic

## 工作机制
1. GitHub Actions 每天 2 次（北京 08:30 / 20:30）自动运行，或手动触发
2. 每个源抓最新 3 篇，RSS 取标题链接 + trafilatura 取正文
3. **免费翻译**（mymemory 公益 API，零 LLM 成本）
4. 结果 commit 回 `news_overseas.json`

## VPS 侧合并
VPS 定时拉取本仓库的 `news_overseas.json`，并入 `content/news.json` 供前端展示。

## 手动触发
仓库 → Actions → collect-overseas → Run workflow
