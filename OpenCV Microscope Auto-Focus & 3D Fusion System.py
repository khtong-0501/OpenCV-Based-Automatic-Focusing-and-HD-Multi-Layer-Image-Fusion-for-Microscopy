"""
操作备忘：左键点选去色；B 笔刷；U 撤销；S 保存；3 环绕页；T Tripo；Q 在编辑器内返回首屏（不保存时）。
"""
import importlib.util
import os
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 兜底：若误用系统 Python（如 pythoncore-3.14）启动，则自动切换到项目 .venv 再运行一次。
_BOOT_ENV_KEY = "PROV1_VENV_BOOTSTRAPPED"
if os.environ.get(_BOOT_ENV_KEY) != "1":
    _here = os.path.dirname(os.path.abspath(__file__))
    _venv_py = os.path.join(_here, ".venv", "Scripts", "python.exe")
    _cur_py = os.path.abspath(sys.executable)
    _want_py = os.path.abspath(_venv_py)
    if os.path.isfile(_venv_py) and _cur_py != _want_py:
        print("检测到非项目环境启动，自动切换到：", _venv_py)
        _env = os.environ.copy()
        _env[_BOOT_ENV_KEY] = "1"
        _code = subprocess.call([_venv_py, os.path.abspath(__file__)], cwd=_here, env=_env)
        sys.exit(_code)

import cv2
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
# 所有导出的 PNG、环绕 HTML 默认写入「项目根目录下的 result/」，便于评委找输出。
RESULT_DIR = os.path.join(ROOT_DIR, "result")

# -----------------------------------------------------------------------------
# cutout_to_3d：文件名是 cutout_to_3d.py（无空格、无 cut_out）。用绝对路径加载，避免工作目录不对时 import 失败。
# -----------------------------------------------------------------------------
def _load_cutout_to_3d_module():
    """从 ProV1 目录加载 cutout_to_3d.py；返回 (module | None, 错误说明 | None)。"""
    # 优先与本脚本同目录；若你从 .vscode 等子文件夹里跑了另一份 color_click_remove，则回退到项目 ProV1。
    candidates = [
        os.path.join(SCRIPT_DIR, "cutout_to_3d.py"),
        os.path.join(ROOT_DIR, "ProV1", "cutout_to_3d.py"),
    ]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if path is None:
        return None, (
            "找不到 cutout_to_3d.py。已查找：\n"
            + "\n".join(candidates)
            + "\n\n请确认仓库里有 ProV1\\cutout_to_3d.py，并尽量运行：\n"
            + os.path.join(ROOT_DIR, "ProV1", "color_click_remove.py")
        )
    try:
        spec = importlib.util.spec_from_file_location("cutout_to_3d", path)
        if spec is None or spec.loader is None:
            return None, "无法解析模块：" + path
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, None
    except Exception as e:
        return None, (
            "加载 cutout_to_3d 时出错（可能是未安装 opencv 或 cutout_to_3d.py 有语法错误）：\n\n"
            f"{type(e).__name__}: {e}\n\n文件：{path}"
        )


_cutout_mod, CUTOUT_TO_3D_IMPORT_ERROR = _load_cutout_to_3d_module()
if _cutout_mod is not None:
    generate_revolve_3d = getattr(_cutout_mod, "generate_revolve_3d", None)
    generate_flat_360_html_from_png = getattr(_cutout_mod, "generate_flat_360_html_from_png", None)
    generate_flat_360_html_from_rgba = getattr(_cutout_mod, "generate_flat_360_html_from_rgba", None)
    generate_frame_stack_360_html_from_rgba_frames = getattr(
        _cutout_mod, "generate_frame_stack_360_html_from_rgba_frames", None
    )
    FLAT_360_HTML_NAME = getattr(_cutout_mod, "FLAT_360_HTML_NAME", "view_flat_360.html")
else:
    generate_revolve_3d = None
    generate_flat_360_html_from_png = None
    generate_flat_360_html_from_rgba = None
    generate_frame_stack_360_html_from_rgba_frames = None
    FLAT_360_HTML_NAME = "view_flat_360.html"
    print("cutout_to_3d 不可用：", CUTOUT_TO_3D_IMPORT_ERROR)
try:
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    from tripo_3d import run_tripo_image_to_3d
except Exception:
    run_tripo_image_to_3d = None
INPUT_NAME = "stacked.png"
OUTPUT_NAME = "color_click_clear.png"

# 点选去色：与点击像素 BGR 的允许偏差（越大一次去掉的颜色范围越宽）。
COLOR_TOLERANCE = 35
# 笔刷模式：圆形笔刷半径（像素）。
BRUSH_SIZE = 25
# 撤销栈最大深度，防止内存过大。
MAX_UNDO_STEPS = 30
# 抽帧后筛选：只保留清晰度排名前 (100 - SHARPNESS_DROP_PERCENTILE)% 的帧。
SHARPNESS_DROP_PERCENTILE = 45.0
# 抽帧后去重：与上一张保留帧平均像素差低于该阈值则判为“太像”。
MIN_ACCEPT_DIFF_MEAN = 2.8

# 长耗时步骤里用来刷新 Tk 进度条的回调类型：(提示文案, 0~1 进度)。
ProgressCallback = Optional[Callable[[str, float], None]]


# =============================================================================
# 文件名与磁盘写入（避免非法字符、重名覆盖、Windows 中文路径写 PNG 失败）
# =============================================================================


def sanitize_output_basename(raw: str) -> str:
    """将用户输入整理为安全的文件名（不含扩展名）。"""
    name = raw.strip()
    if not name:
        return ""
    name = os.path.basename(name)
    if "." in name:
        name = os.path.splitext(name)[0]
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = name.strip(" .")
    return name


def resolve_png_save_path(folder: str, basename: str) -> str:
    """
    决定即将保存的 PNG 完整路径：优先「文件夹/基名.png」；若已存在则自动追加 _1、_2…，防止覆盖旧结果。
    """
    os.makedirs(folder, exist_ok=True)
    base = basename or os.path.splitext(OUTPUT_NAME)[0]
    candidate = os.path.join(folder, f"{base}.png")
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        alt = os.path.join(folder, f"{base}_{n}.png")
        if not os.path.exists(alt):
            if n == 1:
                print("提示：同名文件已存在，已自动改用：", alt)
            return alt
        n += 1


def imwrite_png_rgba(path: str, bgra: np.ndarray) -> bool:
    """
    可靠写入 BGRA PNG：先 cv2.imwrite；失败则用 imencode 再 open(...,'wb')，解决 Windows 下 Unicode 路径兼容问题。
    返回是否确认磁盘上已有非空文件。
    """
    ap = os.path.abspath(os.path.normpath(path))
    os.makedirs(os.path.dirname(ap) or ".", exist_ok=True)
    try:
        if cv2.imwrite(ap, bgra):
            if os.path.isfile(ap) and os.path.getsize(ap) > 0:
                return True
    except Exception:
        pass
    try:
        ok, buf = cv2.imencode(".png", bgra)
        if not ok or buf is None:
            return False
        with open(ap, "wb") as f:
            f.write(buf.tobytes())
        return os.path.isfile(ap) and os.path.getsize(ap) > 0
    except OSError:
        return False


# =============================================================================
# 视频元数据与摄像头探测（首屏里显示「影片多长」、实时模式前检查是否有摄像头）
# =============================================================================


def probe_video_duration_sec(video_path: str) -> float | None:
    """用 OpenCV 读取影片总时长（秒）；无可靠元数据时返回 None。"""
    if not video_path or not os.path.isfile(video_path):
        return None
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        cap = cv2.VideoCapture(video_path, backend)
        if not cap.isOpened():
            cap.release()
            continue
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS))
            nframes = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            cap.release()
        if nframes <= 1 or fps <= 1e-6:
            continue
        return float(nframes / fps)
    return None


def format_video_duration_hint(seconds: float | None) -> str:
    """把秒数格式化成首屏右侧显示的「所选影片时长：…」文案。"""
    if seconds is None or seconds <= 0:
        return "所选影片时长：无法读取"
    if seconds >= 60:
        m = int(seconds // 60)
        s = seconds - m * 60
        return f"所选影片时长：{m} 分 {s:.1f} 秒（共 {seconds:.1f} 秒）"
    return f"所选影片时长：{seconds:.2f} 秒"


def detect_available_camera(max_index: int = 8) -> int | None:
    """
    探测可用摄像头索引。返回首个可用索引，否则返回 None。
    """
    for cam_idx in range(max_index + 1):
        # Windows 下优先尝试 DirectShow，更贴近 USB 摄像头检测。
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap.release()
                continue
            ok, frame = cap.read()
            cap.release()
            if ok and frame is not None:
                return cam_idx
    return None


def choose_input_config_ui() -> Dict[str, object] | None:
    """
    【程序第一个界面 · 全屏 Tk】

    作用：收集输入源与参数，并在后台完成「抽帧 → 融合 → 细化」，得到一张大图 cfg['full']。

    两种模式：
    - 实时 CAM：弹出 OpenCV 窗口，空格开始录指定秒数，按间隔采样。
    - 已录影片：按间隔从文件中抽帧（带进度条）。

    点击「开始」后同一窗口切换到加载页，避免用户以为卡死；完成后销毁窗口并返回 dict（含 full 与 output_basename 等）。
    按 Q（焦点不在输入框时）或「取消」等价于放弃，返回 None。
    """
    outcome: Dict[str, object] = {
        "done": False,
        "cancelled": False,
        "full": None,
        "sampled_frames": None,
        "cfg": None,
    }

    root = tk.Tk()
    root.title("ProV1 输入设置")
    root.attributes("-fullscreen", True)

    def exit_fullscreen(event=None) -> None:
        del event
        root.attributes("-fullscreen", False)
        root.state("zoomed")

    root.bind("<Escape>", exit_fullscreen)

    mode_var = tk.StringVar(value="live")
    video_var = tk.StringVar(value="")
    duration_var = tk.StringVar(value="6")
    interval_var = tk.StringVar(value="0.3")
    output_name_var = tk.StringVar(value=os.path.splitext(OUTPUT_NAME)[0])
    video_duration_hint_var = tk.StringVar(value="所选影片时长：--")
    status_var = tk.StringVar(value="")

    # --- 加载中界面：与「主表单」互斥显示，用于影片抽帧/融合时更新进度条 ---
    loading_frame = tk.Frame(root)
    loading_inner = tk.Frame(loading_frame)
    loading_inner.pack(padx=48, pady=48)
    loading_title = tk.Label(loading_inner, text="加载中…", font=("Microsoft YaHei", 22, "bold"), fg="#1565c0")
    loading_title.pack(pady=(0, 12))
    loading_msg_var = tk.StringVar(value="请稍候…")
    tk.Label(loading_inner, textvariable=loading_msg_var, font=("Microsoft YaHei", 14), wraplength=720, justify="center").pack(
        pady=(0, 16)
    )
    loading_progress_var = tk.DoubleVar(value=0.0)
    loading_bar = ttk.Progressbar(
        loading_inner,
        length=560,
        maximum=1000.0,
        mode="determinate",
        variable=loading_progress_var,
    )
    loading_bar.pack(pady=(0, 8))
    loading_frame.place_forget()

    def set_loading_ui(message: str, fraction: float) -> None:
        loading_msg_var.set(message)
        loading_progress_var.set(max(0.0, min(1000.0, fraction * 1000.0)))
        root.update_idletasks()

    def show_loading_ui(initial_message: str = "加载中…") -> None:
        content.place_forget()
        loading_title.config(text=initial_message)
        set_loading_ui("正在准备…", 0.02)
        loading_frame.place(relx=0.5, rely=0.48, anchor="center")
        root.update_idletasks()

    def hide_loading_show_form() -> None:
        loading_frame.place_forget()
        content.place(relx=0.5, rely=0.48, anchor="center")
        root.update_idletasks()

    def schedule_loading(message: str, fraction: float) -> None:
        def _apply() -> None:
            set_loading_ui(message, fraction)

        root.after(0, _apply)

    def finish_success() -> None:
        uninstall_global_q_key()
        outcome["done"] = True
        root.destroy()

    def finish_error_ui(message: str, restore_form: bool = True) -> None:
        def _show() -> None:
            if restore_form:
                hide_loading_show_form()
            messagebox.showerror("处理失败", message)

        root.after(0, _show)

    # --- 主表单：模式单选、影片路径、采样参数、输出文件名 ---
    content = tk.Frame(root)
    content.place(relx=0.5, rely=0.48, anchor="center")

    tk.Label(content, text="输入模式（仅两个选项）", font=("Microsoft YaHei", 20, "bold")).pack(anchor="w", pady=(0, 10))
    tk.Radiobutton(content, text="实时 CAM（按空格开始录制）", variable=mode_var, value="live", font=("Microsoft YaHei", 14)).pack(anchor="w", padx=(8, 0))
    tk.Radiobutton(content, text="选择已录影片", variable=mode_var, value="file", font=("Microsoft YaHei", 14)).pack(anchor="w", padx=(8, 0), pady=(6, 12))

    video_row = tk.Frame(content)
    video_row.pack(fill="x", pady=(0, 8))
    tk.Label(video_row, text="影片文件：", width=12, anchor="w", font=("Microsoft YaHei", 14)).pack(side="left")
    tk.Entry(video_row, textvariable=video_var, font=("Consolas", 13)).pack(side="left", fill="x", expand=True)

    def on_choose_video() -> None:
        p = filedialog.askopenfilename(
            title="选择影片",
            filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv *.wmv *.m4v"), ("All Files", "*.*")],
        )
        if p:
            video_var.set(p)
            mode_var.set("file")

    tk.Button(video_row, text="浏览", width=12, font=("Microsoft YaHei", 13), command=on_choose_video).pack(side="left", padx=(12, 0))

    def refresh_video_duration_hint(*_args: object) -> None:
        if mode_var.get() != "file":
            video_duration_hint_var.set("所选影片时长：--（实时 CAM）")
            return
        path = video_var.get().strip()
        if not path or not os.path.isfile(path):
            video_duration_hint_var.set("所选影片时长：请先选择影片")
            return
        dur = probe_video_duration_sec(path)
        video_duration_hint_var.set(format_video_duration_hint(dur))

    mode_var.trace_add("write", refresh_video_duration_hint)
    video_var.trace_add("write", refresh_video_duration_hint)
    refresh_video_duration_hint()

    params = tk.LabelFrame(content, text="参数", font=("Microsoft YaHei", 14, "bold"), padx=16, pady=14)
    params.pack(fill="x", pady=(4, 14))
    r1 = tk.Frame(params)
    r1.pack(fill="x", pady=4)
    r1_left = tk.Frame(r1)
    r1_left.pack(side="left")
    tk.Label(r1_left, text="录制秒数（实时 CAM）", width=22, anchor="w", font=("Microsoft YaHei", 14)).pack(side="left")
    tk.Entry(r1_left, textvariable=duration_var, width=14, font=("Consolas", 13)).pack(side="left", padx=(0, 8))
    tk.Label(
        r1,
        textvariable=video_duration_hint_var,
        anchor="e",
        font=("Microsoft YaHei", 13),
        fg="#1565c0",
    ).pack(side="right", padx=(8, 0))
    r2 = tk.Frame(params)
    r2.pack(fill="x", pady=4)
    tk.Label(r2, text="每隔多少秒截 1 张", width=22, anchor="w", font=("Microsoft YaHei", 14)).pack(side="left")
    tk.Entry(r2, textvariable=interval_var, width=14, font=("Consolas", 13)).pack(side="left")
    r3 = tk.Frame(params)
    r3.pack(fill="x", pady=4)
    tk.Label(r3, text="生成图文件名（无扩展名）", width=22, anchor="w", font=("Microsoft YaHei", 14)).pack(side="left")
    tk.Entry(r3, textvariable=output_name_var, width=32, font=("Consolas", 13)).pack(side="left")

    tip = "流程：采样帧 -> 融合细化 -> 框选 -> 点选去背景（后续操作与原本一致）"
    tk.Label(content, text=tip, fg="#555", font=("Microsoft YaHei", 12), wraplength=920, justify="left").pack(anchor="w", pady=(0, 8))
    tk.Label(content, textvariable=status_var, fg="#d00000", font=("Microsoft YaHei", 13, "bold"), wraplength=920, justify="left").pack(anchor="w", pady=(0, 10))
    tk.Label(
        content,
        text="提示：按 Esc 可退出全屏；在输入框外按 Q 可退出整个程序（在文件名框内输入 q 不会退出）",
        fg="#888",
        font=("Microsoft YaHei", 11),
    ).pack(anchor="w", pady=(0, 6))

    def uninstall_global_q_key() -> None:
        try:
            root.unbind_all("<KeyPress>")
        except Exception:
            pass

    # --- 全局快捷键：在任意非输入框控件上按 Q 直接退出整个程序（避免误触文件名里的字母 q）---
    def install_global_q_exit_program() -> None:
        def on_global_key(event: tk.Event) -> None:
            if event.keysym.lower() != "q":
                return
            w = event.widget
            try:
                if w.winfo_class() in ("Entry", "TEntry"):
                    return
            except tk.TclError:
                return
            on_cancel()

        root.bind_all("<KeyPress>", on_global_key)

    # --- 影片模式：后台线程抽帧 → 融合 → 细化，通过 schedule_loading 回传进度 ---
    def run_file_pipeline(pending_cfg: Dict[str, object]) -> None:
        def worker() -> None:
            try:

                def prog(msg: str, f: float) -> None:
                    schedule_loading(msg, f)

                frames = sample_frames_from_video(
                    str(pending_cfg["video_path"]),
                    float(pending_cfg["sample_interval_sec"]),
                    progress_cb=prog,
                )
                if not frames:
                    outcome["cfg"] = None
                    outcome["full"] = None
                    outcome["sampled_frames"] = None
                    finish_error_ui("没有从影片采样到有效帧，请检查影片或抽帧间隔。", True)
                    return
                schedule_loading(f"已采样 {len(frames)} 帧，正在融合…", 0.68)
                full = fuse_sharp_regions(frames, progress_cb=lambda m, f: schedule_loading(m, f))
                schedule_loading("正在细化处理…", 0.92)
                full = refine_fused_image(full)
                schedule_loading("处理完成，即将进入框选…", 1.0)
                outcome["cfg"] = pending_cfg
                outcome["full"] = full
                outcome["sampled_frames"] = frames
                root.after(0, finish_success)
            except Exception as e:
                outcome["cfg"] = None
                outcome["full"] = None
                outcome["sampled_frames"] = None
                finish_error_ui(f"处理出错：{e}", True)

        threading.Thread(target=worker, daemon=True).start()

    # --- 实时模式：主线程先录 OpenCV 窗口（用户空格开始），得到帧列表后再开后台线程做融合/细化 ---
    def run_live_then_fuse(pending_cfg: Dict[str, object]) -> None:
        loading_title.config(text="加载中…")
        set_loading_ui("请在弹出窗口中操作：空格开始录制，按 Q 放弃", 0.08)
        root.update()
        try:
            root.lower()
        except Exception:
            pass
        frames = sample_frames_from_live_cam(
            float(pending_cfg["duration_sec"]),
            float(pending_cfg["sample_interval_sec"]),
            cam_index=int(pending_cfg["cam_index"]),
        )
        if not frames:
            hide_loading_show_form()
            messagebox.showwarning("提示", "未录制到有效画面。")
            return
        set_loading_ui(f"已采样 {len(frames)} 帧，正在融合…", 0.68)

        def worker() -> None:
            try:

                def prog(msg: str, f: float) -> None:
                    schedule_loading(msg, f)

                full = fuse_sharp_regions(frames, progress_cb=lambda m, f: prog(m, f))
                prog("正在细化处理…", 0.92)
                full = refine_fused_image(full)
                prog("处理完成，即将进入框选…", 1.0)
                outcome["cfg"] = pending_cfg
                outcome["full"] = full
                outcome["sampled_frames"] = frames
                root.after(0, finish_success)
            except Exception as e:
                outcome["cfg"] = None
                outcome["full"] = None
                outcome["sampled_frames"] = None
                finish_error_ui(f"处理出错：{e}", True)

        threading.Thread(target=worker, daemon=True).start()

    def on_start() -> None:
        status_var.set("")
        mode = mode_var.get()
        try:
            duration_sec = float(duration_var.get().strip())
            sample_interval_sec = float(interval_var.get().strip())
            if duration_sec <= 0 or sample_interval_sec <= 0:
                raise ValueError
        except Exception:
            messagebox.showerror("参数错误", "录制秒数与截帧间隔必须是大于 0 的数字。")
            return

        video_path = video_var.get().strip()
        if mode == "file" and not os.path.isfile(video_path):
            messagebox.showerror("参数错误", "请选择有效的影片文件。")
            return
        safe_base = sanitize_output_basename(output_name_var.get())
        if not safe_base:
            messagebox.showerror("参数错误", "请填写有效的生成图文件名。")
            return
        cam_index = 0
        if mode == "live":
            found = detect_available_camera(max_index=8)
            if found is None:
                status_var.set("没有可用设备")
                return
            cam_index = int(found)

        pending_cfg: Dict[str, object] = {
            "mode": mode,
            "video_path": video_path,
            "duration_sec": duration_sec,
            "sample_interval_sec": sample_interval_sec,
            "cam_index": cam_index,
            "output_basename": safe_base,
        }

        show_loading_ui("加载中…")
        if mode == "file":
            run_file_pipeline(pending_cfg)
        else:
            root.after(80, lambda: run_live_then_fuse(pending_cfg))

    def on_cancel() -> None:
        uninstall_global_q_key()
        outcome["cancelled"] = True
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_cancel)

    btn = tk.Frame(content)
    btn.pack(fill="x", pady=(8, 0))
    tk.Button(btn, text="开始", width=16, font=("Microsoft YaHei", 14), command=on_start).pack(side="left")
    tk.Button(btn, text="取消 / 退出程序", width=18, font=("Microsoft YaHei", 14), command=on_cancel).pack(side="left", padx=(16, 0))

    install_global_q_exit_program()
    root.mainloop()

    if outcome.get("cancelled") or not outcome.get("done"):
        return None
    full_img = outcome.get("full")
    cfg_out = outcome.get("cfg")
    if full_img is None or cfg_out is None:
        return None
    return {**cfg_out, "full": full_img, "sampled_frames": outcome.get("sampled_frames")}


# =============================================================================
# 从视频文件或摄像头按时间间隔采样多帧（后续融合的原料）
# =============================================================================


def sample_frames_from_video(
    video_path: str,
    sample_interval_sec: float,
    max_samples: int = 300,
    progress_cb: ProgressCallback = None,
) -> List[np.ndarray]:
    """
    按「每隔 sample_interval_sec 秒取一帧」从视频中均匀抽样（由 fps 换算成帧步长）。
    progress_cb 用于更新首屏进度条；max_samples 防止超长视频占满内存。
    """
    cap = None
    for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY):
        cap = cv2.VideoCapture(video_path, backend)
        if cap.isOpened():
            break
        cap.release()
        cap = None
    if cap is None or not cap.isOpened():
        print("无法打开影片：", video_path)
        return []

    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if fps <= 1e-6:
        fps = 30.0
    sample_step = max(1, int(round(sample_interval_sec * fps)))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    candidates: List[np.ndarray] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if idx % sample_step == 0:
            candidates.append(frame.copy())
            if len(candidates) >= max_samples:
                break
        idx += 1
        if progress_cb and total_frames > 0 and idx % 8 == 0:
            frac = 0.05 + 0.55 * min(1.0, idx / float(total_frames))
            progress_cb("正在读取影片并抽帧…", min(0.62, frac))
        elif progress_cb and total_frames <= 0 and idx % 45 == 0:
            progress_cb("正在读取影片并抽帧…", 0.2)
    cap.release()
    frames = filter_sampled_frames(
        candidates,
        sharpness_drop_percentile=SHARPNESS_DROP_PERCENTILE,
        min_accept_diff_mean=MIN_ACCEPT_DIFF_MEAN,
    )
    if progress_cb:
        progress_cb(f"抽帧筛选完成：候选 {len(candidates)} -> 保留 {len(frames)} 张", 0.65)
    return frames


def sample_frames_from_live_cam(duration_sec: float, sample_interval_sec: float, cam_index: int = 0, max_samples: int = 300) -> List[np.ndarray]:
    """
    打开指定索引摄像头，循环读帧直到用户按空格开始录制；录制满 duration_sec 或达 max_samples 张停止。
    按间隔抽样；按 Q/Esc 放弃录制则返回空列表。
    """
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(cam_index, cv2.CAP_MSMF)
    if not cap.isOpened():
        print("无法连接 CAM，请检查 Type-C/USB 连接。")
        return []

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 1e-6:
        fps = 30.0
    sample_step = max(1, int(round(sample_interval_sec * fps)))

    win = "实时 CAM（空格开始录制，Q 退出）"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)

    recording = False
    start_tick = 0.0
    frame_idx = 0
    candidates: List[np.ndarray] = []

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        show = frame.copy()
        if not recording:
            cv2.putText(show, "Press SPACE to start recording", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
        else:
            elapsed = (cv2.getTickCount() - start_tick) / cv2.getTickFrequency()
            left = max(0.0, duration_sec - elapsed)
            cv2.putText(show, f"REC {elapsed:.1f}s / {duration_sec:.1f}s", (12, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 80, 255), 2, cv2.LINE_AA)
            cv2.putText(show, f"Left {left:.1f}s   Samples {len(candidates)}", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 120), 2, cv2.LINE_AA)
            if frame_idx % sample_step == 0:
                candidates.append(frame.copy())
                if len(candidates) >= max_samples:
                    break
            frame_idx += 1
            if elapsed >= duration_sec:
                break

        cv2.imshow(win, show)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            candidates = []
            break
        if not recording and key == 32:
            recording = True
            start_tick = cv2.getTickCount()
            frame_idx = 0

    cap.release()
    try:
        cv2.destroyWindow(win)
    except Exception:
        pass
    return filter_sampled_frames(
        candidates,
        sharpness_drop_percentile=SHARPNESS_DROP_PERCENTILE,
        min_accept_diff_mean=MIN_ACCEPT_DIFF_MEAN,
    )


def frame_sharpness_score(img_bgr: np.ndarray) -> float:
    """以拉普拉斯方差衡量整帧清晰度（越大越清晰）。"""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_32F).var())


def frame_diff_mean(a_bgr: np.ndarray, b_bgr: np.ndarray) -> float:
    """两帧平均像素差（0~255），用于去重。"""
    if a_bgr.shape[:2] != b_bgr.shape[:2]:
        b_bgr = cv2.resize(b_bgr, (a_bgr.shape[1], a_bgr.shape[0]), interpolation=cv2.INTER_AREA)
    diff = cv2.absdiff(a_bgr, b_bgr)
    return float(diff.mean())


def filter_sampled_frames(
    candidates: List[np.ndarray],
    sharpness_drop_percentile: float,
    min_accept_diff_mean: float,
) -> List[np.ndarray]:
    """
    抽帧后质量筛选：
    1) 丢掉清晰度偏低的帧；
    2) 再按时间顺序去掉与上一保留帧几乎相同的帧。
    """
    if not candidates:
        return []
    if len(candidates) <= 2:
        return [f.copy() for f in candidates]

    scored: List[Tuple[np.ndarray, float]] = [(f, frame_sharpness_score(f)) for f in candidates]
    scores = np.array([s for _, s in scored], dtype=np.float32)
    pct = float(max(0.0, min(95.0, sharpness_drop_percentile)))
    sharp_th = float(np.percentile(scores, pct))

    sharp_kept = [f for f, s in scored if s >= sharp_th]
    if len(sharp_kept) < 2:
        # 至少保留几张最清晰的，防止阈值过严。
        top_idx = np.argsort(scores)[::-1][: min(4, len(candidates))]
        sharp_kept = [candidates[int(i)] for i in sorted(top_idx)]

    result: List[np.ndarray] = [sharp_kept[0].copy()]
    for f in sharp_kept[1:]:
        d = frame_diff_mean(result[-1], f)
        if d >= min_accept_diff_mean:
            result.append(f.copy())

    if len(result) < 2 and len(sharp_kept) >= 2:
        result.append(sharp_kept[-1].copy())
    return result


# =============================================================================
# 多帧对齐 + 「取最清晰像素」融合 + 轻度锐化（核心画质步骤）
# =============================================================================


def refine_fused_image(img_bgr: np.ndarray) -> np.ndarray:
    """
    融合后细化：轻度去噪 + 锐化。
    """
    denoise = cv2.bilateralFilter(img_bgr, d=7, sigmaColor=45, sigmaSpace=45)
    blur = cv2.GaussianBlur(denoise, (0, 0), 1.0)
    sharpen = cv2.addWeighted(denoise, 1.25, blur, -0.25, 0)
    return sharpen


def align_to_reference(ref_bgr: np.ndarray, img_bgr: np.ndarray) -> np.ndarray:
    """用 ECC 将当前帧与参考帧做刚性对齐，减轻手持/抖动导致的重影（失败则返回原图）。"""
    ref_gray = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    img_gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    warp = np.eye(2, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-6)
    try:
        cv2.findTransformECC(ref_gray, img_gray, warp, cv2.MOTION_EUCLIDEAN, criteria)
        return cv2.warpAffine(img_bgr, warp, (ref_bgr.shape[1], ref_bgr.shape[0]), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    except Exception:
        return img_bgr


def fuse_sharp_regions(frames: List[np.ndarray], progress_cb: ProgressCallback = None) -> np.ndarray:
    """
    多帧融合：每帧对齐到第一帧后，对每个像素在 Laplacian（清晰度）响应最大的那一帧取色。
    效果类似「多拍几张选最清楚的一块拼成一张」，适合答辩时解释「为什么要采样多帧」。
    """
    if len(frames) == 1:
        if progress_cb:
            progress_cb("融合完成（单帧）", 0.88)
        return frames[0].copy()

    ref = frames[0]
    h, w = ref.shape[:2]
    aligned: List[np.ndarray] = [ref]
    n_extra = len(frames) - 1
    for j, img in enumerate(frames[1:], start=1):
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        aligned.append(align_to_reference(ref, img))
        if progress_cb and n_extra > 0:
            progress_cb("正在对齐多帧…", 0.65 + 0.12 * (j / n_extra))

    sharp_maps = []
    for i, img in enumerate(aligned):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
        sharp_maps.append(cv2.GaussianBlur(np.abs(lap), (5, 5), 0))
        if progress_cb and len(aligned) > 1 and i % max(1, len(aligned) // 5) == 0:
            progress_cb("正在计算清晰度并融合…", 0.77 + 0.1 * (i / len(aligned)))

    score = np.stack(sharp_maps, axis=0)
    best = np.argmax(score, axis=0)
    stack = np.stack(aligned, axis=0)
    rr = np.arange(h)[:, None]
    cc = np.arange(w)[None, :]
    fused = stack[best, rr, cc]
    if progress_cb:
        progress_cb("融合完成", 0.88)
    return fused.astype(np.uint8)


def load_fused_frame_from_video(video_path: str, max_samples: int = 80) -> np.ndarray | None:
    """
    备用/简化入口：均匀_stride 抽帧后调用 fuse_sharp_regions。
    主流程当前用 sample_frames_from_video + fuse，本函数可留给旧脚本或其它模块调用。
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("无法打开影片：", video_path)
        return None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        frame_count = max_samples
    stride = max(1, frame_count // max_samples)

    frames: List[np.ndarray] = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if idx % stride == 0:
            frames.append(frame.copy())
            if len(frames) >= max_samples:
                break
        idx += 1
    cap.release()

    if not frames:
        print("影片读取失败：", video_path)
        return None
    if len(frames) == 1:
        return frames[0]

    print(f"已从影片抽取 {len(frames)} 帧，正在融合清晰区域...")
    return fuse_sharp_regions(frames)


# =============================================================================
# 交互抠图：按颜色生成 mask、合并多种要去除的颜色、框选与笔刷
# =============================================================================


def build_mask_by_color(img_bgr: np.ndarray, target_bgr: Tuple[int, int, int], tolerance: int) -> np.ndarray:
    """在 BGR 立方体内做「与点击颜色距离不超过 tolerance」的像素掩膜（OpenCV inRange）。"""
    b, g, r = target_bgr
    low = np.array(
        [max(0, b - tolerance), max(0, g - tolerance), max(0, r - tolerance)],
        dtype=np.uint8,
    )
    high = np.array(
        [min(255, b + tolerance), min(255, g + tolerance), min(255, r + tolerance)],
        dtype=np.uint8,
    )
    mask = cv2.inRange(img_bgr, low, high)
    return mask.astype(bool)


def build_remove_mask(
    img_bgr: np.ndarray, colors_to_remove: List[Tuple[int, int, int]], tolerance: int
) -> np.ndarray:
    """将用户点选过的多种颜色对应的 mask 做逻辑或，得到「应变为透明」的区域。"""
    if not colors_to_remove:
        return np.zeros(img_bgr.shape[:2], dtype=bool)
    out = np.zeros(img_bgr.shape[:2], dtype=bool)
    for bgr in colors_to_remove:
        out |= build_mask_by_color(img_bgr, bgr, tolerance)
    return out


def select_crop(img: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int, int, int]] | None:
    """
    OpenCV 标准 ROI 交互：用户拖拽矩形后 Enter 确认。
    返回 (裁剪后子图, ROI[x, y, w, h])；Esc 或空框则 None，主流程会回到首屏。
    """
    try:
        win_crop = "1. 框选要处理的区域 - 拖拽框选后按 Enter 确认，按 Esc 取消"
        cv2.namedWindow(win_crop, cv2.WINDOW_NORMAL)
        roi = cv2.selectROI(win_crop, img, fromCenter=False, showCrosshair=True)
        cv2.destroyWindow(win_crop)
    except Exception as e:
        print("框选时出错:", e)
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        return None
    if roi is None or len(roi) < 4 or roi[2] <= 0 or roi[3] <= 0:
        return None
    x, y, rw, rh = int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])
    return img[y : y + rh, x : x + rw].copy(), (x, y, rw, rh)


def run_loading_task_ui(
    title: str, worker_fn: Callable[[Callable[[str, float], None]], Any]
) -> Tuple[bool, Any]:
    """
    通用加载页：显示进度条与阶段文案，后台线程执行任务，完成后自动关闭。
    返回 (ok, result_or_none)。
    """
    state: Dict[str, Any] = {"ok": False, "result": None, "error": ""}
    root = tk.Tk()
    root.title(title)
    root.attributes("-fullscreen", True)

    def exit_full(event=None) -> None:
        del event
        root.attributes("-fullscreen", False)
        root.state("zoomed")

    root.bind("<Escape>", exit_full)

    box = tk.Frame(root)
    box.place(relx=0.5, rely=0.48, anchor="center")
    tk.Label(box, text=title, font=("Microsoft YaHei", 22, "bold"), fg="#1565c0").pack(pady=(0, 12))
    msg_var = tk.StringVar(value="正在准备…")
    tk.Label(box, textvariable=msg_var, font=("Microsoft YaHei", 14), wraplength=860, justify="center").pack(pady=(0, 16))
    prog_var = tk.DoubleVar(value=0.0)
    ttk.Progressbar(box, length=560, maximum=1000.0, mode="determinate", variable=prog_var).pack(pady=(0, 10))
    tk.Label(box, text="提示：Esc 退出全屏（任务会继续执行）", fg="#888", font=("Microsoft YaHei", 11)).pack()

    def set_prog(msg: str, frac: float) -> None:
        msg_var.set(msg)
        prog_var.set(max(0.0, min(1000.0, frac * 1000.0)))
        root.update_idletasks()

    def progress_cb(msg: str, frac: float) -> None:
        root.after(0, lambda: set_prog(msg, frac))

    def worker() -> None:
        try:
            res = worker_fn(progress_cb)
            state["ok"] = True
            state["result"] = res
        except Exception as e:
            state["ok"] = False
            state["error"] = str(e)
        finally:
            root.after(0, root.destroy)

    threading.Thread(target=worker, daemon=True).start()
    root.mainloop()
    if not state["ok"] and state["error"]:
        messagebox.showerror("处理失败", state["error"])
    return bool(state["ok"]), state["result"]


def build_stack_planes_from_sampled_frames(
    sampled_frames: List[np.ndarray],
    roi: Tuple[int, int, int, int],
    progress_cb: ProgressCallback = None,
) -> List[np.ndarray]:
    """
    把抽样帧对齐到参考帧后按同一 ROI 裁剪，得到「一帧一平面」的素材列表（时间顺序不变）。
    """
    if progress_cb:
        progress_cb("正在整理抽帧数据…", 0.03)
    if not sampled_frames:
        if progress_cb:
            progress_cb("没有抽帧数据，跳过多帧平面准备。", 1.0)
        return []
    valid = [f for f in sampled_frames if isinstance(f, np.ndarray) and f.ndim >= 2 and f.size > 0]
    if not valid:
        if progress_cb:
            progress_cb("抽帧数据为空，跳过多帧平面准备。", 1.0)
        return []

    ref = valid[0]
    rh, rw = ref.shape[:2]
    x, y, cw, ch = roi
    x0 = max(0, min(rw - 1, int(x)))
    y0 = max(0, min(rh - 1, int(y)))
    x1 = max(x0 + 1, min(rw, x0 + int(cw)))
    y1 = max(y0 + 1, min(rh, y0 + int(ch)))

    if progress_cb:
        progress_cb(f"正在按 ROI 对齐并裁剪抽帧（共 {len(valid)} 帧）…", 0.12)

    out: List[np.ndarray] = []
    total = len(valid)
    stride = max(1, total // 18)
    for i, frame in enumerate(valid):
        cur = frame
        if cur.shape[:2] != (rh, rw):
            cur = cv2.resize(cur, (rw, rh), interpolation=cv2.INTER_AREA)
        if i > 0:
            cur = align_to_reference(ref, cur)
        crop = cur[y0:y1, x0:x1]
        if crop.size > 0:
            out.append(crop.copy())
        if progress_cb and (i % stride == 0 or i == total - 1):
            frac = 0.12 + 0.85 * ((i + 1) / max(1, total))
            progress_cb(f"正在准备多帧平面素材… {i + 1}/{total}", frac)
    if progress_cb:
        progress_cb(f"多帧平面素材已就绪（可用 {len(out)} 帧）", 1.0)
    return out


def apply_brush_circle(
    rgba: np.ndarray, orig_bgr: np.ndarray, cx: int, cy: int, radius: int
) -> None:
    """笔刷模式：在圆内把 RGBA 恢复为原始 BGR 且不透明（用于涂回误删区域）。"""
    h, w = rgba.shape[:2]
    y0 = max(0, cy - radius)
    y1 = min(h, cy + radius + 1)
    x0 = max(0, cx - radius)
    x1 = min(w, cx + radius + 1)
    for y in range(y0, y1):
        for x in range(x0, x1):
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                rgba[y, x, :3] = orig_bgr[y, x]
                rgba[y, x, 3] = 255


# =============================================================================
# 保存后与操作系统交互：默认程序打开图片/HTML；messagebox 绑定父窗口避免跑到后台
# =============================================================================


def open_file_default(path: str, parent: Optional[tk.Misc] = None) -> bool:
    """用系统默认程序打开文件（Windows 下修复 os.startfile 偶发无效）。"""
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.isfile(p):
        messagebox.showerror("找不到文件", p, parent=parent)
        return False
    ext = os.path.splitext(p)[1].lower()
    try:
        if ext in (".html", ".htm"):
            webbrowser.open(Path(p).as_uri())
            return True
        if sys.platform == "win32":
            os.startfile(p)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            subprocess.run(["open", p], check=False)
            return True
        subprocess.run(["xdg-open", p], check=False)
        return True
    except Exception:
        try:
            if sys.platform == "win32":
                subprocess.run(["cmd", "/c", "start", "", p], shell=False, check=False)
                return True
        except Exception:
            pass
    messagebox.showerror("无法打开文件", p, parent=parent)
    return False


def post_save_choice_ui(png_path: str, frame_stack_bgr: Optional[List[np.ndarray]] = None) -> str:
    """
    【程序第二个界面 · 全屏 Tk】

    在 result/ 已写出 PNG 后出现：可选
    - 观看平面图：系统默认看图软件；
    - 360° 环绕：优先按「抽样帧一帧一平面」顺序后排；无帧素材时退回单图环绕；
    - 返回选片：回到 choose_input_config_ui 做下一轮；
    - 退出：结束进程。

    返回值 "restart" | "quit" 供 main() 决定是否继续 while 循环。
    """
    result_holder: Dict[str, str] = {"action": "restart"}

    root = tk.Tk()
    root.title("保存完成")
    root.attributes("-fullscreen", True)

    def exit_full(event=None) -> None:
        del event
        root.attributes("-fullscreen", False)
        root.state("zoomed")

    root.bind("<Escape>", exit_full)

    content = tk.Frame(root)
    content.place(relx=0.5, rely=0.48, anchor="center")

    tk.Label(content, text="已保存到 result 目录", font=("Microsoft YaHei", 20, "bold")).pack(pady=(0, 8))
    tk.Label(content, text=png_path, font=("Consolas", 11), fg="#333", wraplength=900, justify="center").pack(pady=(0, 16))

    status_var = tk.StringVar(value="请选择：观看平面图 / 360° 环绕观看（多帧平面） / 返回选片 / 退出")
    tk.Label(content, textvariable=status_var, font=("Microsoft YaHei", 13), fg="#1565c0", wraplength=900).pack(pady=(0, 20))

    html_360_path = os.path.join(RESULT_DIR, FLAT_360_HTML_NAME)

    def on_open_2d() -> None:
        p = os.path.abspath(os.path.normpath(png_path))
        for _ in range(80):
            if os.path.isfile(p) and os.path.getsize(p) > 0:
                break
            time.sleep(0.025)
        if open_file_default(p, parent=root):
            status_var.set("已打开平面图（可再点另一项或返回选片）")

    def on_open_3d() -> None:
        if generate_flat_360_html_from_png is None:
            messagebox.showerror(
                "无法使用 cutout_to_3d",
                (CUTOUT_TO_3D_IMPORT_ERROR or "未加载 cutout_to_3d（未知原因）。"),
                parent=root,
            )
            return

        def worker() -> None:
            try:
                root.after(0, lambda: status_var.set("正在生成立体环绕页，请稍候…"))
                pp = os.path.abspath(os.path.normpath(png_path))
                if not os.path.isfile(pp):
                    root.after(0, lambda: messagebox.showerror("错误", "找不到平面图文件：\n" + pp, parent=root))
                    return
                ok = False
                if frame_stack_bgr and generate_frame_stack_360_html_from_rgba_frames is not None:
                    rgba_saved = cv2.imread(pp, cv2.IMREAD_UNCHANGED)
                    if rgba_saved is not None and rgba_saved.ndim == 3 and rgba_saved.shape[2] >= 4:
                        alpha = rgba_saved[:, :, 3]
                        stack_rgba: List[np.ndarray] = []
                        for fbgr in frame_stack_bgr:
                            if fbgr is None or fbgr.size == 0:
                                continue
                            cur = fbgr
                            if cur.ndim == 2:
                                cur = cv2.cvtColor(cur, cv2.COLOR_GRAY2BGR)
                            h, w = cur.shape[:2]
                            a = alpha
                            if alpha.shape[:2] != (h, w):
                                a = cv2.resize(alpha, (w, h), interpolation=cv2.INTER_NEAREST)
                            rgba = np.zeros((h, w, 4), dtype=np.uint8)
                            rgba[:, :, :3] = cur[:, :, :3]
                            rgba[:, :, 3] = a
                            rgba[rgba[:, :, 3] == 0, :3] = 0
                            stack_rgba.append(rgba)
                        if stack_rgba:
                            ok = bool(generate_frame_stack_360_html_from_rgba_frames(stack_rgba, RESULT_DIR, gap_px=5))

                if not ok:
                    ok = generate_flat_360_html_from_png(pp, RESULT_DIR)
                if not ok:
                    root.after(0, lambda: messagebox.showerror("错误", "环绕页生成失败。", parent=root))
                    return
                root.after(0, lambda: status_var.set("正在打开浏览器…"))
                root.after(100, lambda: open_file_default(html_360_path, parent=root))
                root.after(200, lambda: status_var.set("已尝试打开环绕页（可再打开平面图或返回选片）"))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("环绕页出错", str(e), parent=root))
            finally:
                root.after(0, lambda: btn_3d.config(state="normal"))

        btn_3d.config(state="disabled")
        threading.Thread(target=worker, daemon=True).start()

    def uninstall_global_q_key_post() -> None:
        try:
            root.unbind_all("<KeyPress>")
        except Exception:
            pass

    def on_quit() -> None:
        uninstall_global_q_key_post()
        result_holder["action"] = "quit"
        root.destroy()

    def on_restart() -> None:
        uninstall_global_q_key_post()
        result_holder["action"] = "restart"
        root.destroy()

    def install_global_q_quit_program() -> None:
        def on_global_key(event: tk.Event) -> None:
            if event.keysym.lower() != "q":
                return
            on_quit()

        root.bind_all("<KeyPress>", on_global_key)

    btn_row1 = tk.Frame(content)
    btn_row1.pack(pady=8)
    tk.Button(btn_row1, text="观看平面图", width=18, font=("Microsoft YaHei", 14), command=on_open_2d).pack(side="left", padx=8)
    btn_3d = tk.Button(btn_row1, text="360° 环绕观看", width=18, font=("Microsoft YaHei", 14), command=on_open_3d)
    btn_3d.pack(side="left", padx=8)

    btn_row2 = tk.Frame(content)
    btn_row2.pack(pady=20)
    tk.Button(btn_row2, text="返回选片界面（下一个影片）", width=26, font=("Microsoft YaHei", 14), command=on_restart).pack(
        side="left", padx=8
    )
    tk.Button(btn_row2, text="退出程序", width=18, font=("Microsoft YaHei", 14), command=on_quit).pack(side="left", padx=8)

    tk.Label(
        content,
        text="提示：Esc 退出全屏；按 Q 退出整个程序；可多次切换平面图与 360° 环绕页",
        fg="#888",
        font=("Microsoft YaHei", 11),
    ).pack(pady=(16, 0))

    root.protocol("WM_DELETE_WINDOW", on_quit)
    install_global_q_quit_program()
    root.mainloop()
    return result_holder["action"]


# =============================================================================
# OpenCV 抠图编辑器：点选去色 / 笔刷 / 撤销 / 保存 / 可选 Tripo 与环绕页预览
# =============================================================================


def run_editor_session(img: np.ndarray, output_basename: str) -> Optional[str]:
    """
    【核心交互窗口】在融合并裁剪后的 ROI 上工作。

    数据结构：RGBA，alpha=0 表示透明（去背景）。
    - 左键点选：把与点击色接近的像素设透明；右键撤销「一种」颜色规则。
    - B：笔刷模式，涂回前景；U：多级撤销；+/-：笔刷大小。
    - S：后台线程写入 PNG（imwrite_png_rgba 兼容中文路径），成功后关闭本窗口并返回绝对路径。
    - Q：不保存，关闭窗口返回 None。
    """
    orig_bgr = img.copy()
    h, w = img.shape[:2]
    colors_to_remove: List[Tuple[int, int, int]] = []
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = img
    rgba[..., 3] = 255

    history: List[Tuple[np.ndarray, List[Tuple[int, int, int]]]] = []
    brush_mode = False
    brush_size = BRUSH_SIZE
    mouse_down = False

    win_name = "ProV1 - 点选去色 | B笔刷 U撤销 S保存 3=360环绕 T=Tripo Q退出 +/-笔刷"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)

    # 撤销栈：保存整幅 RGBA 与颜色列表快照，便于 U 一步步回退。
    def push_history() -> None:
        if len(history) >= MAX_UNDO_STEPS:
            history.pop(0)
        history.append((rgba.copy(), deepcopy(colors_to_remove)))

    def do_undo() -> None:
        nonlocal rgba, colors_to_remove
        if not history:
            return
        prev_rgba, prev_colors = history.pop()
        rgba[:] = prev_rgba
        colors_to_remove.clear()
        colors_to_remove.extend(prev_colors)
        print("撤销一步，剩余历史", len(history))

    def preview_from_rgba() -> np.ndarray:
        """把 RGBA 合成到白底上显示（保存时仍是透明 PNG）。"""
        white = np.full((h, w, 3), 255, dtype=np.uint8)
        alpha = rgba[:, :, 3].astype(np.float32) / 255.0
        alpha = alpha[:, :, np.newaxis]
        out = (alpha * rgba[:, :, :3] + (1 - alpha) * white).astype(np.uint8)
        mode_str = "笔刷复原" if brush_mode else "点选去色"
        tip = f"{mode_str} | 去除{len(colors_to_remove)}种 | 笔刷{brush_size} | S保存后进入下一步 | Q退出返回选片"
        cv2.putText(out, tip, (8, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 200), 1, cv2.LINE_AA)
        if brush_mode:
            cv2.putText(out, "B=关闭笔刷", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 180, 0), 1, cv2.LINE_AA)
        return out

    def update_preview() -> None:
        cv2.imshow(win_name, preview_from_rgba())

    def on_mouse(event, x, y, flags, param) -> None:
        nonlocal mouse_down
        x, y = int(x), int(y)
        if x < 0 or x >= w or y < 0 or y >= h:
            if event == cv2.EVENT_LBUTTONUP:
                mouse_down = False
            return

        if brush_mode:
            if event == cv2.EVENT_LBUTTONDOWN:
                mouse_down = True
                push_history()
                apply_brush_circle(rgba, orig_bgr, x, y, brush_size)
                update_preview()
            elif event == cv2.EVENT_MOUSEMOVE and (flags & cv2.EVENT_FLAG_LBUTTON):
                if mouse_down:
                    apply_brush_circle(rgba, orig_bgr, x, y, brush_size)
                    update_preview()
            elif event == cv2.EVENT_LBUTTONUP:
                mouse_down = False
        else:
            if event == cv2.EVENT_LBUTTONDOWN:
                push_history()
                bgr = tuple(int(v) for v in orig_bgr[y, x])
                colors_to_remove.append(bgr)
                remove_mask = build_remove_mask(orig_bgr, colors_to_remove, COLOR_TOLERANCE)
                rgba[..., :3] = orig_bgr
                rgba[..., 3] = 255
                rgba[remove_mask, 3] = 0
                print("新增去除颜色（BGR）:", bgr, "共", len(colors_to_remove), "种")
                update_preview()
            elif event == cv2.EVENT_RBUTTONDOWN:
                if colors_to_remove:
                    push_history()
                    colors_to_remove.pop()
                    remove_mask = build_remove_mask(orig_bgr, colors_to_remove, COLOR_TOLERANCE)
                    rgba[..., :3] = orig_bgr
                    rgba[..., 3] = 255
                    rgba[remove_mask, 3] = 0
                    print("撤销一种去除颜色，剩余", len(colors_to_remove), "种")
                    update_preview()

    cv2.setMouseCallback(win_name, on_mouse)
    update_preview()

    print("操作说明：")
    print("  左键：点选去色  |  右键：撤销上一种  |  B：复原笔刷  |  U：撤销")
    print("  S：保存（透明底）并进入下一步  |  3：生成本地 360° 环绕页  |  T：Tripo  |  Q：返回选片  |  +/-：笔刷")

    out_path = resolve_png_save_path(RESULT_DIR, output_basename)
    tripo_stem = os.path.splitext(os.path.basename(out_path))[0]
    tripo_input_path = os.path.join(RESULT_DIR, f"{tripo_stem}_tripo_input.png")

    # 保存与主循环解耦：避免 imwrite 阻塞按键响应；成功后再把绝对路径塞回主线程读一次。
    save_lock = threading.Lock()
    saved_path_holder: Dict[str, Optional[str]] = {"path": None}

    def save_in_background() -> None:
        try:
            snapshot = rgba.copy()
            trans = snapshot[:, :, 3] == 0
            snapshot[trans, :3] = 0
            apath = os.path.abspath(os.path.normpath(out_path))
            if imwrite_png_rgba(apath, snapshot):
                print("已保存透明 PNG：", apath)
                with save_lock:
                    saved_path_holder["path"] = apath
            else:
                print("保存失败：无法写入", apath)
        except Exception as e:
            print("保存失败:", e)

    running = True
    while running:
        key = cv2.waitKey(1) & 0xFF
        with save_lock:
            sp = saved_path_holder["path"]
            saved_path_holder["path"] = None
        if sp is not None:
            try:
                cv2.destroyWindow(win_name)
            except Exception:
                pass
            cv2.destroyAllWindows()
            return sp

        if key in (ord("q"), 27):
            running = False
            break
        if key == ord("u") or key == ord("U"):
            do_undo()
            update_preview()
        elif key == ord("s") or key == ord("S"):
            threading.Thread(target=save_in_background, daemon=True).start()
            print("正在后台保存…")
        elif key == ord("b") or key == ord("B"):
            brush_mode = not brush_mode
            update_preview()
        elif key in (ord("+"), ord("=")):
            brush_size = min(100, brush_size + 5)
            update_preview()
        elif key == ord("-"):
            brush_size = max(5, brush_size - 5)
            update_preview()
        elif key == ord("3") and generate_flat_360_html_from_rgba is not None:
            def do_flat360() -> None:
                try:
                    print("正在生成 360° 环绕页…")
                    if generate_flat_360_html_from_rgba(rgba.copy(), RESULT_DIR):
                        hp = os.path.join(RESULT_DIR, FLAT_360_HTML_NAME)
                        if sys.platform == "win32":
                            try:
                                os.startfile(hp)  # type: ignore[attr-defined]
                            except Exception:
                                subprocess.run(["cmd", "/c", "start", "", hp], shell=False, check=False)
                        else:
                            webbrowser.open(Path(hp).as_uri())
                except Exception as e:
                    print("环绕页生成出错:", e)

            threading.Thread(target=do_flat360, daemon=True).start()
        elif key == ord("3") and generate_flat_360_html_from_rgba is None:
            print("未找到 cutout_to_3d 模块。可单独运行：python ProV1/cutout_to_3d.py")
        elif key in (ord("t"), ord("T")) and run_tripo_image_to_3d is not None:
            def do_tripo() -> None:
                try:
                    snap = rgba.copy()
                    trans = snap[:, :, 3] == 0
                    snap[trans, :3] = 0
                    os.makedirs(RESULT_DIR, exist_ok=True)
                    cv2.imwrite(tripo_input_path, snap)
                    run_tripo_image_to_3d(tripo_input_path, RESULT_DIR)
                except Exception as e:
                    print("Tripo 出错:", e)

            threading.Thread(target=do_tripo, daemon=True).start()
        elif key in (ord("t"), ord("T")) and run_tripo_image_to_3d is None:
            print("未找到 tripo_3d 或未安装 tripo3d。请：pip install tripo3d，并设置 TRIPO_API_KEY。")

    try:
        cv2.destroyWindow(win_name)
    except Exception:
        pass
    cv2.destroyAllWindows()
    return None


def main() -> None:
    """
    程序总控：无限循环直到用户在第二屏选「退出」或首屏取消。

    一轮流程：首屏拿 full 图 → 框选 → 编辑器 →（若保存）第二屏 → 根据选择 restart/quit。
    """
    while True:
        cfg = choose_input_config_ui()
        if cfg is None:
            print("已退出。")
            return

        full = cfg.pop("full")
        if full is None:
            print("没有可用的融合图。")
            continue

        output_basename = str(cfg.get("output_basename", os.path.splitext(OUTPUT_NAME)[0]))

        sampled_frames_raw = cfg.get("sampled_frames")
        sampled_frames: List[np.ndarray] = sampled_frames_raw if isinstance(sampled_frames_raw, list) else []

        crop_ret = select_crop(full)
        if crop_ret is None:
            print("未选择区域，返回选片界面。")
            continue
        img, roi = crop_ret
        ok_stack, stack_res = run_loading_task_ui(
            "处理中（准备多帧平面数据）…",
            lambda prog: build_stack_planes_from_sampled_frames(sampled_frames, roi, progress_cb=prog),
        )
        if not ok_stack:
            print("多帧平面准备失败，返回选片界面。")
            continue
        frame_stack_bgr = stack_res if isinstance(stack_res, list) else []

        saved_png = run_editor_session(img, output_basename)
        if saved_png:
            action = post_save_choice_ui(saved_png, frame_stack_bgr=frame_stack_bgr)
            if action == "quit":
                print("用户退出程序。")
                return
            continue

        print("未保存（Q），返回选片界面。")


# -----------------------------------------------------------------------------
# 直接运行本脚本时：保证项目根在 sys.path，便于 import 兄弟模块；异常时打印栈并关掉 OpenCV 窗口。
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    if ROOT_DIR not in sys.path:
        sys.path.insert(0, ROOT_DIR)
    try:
        main()
    except Exception as e:
        print("程序出错:", e)
        traceback.print_exc()
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
        sys.exit(1)
