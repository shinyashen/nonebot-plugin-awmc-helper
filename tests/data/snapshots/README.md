# 测试快照（真实数据，2026-09-29 采集；2026-10-05 maimaiinfo 部分刷新）

测试造数的唯一取材来源：全部条目裁剪自下列真实数据源原样 payload（字段与值未手改）。
生成脚本与原始抓取物在 local/scratch/（不入库）；凭据只存在于 local/，不入本目录。
各源采集 commit/日期以 meta.json 为准。

刷新流程：重跑 local/scratch/capture_snapshot.py（+round2/round3）→ build_snapshots.py →
按 meta.json 里的 commit/日期核对 → 同步修正依赖具体值的断言。

「当前态」场景（国服当前进度、限曲身份、增量段等会随版本推进过期）也以本快照为准构造；
快照无原型的路径（如国服限定曲，当前真实数据已不存在）在 fixture 内构造并注明。

2026-10-06 增补 munet_inarau.json（三源一档的场景文件）：居並ぶ穀物と溜息まじりの
運送屋 MAGiCAL 标准谱面追加——MuNET GetById 实时取回、otoge/maimaiinfo 裁自本地
克隆（commit 见 meta.json munet_inarau 条目），供「既有曲追加谱面组」批次路径用例。

2026-10-11 增补 yuzu_plate.json（全量未裁剪）：柚子逐牌曲单（maimaidxplate
等价的 v2 get_plate 端点，Hoshino 完成表曲单同源）——国服牌子过筛（Q60）
的基准数据；含「日服已删・国服在列」23 首的排除语义与舞ReMASTER 名单
（DX 时代追加的旧曲 ReM 不计入舞/霸）。

2026-10-24 增补 simai/ 目录（12+1 首）：落雪素材站 assets2 的 simai 谱面文本
（`chart/{官方id}.txt`，热链公开资源），谱面预览（chart_preview）解析器与渲染
测试的取材来源；物量对拍基准 = divingfish_music.json。含 SD/DX/宴谱与边缘
方言样本（700 疾走あんさんぶる 的同位圆滑 `1<1`、10030 的无星链、11355 的
EX 修饰密度等）。
