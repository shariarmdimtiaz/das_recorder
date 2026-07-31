from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5 import QtWidgets, QtGui


def is_frozen() -> bool:
    """
    True when running as PyInstaller EXE.
    False when running as normal Python script.
    """
    return getattr(sys, "frozen", False)


def app_base_path() -> Path:
    """
    Return the correct application base path.

    Normal Python:
        project folder

    PyInstaller --onedir:
        folder containing DASRecorder.exe

    PyInstaller --onefile:
        temporary extracted folder is sys._MEIPASS,
        but external editable files should be near the exe.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parent


def bundled_resource_path(relative_path: str) -> Path:
    """
    Return path to bundled resource.

    Use this for files included by PyInstaller using --add-data,
    such as icons, internal config, and assets.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path

    return Path(__file__).resolve().parent / relative_path


PROJECT_ROOT = app_base_path()
SRC_ROOT = PROJECT_ROOT / "src"

# During normal Python development, import package from src/
if SRC_ROOT.exists():
    sys.path.insert(0, str(SRC_ROOT))

from das_recorder_ui.config import RecorderConfig
from das_recorder_ui.firewall import configure_dunay_firewall_rules
from das_recorder_ui.main_window import MainWindow


def get_writable_config_path() -> Path:
    """Return the current Windows user's writable settings file."""
    app_data = os.environ.get("APPDATA")
    if app_data:
        settings_root = Path(app_data)
    else:
        settings_root = Path.home() / "AppData" / "Roaming"
    return settings_root / "H4Tech" / "DASRecorder" / "default_config.json"


def get_config_path() -> Path:
    """
    Prefer the user's saved defaults, then the external/bundled factory config.
    """
    user_config = get_writable_config_path()
    if user_config.exists():
        return user_config

    external_config = PROJECT_ROOT / "config" / "default_config.json"

    if external_config.exists():
        return external_config

    return bundled_resource_path("config/default_config.json")


def get_icon_path() -> Path:
    """
    Get DASRecorder icon path for normal Python and EXE.
    """
    external_icon = PROJECT_ROOT / "src" / "das_recorder_ui" / "assets" / "das_icon.ico"

    if external_icon.exists():
        return external_icon

    return bundled_resource_path("das_recorder_ui/assets/das_icon.ico")


def main() -> int:
    if "--configure-firewall" in sys.argv:
        ok, message = configure_dunay_firewall_rules(elevate=False)
        print(message)
        return 0 if ok else 1

    config_path = get_config_path()
    config = RecorderConfig.load(config_path)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("DASRecorder")
    app.setOrganizationName("H4Tech")

    icon_path = get_icon_path()
    if icon_path.exists():
        app.setWindowIcon(QtGui.QIcon(str(icon_path)))

    window = MainWindow(config, config_path=get_writable_config_path())

    # Also apply icon directly to main window.
    if icon_path.exists():
        window.setWindowIcon(QtGui.QIcon(str(icon_path)))

    window.show()

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())

# from __future__ import annotations

# import sys
# from pathlib import Path

# from PyQt5 import QtWidgets

# PROJECT_ROOT = Path(__file__).resolve().parent
# SRC_ROOT = PROJECT_ROOT / "src"
# sys.path.insert(0, str(SRC_ROOT))

# from das_recorder_ui.config import RecorderConfig
# from das_recorder_ui.main_window import MainWindow


# def main() -> int:
#     config_path = PROJECT_ROOT / "config" / "default_config.json"
#     config = RecorderConfig.load(config_path)
#     app = QtWidgets.QApplication(sys.argv)
#     window = MainWindow(config)
#     window.show()
#     return app.exec_()


# if __name__ == "__main__":
#     raise SystemExit(main())
