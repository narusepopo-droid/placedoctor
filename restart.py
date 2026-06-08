#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
플레이스닥터 서버 재시작 스크립트 — 좀비 서버 근본 차단 (I단계 할일1).

며칠간 반복된 "코드를 고쳐도 화면이 안 바뀜"의 원인:
  Windows에서 `uvicorn --reload`는 워커를 multiprocessing 으로 spawn 한다.
  reload(부모)가 비정상 종료되면 워커(자식)가 LISTEN 소켓을 상속한 채 '좀비'로 남고,
  netstat 은 그 소켓의 소유자를 **죽은 부모 PID** 로 잘못 표시한다.
  → "포트 소유자만 kill" 하면 죽은 PID를 죽이려다 실패, 포트가 영영 안 풀린다.

이 스크립트의 근본 해결:
  1) 포트 8000 점유 프로세스를 netstat 으로 찾아 종료.
  2) 소유자가 이미 죽었는데 포트가 안 풀리면 → 그 부모의 multiprocessing 자식
     (`--multiprocessing-fork`, `parent_pid=<죽은PID>`) 을 찾아 종료. (상속-소켓 좀비)
  3) `--reload` 없이 **단일 프로세스**로 기동 → 애초에 좀비가 안 생긴다.
  4) /health 200 확인 후 "서버 준비 완료" 출력.

⚠️ 서버는 항상 이 스크립트로만 띄운다. `uvicorn --reload` 를 직접 쓰지 말 것.
   화면이 안 바뀌면 이 스크립트 한 번 실행이면 끝.
   (플마 GUI 'pythonw … 플레이스마스터_실행.pyw' 등 무관 프로세스는 건드리지 않는다.)

서버 콘솔 로그는 같은 폴더의 server.log 에 UTF-8 로 기록된다.

사용:  python restart.py   (또는 restart.bat 더블클릭)
"""
import os
import subprocess
import sys
import time
import urllib.request

# cp949 콘솔에서도 한글/이모지 출력이 깨지거나 크래시하지 않도록 stdout 을 UTF-8 로.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOST = "127.0.0.1"
PORT = 8000
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "server.log")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True).stdout or ""
    except FileNotFoundError:
        return ""


def find_pids_on_port(port: int) -> set[str]:
    """netstat -ano 로 해당 포트(로컬 주소)를 잡고 있는 모든 PID 수집."""
    pids: set[str] = set()
    needle = f":{port}"
    for line in _run(["netstat", "-ano"]).splitlines():
        if needle not in line:
            continue
        parts = line.split()
        if len(parts) < 5 or not parts[1].endswith(needle):
            continue
        pid = parts[-1]
        if pid.isdigit() and pid != "0":
            pids.add(pid)
    return pids


def pid_alive(pid: str) -> bool:
    out = _run(["tasklist", "/FI", f"PID eq {pid}", "/NH"])
    return pid in out


def python_procs() -> list[tuple[str, str]]:
    """실행 중인 python.exe/pythonw.exe 의 (PID, CommandLine) 목록 (PowerShell CIM)."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" "
        "| ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps])
    procs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if "\t" in line:
            pid, cmd = line.split("\t", 1)
            pid = pid.strip()
            if pid.isdigit():
                procs.append((pid, cmd))
    return procs


def kill_pid(pid: str, why: str = "") -> None:
    tag = f" ({why})" if why else ""
    print(f"    - 종료: PID {pid}{tag}")
    subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True, text=True)


def free_port(port: int) -> bool:
    """포트가 빌 때까지 점유 프로세스 + 상속-소켓 좀비를 반복 종료. 성공 시 True."""
    for _ in range(15):
        pids = find_pids_on_port(port)
        if not pids:
            return True
        for pid in sorted(pids):
            if pid_alive(pid):
                kill_pid(pid, "포트 점유")
            else:
                # 죽은 소유자인데 포트가 잡혀 있음 → 소켓을 상속한 multiprocessing 자식을 찾아 종료
                for cpid, cmd in python_procs():
                    if "--multiprocessing-fork" in cmd and f"parent_pid={pid}" in cmd:
                        kill_pid(cpid, f"상속-소켓 좀비, parent_pid={pid}")
        time.sleep(0.8)
    return not find_pids_on_port(port)


def wait_health(timeout: float = 45.0) -> bool:
    url = f"http://{HOST}:{PORT}/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.7)
    return False


def main() -> int:
    os.chdir(HERE)
    print("=" * 56)
    print("  플레이스닥터 서버 재시작 (single-process, no --reload)")
    print("=" * 56)

    print(f"[1/3] 포트 {PORT} 정리 (좀비 서버·상속-소켓 워커 포함)...")
    if not free_port(PORT):
        print(f"  [실패] 포트 {PORT}를 비우지 못했습니다. 작업관리자에서 수동 확인 필요.")
        return 1
    print(f"  [OK] 포트 {PORT} 비움")

    print(f"[2/3] 서버 기동 → {LOG_PATH}")
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",   # 로그 즉시 flush
        "PYTHONUTF8": "1",          # 한글 로그 깨짐 방지
        "PYTHONIOENCODING": "utf-8",
    }
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS: 콘솔 없이 분리 기동 → 이 스크립트가 끝나도 서버는 계속 실행
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    logf = open(LOG_PATH, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--loop", "none", "--host", HOST, "--port", str(PORT)],
        cwd=HERE,
        stdout=logf,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=creationflags,
    )
    print(f"  서버 프로세스 PID {proc.pid}")

    print("[3/3] 기동 확인 (/health)...")
    if wait_health():
        print("=" * 56)
        print(f"  [준비 완료] http://{HOST}:{PORT}/")
        print(f"  로그: {LOG_PATH}")
        print("=" * 56)
        return 0

    print("  [실패] 헬스체크 실패 — server.log 를 확인하세요.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
