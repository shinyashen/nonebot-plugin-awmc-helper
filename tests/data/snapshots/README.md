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
