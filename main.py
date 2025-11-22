import sys
import os
import time
import threading
import socket
import ctypes
import configparser
import warnings
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import RotatingFileHandler

# 忽略sip相关的DeprecationWarning（兼容Win7和旧版本PyQt）
warnings.filterwarnings('ignore', category=DeprecationWarning)

# 检查是否在打包环境中运行
is_frozen = getattr(sys, 'frozen', False)
base_path = sys._MEIPASS if is_frozen else os.path.dirname(os.path.abspath(__file__))

# 导入PyQt5库
try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QPushButton, QTextEdit, QLabel, QStatusBar, QSizePolicy,
                               QMessageBox, QDialog, QTextBrowser, QFrame, QScrollArea)
    from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QSize, QMetaObject, Q_ARG, pyqtSlot
    from PyQt5.QtGui import (QIcon, QFont, QColor, QPalette, QTextCharFormat, 
                           QTextCursor, QLinearGradient, QPainter, QBrush, QPen)
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
            "ntp.ntsc.ac.cn",
            "ntp.aliyun.com",
            "pool.ntp.org",
            "time.windows.com", 
            "ntp.tencent.com",
            "time.edu.cn",
            "ntp.tuna.tsinghua.edu.cn",
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
                    results.append(f"<span style='color:#2196F3; font-weight:bold;'>{server}:</span> {status}")
                else:
                    status = f"❌ 失败: {error} (延迟: {delay:.2f}ms)"
                    results.append(f"<span style='color:#F44336; font-weight:bold;'>{server}:</span> {status}")
            
            result_text = "<br>".join(results)
            self.test_finished.emit(f"<h3 style='color:#2196F3;'>服务器测试结果:</h3>{result_text}")
        
        except Exception as e:
            self.test_finished.emit(f"<span style='color:#F44336; font-weight:bold;'>测试过程中发生错误:</span> {str(e)}")

# 自定义消息框（修复Win7兼容性）
class CustomMessageBox(QDialog):
    def __init__(self, parent=None, title="", message="", is_success=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setMinimumHeight(220)
        
        # 修复Win7兼容性：移除WA_TranslucentBackground，改用普通窗口+边框
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        
        # 主容器
        container = QWidget(self)
        container.setObjectName("messageBoxContainer")
        container.setMinimumSize(500, 220)
        
        layout = QVBoxLayout(container)
        layout.setContentsMargins(25, 25, 25, 25)
        layout.setSpacing(20)
        
        # 图标和标题区域
        header_layout = QHBoxLayout()
        
        # 状态图标
        icon_label = QLabel()
        icon_label.setFixedSize(48, 48)
        if is_success:
            icon_label.setText("✅")
            icon_label.setStyleSheet("font-size: 36px; color: white;")
        else:
            icon_label.setText("❌")
            icon_label.setStyleSheet("font-size: 36px; color: white;")
        icon_label.setAlignment(Qt.AlignCenter)
        header_layout.addWidget(icon_label)
        
        header_layout.addSpacing(15)
        
        # 标题
        title_label = QLabel(title)
        title_label.setFont(QFont("Microsoft YaHei", 16, QFont.Bold))
        title_label.setStyleSheet("color: white;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # 消息文本
        formatted_message = message.replace('\n', '<br>')
        
        text_browser = QTextBrowser()
        text_browser.setOpenExternalLinks(False)
        text_browser.setReadOnly(True)
        text_browser.setHtml(f"""
            <div style="font-family: 'Microsoft YaHei', Arial, sans-serif; font-size: 14px; line-height: 1.8; color: white;">
                {formatted_message}
            </div>
        """)
        text_browser.setStyleSheet("""
            QTextBrowser {
                background-color: transparent;
                border: none;
                padding: 5px;
            }
        """)
        
        layout.addWidget(text_browser)
        
        # 确定按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        ok_btn = QPushButton("确定")
        ok_btn.setFixedHeight(42)
        ok_btn.setFixedWidth(120)
        ok_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #ffffff, stop:1 #f0f0f0);
                border: 1px solid #cccccc;
                border-radius: 6px;
                color: #333333;
                font-weight: bold;
                font-size: 14px;
                padding: 5px 15px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #f8f8f8, stop:1 #e8e8e8);
                border: 1px solid #999999;
            }
            QPushButton:pressed {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                            stop:0 #e8e8e8, stop:1 #d8d8d8);
                border: 1px solid #666666;
            }
        """)
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(container)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        # 设置背景颜色（高对比度）- 移除box-shadow
        if is_success:
            container.setStyleSheet("""
                #messageBoxContainer {
                    background-color: #4CAF50;
                    border-radius: 12px;
                    border: 1px solid #388E3C;
                }
            """)
        else:
            container.setStyleSheet("""
                #messageBoxContainer {
                    background-color: #F44336;
                    border-radius: 12px;
                    border: 1px solid #D32F2F;
                }
            """)
        
        # 设置焦点
        ok_btn.setFocus()

    def paintEvent(self, event):
        # 简化绘制，修复Win7兼容性
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 直接绘制背景色，避免复杂的阴影计算
        painter.fillRect(self.rect(), QBrush(QColor(240, 240, 240, 200)))

# 带边框的框架（兼容Win7）
class BorderFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("borderFrame")
        # 设置边框效果（兼容Win7）
        self.setStyleSheet("""
            #borderFrame {
                border-radius: 10px;
                border: 1px solid #cccccc;
            }
        """)

# 主窗口
class TimeSyncApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("时间同步工具 v2.3")
        self.setMinimumSize(950, 750)
        # 修复QWidget::setMaximumSize警告，使用Qt允许的最大尺寸
        self.setMaximumSize(QSize(16777215, 16777215))
        
        # 使用自定义标题栏，隐藏系统默认标题栏
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowCloseButtonHint)
        
        # 设置图标
        icon_path = os.path.join(base_path, "clock.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            # 添加图标不存在的日志提示
            self.logger.warning(f"图标文件未找到: {icon_path}")
            # 可选：使用默认图标
            self.setWindowIcon(QIcon.fromTheme("clock", QIcon()))
        
        # 初始化配置
        self.config_file = "settings.ini"
        self.default_servers = [
            "ntp.ntsc.ac.cn",
            "pool.ntp.org",
            "ntp.aliyun.com",
            "time.windows.com", 
            "ntp.tencent.com",
            "time.edu.cn",
            "ntp.tuna.tsinghua.edu.cn",
            "ntp1.aliyun.com",
            "ntp2.aliyun.com"
        ]
        self.servers = self.default_servers.copy()
        self.dark_mode = False  # 默认亮色模式
        
        # 窗口拖动相关变量
        self.is_dragging = False
        self.drag_start_pos = None
        
        # 设置日志
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

    def paintEvent(self, event):
        # 简化绘制，修复Win7兼容性
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # 绘制窗口背景
        painter.fillRect(self.rect(), QBrush(self.palette().window().color()))
    
    def mousePressEvent(self, event):
        """鼠标按下事件 - 实现窗口拖动"""
        if event.button() == Qt.LeftButton and event.y() < 40:  # 只在标题栏区域允许拖动
            self.is_dragging = True
            self.drag_start_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    
    def mouseMoveEvent(self, event):
        """鼠标移动事件 - 实现窗口拖动"""
        if self.is_dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_start_pos)
            event.accept()
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件 - 结束窗口拖动"""
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
    
    def create_ui(self):
        # 创建主容器
        main_container = QWidget()
        self.setCentralWidget(main_container)
        main_layout = QVBoxLayout(main_container)
        main_layout.setContentsMargins(0, 0, 0, 0)  # 去掉主容器边距
        main_layout.setSpacing(0)
        
        # 自定义标题栏（高度40px）
        title_bar = QWidget()
        title_bar.setFixedHeight(40)
        title_bar.setObjectName("titleBar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(15, 0, 10, 0)
        title_layout.setSpacing(15)
        
        # 标题区域
        title_icon = QLabel("⏱️")
        title_icon.setFont(QFont("Arial", 14))
        title_label = QLabel("时间同步工具 v2.3")
        title_label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        
        title_layout.addWidget(title_icon)
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        
        # 窗口控制按钮组（统一样式和大小）
        control_buttons = QHBoxLayout()
        control_buttons.setSpacing(0)
        
        # 最小化按钮（图标：—）
        self.min_btn = QPushButton("—")
        self.min_btn.setFixedSize(36, 36)
        self.min_btn.clicked.connect(self.showMinimized)
        control_buttons.addWidget(self.min_btn)
        
        # 最大化/还原按钮（图标：□ / ☐）
        self.max_btn = QPushButton("□")
        self.max_btn.setFixedSize(36, 36)
        self.max_btn.clicked.connect(self.toggle_maximize)
        control_buttons.addWidget(self.max_btn)
        
        # 关闭按钮（保留原文字：✕）
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(36, 36)
        control_buttons.addWidget(self.close_btn)
        
        title_layout.addLayout(control_buttons)
        main_layout.addWidget(title_bar)
        
        # 主内容区域（带边框和内边距）
        content_frame = BorderFrame()
        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)
        main_layout.addWidget(content_frame, 1)  # 主内容区域占满所有可用空间
        
        # 功能按钮区域
        function_btn_layout = QHBoxLayout()
        function_btn_layout.setSpacing(12)
        function_btn_layout.setContentsMargins(0, 0, 0, 10)
        
        # 同步按钮（主按钮，突出显示）
        self.sync_btn = QPushButton("🔄 手动同步时间")
        self.sync_btn.setFixedHeight(48)
        self.sync_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.sync_btn.setMinimumWidth(150)
        function_btn_layout.addWidget(self.sync_btn)
        self.sync_btn.clicked.connect(self.manual_sync)
        
        # 测试连接按钮
        self.test_btn = QPushButton("🔍 测试服务器连接")
        self.test_btn.setFixedHeight(48)
        self.test_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.test_btn.setMinimumWidth(150)
        function_btn_layout.addWidget(self.test_btn)
        self.test_btn.clicked.connect(self.test_servers)
        
        # 主题切换按钮
        self.theme_btn = QPushButton("🌙 切换至暗黑模式")
        self.theme_btn.setFixedHeight(48)
        self.theme_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.theme_btn.setMinimumWidth(150)
        function_btn_layout.addWidget(self.theme_btn)
        self.theme_btn.clicked.connect(self.toggle_theme)
        
        # 清除日志按钮
        self.clear_btn = QPushButton("🧹 清除日志")
        self.clear_btn.setFixedHeight(48)
        self.clear_btn.setFont(QFont("Microsoft YaHei", 11, QFont.Bold))
        self.clear_btn.setMinimumWidth(150)
        function_btn_layout.addWidget(self.clear_btn)
        self.clear_btn.clicked.connect(self.clear_log)
        
        content_layout.addLayout(function_btn_layout)
        
        # 服务器配置区域（合理高度）
        server_frame = QFrame()
        server_frame.setObjectName("serverFrame")
        server_layout = QVBoxLayout(server_frame)
        server_layout.setContentsMargins(15, 15, 15, 15)
        server_layout.setSpacing(10)
        
        # 服务器区域标题
        server_header = QHBoxLayout()
        server_icon = QLabel("🌐")
        server_icon.setFont(QFont("Arial", 12))
        server_label = QLabel("NTP服务器配置 (每行一个)")
        server_label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        server_header.addWidget(server_icon)
        server_header.addSpacing(8)
        server_header.addWidget(server_label)
        server_header.addStretch()
        server_layout.addLayout(server_header)
        
        # 服务器编辑框（合理高度）
        self.server_edit = QTextEdit()
        self.server_edit.setFixedHeight(80)  # 适当高度
        self.server_edit.setFont(QFont("Consolas", 10))
        self.server_edit.setLineWrapMode(QTextEdit.NoWrap)
        self.server_edit.setText("\n".join(self.servers))
        # 设置编辑框内边距，确保内容不被边框遮挡
        self.server_edit.setStyleSheet("""
            QTextEdit {
                padding: 8px;
                border-radius: 6px;
            }
        """)
        server_layout.addWidget(self.server_edit)
        
        content_layout.addWidget(server_frame)
        
        # 日志显示区域（修复显示不全问题）- 占满所有剩余空间
        log_frame = QFrame()
        log_frame.setObjectName("logFrame")
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(15, 15, 15, 15)
        log_layout.setSpacing(10)
        
        # 日志区域标题
        log_header = QHBoxLayout()
        log_icon = QLabel("📋")
        log_icon.setFont(QFont("Arial", 12))
        log_label = QLabel("同步日志 (多颜色显示)")
        log_label.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
        log_header.addWidget(log_icon)
        log_header.addSpacing(8)
        log_header.addWidget(log_label)
        log_header.addStretch()
        log_layout.addLayout(log_header)
        
        # 日志显示框（修复显示不全，确保完全滚动）
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QTextEdit.WidgetWidth)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setAcceptRichText(True)
        # 强制启用滚动条
        self.log_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # 设置文本交互方式
        self.log_view.setTextInteractionFlags(Qt.TextBrowserInteraction)
        # 关键修复：设置合适的内边距，确保底部内容不被遮挡
        self.log_view.setStyleSheet("""
            QTextEdit {
                padding: 10px;
                border-radius: 6px;
                line-height: 1.5;
            }
        """)
        # 设置大小策略，确保日志区域占满剩余空间
        self.log_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout.addWidget(self.log_view, 1)  # 日志区域占满剩余空间
        
        content_layout.addWidget(log_frame, 1)  # 日志区域占满主内容区域剩余空间
        
        # 状态栏（高对比度）
        self.status_bar = QStatusBar()
        self.status_bar.setFixedHeight(30)
        self.setStatusBar(self.status_bar)
        self.status_bar.setFont(QFont("Microsoft YaHei", 9))
        
        self.status_label = QLabel("🚀 就绪 - 程序启动成功")
        self.status_label.setMinimumWidth(300)
        self.status_bar.addWidget(self.status_label)
        
        self.current_time_label = QLabel("")
        self.current_time_label.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.current_time_label.setMinimumWidth(200)
        self.status_bar.addPermanentWidget(self.current_time_label)
        
        # 应用主题
        self.apply_theme()
        
        # 连接服务器配置变更
        self.server_edit.textChanged.connect(self.save_servers)
    
    def toggle_maximize(self):
        """切换窗口最大化/还原"""
        if self.isMaximized():
            self.showNormal()
            self.max_btn.setText("□")
        else:
            self.showMaximized()
            self.max_btn.setText("☐")
    
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
        ui_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        ui_handler.setFormatter(ui_formatter)
        self.logger.addHandler(ui_handler)
    
    @pyqtSlot(str, int)
    def append_log(self, message, level=logging.INFO):
        """向UI添加日志（确保完全显示）"""
        # 禁用更新，提高性能
        self.log_view.blockSignals(True)
        
        # 移动光标到最后
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        
        if "<br>" in message or "<h3>" in message:
            # HTML格式消息 - 确保换行正确
            self.log_view.insertHtml(f"<div style='margin-bottom: 4px;'>{message}</div><br>")
        else:
            # 普通文本消息（保留多颜色显示）
            format = QTextCharFormat()
            if level == logging.ERROR:
                format.setForeground(QColor("#F44336"))  # 亮红色 - 错误
                format.setFontWeight(QFont.Bold)
            elif level == logging.WARNING:
                format.setForeground(QColor("#FF9800"))  # 橙色 - 警告
                format.setFontWeight(QFont.Bold)
            elif level == logging.INFO:
                format.setForeground(QColor("#2196F3"))  # 亮蓝色 - 信息
            elif level == logging.DEBUG:
                format.setForeground(QColor("#4CAF50"))  # 绿色 - 调试
            else:
                format.setForeground(QColor("#666666"))  # 深灰色 - 其他
            
            cursor.insertText(message + "\n", format)
        
        # 确保光标在最后，强制滚动到底部
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()
        
        # 启用更新
        self.log_view.blockSignals(False)
        
        # 强制刷新界面
        QApplication.processEvents()
    
    def apply_theme(self):
        """完善主题一致性 - 所有按钮都有主题色"""
        palette = QPalette()
        
        if self.dark_mode:
            # ---------------------- 暗黑模式 ----------------------
            palette.setColor(QPalette.Window, QColor(30, 30, 30))      # 主背景（深灰色）
            palette.setColor(QPalette.WindowText, QColor(224, 224, 224)) # 文本（亮灰色）
            palette.setColor(QPalette.Base, QColor(40, 40, 40))        # 编辑框背景（深灰色）
            palette.setColor(QPalette.AlternateBase, QColor(50, 50, 50))# 交替背景
            palette.setColor(QPalette.ToolTipBase, QColor(30, 30, 30))  # 提示框背景
            palette.setColor(QPalette.ToolTipText, QColor(224, 224, 224))# 提示框文本
            palette.setColor(QPalette.Text, QColor(224, 224, 224))      # 编辑框文本
            palette.setColor(QPalette.Button, QColor(50, 50, 50))       # 按钮背景
            palette.setColor(QPalette.ButtonText, QColor(224, 224, 224))# 按钮文本
            palette.setColor(QPalette.BrightText, QColor(255, 255, 255))# 高亮文本
            palette.setColor(QPalette.Highlight, QColor(33, 150, 243))  # 高亮色（亮蓝色）
            palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))# 高亮文本
            
            # 标题栏样式
            title_bar_style = """
                #titleBar {
                    background-color: #252525;
                    border-bottom: 1px solid #404040;
                }
            """
            
            # 窗口控制按钮样式（暗黑模式）
            control_btn_style = """
                QPushButton {
                    background-color: transparent;
                    color: #bbbbbb;
                    border: none;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 0px;
                }
                QPushButton:hover {
                    background-color: #404040;
                    color: white;
                }
                QPushButton:pressed {
                    background-color: #505050;
                }
                QPushButton:last-child:hover {
                    background-color: #F44336;
                    color: white;
                }
                QPushButton:last-child:pressed {
                    background-color: #D32F2F;
                }
            """
            
            # 主按钮样式（同步按钮 - 亮蓝色）
            main_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #2196F3, stop:1 #1976D2);
                    border: 1px solid #0D47A1;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #2979FF, stop:1 #1565C0);
                    border: 1px solid #0A3D62;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #1976D2, stop:1 #0D47A1);
                    border: 1px solid #083364;
                }
                QPushButton:disabled {
                    background: #424242;
                    color: #BDBDBD;
                    border: 1px solid #616161;
                }
            """
            
            # 次要按钮样式（测试服务器 - 青绿色）
            test_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #00BCD4, stop:1 #0097A7);
                    border: 1px solid #006064;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #00E5FF, stop:1 #00ACC1);
                    border: 1px solid #004D40;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #0097A7, stop:1 #006064);
                    border: 1px solid #00332E;
                }
                QPushButton:disabled {
                    background: #424242;
                    color: #BDBDBD;
                    border: 1px solid #616161;
                }
            """
            
            # 主题切换按钮样式（紫色）
            theme_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #9C27B0, stop:1 #7B1FA2);
                    border: 1px solid #4A148C;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #EA80FC, stop:1 #AB47BC);
                    border: 1px solid #6A1B9A;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #7B1FA2, stop:1 #4A148C);
                    border: 1px solid #3A006F;
                }
                QPushButton:disabled {
                    background: #424242;
                    color: #BDBDBD;
                    border: 1px solid #616161;
                }
            """
            
            # 清除日志按钮样式（橙色）
            clear_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #FF9800, stop:1 #F57C00);
                    border: 1px solid #E65100;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #FFB74D, stop:1 #FB8C00);
                    border: 1px solid #CC4125;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #F57C00, stop:1 #E65100);
                    border: 1px solid #A02F10;
                }
                QPushButton:disabled {
                    background: #424242;
                    color: #BDBDBD;
                    border: 1px solid #616161;
                }
            """
            
            # 框架样式
            frame_style = """
                #serverFrame, #logFrame {
                    background-color: #353535;
                    border: 1px solid #505050;
                    border-radius: 8px;
                }
                #borderFrame {
                    background-color: #252525;
                    border: 1px solid #404040;
                    border-radius: 10px;
                }
            """
            
            # 文本编辑框样式
            text_edit_style = """
                QTextEdit {
                    background-color: #404040;
                    color: #e0e0e0;
                    border: 1px solid #606060;
                    selection-background-color: #3949AB;
                }
                QTextEdit:focus {
                    border: 1px solid #2196F3;
                    background-color: #454545;
                }
            """
            
            # 状态栏样式
            status_bar_style = """
                QStatusBar {
                    background-color: #303030;
                    color: #e0e0e0;
                    border-top: 1px solid #505050;
                }
            """
            
            # 主题按钮文本更新
            self.theme_btn.setText("☀️ 切换至亮色模式")
            
        else:
            # ---------------------- 亮色模式 ----------------------
            palette.setColor(QPalette.Window, QColor(248, 249, 250))    # 主背景（淡白色）
            palette.setColor(QPalette.WindowText, QColor(33, 33, 33))   # 文本（深黑色）
            palette.setColor(QPalette.Base, QColor(255, 255, 255))      # 编辑框背景（纯白色）
            palette.setColor(QPalette.AlternateBase, QColor(245, 245, 245))# 交替背景
            palette.setColor(QPalette.ToolTipBase, QColor(255, 255, 255))# 提示框背景
            palette.setColor(QPalette.ToolTipText, QColor(33, 33, 33))  # 提示框文本
            palette.setColor(QPalette.Text, QColor(33, 33, 33))         # 编辑框文本
            palette.setColor(QPalette.Button, QColor(240, 240, 240))    # 按钮背景
            palette.setColor(QPalette.ButtonText, QColor(33, 33, 33))   # 按钮文本
            palette.setColor(QPalette.BrightText, QColor(255, 0, 0))    # 高亮文本
            palette.setColor(QPalette.Highlight, QColor(33, 150, 243))  # 高亮色（清新蓝色）
            palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))# 高亮文本
            
            # 标题栏样式
            title_bar_style = """
                #titleBar {
                    background-color: #f8f9fa;
                    border-bottom: 1px solid #e0e0e0;
                }
            """
            
            # 窗口控制按钮样式（亮色模式）
            control_btn_style = """
                QPushButton {
                    background-color: transparent;
                    color: #666666;
                    border: none;
                    font-size: 16px;
                    font-weight: bold;
                    border-radius: 0px;
                }
                QPushButton:hover {
                    background-color: #e9ecef;
                    color: #333333;
                }
                QPushButton:pressed {
                    background-color: #dee2e6;
                }
                QPushButton:last-child:hover {
                    background-color: #F44336;
                    color: white;
                }
                QPushButton:last-child:pressed {
                    background-color: #D32F2F;
                }
            """
            
            # 主按钮样式（同步按钮 - 清新蓝色）
            main_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #2196F3, stop:1 #1976D2);
                    border: 1px solid #0D47A1;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #2979FF, stop:1 #1565C0);
                    border: 1px solid #0A3D62;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #1976D2, stop:1 #0D47A1);
                    border: 1px solid #083364;
                }
                QPushButton:disabled {
                    background: #E3F2FD;
                    color: #90CAF9;
                    border: 1px solid #BBDEFB;
                }
            """
            
            # 次要按钮样式（测试服务器 - 青绿色）
            test_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #00BCD4, stop:1 #0097A7);
                    border: 1px solid #006064;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #00E5FF, stop:1 #00ACC1);
                    border: 1px solid #004D40;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #0097A7, stop:1 #006064);
                    border: 1px solid #00332E;
                }
                QPushButton:disabled {
                    background: #E0F7FA;
                    color: #80DEEA;
                    border: 1px solid #B2EBF2;
                }
            """
            
            # 主题切换按钮样式（紫色）
            theme_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #9C27B0, stop:1 #7B1FA2);
                    border: 1px solid #4A148C;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #EA80FC, stop:1 #AB47BC);
                    border: 1px solid #6A1B9A;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #7B1FA2, stop:1 #4A148C);
                    border: 1px solid #3A006F;
                }
                QPushButton:disabled {
                    background: #F3E5F5;
                    color: #CE93D8;
                    border: 1px solid #E1BEE7;
                }
            """
            
            # 清除日志按钮样式（橙色）
            clear_btn_style = """
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #FF9800, stop:1 #F57C00);
                    border: 1px solid #E65100;
                    border-radius: 8px;
                    color: white;
                    font-weight: bold;
                    padding: 10px 15px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #FFB74D, stop:1 #FB8C00);
                    border: 1px solid #CC4125;
                }
                QPushButton:pressed {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #F57C00, stop:1 #E65100);
                    border: 1px solid #A02F10;
                }
                QPushButton:disabled {
                    background: #FFF3E0;
                    color: #FFCC80;
                    border: 1px solid #FFE0B2;
                }
            """
            
            # 框架样式
            frame_style = """
                #serverFrame, #logFrame {
                    background-color: white;
                    border: 1px solid #e0e0e0;
                    border-radius: 8px;
                }
                #borderFrame {
                    background-color: #f9f9f9;
                    border: 1px solid #e0e0e0;
                    border-radius: 10px;
                }
            """
            
            # 文本编辑框样式
            text_edit_style = """
                QTextEdit {
                    background-color: white;
                    color: #333333;
                    border: 1px solid #dddddd;
                    selection-background-color: #BBDEFB;
                }
                QTextEdit:focus {
                    border: 1px solid #2196F3;
                    background-color: #FAFAFA;
                }
            """
            
            # 状态栏样式
            status_bar_style = """
                QStatusBar {
                    background-color: #f5f5f5;
                    color: #333333;
                    border-top: 1px solid #e0e0e0;
                }
            """
            
            # 主题按钮文本更新
            self.theme_btn.setText("🌙 切换至暗黑模式")
        
        # 应用全局样式
        self.setPalette(palette)
        self.status_bar.setStyleSheet(status_bar_style)
        
        # 应用标题栏样式
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {palette.color(QPalette.Window).name()};
                font-family: 'Microsoft YaHei', Arial, sans-serif;
            }}
            QLabel {{
                color: {palette.color(QPalette.WindowText).name()};
                font-family: 'Microsoft YaHei', Arial, sans-serif;
            }}
            {title_bar_style}
            {text_edit_style}
            {frame_style}
            QTextBrowser {{
                background-color: transparent;
                color: {palette.color(QPalette.Text).name()};
                border: none;
                padding: 5px;
            }}
            QStatusBar QLabel {{
                color: {palette.color(QPalette.WindowText).name()};
            }}
            QScrollBar:vertical {{
                border: 1px solid #cccccc;
                background: {palette.color(QPalette.Base).name()};
                width: 12px;
                margin: 0px 0px 0px 0px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical {{
                background: #999999;
                min-height: 20px;
                border-radius: 6px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #666666;
            }}
            QScrollBar::add-line:vertical {{
                border: 1px solid #cccccc;
                background: {palette.color(QPalette.Base).name()};
                height: 12px;
                border-radius: 6px;
                subcontrol-position: bottom;
                subcontrol-origin: margin;
            }}
            QScrollBar::sub-line:vertical {{
                border: 1px solid #cccccc;
                background: {palette.color(QPalette.Base).name()};
                height: 12px;
                border-radius: 6px;
                subcontrol-position: top;
                subcontrol-origin: margin;
            }}
        """)
        
        # 应用窗口控制按钮样式
        self.min_btn.setStyleSheet(control_btn_style)
        self.max_btn.setStyleSheet(control_btn_style)
        self.close_btn.setStyleSheet(control_btn_style)
        
        # 单独设置所有功能按钮的主题样式
        self.sync_btn.setStyleSheet(main_btn_style)    # 同步按钮（蓝色）
        self.test_btn.setStyleSheet(test_btn_style)    # 测试服务器（青绿色）
        self.theme_btn.setStyleSheet(theme_btn_style)  # 主题切换（紫色）
        self.clear_btn.setStyleSheet(clear_btn_style)  # 清除日志（橙色）
        
        # 重新连接关闭按钮功能
        self.close_btn.clicked.connect(self.close)
    
    def toggle_theme(self):
        """切换主题"""
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        self.save_config()
        theme_name = "暗黑模式" if self.dark_mode else "亮色模式"
        self.logger.info(f"🎨 主题切换到: {theme_name}")
        self.append_log(f"🎨 主题切换到: {theme_name}", logging.INFO)
    
    def load_config(self):
        """加载配置"""
        config = configparser.ConfigParser()
        try:
            if os.path.exists(self.config_file):
                config.read(self.config_file, encoding='utf-8')
                if 'Settings' in config:
                    self.dark_mode = config.getboolean('Settings', 'dark_mode', fallback=False)
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
            self.append_log(f"🌐 服务器配置已更新: {len(self.servers)} 个服务器", logging.INFO)
        else:
            self.logger.warning("服务器配置为空，保留当前配置")
            self.append_log("⚠️ 警告: 服务器配置为空，保留当前配置", logging.WARNING)
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
        """测试所有NTP服务器连接"""
        self.logger.info("🔧 开始测试所有NTP服务器连接...")
        self.test_btn.setEnabled(False)
        self.status_label.setText("🔍 正在测试服务器连接...")
        self.append_log("🔧 开始测试所有NTP服务器连接...", logging.INFO)
        
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
        self.status_label.setText("✅ 就绪 - 同步完成" if success else "❌ 同步失败")
        
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
        self.append_log(f"🔍 {message}", logging.INFO)
    
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
        """更新当前时间显示（高对比度）"""
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.current_time_label.setText(f"⏰ 当前时间: {current_time}")
    
    def closeEvent(self, event):
        """关闭事件处理"""
        self.logger.info("📤 CloseOperation: 程序正在关闭，清理资源...")
        
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
    
    # 设置高DPI支持（兼容Win7）
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    
    app = QApplication(sys.argv)
    
    # 设置应用样式（兼容Win7的Fusion风格）
    app.setStyle("Fusion")
    
    window = TimeSyncApp()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
