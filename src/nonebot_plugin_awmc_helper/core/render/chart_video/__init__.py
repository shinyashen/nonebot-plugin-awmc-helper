"""谱面预览视频渲染（落雪站内 canvas 渲染器的 PIL 逐语句移植）。

绘制逻辑对照 maimai.lxns.net 渲染器 bundle（beautify 存档
``local/reference/lxns-renderer/chart-canvas.pretty.js``，各方法标注 L* 行号；
逐函数对照档案见 ``local/reference/lxns-renderer/parity-notes.md``）。
note 数据用 ``chart_notes.parse_site_chart``（同一 bundle 的解析器逐语句移植），
不使用 ``core/simai.py``（那是物量对拍用的 MaiConverter 内核，note 切分口径
不同：一个 token 的 ``*`` 分段在站内是同一 note 的多条并行动径）。

站内页面实际生效的配置（chartpage.js 设置默认值 L881-902，勿用渲染器类默认值）：
hiSpeed=6（回调 setHiSpeed → ×0.6 = 3.6）、alwaysKeepHiSpeed=false、
slideDelay=0、**slideRotation=true**、mirrorMode=none、**judgmentLineDesign=simple**
（白色判定圈 + 8 按钮点，无 sensor 底图）、pinkSlideStart/normalColorBreakSlide/
highlightEx/ddr 均关、showBpm/showNoteTotal/showBreakCount/showFireworks/
showHitEffect 开；播放时钟在 chart beat 0 起、音频从 beat 4（1 小节 lead-in）
起播（chartpage.js syncFrame），因此视频前 4 拍为空场。

关键语义（均引自源码，勿凭直觉改）：
- 运动（calculateNotePosition L307）：接近窗前半程 0.25R 驻留、scale 0→1
  （**尺寸渐显而非 alpha**，tap sprite 显示尺寸 ×scale L449），后半程
  0.25R→R；tap 判定后立即消失；hold 头判定后驻留 R；
- 滑键头 = **星形头**（hasTapHead 仅 `@` 修饰，simai 数据恒无 → 星头，
  L8411）；`!`/`?`（headless）无头，星于起点按钮生长（renderSlideStar
  L6115）；slideRotation 开 → 接近期星头自旋（calculateStarRotation L6321）、
  移动星朝向路径切向 +π/2（getPathTangentAngle L6386）；
- slide 轨道透明度 ge()（L5527）：普通段与 wifi 段（视作 fading 档）分别取，
  slideDelay=0；单路径可含多段（[] 内多段），`*` 分段为并行动径；
- tap = 四象限线性渐变环 + 双白环 + 黑描边 + 中心点（drawTapNoteVector L495）；
  EX 覆盖环画在 scale=1 的 sprite 内（1.19×f），显示再 ×scale → 终效 1.19·f·s；
- hold = 双六棱柱：EX 覆盖环顶点序 O,k,F,N,L,A（L6519）、六面渐变轴
  **os→oe 外边端点**（L6567）、进行中白色呼吸 Se 作用于渐变两端与白边、
  头部光晕 E 表径向渐变（drawActiveHeadGlow L6584）；
- touch = 四瓣**环形**渐变花瓣（外三角正向 + 内三角反向 = nonzero 空心环，
  L6807）带投影（sb 层），touch hold 有四色进度环 gauge（L6818）与
  部件级分层排序（ke/Ae L6672：区序 B,E,A,D,C + we() 键 + De 哨兵）；
- 按压涟漪 = tt 16 停点径向渐变环带 [0.8r,1.2r]（drawRippleRing L7225）
  screen 混合，非描边圆；touch hold 松手有 hexagon 打击特效（L8549）；
- touch 命中 = lighter 中心环（ft 62 停点）+ 双层星爆（Ft 类 L7509）；
- 花火（`!` fire touch 专属，Wt L7665）：15 色楔形 + 光球 + >0.6s 切洞；
- 帧序（renderFrame L7997 / renderNotes L8104）：clear → 判定线 → 花火 →
  HUD → clip 圆 → 轨道(slides **倒序**) → 星(倒序) → 接近指示(hold/slide
  弧+全对 GOLD 连接线) → tap 弧批 → 分层头(**taps 升序→holds 升序→slides
  升序**，L8271-8283) → touch 边框/部件 → 涟漪/touch hold 松手/命中/打击。

帧在 2× 超采样画布绘制（等价站内 G=2 backing）后缩至 600²，rawvideo 管道
喂 ffmpeg 合成 h264+aac mp4（音频前补 1 小节静音，与视频时间轴一致）。

公共出口（签名不变）：
- :func:`render_chart_video` —— 渲染入口（encoder）
- ffmpeg 探测：``FFmpegUnavailableError`` / ``resolve_ffmpeg`` /
  ``FFmpegStatus`` / ``check_ffmpeg``
- 渲染配置：``RenderConfig``（超采样/流速单值对象，一次渲染一份、可并发）
  与 ``DEFAULT_HI_SPEED`` / ``normalize_hi_speed``

子模块分层（依赖单向，无环）：
``constants ← render_config ← notes ← motion ← canvas ← sprites ←
note_renderer/slide_renderer(← slide_geometry 混入)/touch_renderer ←
press_fx(涟漪/光晕)/fireworks/touch_hit ← composer ← encoder(+ffmpeg_check)``；
HUD（hud._HudMixin）与特效方法（press_fx._RendererEffectsMixin）、slide
路径几何（slide_geometry._SlideGeometryMixin）按混入接入宿主类。超采样/
流速活在 render_config.RenderConfig（frozen），由 render_chart_video 构造
并贯穿全部渲染器——并发渲染互不可见、无需加锁。

私有名的再导出仅为既有测试与调研脚本（cv._Note 等）保兼容，不构成
新公共 API。
"""

from .notes import _Note as _Note
from .motion import _button_angle as _button_angle
from .motion import _hold_flicker as _hold_flicker
from .motion import _note_position as _note_position
from .encoder import render_chart_video as render_chart_video
from .composer import _ChartRenderer as _ChartRenderer
from .ffmpeg_check import FFmpegStatus as FFmpegStatus
from .ffmpeg_check import FFmpegUnavailableError as FFmpegUnavailableError
from .ffmpeg_check import check_ffmpeg as check_ffmpeg
from .ffmpeg_check import resolve_ffmpeg as resolve_ffmpeg
from .render_config import DEFAULT_HI_SPEED as DEFAULT_HI_SPEED
from .render_config import _DEFAULT_HI_SPEED as _DEFAULT_HI_SPEED
from .render_config import RenderConfig as RenderConfig
from .render_config import normalize_hi_speed as normalize_hi_speed
from .slide_renderer import _SlideRenderer as _SlideRenderer
