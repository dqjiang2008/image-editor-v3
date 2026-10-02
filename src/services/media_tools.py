"""FFmpeg / FFprobe 定位与自动安装（全局唯一入口）。

背景（真实故障）：
    视频页点「合并视频」直接失败，日志只有一句
    「ffmpeg 未安装，请安装 ffmpeg 并添加到系统 PATH」。
    根因是当时全项目都用 shutil.which("ffmpeg") 查找，
    而这台机器上 ffmpeg 不在 PATH、没放进 tools/、也没装
    imageio-ffmpeg / moviepy，于是合并与时长探测全部不可用。

本模块提供：
    find_ffmpeg() / find_ffprobe()   按优先级查找（带缓存）
    ffmpeg_available()               是否可用
    install_hint()                   多方案安装指引（给用户看）
    ensure_ffmpeg()                  先找，找不到就自动下载安装
    download_ffmpeg()                下载静态构建并释放到 tools/ffmpeg/bin
    probe_duration()                 读媒体时长（ffprobe 缺失时退化用 ffmpeg -i）

查找优先级：
    ① 环境变量 FFMPEG_BINARY / FFMPEG_PATH（用户显式指定）
    ② 项目自带 tools/ffmpeg/bin/ffmpeg.exe（本模块下载目标，随项目走）
    ③ 系统 PATH
    ④ imageio-ffmpeg 附带的二进制
    ⑤ Windows 常见安装位置（winget / choco / scoop / Program Files）
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional, Tuple

__all__ = [
    "find_ffmpeg", "find_ffprobe", "ffmpeg_available",
    "ffmpeg_dir", "clear_cache", "install_hint",
    "download_ffmpeg", "ensure_ffmpeg",
    "probe_duration", "probe_has_audio", "run_ffmpeg", "run_ffprobe",
    "open_dir",
]

# 环境变量名（用户可用它指定自定义 ffmpeg）
ENV_KEYS = ("FFMPEG_BINARY", "FFMPEG_PATH", "IMAGEIO_FFMPEG_EXE")

# 按顺序尝试的静态构建下载源（体积从小到大；最后一个为 GitHub 官方镜像）
DOWNLOAD_SOURCES = (
    ("gyan.dev essentials",
     "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"),
    ("BtbN win64-gpl",
     "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
     "ffmpeg-master-latest-win64-gpl.zip"),
)

_CACHE: dict = {"ffmpeg": None, "ffprobe": None, "searched": False}


def project_root() -> Path:
    """项目根目录（src/services/media_tools.py → 上三级）。"""
    return Path(__file__).resolve().parent.parent.parent


def ffmpeg_dir() -> Path:
    """项目自带 ffmpeg 目录（下载目标）。"""
    return project_root() / "tools" / "ffmpeg" / "bin"


def clear_cache():
    """清除查找缓存（安装完 ffmpeg 后必须调用）。"""
    _CACHE.update({"ffmpeg": None, "ffprobe": None, "searched": False})


def _exe_names(name: str) -> Tuple[str, ...]:
    return (f"{name}.exe", name) if sys.platform == "win32" else (name,)


def _valid(path: Optional[str]) -> Optional[str]:
    """校验路径确实是个可执行文件（避免匹配到同名目录）。"""
    if not path:
        return None
    p = Path(path)
    try:
        return str(p.resolve()) if p.is_file() else None
    except OSError:
        return None


def _candidate_dirs() -> list:
    """额外的 ffmpeg 常见安装目录。"""
    dirs = []
    env_path = os.environ.get("PATH", "")
    if env_path:
        # PATH 里的目录由 shutil.which 负责，这里只补非常规位置
        pass
    local = os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    if local:
        dirs += [
            Path(local) / "Microsoft" / "WinGet" / "Links",
            Path(local) / "ffmpeg" / "bin",
        ]
    dirs += [
        Path(prog) / "ffmpeg" / "bin",
        Path(prog86) / "ffmpeg" / "bin",
        Path(r"C:\ffmpeg\bin"),
        Path(r"C:\ProgramData\chocolatey\bin"),
    ]
    user = os.environ.get("USERPROFILE", "")
    if user:
        dirs.append(Path(user) / "scoop" / "shims")
    return dirs


def _find(name: str) -> Optional[str]:
    """按优先级查找 ffmpeg / ffprobe，返回绝对路径（找不到返回 None）。"""
    names = _exe_names(name)

    # ① 环境变量（用户显式指定优先）
    for key in ENV_KEYS:
        val = os.environ.get(key)
        if not val:
            continue
        p = Path(val)
        # 允许直接把 FFMPEG_BINARY 指到 ffmpeg.exe 或它所在目录
        for cand in (p, p / names[0]):
            got = _valid(str(cand))
            if got and cand.name.lower().startswith(name):
                return got

    # ② 项目自带目录（本模块的下载目标）
    for n in names:
        got = _valid(str(ffmpeg_dir() / n))
        if got:
            return got

    # ③ 系统 PATH
    got = shutil.which(name) or shutil.which(names[0])
    got = _valid(got)
    if got:
        return got

    # ④ imageio-ffmpeg 自带的二进制（仅 ffmpeg，没有 ffprobe）
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg  # type: ignore
            got = _valid(imageio_ffmpeg.get_ffmpeg_exe())
            if got:
                return got
        except Exception:
            pass

    # ⑤ 常见安装目录
    for d in _candidate_dirs():
        for n in names:
            got = _valid(str(Path(d) / n))
            if got:
                return got
    return None


def find_ffmpeg(refresh: bool = False) -> Optional[str]:
    """查找 ffmpeg 可执行文件（结果缓存；安装后请 refresh=True）。"""
    if refresh:
        clear_cache()
    if _CACHE["ffmpeg"] is None and not _CACHE["searched"]:
        _CACHE["searched"] = True
        _CACHE["ffmpeg"] = _find("ffmpeg")
    return _CACHE["ffmpeg"]


def find_ffprobe(refresh: bool = False) -> Optional[str]:
    """查找 ffprobe 可执行文件。"""
    if refresh:
        clear_cache()
    if _CACHE["ffprobe"] is None:
        _CACHE["ffprobe"] = _find("ffprobe")
    return _CACHE["ffprobe"]


def ffmpeg_available() -> bool:
    """ffmpeg 是否可用（不触发下载）。"""
    return bool(find_ffmpeg())


def install_hint() -> str:
    """给用户看的多方案安装指引。"""
    bundled = ffmpeg_dir() / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    lines = [
        "未检测到 FFmpeg：合并视频 / 时长探测等功能需要它。",
        "",
        "方案 A（推荐，本程序自动完成）：点「⬇ 安装 FFmpeg」按钮，"
        "自动下载约 90MB 静态构建到：",
        f"    {bundled.parent}",
        "",
        "方案 B（Windows 包管理器，需自行重开程序）：",
        "    winget install Gyan.FFmpeg",
        "    choco install ffmpeg-full",
        "    scoop install ffmpeg",
        "",
        "方案 C（手动）：到 https://www.gyan.dev/ffmpeg/builds/ 下载 "
        "release-essentials.zip，解压后把 bin\\ffmpeg.exe、bin\\ffprobe.exe "
        "放到上面那个目录，或把 bin 目录加入系统 PATH。",
        "",
        "安装完成后重启本程序即可。",
    ]
    return "\n".join(lines)


# ── 自动下载安装 ────────────────────────────────────────────────────────
def _notify(cb: Optional[Callable], done: int, total: int, msg: str):
    if not cb:
        return
    try:
        cb(done, total, msg)
    except TypeError:
        cb(msg)  # 兼容只接受一个 message 参数的回调


class Cancelled(Exception):
    """用户在下载过程中取消（GUI 用它区分「取消」与「失败」）。"""


def _download_to(url: str, dest: Path, cb: Optional[Callable], timeout: int = 900,
                 should_cancel: Optional[Callable] = None):
    """流式下载并回调进度（(已下载, 总大小, 阶段说明)）。

    每写一块检查一次 should_cancel()，用户取消时抛 Cancelled。
    """
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "image-editor-v2"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                if should_cancel and should_cancel():
                    raise Cancelled("已取消下载")
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                _notify(cb, done, total, "下载中")
    return dest


def _extract_ffmpeg(zip_path: Path, cb: Optional[Callable]) -> list:
    """把压缩包里的 ffmpeg/ffprobe 释放到 tools/ffmpeg/bin。"""
    target = ffmpeg_dir()
    target.mkdir(parents=True, exist_ok=True)
    wanted = {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}
    extracted = []
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        for member in members:
            if Path(member).name.lower() not in wanted:
                continue
            _notify(cb, 0, 0, f"解压 {Path(member).name}")
            out = target / Path(member).name
            out.write_bytes(zf.read(member))
            if sys.platform != "win32":
                out.chmod(0o755)
            extracted.append(out.name)
    if not any(n.lower().startswith("ffmpeg") for n in extracted):
        raise RuntimeError("压缩包内未找到 ffmpeg 可执行文件")
    return extracted


def _install_via_pip(cb: Optional[Callable],
                     should_cancel: Optional[Callable] = None) -> Tuple[bool, str]:
    """兜底方案：pip 安装 imageio-ffmpeg，并把其二进制复制到 tools 目录。"""
    if should_cancel and should_cancel():
        return False, "已取消安装 FFmpeg"
    try:
        _notify(cb, 0, 0, "尝试 pip 安装 imageio-ffmpeg（备用方案）…")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "imageio-ffmpeg"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600,
        )
        if proc.returncode != 0:
            return False, f"pip 安装 imageio-ffmpeg 失败: {(proc.stdout or '')[-300:]}"
        import importlib
        imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
        src = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if not src.is_file():
            return False, "imageio-ffmpeg 未提供可执行文件"
        target = ffmpeg_dir()
        target.mkdir(parents=True, exist_ok=True)
        dst = target / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        shutil.copy2(src, dst)
        clear_cache()
        exe = find_ffmpeg(refresh=True)
        if exe:
            return True, f"已用 imageio-ffmpeg 安装 FFmpeg: {exe}（无 ffprobe）"
        return False, "复制后仍无法定位 FFmpeg"
    except Exception as e:
        return False, f"pip 方案失败: {e}"


def download_ffmpeg(progress_cb: Optional[Callable] = None,
                    should_cancel: Optional[Callable] = None,
                    timeout: int = 900) -> Tuple[bool, str]:
    """自动下载安装 FFmpeg 静态构建到 tools/ffmpeg/bin。

    Returns:
        (是否成功, 结果说明)  —— 说明可用于直接展示给用户。
    """
    import tempfile

    errors = []
    for label, url in DOWNLOAD_SOURCES:
        if should_cancel and should_cancel():
            return False, "已取消安装 FFmpeg"
        tmp = Path(tempfile.gettempdir()) / "ffmpeg_download.zip"
        try:
            _notify(progress_cb, 0, 0, f"正在从 {label} 下载 FFmpeg…")
            _download_to(url, tmp, progress_cb, timeout, should_cancel=should_cancel)
            _extract_ffmpeg(tmp, progress_cb)
            clear_cache()
            exe = find_ffmpeg(refresh=True)
            if exe:
                return True, f"✅ FFmpeg 安装完成: {exe}"
            errors.append(f"{label}: 解压后仍找不到 ffmpeg")
        except Cancelled:
            return False, "已取消安装 FFmpeg"
        except Exception as e:
            errors.append(f"{label}: {e}")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    ok, msg = _install_via_pip(progress_cb, should_cancel)
    if ok:
        return True, msg
    errors.append(msg)
    return False, "自动安装失败：\n  " + "\n  ".join(errors) + "\n\n" + install_hint()


def ensure_ffmpeg(progress_cb: Optional[Callable] = None,
                  should_cancel: Optional[Callable] = None,
                  allow_download: bool = True) -> Tuple[bool, str]:
    """确保 ffmpeg 可用：先查找，找不到且允许时自动下载安装。"""
    exe = find_ffmpeg(refresh=True)
    if exe:
        return True, exe
    if not allow_download:
        return False, install_hint()
    return download_ffmpeg(progress_cb, should_cancel)


# ── 调用辅助（统一隐藏黑窗、统一超时） ──────────────────────────────────
def _windows_hidden() -> dict:
    """Windows 下隐藏控制台窗口；其他平台返回空。"""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": si, "creationflags": subprocess.CREATE_NO_WINDOW}


def run_ffmpeg(args, timeout: int = 300):
    """执行 ffmpeg，返回 CompletedProcess；未安装则抛 FileNotFoundError。"""
    exe = find_ffmpeg()
    if not exe:
        raise FileNotFoundError(install_hint())
    cmd = [exe] + [str(a) for a in args]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, **_windows_hidden())


def run_ffprobe(args, timeout: int = 30):
    """执行 ffprobe，返回 CompletedProcess；未安装则抛 FileNotFoundError。"""
    exe = find_ffprobe()
    if not exe:
        raise FileNotFoundError("ffprobe 未安装")
    cmd = [exe] + [str(a) for a in args]
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, **_windows_hidden())


# ── 媒体信息探测 ────────────────────────────────────────────────────────
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")


def probe_duration(path, timeout: int = 20) -> Optional[float]:
    """读取媒体时长（秒）。

    优先 ffprobe；机器上只有 ffmpeg 时退化解析 `ffmpeg -i` 的 stderr，
    避免「没装 ffprobe → 时长一律拿不到」导致的界面异常。
    """
    p = str(path)
    if find_ffprobe():
        try:
            proc = run_ffprobe(
                ["-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", p],
                timeout=timeout,
            )
            if proc.returncode == 0 and (proc.stdout or "").strip():
                return float(proc.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    if find_ffmpeg():
        try:
            proc = run_ffmpeg(["-i", p], timeout=timeout)
            m = _DURATION_RE.search(proc.stderr or "")
            if m:
                h, mi, s = m.group(1), m.group(2), m.group(3)
                return int(h) * 3600 + int(mi) * 60 + float(s)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return None


def probe_has_audio(path, timeout: int = 30) -> Optional[bool]:
    """判断媒体是否含音频流；无法判断时返回 None（调用方自行决定默认值）。"""
    p = str(path)
    if find_ffprobe():
        try:
            proc = run_ffprobe(["-v", "quiet", "-print_format", "json",
                                "-show_streams", "-select_streams", "a", p],
                               timeout=timeout)
            if proc.returncode == 0:
                return '"codec_type"' in (proc.stdout or "")
        except (OSError, subprocess.SubprocessError):
            return None
    if find_ffmpeg():
        try:
            proc = run_ffmpeg(["-i", p], timeout=timeout)
            return "Audio:" in (proc.stderr or "")
        except (OSError, subprocess.SubprocessError):
            return None
    return None


# ── 手动安装方案的便捷入口 ────────────────────────────────────────────────────
def open_dir(path: Path) -> None:
    """在资源管理器中打开目录（手动安装方案的便捷入口）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    _open_dir_in_explorer(path)


def _open_dir_in_explorer(path: Path) -> None:
    import os
    import subprocess
    if sys.platform == "win32":
        try:
            os.startfile(str(path))
        except Exception:
            subprocess.Popen(["explorer.exe", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])