"""Keqing1 Workbench Launcher - 轻量Windows启动器."""

import logging
import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote, urlencode

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
APP_TITLE = "Keqing1 工作台"
DEFAULT_PORT = 8000
LAUNCH_CMD_TEMPLATE = [
    r".\.venv-win\Scripts\python.exe",
    "workbench/main.py",
    "--port",
    str(DEFAULT_PORT),
    "local",
]
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "workbench-launcher.log"
SERVER_LOG_FILE_NAME = "workbench-server.log"
PID_FILE_NAME = ".workbench-launcher.pid"
URL = f"http://127.0.0.1:{DEFAULT_PORT}"
WORKBENCH_FINGERPRINT = "<title>麻将回放分析</title>"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def find_project_root() -> Path:
    """自动定位项目根目录。

    查找策略：
    1. exe 所在目录（开发模式或 exe 放在根目录）
    2. exe 所在目录的父目录（exe 在 dist/ 子目录）
    3. 当前工作目录
    """
    configured_root = os.environ.get("KEQING1_ROOT")
    if configured_root:
        candidate = Path(configured_root).expanduser().resolve()
        if (candidate / "workbench" / "main.py").exists():
            return candidate

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
    else:
        exe_dir = Path(__file__).resolve().parent.parent

    # 检查 exe_dir 是否是项目根目录（存在 workbench/main.py）
    if (exe_dir / "workbench" / "main.py").exists():
        return exe_dir

    # 检查父目录（exe 在 dist/ 下）
    parent = exe_dir.parent
    if (parent / "workbench" / "main.py").exists():
        return parent

    # 检查当前工作目录
    cwd = Path.cwd()
    if (cwd / "workbench" / "main.py").exists():
        return cwd

    # 都找不到，返回 exe_dir 并在后续报错
    return exe_dir


def setup_logging(project_root: Path) -> logging.Logger:
    """配置日志，写入 logs/workbench-launcher.log."""
    log_dir = project_root / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / LOG_FILE_NAME

    logger = logging.getLogger("workbench-launcher")
    logger.setLevel(logging.DEBUG)

    # 文件 handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)

    # 控制台 handler（调试用，打包后无控制台则无效）
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


def is_port_in_use(port: int) -> bool:
    """检查端口是否被占用."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def is_workbench_http(port: int) -> bool:
    """检查端口是否由 Keqing1 工作台提供服务."""
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = resp.read(8192).decode("utf-8", errors="replace")
            return resp.status < 500 and WORKBENCH_FINGERPRINT in body
    except Exception:
        return False


def read_pid_file(project_root: Path) -> int | None:
    """读取 PID 文件，返回进程 PID 或 None."""
    pid_file = project_root / PID_FILE_NAME
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
        # 检查进程是否还存在
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return pid
        else:
            pid_file.unlink(missing_ok=True)
            return None
    except (ValueError, OSError):
        pid_file.unlink(missing_ok=True)
        return None


def write_pid_file(project_root: Path, pid: int) -> None:
    """写入 PID 文件."""
    pid_file = project_root / PID_FILE_NAME
    pid_file.write_text(str(pid), encoding="utf-8")


def remove_pid_file(project_root: Path) -> None:
    """删除 PID 文件."""
    pid_file = project_root / PID_FILE_NAME
    pid_file.unlink(missing_ok=True)


def open_browser(url: str) -> None:
    """打开浏览器."""
    import webbrowser
    webbrowser.open(url)


def load_review_history(project_root: Path) -> list[dict]:
    index_path = project_root / "artifacts" / "replays" / "index.json"
    report_dir = project_root / "artifacts" / "replay_model_reviews"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        index = {}

    grouped: dict[tuple[str, int], dict] = {}
    model_labels = {
        "70k": "70k",
        "ext_mortal": "ext_mortal",
        "V2_candidate": "V2 candidate",
    }
    if report_dir.exists():
        for report_path in report_dir.glob("*.json"):
            parts = report_path.stem.rsplit("__", 2)
            if len(parts) != 3 or not parts[2].startswith("p"):
                continue
            replay_id, safe_model, player_part = parts
            meta = index.get(replay_id)
            if not isinstance(meta, dict):
                continue
            try:
                player_id = int(player_part[1:])
            except ValueError:
                continue
            key = (replay_id, player_id)
            names = meta.get("player_names") or []
            item = grouped.setdefault(key, {
                "replay_id": replay_id,
                "created_at": meta.get("created_at", ""),
                "player_id": player_id,
                "player_name": names[player_id] if 0 <= player_id < len(names) else f"P{player_id}",
                "models": [],
                "teacher_report_paths": [],
                "external_review_links": meta.get("external_review_links", {}),
            })
            item["models"].append(model_labels.get(safe_model, safe_model))
            item["teacher_report_paths"].append(report_path.relative_to(project_root).as_posix())

    model_order = {
        "ext_mortal": 0,
        "70k": 1,
        "V2 candidate": 2,
    }
    items = list(grouped.values())
    for item in items:
        pairs = sorted(
            zip(item["models"], item["teacher_report_paths"]),
            key=lambda pair: (model_order.get(pair[0], 100), pair[0]),
        )
        item["models"] = [pair[0] for pair in pairs]
        item["teacher_report_paths"] = [pair[1] for pair in pairs]
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


def review_url(item: dict) -> str:
    """构造 canonical Review Workspace URL（/reviews/:replayId）。

    replayId 进入 path 并做 URL 编码；teacher_reports 通过 tuple list 逐项
    追加，重复的参数不会被压平。
    """
    query = [
        ("player_id", str(item["player_id"])),
    ]
    query.extend(("teacher_reports", path) for path in item["teacher_report_paths"])
    replay_id = quote(str(item["replay_id"]), safe="")
    return f"{URL}/reviews/{replay_id}?{urlencode(query)}"


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

class WorkbenchLauncher:
    """Keqing1 工作台启动器 GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.project_root = find_project_root()
        self.logger = setup_logging(self.project_root)
        self.process: subprocess.Popen | None = None
        self.server_log_handle = None
        self.running = False
        self.checking = False
        self.pending_open_url: str | None = None
        self.history_items: dict[str, dict] = {}

        self.logger.info("=" * 60)
        self.logger.info("启动器初始化")
        self.logger.info(f"项目根目录: {self.project_root}")
        self.logger.info(f"Python: {sys.executable}")

        self._setup_ui()
        self._refresh_history()
        self._check_initial_state()

    def _setup_ui(self) -> None:
        """初始化 UI."""
        self.root.title(APP_TITLE)
        self.root.geometry("720x520")
        self.root.minsize(640, 460)

        # 居中显示
        self.root.update_idletasks()
        w = 720
        h = 520
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        # 样式
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Microsoft YaHei", 14, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei", 10))
        style.configure("Start.TButton", font=("Microsoft YaHei", 11))
        style.configure("Stop.TButton", font=("Microsoft YaHei", 10))

        # 主框架
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        title_label = ttk.Label(main_frame, text=APP_TITLE, style="Title.TLabel")
        title_label.pack(pady=(0, 15))

        # 状态框架
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 15))

        ttk.Label(status_frame, text="状态:").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="未启动")
        self.status_label = ttk.Label(
            status_frame,
            textvariable=self.status_var,
            style="Status.TLabel",
            foreground="gray",
        )
        self.status_label.pack(side=tk.LEFT, padx=(5, 0))

        # URL 标签（初始隐藏）
        self.url_var = tk.StringVar(value="")
        self.url_label = ttk.Label(
            main_frame,
            textvariable=self.url_var,
            foreground="blue",
            cursor="hand2",
        )
        self.url_label.pack(pady=(0, 10))
        self.url_label.bind("<Button-1>", lambda e: open_browser(URL))
        self.url_label.pack_forget()

        # 按钮框架
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))

        self.start_btn = ttk.Button(
            btn_frame,
            text="启动工作台",
            style="Start.TButton",
            command=self._on_start,
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.stop_btn = ttk.Button(
            btn_frame,
            text="停止工作台",
            style="Stop.TButton",
            command=self._on_stop,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))

        # Checkbox 框架
        check_frame = ttk.Frame(main_frame)
        check_frame.pack(fill=tk.X)

        self.auto_open_var = tk.BooleanVar(value=True)
        self.auto_open_cb = ttk.Checkbutton(
            check_frame,
            text="启动后自动打开浏览器",
            variable=self.auto_open_var,
        )
        self.auto_open_cb.pack(side=tk.LEFT)

        history_frame = ttk.LabelFrame(main_frame, text="历史 Review", padding=8)
        history_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        tree_frame = ttk.Frame(history_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.history_tree = ttk.Treeview(
            tree_frame,
            columns=("created_at", "player", "models"),
            show="headings",
            height=9,
            selectmode="browse",
        )
        self.history_tree.heading("created_at", text="时间")
        self.history_tree.heading("player", text="视角")
        self.history_tree.heading("models", text="模型")
        self.history_tree.column("created_at", width=145, minwidth=130, stretch=False)
        self.history_tree.column("player", width=120, minwidth=90, stretch=False)
        self.history_tree.column("models", width=330, minwidth=220, stretch=True)
        history_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=history_scroll.set)
        self.history_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        history_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_tree.bind("<Double-1>", lambda _event: self._on_open_history())

        history_buttons = ttk.Frame(history_frame)
        history_buttons.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(history_buttons, text="打开选中 Review", command=self._on_open_history).pack(side=tk.LEFT)
        ttk.Button(history_buttons, text="刷新", command=self._refresh_history).pack(side=tk.LEFT, padx=(8, 0))

        # 进度条（初始隐藏）
        self.progress = ttk.Progressbar(main_frame, mode="indeterminate")

        # 项目路径显示
        path_frame = ttk.Frame(main_frame)
        path_frame.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(
            path_frame,
            text=f"项目: {self.project_root.name}",
            foreground="gray",
            font=("Microsoft YaHei", 8),
        ).pack(side=tk.LEFT)

        # 关闭窗口处理
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _refresh_history(self) -> None:
        selected = self.history_tree.selection()
        selected_id = selected[0] if selected else None
        for row_id in self.history_tree.get_children():
            self.history_tree.delete(row_id)
        self.history_items.clear()
        for item in load_review_history(self.project_root):
            row_id = f"{item['replay_id']}|{item['player_id']}"
            self.history_items[row_id] = item
            self.history_tree.insert(
                "",
                tk.END,
                iid=row_id,
                values=(item["created_at"], item["player_name"], ", ".join(item["models"])),
            )
        if selected_id and selected_id in self.history_items:
            self.history_tree.selection_set(selected_id)

    def _selected_history_item(self) -> dict | None:
        selected = self.history_tree.selection()
        return self.history_items.get(selected[0]) if selected else None

    def _on_open_history(self) -> None:
        item = self._selected_history_item()
        if item is None:
            messagebox.showinfo("历史 Review", "请先选择一条 Review。")
            return
        target_url = review_url(item)
        if self.running or is_workbench_http(DEFAULT_PORT):
            open_browser(target_url)
            return
        self.pending_open_url = target_url
        self._on_start()

    def _check_initial_state(self) -> None:
        """检查初始状态：端口是否已被占用."""
        if is_port_in_use(DEFAULT_PORT):
            if is_workbench_http(DEFAULT_PORT):
                self.logger.info(f"端口 {DEFAULT_PORT} 上的工作台已运行")
                self._set_running_state(external=True)
            else:
                self.logger.warning(f"端口 {DEFAULT_PORT} 被占用但无法访问")
                self._set_error_state(f"端口 {DEFAULT_PORT} 已被其他程序占用")
        else:
            # 检查是否有之前启动的进程
            pid = read_pid_file(self.project_root)
            if pid:
                self.logger.info(f"发现 PID 文件，PID={pid}，但端口未响应，清理 PID 文件")
                remove_pid_file(self.project_root)

    def _set_running_state(self, external: bool = False) -> None:
        """设置为运行中状态."""
        self.running = True
        self.status_var.set("已运行")
        self.status_label.configure(foreground="green")
        self.url_var.set(URL)
        self.url_label.pack(pady=(0, 10))
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL if not external else tk.DISABLED)
        if external:
            self.status_var.set("已运行 (外部进程)")

    def _set_stopped_state(self) -> None:
        """设置为已停止状态."""
        self.running = False
        self.status_var.set("未启动")
        self.status_label.configure(foreground="gray")
        self.url_var.set("")
        self.url_label.pack_forget()
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)

    def _set_starting_state(self) -> None:
        """设置为启动中状态."""
        self.status_var.set("启动中...")
        self.status_label.configure(foreground="orange")
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.DISABLED)
        self.progress.pack(fill=tk.X, pady=(10, 0))
        self.progress.start(15)

    def _set_error_state(self, msg: str) -> None:
        """设置为错误状态."""
        self.status_var.set(f"错误: {msg}")
        self.status_label.configure(foreground="red")
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.progress.stop()
        self.progress.pack_forget()

    def _on_start(self) -> None:
        """启动按钮回调."""
        if self.running:
            return

        # 检查项目结构
        main_py = self.project_root / "src" / "main.py"
        if not main_py.exists():
            self.logger.error(f"找不到 {main_py}")
            messagebox.showerror("错误", f"找不到项目入口文件:\n{main_py}\n\n请确保启动器位于项目根目录或 dist/ 子目录。")
            return

        # 检查 Python 可执行文件
        python_exe = self.project_root / ".venv-win" / "Scripts" / "python.exe"
        if not python_exe.exists():
            self.logger.error(f"找不到 {python_exe}")
            messagebox.showerror("错误", f"找不到 Python 虚拟环境:\n{python_exe}\n\n请先创建 .venv-win 虚拟环境。")
            return

        # 检查端口
        if is_port_in_use(DEFAULT_PORT):
            if is_workbench_http(DEFAULT_PORT):
                self.logger.info(f"端口 {DEFAULT_PORT} 已在运行，直接打开浏览器")
                self._set_running_state(external=True)
                target_url = self.pending_open_url
                self.pending_open_url = None
                if target_url or self.auto_open_var.get():
                    open_browser(target_url or URL)
                return
            else:
                messagebox.showwarning("警告", f"端口 {DEFAULT_PORT} 已被其他程序占用，请先关闭该程序或更换端口。")
                return

        self._set_starting_state()
        self.logger.info("正在启动工作台...")

        # 在后台线程启动进程
        threading.Thread(target=self._start_process, daemon=True).start()

    def _start_process(self) -> None:
        """在后台线程中启动工作台进程."""
        try:
            # 构建命令
            cmd = LAUNCH_CMD_TEMPLATE.copy()
            cmd[0] = str(self.project_root / ".venv-win" / "Scripts" / "python.exe")

            self.logger.info(f"命令: {' '.join(cmd)}")
            self.logger.info(f"工作目录: {self.project_root}")

            server_log_path = self.project_root / LOG_DIR_NAME / SERVER_LOG_FILE_NAME
            self.server_log_handle = server_log_path.open("a", encoding="utf-8", buffering=1)
            self.server_log_handle.write(f"\n===== launcher start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")

            # 启动进程（隐藏控制台窗口）
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            self.process = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=self.server_log_handle,
                stderr=subprocess.STDOUT,
            )

            # 写入 PID 文件
            write_pid_file(self.project_root, self.process.pid)
            self.logger.info(f"进程已启动，PID={self.process.pid}")

            # 等待端口响应（最多 60 秒）
            for i in range(60):
                if self.process.poll() is not None:
                    # 进程已退出
                    exit_code = self.process.returncode
                    self.logger.error(f"进程退出，exit_code={exit_code}")
                    self._close_server_log()
                    self.root.after(0, lambda: self._set_error_state(
                        f"进程退出 (code={exit_code})，请查看 logs/{SERVER_LOG_FILE_NAME}"
                    ))
                    remove_pid_file(self.project_root)
                    return

                if is_workbench_http(DEFAULT_PORT):
                    self.logger.info(f"端口 {DEFAULT_PORT} 已响应，启动成功")
                    self.root.after(0, self._on_start_success)
                    return

                time.sleep(1)

            # 超时
            self.logger.warning("等待端口响应超时（60秒）")
            self._terminate_owned_process()
            self.root.after(0, lambda: self._set_error_state("启动超时（60秒）"))

        except Exception as e:
            self.logger.exception(f"启动失败: {e}")
            self._terminate_owned_process()
            self.root.after(0, lambda: self._set_error_state(str(e)))
            remove_pid_file(self.project_root)

    def _close_server_log(self) -> None:
        if self.server_log_handle is not None:
            try:
                self.server_log_handle.close()
            except OSError:
                pass
            self.server_log_handle = None

    def _terminate_owned_process(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=10,
            )
        self.process = None
        self._close_server_log()

    def _on_start_success(self) -> None:
        """启动成功回调."""
        self._set_running_state()
        self.logger.info("工作台启动成功")

        target_url = self.pending_open_url
        self.pending_open_url = None
        if target_url or self.auto_open_var.get():
            # 延迟 500ms 打开浏览器，确保服务完全就绪
            self.root.after(500, lambda: open_browser(target_url or URL))

    def _on_stop(self) -> None:
        """停止按钮回调."""
        if not self.running:
            return

        # 读取 PID 文件
        pid = read_pid_file(self.project_root)
        if pid is None:
            self.logger.warning("未找到 PID 文件，无法停止进程")
            self._set_stopped_state()
            return

        self.logger.info(f"正在停止工作台，PID={pid}")

        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32

            # 尝试打开进程
            handle = kernel32.OpenProcess(0x1F0FFF, False, pid)  # PROCESS_ALL_ACCESS
            if handle:
                # 终止进程树（包括子进程）
                # 使用 taskkill /T 来终止进程树
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=10,
                )
                kernel32.CloseHandle(handle)
                self.logger.info(f"进程 {pid} 已停止")
            else:
                self.logger.warning(f"无法打开进程 {pid}，可能已退出")

        except Exception as e:
            self.logger.exception(f"停止进程失败: {e}")

        remove_pid_file(self.project_root)
        self.process = None
        self._close_server_log()
        self._set_stopped_state()
        self.logger.info("工作台已停止")

    def _on_close(self) -> None:
        """关闭窗口回调."""
        if self.running:
            if messagebox.askyesno("确认", "工作台正在运行中，是否停止并退出？"):
                self._on_stop()
                self.root.destroy()
            # 如果选择否，不关闭窗口
        else:
            self.root.destroy()


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    root = tk.Tk()
    app = WorkbenchLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()
