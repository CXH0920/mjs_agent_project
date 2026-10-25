"""
PC 客户端连接与截图模块：仅提供屏幕读取能力。

通过枚举官方 PC 客户端窗口（名将杀，进程 mjs.exe + 窗口类 UnityWndClass，
2026-10-25 真机取证定稿）读取窗口画面：Windows.Graphics.Capture 为主路径
（Win10 1903+，遮挡可采集），GDI BitBlt 客户区裁剪为兜底（窗口须可见）。
PrintWindow 经真机取证对该客户端稳定失败，不采用。选型依据见
docs/design/pc_official_client_capture_design.md。
本模块**故意不实现**以下功能，遵守项目法律红线（见 CLAUDE.md 与 AGENTS.md）：
- 任何输入发送（键盘、鼠标、代切窗口化、模拟输入）
- 自动点击、自动选将、自动战斗
- 游戏进程注入、内存修改、hook
- 反作弊绕过
任何请求此类功能的 Issue/PR 都将被拒绝合并。
如需扩展，请先更新 LICENSE（附加使用条款）、TERMS.md、CLAUDE.md、AGENTS.md，
并通过开发者本人审批。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── 窗口识别特征（双条件匹配；客户端版本更新若失效，改这里并重取证）──
PC_WINDOW_PROCESS = "mjs.exe"
PC_WINDOW_CLASS = "UnityWndClass"
PC_WINDOW_TITLE = "名将杀"

# ── R4 异常文案（需求文档第三节 R4 定稿，勿改语义）──
MSG_CLIENT_NOT_RUNNING = "未检测到名将杀客户端窗口，请先启动客户端"
MSG_MINIMIZED = "请还原客户端窗口后再采集"
MSG_BLACK_FRAME = (
    "检测到全屏黑屏，请将客户端切换为窗口化（游戏内设置或 Alt+Enter）；"
    "若已处于窗口化，请确认游戏画面已完全加载"
)
MSG_MULTI_WINDOW = "检测到多个名将杀客户端窗口，请在设备列表中选择一个（不支持多开）"

# 黑帧判定阈值：取证正常画面灰度均值 90~111、近黑占比 ≤7%，黑帧双指标远离
# 该区间；均值与占比同时命中才判定，避免暗色调界面误杀
_BLACK_MEAN_LIMIT = 8.0
_BLACK_DARK_RATIO = 0.98
_BLACK_DARK_LEVEL = 16

# WGC 首帧等待上界：连接自检时等首帧到达的时间
_WGC_FIRST_FRAME_TIMEOUT_S = 5.0

# Win32 DLL 加载带平台守卫：ctypes.WinDLL 仅存在于 Windows，而本模块被
# capture_connection 顶层 import，Linux（CI/跨平台开发）必须可导入。占位
# 对象的任意属性解析为哑函数：下方签名声明块对哑函数赋 .argtypes/.restype
# 同样成立（赋在临时函数对象上即丢弃），测试对 _USER32.xxx 打桩获得属性
# 宿主；未被桩掉的触达（如 connect 首行的 ensure_dpi_awareness）按 Win32
# 调用失败语义返回 0，走调用方既有容错分支


class _NullWinDLL:
    """非 Windows 占位 DLL：属性访问返回报 0 的哑函数。"""

    def __getattr__(self, name: str):
        def _null_call(*_args, **_kwargs) -> int:
            return 0

        return _null_call


if sys.platform == "win32":
    _USER32 = ctypes.WinDLL("user32", use_last_error=True)
    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _GDI32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _DWMAPI = ctypes.WinDLL("dwmapi", use_last_error=True)
else:
    _USER32 = _KERNEL32 = _GDI32 = _DWMAPI = _NullWinDLL()

# 64 位句柄与指针参数必须显式声明，否则 ctypes 按 32 位 int 截断/溢出
_USER32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
_USER32.SetProcessDpiAwarenessContext.restype = wt.BOOL
_USER32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
_USER32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_USER32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
_USER32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
_USER32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_USER32.IsWindowVisible.restype = wt.BOOL
_USER32.IsWindow.argtypes = [ctypes.c_void_p]
_USER32.IsWindow.restype = wt.BOOL
_USER32.IsIconic.argtypes = [ctypes.c_void_p]
_USER32.IsIconic.restype = wt.BOOL
_USER32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
_USER32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
_USER32.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.POINT)]
_USER32.GetDC.restype = ctypes.c_void_p
_USER32.GetDC.argtypes = [ctypes.c_void_p]
_USER32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_USER32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
_USER32.PrintWindow.restype = wt.INT
_KERNEL32.OpenProcess.restype = ctypes.c_void_p
_KERNEL32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_KERNEL32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]
_KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
_GDI32.CreateCompatibleDC.restype = ctypes.c_void_p
_GDI32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_GDI32.CreateCompatibleBitmap.restype = ctypes.c_void_p
_GDI32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_GDI32.SelectObject.restype = ctypes.c_void_p
_GDI32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_GDI32.DeleteObject.argtypes = [ctypes.c_void_p]
_GDI32.DeleteDC.argtypes = [ctypes.c_void_p]
_GDI32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wt.DWORD]
_GDI32.BitBlt.restype = wt.BOOL
_DWMAPI.DwmGetWindowAttribute.argtypes = [
    ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD]
# 注意不可声明 restype=HRESULT：ctypes 会把非零 HRESULT 自动转成 OSError
# 抛出，这里按数值自行判断
_DWMAPI.DwmGetWindowAttribute.restype = ctypes.c_long


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", wt.DWORD * 3)]


_GDI32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wt.UINT, wt.UINT,
                             ctypes.c_void_p, ctypes.POINTER(_BitmapInfo), wt.UINT]

_dpi_awareness_done = False


def ensure_dpi_awareness() -> None:
    """进程级 Per-Monitor-V2 声明（幂等）：未声明进程的窗口坐标会被系统按
    DPI 虚拟化谎报（真机取证：125% 屏上客户区谎报 2048×1088，真实 2560×1361），
    坐标裁剪全部失准。Qt6 宿主进程通常已自行声明，重复调用失败即无需处理。"""
    global _dpi_awareness_done
    if _dpi_awareness_done:
        return
    ok = _USER32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    if not ok:
        logger.debug("进程 DPI 感知声明未生效（宿主可能已声明），窗口坐标按物理像素读取")
    _dpi_awareness_done = True


@dataclass
class PcWindowInfo:
    """一个候选客户端窗口（对齐 EmulatorDeviceInfo 的 UI 消费角色）。"""

    hwnd: int
    pid: int
    title: str
    process_name: str
    client_size: tuple[int, int]
    iconic: bool

    @property
    def label(self) -> str:
        """设备下拉展示文案：标题 + 客户区分辨率。"""
        width, height = self.client_size
        return f"{self.title}（{width}×{height}）"


def _process_name_of(pid: int) -> str:
    """按 PID 查询进程可执行名（小写文件名），失败返回空串。"""
    handle = _KERNEL32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if _KERNEL32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        _KERNEL32.CloseHandle(handle)


def probe_pc_windows() -> list[PcWindowInfo]:
    """枚举运行中的官方 PC 客户端窗口（进程名 + 窗口类名双条件）。

    返回候选列表；空列表 = 未运行（R4：调用方按未启动提示）。
    """
    ensure_dpi_awareness()
    found: list[PcWindowInfo] = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _enum_callback(hwnd, _lparam):
        if not _USER32.IsWindowVisible(hwnd):
            return True
        if _USER32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        class_name = ctypes.create_unicode_buffer(256)
        _USER32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value != PC_WINDOW_CLASS:
            return True
        pid = wt.DWORD()
        _USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if _process_name_of(pid.value) != PC_WINDOW_PROCESS:
            return True
        title = ctypes.create_unicode_buffer(256)
        _USER32.GetWindowTextW(hwnd, title, 256)
        client = wt.RECT()
        _USER32.GetClientRect(hwnd, ctypes.byref(client))
        found.append(PcWindowInfo(
            hwnd=int(hwnd),
            pid=pid.value,
            title=title.value,
            process_name=PC_WINDOW_PROCESS,
            client_size=(client.right, client.bottom),
            iconic=bool(_USER32.IsIconic(hwnd)),
        ))
        return True

    _USER32.EnumWindows(_enum_callback, 0)
    return found


def is_black_frame(image: Image.Image) -> bool:
    """黑帧判定：灰度均值与近黑像素占比双指标同时命中（独占全屏检测依据）。"""
    gray = np.asarray(image.convert("L"))
    mean = float(gray.mean())
    dark_ratio = float((gray < _BLACK_DARK_LEVEL).mean())
    return mean < _BLACK_MEAN_LIMIT and dark_ratio > _BLACK_DARK_RATIO


def _client_metrics(hwnd: int) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """客户区（尺寸, 屏幕物理坐标原点）；最小化或无效窗口返回 None。"""
    if not _USER32.IsWindow(ctypes.c_void_p(hwnd)) or _USER32.IsIconic(ctypes.c_void_p(hwnd)):
        return None
    client = wt.RECT()
    if not _USER32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(client)):
        return None
    width, height = client.right - client.left, client.bottom - client.top
    if width <= 0 or height <= 0:
        return None
    origin = wt.POINT(client.left, client.top)
    if not _USER32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(origin)):
        return None
    return (width, height), (origin.x, origin.y)


def _dwm_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """DWM 可见边框（物理坐标 left/top/right/bottom）：WGC 帧原点与尺寸均与其
    对齐，用于推算客户区在帧内偏移与帧尺寸对账。"""
    rect = wt.RECT()
    result = _DWMAPI.DwmGetWindowAttribute(
        ctypes.c_void_p(hwnd), 13, ctypes.byref(rect), ctypes.sizeof(rect))  # EXTENDED_FRAME_BOUNDS
    if result != 0:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


# 帧尺寸对账容差：WGC 帧尺寸应与 DWM 可见边框一致（真机取证精确吻合），
# 留余量容忍窗口 resize 瞬间帧池跟随延迟
_FRAME_SIZE_TOLERANCE = 32


def _crop_wgc_frame_to_client(frame: np.ndarray, hwnd: int) -> Image.Image | None:
    """WGC 整窗帧（含标题栏）→ 客户区 RGB 图。窗口尺寸可变，偏移每帧实算。

    帧通道为 Direct3D B8G8R8A8（BGRA）：库的 frame_buffer 是 B8G8R8A8 纹理
    内存，取前三通道后必须反转红蓝，否则游戏暖色调整体偏蓝紫（2026-10-26
    真机发现；此前 WGC 误连灰白色工具 UI，三通道相等掩盖了互换）。
    帧尺寸与目标窗口可见边框对账（偏差超容差即丢帧）：WGC 会话理论上按
    句柄定向，但任何未知的错误 attach（如历史上按标题子串匹配命中
    "名将杀 Agent" 等无关窗口）在此被尺寸差拦截，杜绝截错窗口的画面
    被当作成功结果返回（显示诚实红线）。
    """
    metrics = _client_metrics(hwnd)
    if metrics is None:
        return None
    (width, height), (screen_x, screen_y) = metrics
    frame_height, frame_width = frame.shape[:2]
    bounds = _dwm_frame_bounds(hwnd)
    if bounds is not None:
        bounds_w, bounds_h = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if (abs(frame_width - bounds_w) > _FRAME_SIZE_TOLERANCE
                or abs(frame_height - bounds_h) > _FRAME_SIZE_TOLERANCE):
            logger.warning(
                "WGC 帧尺寸与目标窗口可见边框失配（帧 %dx%d，边框 %dx%d），"
                "疑非目标窗口，本帧丢弃走兜底", frame_width, frame_height, bounds_w, bounds_h)
            return None
        frame_origin = (bounds[0], bounds[1])
    else:
        # DWM 边框查询失败时的回退：窗口矩形含不可见阴影（Win10 左右各约
        # 7~9px、上边为 0），而 WGC 帧从可见边框起算。水平方向按"阴影左右
        # 对称"用（矩形宽 - 帧宽）/2 自校准扣除左阴影——此前直接用矩形左上
        # 导致横向偏移大一个阴影宽、右侧被裁约 7px（真机实测 1687 vs 1694、
        # 1913 vs 1920）；垂直方向上边阴影为 0，矩形顶即帧顶，无需修正
        rect = wt.RECT()
        _USER32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        shadow = (rect.right - rect.left - frame_width) // 2
        frame_origin = (rect.left + shadow, rect.top)
    left = max(0, screen_x - frame_origin[0])
    top = max(0, screen_y - frame_origin[1])
    right = min(frame_width, left + width)
    bottom = min(frame_height, top + height)
    if right - left < width // 2 or bottom - top < height // 2:
        logger.warning("WGC 帧与客户区尺寸失配（帧 %dx%d，客户区 %dx%d），本帧丢弃",
                       frame_width, frame_height, width, height)
        return None
    crop = frame[top:bottom, left:right, :3][:, :, ::-1]  # BGRA→RGB
    return Image.fromarray(np.ascontiguousarray(crop))


def _bitblt_client_capture(hwnd: int) -> Image.Image | None:
    """GDI BitBlt 兜底：屏幕 DC 按客户区矩形直取（窗口须可见，遮挡部分截不到）。"""
    metrics = _client_metrics(hwnd)
    if metrics is None:
        return None
    (width, height), (screen_x, screen_y) = metrics

    hdc_screen = _USER32.GetDC(None)
    if not hdc_screen:
        logger.error("获取屏幕 DC 失败")
        return None
    hdc_mem = _GDI32.CreateCompatibleDC(hdc_screen)
    bitmap = _GDI32.CreateCompatibleBitmap(hdc_screen, width, height)
    _GDI32.SelectObject(hdc_mem, bitmap)
    try:
        # SRCCOPY|CAPTUREBLT：后者带上分层窗口内容
        ok = _GDI32.BitBlt(hdc_mem, 0, 0, width, height, hdc_screen,
                           screen_x, screen_y, 0x00CC0020 | 0x40000000)
        if not ok:
            logger.warning("BitBlt 客户区复制失败（可能窗口不在任何显示器可见区）")
            return None
        info = _BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 顶向下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buffer = ctypes.create_string_buffer(width * height * 4)
        if _GDI32.GetDIBits(hdc_mem, bitmap, 0, height, buffer, ctypes.byref(info), 0) == 0:
            logger.error("GetDIBits 读取位图失败")
            return None
    finally:
        _GDI32.DeleteObject(bitmap)
        _GDI32.DeleteDC(hdc_mem)
        _USER32.ReleaseDC(None, hdc_screen)

    pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)
    return Image.fromarray(np.ascontiguousarray(pixels[:, :, :3][:, :, ::-1]))  # BGRA→RGB


class _WgcSession:
    """后台 WGC 会话：按窗口句柄定向连接，帧回调线程持续刷新最新帧。

    必须用 window_hwnd 定向：window_name 是标题**子串匹配**（库文档明示），
    工具自身主窗口"名将杀 Agent"包含"名将杀"，连接时工具窗口必在 Z 序
    顶端，实测 WGC 会 attach 到工具自身窗口、截出工具 UI（2026-10-26
    真机事故）；游戏标题的任何子串均为工具标题子串，字符串层面无解，
    句柄定向是唯一根治。
    """

    def __init__(self, hwnd: int) -> None:
        self._hwnd = hwnd
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None   # BGRA（Direct3D B8G8R8A8）
        self._frame_at = 0.0
        self._control = None
        self._thread: threading.Thread | None = None
        self._start_error = ""

    @property
    def start_error(self) -> str:
        return self._start_error

    def start(self) -> bool:
        """启动捕获线程；返回是否成功创建（首帧由 wait_first_frame 等待）。"""
        try:
            from windows_capture import WindowsCapture
        except ImportError as e:
            self._start_error = f"windows-capture 依赖不可用: {e}"
            return False

        capture = WindowsCapture(
            cursor_capture=False,  # OCR 采集不带光标：光标悬停会遮挡 UI 元素
            draw_border=None,
            monitor_index=None,
            window_hwnd=self._hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            # frame_buffer 在回调返回后失效，必须立即拷贝
            with self._lock:
                # BGRA HxWx4（Direct3D B8G8R8A8；save_as_image 走 cv2 BGR 约定故正确，
                # 直接消费必须自行反转红蓝，见 _crop_wgc_frame_to_client）
                self._frame = np.array(frame.frame_buffer)
                self._frame_at = time.monotonic()
                if self._control is None:
                    self._control = capture_control

        @capture.event
        def on_closed():
            logger.info("PC 客户端 WGC 捕获会话已关闭")

        def _run():
            try:
                capture.start()
            except Exception as e:  # noqa: BLE001 线程边界，任何异常都要落日志
                with self._lock:
                    self._start_error = str(e)
                logger.warning("WGC 会话启动失败（系统低于 Win10 1903 或窗口已关闭）: %s", e)

        self._thread = threading.Thread(target=_run, name="pc-wgc-capture", daemon=True)
        self._thread.start()
        return True

    def wait_first_frame(self, timeout_s: float = _WGC_FIRST_FRAME_TIMEOUT_S) -> bool:
        """等待首帧到达（连接自检用）。"""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None:
                    return True
                if self._start_error:
                    return False
            time.sleep(0.05)
        return False

    def latest_frame(self) -> np.ndarray | None:
        """最新 BGRA 帧（无帧返回 None，消费方负责红蓝反转）。客户端失焦停渲时返回最后一帧，属画面
        本身停更而非采集缺陷，识别层的结果比对天然兼容，不做新鲜度特判。"""
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def stop(self) -> None:
        with self._lock:
            control = self._control
            self._frame = None
            self._control = None
        if control is not None:
            try:
                control.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("WGC 会话停止异常（可忽略）: %s}", e)


class PcClientCapture:
    """封装官方 PC 客户端窗口的只读采集；接口对齐 AdbCapture，下游管线无感复用。"""

    def __init__(self, window_title: str = PC_WINDOW_TITLE) -> None:
        self._window_title = window_title
        self._hwnd = 0
        self._wgc: _WgcSession | None = None
        self._connected = False

    # ── 会话标识（对齐 AdbCapture 属性语义）──────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_serial(self) -> str:
        """当前连接的窗口标识（标题 + 句柄），未连接为空串。"""
        return f"{self._window_title} (hwnd={self._hwnd})" if self._connected else ""

    def set_target_window(self, hwnd: int) -> None:
        """切换目标窗口（连接前设置）：多实例时由 UI 用户选择指定。"""
        if self._connected and self._hwnd != hwnd:
            self._disconnect_safe()
        self._hwnd = hwnd

    # ── 连接管理 ──────────────────────────────────────────────────────

    def connect(self, *, validate_capture: bool = False) -> tuple[bool, str]:
        """连接客户端窗口：解析目标 → 启动采集会话 → 可选首帧校验。

        Args:
            validate_capture: 连接后截一帧并校验非黑帧才算成功（用户触发路径）。
        """
        ensure_dpi_awareness()
        resolved = self._resolve_target()
        if isinstance(resolved, str):
            return False, resolved
        self._hwnd = resolved
        hwnd = ctypes.c_void_p(self._hwnd)

        if self._connected:
            return True, "已处于连接状态"

        if not _USER32.IsWindow(hwnd) or _USER32.IsIconic(hwnd):
            return False, MSG_MINIMIZED

        self._start_wgc()
        if validate_capture:
            ok, payload = self.screencap_full(log_success=False)
            if not ok:
                self._disconnect_safe()
                detail = payload if isinstance(payload, str) else "画面校验失败"
                return False, f"连接成功但画面校验失败: {detail}"

        self._connected = True
        mode = "WGC + BitBlt 兜底" if self._wgc else "BitBlt（WGC 不可用或多实例）"
        logger.info("PC 客户端连接成功 (hwnd=%s，采集路径: %s)", self._hwnd, mode)
        return True, f"连接成功 ({self._window_title})"

    def disconnect(self) -> tuple[bool, str]:
        self._disconnect_safe()
        return True, "已断开连接"

    def _disconnect_safe(self) -> None:
        if self._wgc is not None:
            self._wgc.stop()
            self._wgc = None
        self._connected = False

    def _resolve_target(self) -> int | str:
        """解析目标窗口：已有句柄校验存活（失效按标题重绑），否则按标题枚举。"""
        if self._hwnd:
            if _USER32.IsWindow(ctypes.c_void_p(self._hwnd)):
                return self._hwnd
            logger.info("缓存的窗口句柄已失效，按标题重新发现")
            self._hwnd = 0
        windows = probe_pc_windows()
        if not windows:
            return MSG_CLIENT_NOT_RUNNING
        if len(windows) == 1:
            return windows[0].hwnd
        return MSG_MULTI_WINDOW

    def _start_wgc(self) -> None:
        """按已解析的窗口句柄启动 WGC 主路径；启动失败静默降级 BitBlt-only。

        句柄定向后无标题歧义（含多实例场景：用户在 UI 选择的任一窗口
        均可精确定向），失败仅剩系统不支持（<Win10 1903）或会话异常两类。
        """
        session = _WgcSession(self._hwnd)
        if not session.start():
            logger.info("WGC 会话不可用（%s），退 BitBlt 采集", session.start_error)
            return
        if not session.wait_first_frame():
            logger.info("WGC 首帧超时（%s），退 BitBlt 采集",
                        session.start_error or "5 秒内无帧")
            session.stop()
            return
        self._wgc = session

    # ── 截图 ──────────────────────────────────────────────────────────

    def screencap_full(self, *, log_success: bool = True) -> tuple[bool, Image.Image | str]:
        """截取客户端客户区画面，返回 (是否成功, PIL Image 或错误消息)。"""
        if not self._connected and not self._hwnd:
            return False, "尚未连接，请先连接 PC 客户端"

        hwnd = ctypes.c_void_p(self._hwnd)
        if not _USER32.IsWindow(hwnd):
            self._disconnect_safe()
            return False, MSG_CLIENT_NOT_RUNNING
        if _USER32.IsIconic(hwnd):
            return False, MSG_MINIMIZED

        # 主路径：WGC 最新帧 → 客户区裁剪 → 黑帧校验（裁剪异常降级兜底）
        if self._wgc is not None:
            frame = self._wgc.latest_frame()
            if frame is not None:
                try:
                    image = _crop_wgc_frame_to_client(frame, self._hwnd)
                except Exception as e:  # noqa: BLE001 采集降级链不允许单帧异常炸穿
                    logger.warning("WGC 帧客户区裁剪异常，本轮降级 BitBlt: %s", e)
                    image = None
                if image is not None and not is_black_frame(image):
                    if log_success:
                        logger.info("截图成功: %s x %s（WGC）", image.width, image.height)
                    return True, image

        # 兜底：BitBlt 客户区直取（窗口须可见；被遮挡部分截不到）
        image = _bitblt_client_capture(self._hwnd)
        if image is not None and not is_black_frame(image):
            if log_success:
                logger.info("截图成功: %s x %s（BitBlt）", image.width, image.height)
            return True, image

        if image is None:
            self._disconnect_safe()
            return False, MSG_CLIENT_NOT_RUNNING
        return False, MSG_BLACK_FRAME

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"PcClientCapture(title={self._window_title!r}, {status})"
