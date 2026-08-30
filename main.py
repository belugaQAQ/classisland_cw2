from __future__ import annotations

try:
    from ClassWidgets.SDK import CW2Plugin
except ImportError:  # 允许在插件目录中直接调试
    from src.core.plugin import CW2Plugin

try:
    from .installer import Installer
except ImportError:  # CW2 外部插件入口按文件加载
    from installer import Installer


class Plugin(CW2Plugin):
    """ClassIsland 启动与安装助手。"""

    def __init__(self, plugin_api):
        super().__init__(plugin_api)
        self.installer: Installer | None = None

    def on_load(self):
        super().on_load()
        self.installer = Installer()
        self.installer.show()

    def on_unload(self):
        if self.installer is not None:
            self.installer.close()
            self.installer = None
