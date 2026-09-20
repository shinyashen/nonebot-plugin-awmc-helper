# Changelog

本项目的所有重要变更都记录在本文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 初始开发版本：主插件 + 子插件架构（官方嵌套插件机制），数据层基于 maimai-py。
- 子插件：bind（绑定与设置）、music_query（查歌）、alias（别名）、score_query（查分）、
  score_tools（分数线/推分/排名）、tables（定数表/完成表/牌子/进度/分数列表）、
  random_song（随机谱面）、fortune（今日运势）、guess（猜歌）、arcade（机厅排卡）。
- 统一 SQLite 存储（localstore 数据目录 `awmc.db`），曲库每日 4 点自动刷新并快照入库，
  断网自动降级上次快照。
- 核心层：唯一 MaimaiClient 单例、绑定（水鱼用户名/Import-Token、落雪 OAuth/好友码）、
  成绩服务（异常统一映射）、分数线/推分计算（RA 复用 maimai-py ScoreCoefficient）。
- ext 直连层：柚子别名投票 + SSE 推送、水鱼 RA 排行、落雪 OAuth、华立机厅数据。
- 每日 4 点：曲库刷新、华立机厅同步与排卡人数清零。
- 完整 nonebug + respx 测试（80 例）与入库文档（docs/ 7 篇 + CONTRIBUTING）。
