@echo off
echo 正在安装依赖...
pip install pyqt5 ntplib pywin32

echo 正在使用Nuitka编译...
python -m nuitka ^
    --standalone ^
    --onefile ^
    --enable-plugin=pyqt5 ^
    --windows-icon-from-ico=clock.ico ^
    --windows-company-name="TimeSyncTool" ^
    --windows-product-name="TimeSyncTool" ^
    --windows-file-description="时间同步工具" ^
    --windows-product-version=1.5 ^
    --windows-disable-console ^
    --include-package=ntplib ^
    --include-package=win32timezone ^
    --include-package=win32api ^
    --include-package=win32con ^
    --include-package=win32security ^
    --output-dir=dist ^
    time_sync_app.py

echo 打包完成！
echo 可执行文件位于: dist\time_sync_app.exe
pause
