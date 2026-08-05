"""PyInstaller 使用的包安全启动器。"""

from deepseek_cli.desktop.main import main


if __name__ == "__main__":
    raise SystemExit(main())
