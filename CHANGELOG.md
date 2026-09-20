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
- 统一 SQLite 存储（localstore 数据目录 `awmc.db`），曲库每日 4 点自动刷新并快照入库。
