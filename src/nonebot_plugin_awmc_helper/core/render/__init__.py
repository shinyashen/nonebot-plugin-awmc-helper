"""绘图基座：字体、素材缓存、公共工具、曲库卡片。

消费方一律按子模块导入（``from ...core.render.tools import ...`` 等）；
此处仅再导出 ``assets`` 单例（测试经包根 patch 目标定位用）。
"""

from .assets import assets

__all__ = ["assets"]
