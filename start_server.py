"""DAPENG Alpha v4.0 - 可靠启动脚本
自动检测端口、杀旧进程、启动服务器
"""
import subprocess
import sys
import time
import os
import json

# 配置：项目根取本文件所在目录，用户级目录从环境变量推导，不写死任何机器路径
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PREFERRED_PORTS = [8900, 8901, 8902]
PORT_FILE = os.path.join(PROJECT_DIR, ".current_port")

_HOME = os.path.expanduser("~")
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA") or os.path.join(_HOME, "AppData", "Local")
_WORKBUDDY_HOME = os.path.join(_HOME, ".workbuddy")

# 解释器候选路径（按优先级；托管运行时目录在启动时展开其版本子目录）
_PYTHON_CANDIDATES = [
    os.path.join(PROJECT_DIR, ".venv", "Scripts", "python.exe"),
    os.path.join(_WORKBUDDY_HOME, "binaries", "python", "envs", "default", "Scripts", "python.exe"),
    sys.executable,
    os.path.join(_LOCALAPPDATA, "Programs", "Python", "Python311", "python.exe"),
]
_PYTHON_VERSIONS_DIR = os.path.join(_WORKBUDDY_HOME, "binaries", "python", "versions")


def resolve_python():
    """自动探测可用解释器：按候选列表逐个实跑验证 `import fastapi, uvicorn`。

    返回第一个通过验证的解释器绝对路径；全部失败则打印清晰错误（列出试过的
    路径与各自失败原因）并返回 None，绝不静默失败。
    """
    candidates = list(_PYTHON_CANDIDATES)
    try:
        if os.path.isdir(_PYTHON_VERSIONS_DIR):
            for name in sorted(os.listdir(_PYTHON_VERSIONS_DIR)):
                candidates.append(
                    os.path.join(_PYTHON_VERSIONS_DIR, name, "python.exe")
                )
    except OSError:
        pass

    tried = []
    seen = set()
    for cand in candidates:
        if not cand:
            continue
        try:
            norm = os.path.normcase(os.path.abspath(cand))
        except (TypeError, ValueError):
            continue
        if norm in seen:
            continue
        seen.add(norm)
        if not os.path.exists(cand):
            tried.append((cand, "路径不存在"))
            continue
        try:
            result = subprocess.run(
                [cand, "-c", "import fastapi, uvicorn"],
                capture_output=True,
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 - 任意验证失败都视为不可用
            tried.append((cand, f"验证异常: {exc}"))
            continue
        if result.returncode == 0:
            print(f"  选用解释器: {cand}")
            return cand
        tried.append((cand, "无法 import fastapi/uvicorn"))

    print("  [错误] 未找到可用解释器（需能 import fastapi, uvicorn），已尝试：")
    for path, reason in tried:
        print(f"    - {path} : {reason}")
    return None


def find_pid_on_port(port):
    """查找占用指定端口的PID"""
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                return int(parts[-1])
    except Exception:
        pass
    return None


def kill_pid(pid):
    """强制杀死进程"""
    try:
        import ctypes
        PROCESS_TERMINATE = 0x0001
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if handle:
            ctypes.windll.kernel32.TerminateProcess(handle, 1)
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    
    # Fallback to taskkill
    try:
        subprocess.run(["taskkill", "/f", "/pid", str(pid)], 
                       capture_output=True, timeout=5)
        return True
    except Exception:
        pass
    return False


def find_available_port():
    """找到可用端口"""
    for port in PREFERRED_PORTS:
        pid = find_pid_on_port(port)
        if pid is None:
            return port, None
        
        # 尝试杀掉旧进程
        print(f"  端口 {port} 被PID {pid} 占用，尝试终止...")
        if kill_pid(pid):
            time.sleep(2)
            if find_pid_on_port(port) is None:
                return port, None
            print(f"  PID {pid} 终止后端口仍被占用")
        else:
            print(f"  无法终止PID {pid}")
    
    # 所有首选端口都不可用，使用8903
    return 8903, None


def start_server(port, python):
    """启动uvicorn服务器（python 为已探测并验证通过的解释器路径）"""
    os.chdir(PROJECT_DIR)

    # 用start命令在独立进程中启动（不受沙箱影响）
    cmd = (
        f'cmd /c start "DAPENG Alpha" /B '
        f'"{python}" -m uvicorn server.main:app '
        f'--host 127.0.0.1 --port {port}'
    )

    print(f"  启动命令: uvicorn server.main:app --port {port}")
    subprocess.Popen(cmd, shell=True, cwd=PROJECT_DIR)
    
    # 等待服务器就绪
    for i in range(15):
        time.sleep(1)
        try:
            import urllib.request
            with urllib.request.urlopen(
                f"http://localhost:{port}/api/health", timeout=3
            ) as resp:
                data = json.loads(resp.read().decode())
                if data.get("ok"):
                    return True
        except Exception:
            pass
        if i % 3 == 0:
            print(f"  等待服务器就绪... ({i+1}s)")
    
    return False


def main():
    print("=" * 50)
    print("  DAPENG Alpha v4.0 启动器")
    print("=" * 50)

    # 0. 探测解释器（自动探测 + 实跑验证，避免写死的失效路径）
    print("\n[0/3] 探测 Python 解释器...")
    python = resolve_python()
    if python is None:
        print("\n[错误] 未找到可用 Python 解释器，启动中止。")
        return

    # 1. 找可用端口
    print("\n[1/3] 检测端口...")
    port, _ = find_available_port()
    print(f"  使用端口: {port}")

    # 2. 启动服务器
    print("\n[2/3] 启动服务器...")
    success = start_server(port, python)
    
    # 3. 记录端口
    with open(PORT_FILE, "w") as f:
        f.write(str(port))
    
    if success:
        print(f"\n[3/3] ✅ 启动成功！")
        print(f"  访问地址: http://localhost:{port}/dashboard.html")
        print(f"  健康检查: http://localhost:{port}/api/health")
    else:
        print(f"\n[3/3] ⚠️ 服务器可能未就绪，请手动检查")
        print(f"  手动访问: http://localhost:{port}/dashboard.html")
    
    print(f"\n  当前端口已记录到: {PORT_FILE}")


if __name__ == "__main__":
    main()
