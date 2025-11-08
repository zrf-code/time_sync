import sys
import os
import time
import threading
import socket
import ctypes
import configparser
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import RotatingFileHandler

# 检查是否在打包环境中运行
is_frozen = getattr(sys, 'frozen', False)
base_path = sys._MEIPASS if is_frozen else os.path.dirname(os.path.abspath(__file__))

# 导入PyQt5库
try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QPushButton, QTextEdit, QLabel, QStatusBar, QSizePolicy,
                               QMessageBox, QDialog, QTextBrowser)
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QMetaObject, Q_ARG, pyqtSlot
    from PyQt5.QtGui import (QIcon, QFont, QColor, QPalette, QTextCharFormat, 
                           QTextCursor, QLinearGradient)
except ImportError:
    print("请先安装PyQt5: pip install pyqt5")
    sys.exit(1)

# 导入ntplib
try:
    import ntplib
except ImportError:
    print("请先安装ntplib: pip install ntplib")
    sys.exit(1)

# 检查管理员权限
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# 以管理员身份重新启动
def run_as_admin():
    executable = sys.executable
    script = os.path.abspath(sys.argv[0])
    params = ' '.join([script] + sys.argv[1:])
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, params, None, 1)
        return True
    except:
        return False

# 设置Windows系统时间
def set_windows_time(utc_time):
    """设置Windows系统时间"""
    try:
        # 转换为本地时间
        local_time = utc_time.astimezone()
        
        # 创建SYSTEMTIME结构
        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [
                ("wYear", ctypes.c_ushort),
                ("wMonth", ctypes.c_ushort),
                ("wDayOfWeek", ctypes.c_ushort),
                ("wDay", ctypes.c_ushort),
                ("wHour", ctypes.c_ushort),
                ("wMinute", ctypes.c_ushort),
                ("wSecond", ctypes.c_ushort),
                ("wMilliseconds", ctypes.c_ushort)
            ]
        
        st = SYSTEMTIME()
        st.wYear = local_time.year
        st.wMonth = local_time.month
        st.wDay = local_time.day
        st.wDayOfWeek = local_time.weekday()  # 0=Monday, 6=Sunday
        st.wHour = local_time.hour
        st.wMinute = local_time.minute
        st.wSecond = local_time.second
        st.wMilliseconds = local_time.microsecond // 1000
        
        # 调用Windows API
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        success = kernel32.SetLocalTime(ctypes.byref(st))
        
        if success:
            return True, f"系统时间已更新: {local_time.strftime('%Y-%m-%d %H:%M:%S')}"
        else:
            error_code = ctypes.get_last_error()
            return False, f"设置系统时间失败，错误代码: {error_code}"
    
    except Exception as e:
        return False, f"设置系统时间时发生错误: {str(e)}"

# NTP时间同步器
class NTPSync:
    def __init__(self, servers=None, timeout=15):
        # 默认NTP服务器列表
        self.default_servers = [
            "pool.ntp.org",
            "time.windows.com", 
            "time.nist.gov",
            "ntp.aliyun.com",
            "time.apple.com",
            "ntp.tencent.com",
            "ntp1.aliyun.com",
            "ntp2.aliyun.com",
            "ntp3.aliyun.com",
            "ntp4.aliyun.com",
            "time1.cloud.tencent.com",
            "time2.cloud.tencent.com",
            "time3.cloud.tencent.com",
            "time4.cloud.tencent.com"
        ]
        self.servers = servers or self.default_servers
        self.timeout = timeout
        self.logger = logging.getLogger("NTPSync")
    
    def get_time_from_server(self, server):
        """从单个NTP服务器获取时间，返回延迟"""
        start_time = time.time()
        try:
            client = ntplib.NTPClient()
            response = client.request(server, version=3, timeout=self.timeout)
            elapsed_time = (time.time() - start_time) * 1000  # 转换为毫秒
            return True, response, None, elapsed_time
        except socket.timeout:
            elapsed_time = (time.time() - start_time) * 1000
            return False, None, f"连接超时 ({self.timeout}秒)", elapsed_time
        except socket.gaierror:
            elapsed_time = (time.time() - start_time) * 1000
            return False, None, "DNS解析失败", elapsed_time
        except Exception as e:
            elapsed_time = (time.time() - start_time) * 1000
            return False, None, str(e), elapsed_time
    
    def sync_time(self):
        """尝试从多个服务器同步时间"""
        results = []
        
        for server in self.servers:
            success, response, error, delay = self.get_time_from_server(server)
            results.append({
                'server': server,
                'success': success,
                'response': response,
                'error': error,
                'delay': delay
            })
            
            if success:
                # 转换为UTC时间
                utc_time = datetime.fromtimestamp(response.tx_time, timezone.utc)
                return True, utc_time, server, delay, results
        
        return False, None, None, None, results

# 日志处理器
class LogHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
    
    def emit(self, record):
        msg = self.format(record)
        QMetaObject.invokeMethod(self.text_widget, "append_log", Qt.QueuedConnection,
                               Q_ARG(str, msg), Q_ARG(int, record.levelno))

# 后台同步线程
class SyncThread(QThread):
    sync_finished = pyqtSignal(bool, str, str, float)  # success, message, server, delay
    sync_progress = pyqtSignal(str)
    
    def __init__(self, servers):
        super().__init__()
        self.servers = servers
    
    def run(self):
        try:
            self.sync_progress.emit("开始时间同步...")
            ntp_sync = NTPSync(self.servers, timeout=15)
            
            success, utc_time, server, delay, results = ntp_sync.sync_time()
            
            if success:
                # 设置系统时间
                set_success, set_message = set_windows_time(utc_time)
                if set_success:
                    local_time = utc_time.astimezone()
                    message = f"时间同步成功!\n服务器: {server}\n延迟: {delay:.2f}ms\n本地时间: {local_time.strftime('%Y-%m-%d %H:%M:%S')}"
                    self.sync_finished.emit(True, message, server, delay)
                else:
                    self.sync_finished.emit(False, f"同步失败: {set_message}", "", 0.0)
            else:
                error_messages = []
                for result in results:
                    if not result['success']:
                        error_messages.append(f"{result['server']}: {result['error']} (延迟: {result['delay']:.2f}ms)")
                error_msg = "所有服务器同步失败:\n" + "\n".join(error_messages)
                self.sync_finished.emit(False, error_msg, "", 0.0)
        
        except Exception as e:
            self.sync_finished.emit(False, f"同步过程中发生错误: {str(e)}", "", 0.0)

# 服务器测试线程
class TestServersThread(QThread):
    test_finished = pyqtSignal(str)
    test_progress = pyqtSignal(str)
    
    def __init__(self, servers):
        super().__init__()
        self.servers = servers
    
    def run(self):
        try:
            self.test_progress.emit("开始测试所有NTP服务器连接...")
            ntp_sync = NTPSync(self.servers, timeout=5)
            results = []
            
            for i, server in enumerate(self.servers):
                self.test_progress.emit(f"测试服务器 ({i+1}/{len(self.servers)}): {server}")
                success, _, error, delay = ntp_sync.get_time_from_server(server)
                
                if success:
                    status = f"✅ 成功 (延迟: {delay:.2f}ms)"
                    results.append(f"<span style='color:#4ECDC4; font-weight:bold;'>{server}:</span> {status}")
                else:
                    status = f"❌ 失败: {error} (延迟: {delay:.2f}ms)"
                    results.append(f"<span style='color:#FF6B6B; font-weight:bold;'>{server}:</span> {status}")
            
            result_text = "<br>".join(results)
            self.test_finished.emit(f"<h3>服务器测试结果:</h3>{result_text}")
        
        except Exception as e:
            self.test_finished.emit(f"<span style='color:#FF6B6B; font-weight:bold;'>测试过程中发生错误:</span> {str(e)}")

# 自定义消息框，解决对比度问题
class CustomMessageBox(QDialog):
    def __init__(self, parent=None, title="", message="", is_success=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(450)
        self.setMinimumHeight(200)
        
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # 消息文本 - 修复f-string中的反斜杠问题
        formatted_message = message.replace('\n', '<br>')
        
        text_browser = QTextBrowser()
        text_browser.setOpenExternalLinks(False)
        text_browser.setReadOnly(True)
        text_browser.setHtml(f"""
            <div style="font-family: 'Microsoft YaHei', Arial, sans-serif; font-size: 14px; line-height: 1.5;">
                {formatted_message}
            </div>
        """)
        
        # 根据成功/失败设置样式
        if is_success:
            text_browser.setStyleSheet("""
                QTextBrowser {
                    background-color: #28a745;
                    color: white;
                    border-radius: 8px;
                    padding: 20px;
                    font-weight: bold;
                    border: 2px solid #218838;
                }
            """)
        else:
            text_browser.setStyleSheet("""
                QTextBrowser {
                    background-color: #dc3545;
                    color: white;
                    border-radius: 8px;
                    padding: 20px;
                    font-weight: bold;
                    border: 2px solid #c82333;
                }
            """)
        
        layout.addWidget(text_browser)
        
        # 确定按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        ok_btn = QPushButton("确定")
        ok_btn.setFixedHeight(40)
        ok_btn.setFixedWidth(100)
        ok_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #4a4a4a, stop:1 #3a3a3a);
                border: 1px solid #555555;
                border-radius: 6px;
                color: white;
                font-weight: bold;
                font-size: 14px;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #555555, stop:1 #454545);
                border: 1px solid #777777;
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #3a3a3a, stop:1 #2a2a2a);
                border: 1px solid #666666;
            }
        """)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        self.setLayout(layout)
        
        # 设置窗口标志
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        
        # 设置焦点
        ok_btn.setFocus()
        
        # 设置窗口背景
        self.setStyleSheet("""
            QDialog {
                background-color: #2d2d30;
                border: 1px solid #444444;
            }
        """)

# 主窗口
class TimeSyncApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("时间同步工具 v1.5")
        self.setMinimumSize(850, 650)
        self.setMaximumSize(1200, 800)
        
        # 设置图标
        icon_path = os.path.join(base_path, "clock.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # 初始化配置
        self.config_file = "settings.ini"
        self.default_servers = [
            "pool.ntp.org",
            "time.windows.com", 
            "time.nist.gov",
            "ntp.aliyun.com",
            "time.apple.com",
            "ntp.tencent.com",
            "ntp1.aliyun.com",
            "ntp2.aliyun.com"
        ]
        self.servers = self.default_servers.copy()  # 使用副本
        self.dark_mode = True
        
        # 设置日志 - 必须在加载配置之前设置
        self.setup_logging()
        
        # 加载配置
        self.load_config()
        
        # 创建UI
        self.create_ui()
        
        # 启动自动同步
        QTimer.singleShot(1000, self.auto_sync)
        
        # 启动时间更新定时器
        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self.update_current_time)
        self.time_timer.start(1000)
    
    def create_ui(self):
        # 主窗口部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # 顶部标题
        title_label = QLabel("⏱️ 时间同步工具")
        title_label.setFont(QFont("Microsoft YaHei", 18, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("color: #4ECDC4; margin: 5px 0;")
        main_layout.addWidget(title_label)
        
        # 顶部按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        
        # 同步按钮
        self.sync_btn = QPushButton("🔄 手动同步时间")
        self.sync_btn.setFixedHeight(45)
        self.sync_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.sync_btn.clicked.connect(self.manual_sync)
        button_layout.addWidget(self.sync_btn)
        
        # 测试连接按钮
        self.test_btn = QPushButton("🔍 测试服务器连接")
        self.test_btn.setFixedHeight(45)
        self.test_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.test_btn.clicked.connect(self.test_servers)
        button_layout.addWidget(self.test_btn)
        
        # 主题切换按钮
        self.theme_btn = QPushButton("🌙 暗黑模式" if self.dark_mode else "☀️ 亮色模式")
        self.theme_btn.setFixedHeight(45)
        self.theme_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.theme_btn.clicked.connect(self.toggle_theme)
        button_layout.addWidget(self.theme_btn)
        
        # 清除日志按钮
        self.clear_btn = QPushButton("🧹 清除日志")
        self.clear_btn.setFixedHeight(45)
        self.clear_btn.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.clear_btn.clicked.connect(self.clear_log)
        button_layout.addWidget(self.clear_btn)
        
        main_layout.addLayout(button_layout)
        
        # 服务器配置区域
        server_layout = QVBoxLayout()
        server_layout.setSpacing(5)
        
        server_label = QLabel("🌐 NTP服务器配置 (每行一个):")
        server_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        server_layout.addWidget(server_label)
        
        self.server_edit = QTextEdit()
        self.server_edit.setFixedHeight(120)
        self.server_edit.setFont(QFont("Consolas", 10))
        self.server_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.server_edit.setText("\n".join(self.servers))
        server_layout.addWidget(self.server_edit)
        
        main_layout.addLayout(server_layout)
        
        # 日志显示区域
        log_label = QLabel("📋 同步日志:")
        log_label.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        main_layout.addWidget(log_label)
        
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setAcceptRichText(True)  # 允许富文本
        main_layout.addWidget(self.log_view, 1)
        
        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.setFont(QFont("Microsoft YaHei", 9))
        
        self.status_label = QLabel("🚀 就绪 - 程序启动成功")
        self.status_label.setMinimumWidth(300)
        self.status_bar.addWidget(self.status_label, 1)
        
        self.current_time_label = QLabel("")
        self.current_time_label.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        self.current_time_label.setMinimumWidth(200)
        self.status_bar.addPermanentWidget(self.current_time_label)
        
        # 应用主题
        self.apply_theme()
        
        # 连接服务器配置变更
        self.server_edit.textChanged.connect(self.save_servers)
    
    def setup_logging(self):
        # 配置日志
        self.logger = logging.getLogger("TimeSyncApp")
        self.logger.setLevel(logging.INFO)
        
        # 文件日志
        file_handler = RotatingFileHandler("timesync.log", maxBytes=1024*1024, backupCount=5, encoding='utf-8')
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # UI日志
        ui_handler = LogHandler(self)
        ui_formatter = logging.Formatter('%(levelname)s: %(message)s')
        ui_handler.setFormatter(ui_formatter)
        self.logger.addHandler(ui_handler)
    
    @pyqtSlot(str, int)
    def append_log(self, message, level=logging.INFO):
        """向UI添加日志"""
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if "<br>" in message or "<h3>" in message:
            # HTML格式消息
            self.log_view.insertHtml(f"<div style='margin-bottom: 5px;'>{message}</div>")
            self.log_view.insertPlainText("\n")
        else:
            # 普通文本消息
            format = QTextCharFormat()
            if level == logging.ERROR:
                format.setForeground(QColor("#FF6B6B"))  # 红色
                format.setFontWeight(QFont.Bold)
            elif level == logging.WARNING:
                format.setForeground(QColor("#FFD166"))  # 黄色
                format.setFontWeight(QFont.Bold)
            elif level == logging.INFO:
                format.setForeground(QColor("#4ECDC4"))  # 青色
            else:
                format.setForeground(QColor("#A0A0A0"))  # 灰色
            
            cursor.insertText(message + "\n", format)
        
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
    
    def apply_theme(self):
        """应用主题"""
        palette = QPalette()
        
        if self.dark_mode:
            # 暗黑模式
            palette.setColor(QPalette.Window, QColor(30, 30, 33))
            palette.setColor(QPalette.WindowText, QColor(230, 230, 230))
            palette.setColor(QPalette.Base, QColor(25, 25, 27))
            palette.setColor(QPalette.AlternateBase, QColor(45, 45, 48))
            palette.setColor(QPalette.ToolTipBase, QColor(25, 25, 27))
            palette.setColor(QPalette.ToolTipText, QColor(230, 230, 230))
            palette.setColor(QPalette.Text, QColor(230, 230, 230))
            palette.setColor(QPalette.Button, QColor(50, 50, 54))
            palette.setColor(QPalette.ButtonText, QColor(230, 230, 230))
            palette.setColor(QPalette.BrightText, QColor(255, 100, 100))
            palette.setColor(QPalette.Highlight, QColor(65, 130, 240))
            palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
            
            # 按钮样式 - 增强对比度
            button_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #4a4a4a, stop:1 #3a3a3a);
                    border: 1px solid #666666;
                    border-radius: 6px;
                    color: white;
                    font-weight: bold;
                    padding: 8px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #5a5a5a, stop:1 #4a4a4a);
                    border: 1px solid #888888;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #3a3a3a, stop:1 #2a2a2a);
                    border: 1px solid #777777;
                }
                QPushButton:disabled {
                    background: #3a3a3a;
                    color: #aaaaaa;
                    border: 1px solid #555555;
                }
            """
            
            # 文本编辑样式
            text_edit_style = """
                QTextEdit {
                    background-color: #1e1e1e;
                    color: #e0e0e0;
                    border: 1px solid #555555;
                    border-radius: 6px;
                    padding: 8px;
                }
                QTextEdit:focus {
                    border: 1px solid #4a86e8;
                    background-color: #252525;
                }
            """
            
            # 状态栏样式
            status_bar_style = """
                QStatusBar {
                    background-color: #252525;
                    color: #cccccc;
                    border-top: 1px solid #444444;
                }
            """
        else:
            # 亮色模式
            palette.setColor(QPalette.Window, QColor(248, 248, 248))
            palette.setColor(QPalette.WindowText, QColor(30, 30, 30))
            palette.setColor(QPalette.Base, QColor(255, 255, 255))
            palette.setColor(QPalette.AlternateBase, QColor(240, 240, 240))
            palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))
            palette.setColor(QPalette.ToolTipText, QColor(30, 30, 30))
            palette.setColor(QPalette.Text, QColor(30, 30, 30))
            palette.setColor(QPalette.Button, QColor(235, 235, 235))
            palette.setColor(QPalette.ButtonText, QColor(30, 30, 30))
            palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
            palette.setColor(QPalette.Highlight, QColor(0, 120, 215))
            palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
            
            # 按钮样式
            button_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #f8f8f8, stop:1 #e8e8e8);
                    border: 1px solid #cccccc;
                    border-radius: 6px;
                    color: #333333;
                    font-weight: bold;
                    padding: 8px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #ffffff, stop:1 #f0f0f0);
                    border: 1px solid #999999;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #e8e8e8, stop:1 #d8d8d8);
                    border: 1px solid #888888;
                }
                QPushButton:disabled {
                    background: #e0e0e0;
                    color: #888888;
                    border: 1px solid #cccccc;
                }
            """
            
            # 文本编辑样式
            text_edit_style = """
                QTextEdit {
                    background-color: white;
                    color: #333333;
                    border: 1px solid #cccccc;
                    border-radius: 6px;
                    padding: 8px;
                }
                QTextEdit:focus {
                    border: 1px solid #0078D7;
                    background-color: #fafafa;
                }
            """
            
            # 状态栏样式
            status_bar_style = """
                QStatusBar {
                    background-color: #f0f0f0;
                    color: #333333;
                    border-top: 1px solid #dddddd;
                }
            """
        
        self.setPalette(palette)
        
        # 应用样式
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {palette.color(QPalette.Window).name()};
                font-family: 'Microsoft YaHei', Arial, sans-serif;
            }}
            QLabel {{
                color: {palette.color(QPalette.WindowText).name()};
                font-family: 'Microsoft YaHei', Arial, sans-serif;
            }}
            {button_style}
            {text_edit_style}
            {status_bar_style}
            QTextBrowser {{
                background-color: {palette.color(QPalette.Base).name()};
                color: {palette.color(QPalette.Text).name()};
                border: 1px solid {palette.color(QPalette.Button).name()};
                border-radius: 6px;
                padding: 10px;
            }}
            QStatusBar QLabel {{
                color: {palette.color(QPalette.WindowText).name()};
            }}
        """)
        
        # 更新主题按钮文本
        self.theme_btn.setText("🌙 暗黑模式" if self.dark_mode else "☀️ 亮色模式")
    
    def toggle_theme(self):
        """切换主题"""
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.save_config()
        self.logger.info(f"主题切换到: {'暗黑模式' if self.dark_mode else '亮色模式'}")
        self.append_log(f"主题切换到: {'暗黑模式' if self.dark_mode else '亮色模式'}", logging.INFO)
    
    def load_config(self):
        """加载配置"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.config_file):
                config.read(self.config_file, encoding='utf-8')
                if 'Settings' in config:
                    self.dark_mode = config.getboolean('Settings', 'dark_mode', fallback=True)
                    if 'servers' in config['Settings']:
                        server_list = [s.strip() for s in config['Settings']['servers'].split('\n') if s.strip()]
                        if server_list:
                            self.servers = server_list
                            self.logger.info(f"从配置文件加载了 {len(self.servers)} 个服务器")
                        else:
                            self.servers = self.default_servers.copy()
                            self.logger.warning("配置文件中的服务器列表为空，使用默认服务器")
                    else:
                        self.servers = self.default_servers.copy()
                        self.logger.info("配置文件中没有服务器配置，使用默认服务器")
            else:
                self.servers = self.default_servers.copy()
                self.logger.info("配置文件不存在，使用默认服务器配置")
        except Exception as e:
            self.logger.error(f"加载配置失败: {e}")
            self.servers = self.default_servers.copy()
    
    def save_config(self):
        """保存配置"""
        config = configparser.ConfigParser()
        config['Settings'] = {
            'dark_mode': str(self.dark_mode),
            'servers': '\n'.join(self.servers)
        }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                config.write(f)
            self.logger.info(f"配置已保存，包含 {len(self.servers)} 个服务器")
        except Exception as e:
            self.logger.error(f"保存配置失败: {e}")
    
    def save_servers(self):
        """保存服务器配置"""
        text = self.server_edit.toPlainText().strip()
        new_servers = [line.strip() for line in text.split('\n') if line.strip()]
        
        if new_servers:
            self.servers = new_servers
            self.save_config()
            self.logger.info(f"服务器配置已更新: {len(self.servers)} 个服务器")
            self.append_log(f"服务器配置已更新: {len(self.servers)} 个服务器", logging.INFO)
        else:
            self.logger.warning("服务器配置为空，保留当前配置")
            self.append_log("警告: 服务器配置为空，保留当前配置", logging.WARNING)
            # 恢复之前的配置
            self.server_edit.setText("\n".join(self.servers))
    
    def auto_sync(self):
        """自动同步时间"""
        self.logger.info("🚀 启动自动时间同步...")
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("🔄 同步中...")
        self.status_label.setText("⏳ 正在自动同步时间...")
        
        self.sync_thread = SyncThread(self.servers)
        self.sync_thread.sync_finished.connect(self.on_sync_finished)
        self.sync_thread.sync_progress.connect(self.on_sync_progress)
        self.sync_thread.start()
    
    def manual_sync(self):
        """手动同步时间"""
        self.logger.info("👤 用户手动触发时间同步")
        self.sync_btn.setEnabled(False)
        self.sync_btn.setText("🔄 同步中...")
        self.status_label.setText("⏳ 正在手动同步时间...")
        
        self.sync_thread = SyncThread(self.servers)
        self.sync_thread.sync_finished.connect(self.on_sync_finished)
        self.sync_thread.sync_progress.connect(self.on_sync_progress)
        self.sync_thread.start()
    
    def test_servers(self):
        """测试所有服务器连接"""
        self.logger.info("🔧 开始测试所有NTP服务器连接...")
        self.test_btn.setEnabled(False)
        self.status_label.setText("🔍 正在测试服务器连接...")
        self.append_log("开始测试所有NTP服务器连接...", logging.INFO)
        
        self.test_thread = TestServersThread(self.servers)
        self.test_thread.test_finished.connect(self.on_test_finished)
        self.test_thread.test_progress.connect(self.on_test_progress)
        self.test_thread.start()
    
    def on_sync_progress(self, message):
        """同步进度更新"""
        self.status_label.setText(message)
        self.logger.info(message)
    
    def on_sync_finished(self, success, message, server, delay):
        """同步完成处理"""
        self.sync_btn.setEnabled(True)
        self.sync_btn.setText("🔄 手动同步时间")
        self.status_label.setText("✅ 就绪" if success else "❌ 同步失败")
        
        if success:
            self.logger.info(f"✅ 时间同步成功: {message}")
            msg_box = CustomMessageBox(self, "同步成功", message, True)
            msg_box.exec_()
        else:
            self.logger.error(f"❌ 时间同步失败: {message}")
            msg_box = CustomMessageBox(self, "同步失败", message, False)
            msg_box.exec_()
    
    def on_test_progress(self, message):
        """测试进度更新"""
        self.status_label.setText(message)
        self.logger.info(message)
        self.append_log(message, logging.INFO)
    
    def on_test_finished(self, result_html):
        """测试完成处理"""
        self.test_btn.setEnabled(True)
        self.status_label.setText("✅ 服务器测试完成")
        self.logger.info("✅ 服务器测试完成")
        self.append_log("✅ 服务器测试完成", logging.INFO)
        
        # 显示测试结果
        self.append_log(result_html, logging.INFO)
    
    def clear_log(self):
        """清除日志"""
        self.log_view.clear()
        self.logger.info("🧹 日志已清除")
        self.append_log("🧹 日志已清除", logging.INFO)
    
    def update_current_time(self):
        """更新当前时间显示"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.current_time_label.setText(f"⏰ 当前时间: {current_time}")
    
    def closeEvent(self, event):
        """关闭事件处理"""
        self.logger.info("CloseOperation: 程序正在关闭，清理资源...")
        
        # 停止所有线程
        if hasattr(self, 'sync_thread') and self.sync_thread.isRunning():
            self.sync_thread.quit()
            self.sync_thread.wait(2000)
        
        if hasattr(self, 'test_thread') and self.test_thread.isRunning():
            self.test_thread.quit()
            self.test_thread.wait(2000)
        
        # 停止定时器
        if hasattr(self, 'time_timer') and self.time_timer.isActive():
            self.time_timer.stop()
        
        event.accept()

# 主程序入口
def main():
    # 检查管理员权限
    if not is_admin():
        if not run_as_admin():
            app = QApplication(sys.argv)
            QMessageBox.critical(None, "权限错误", "需要管理员权限才能设置系统时间！")
            sys.exit(1)
        else:
            sys.exit(0)
    
    # 设置高DPI支持 - 必须在创建QApplication之前设置
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    
    app = QApplication(sys.argv)
    
    # 设置应用样式
    app.setStyle("Fusion")
    
    window = TimeSyncApp()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
