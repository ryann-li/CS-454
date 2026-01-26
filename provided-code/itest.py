#!/usr/bin/env python3
import os
import re
import sys
import time
import errno
import subprocess
from pathlib import Path

def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)

def is_mounted(mount_dir: Path) -> bool:
    cp = subprocess.run(["mountpoint", "-q", str(mount_dir)])
    return cp.returncode == 0

def fusermount_u(mount_dir: Path):
    subprocess.run(
        ["fusermount", "-u", str(mount_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def terminate_proc(p: subprocess.Popen):
    if p is None:
        return
    if p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=2)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass

def wait_for_mount(mount_dir: Path, timeout_s: float = 5.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if is_mounted(mount_dir):
            return True
        time.sleep(0.05)
    return False

def wait_for_server_env(server_proc: subprocess.Popen, timeout_s: float = 3.0):
    addr = None
    port = None
    buf = ""

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        line = server_proc.stdout.readline() if server_proc.stdout else ""
        if line:
            buf += line
            m1 = re.search(r"export\s+SERVER_ADDRESS\s*=\s*([^\s]+)", buf)
            m2 = re.search(r"export\s+SERVER_PORT\s*=\s*([0-9]+)", buf)
            if m1:
                addr = m1.group(1).strip()
            if m2:
                port = m2.group(1).strip()
            if addr and port:
                return addr, port, buf
        else:
            time.sleep(0.05)

    return addr, port, buf

def main():
    here = Path(".").resolve()

    # 0) Build first
    print("[0/6] Building with `make clean all`...")
    build = run(["make", "clean", "all"])
    if build.returncode != 0:
        print("BUILD FAILED ❌")
        print(build.stdout)
        print(build.stderr)
        sys.exit(1)
    print("Build OK ✅\n")

    server_bin = here / "watdfs_server"
    client_bin = here / "watdfs_client"

    if not server_bin.exists() or not client_bin.exists():
        print("ERROR: build succeeded but watdfs_server / watdfs_client not found.")
        sys.exit(1)

    base = Path(f"/tmp/{os.environ.get('USER','user')}/watdfs_itest")
    server_dir = base / "server"
    cache_dir = base / "cache"
    mount_dir = base / "mount"
    logs_dir = base / "logs"

    for d in [server_dir, cache_dir, mount_dir, logs_dir]:
        d.mkdir(parents=True, exist_ok=True)

    fusermount_u(mount_dir)

    print("== WatDFS integration test ==")
    print(f"server_dir: {server_dir}")
    print(f"cache_dir : {cache_dir}")
    print(f"mount_dir : {mount_dir}")
    print(f"logs_dir  : {logs_dir}")
    print()

    server_proc = None
    client_proc = None

    try:
        # 1) Start server
        print("[1/6] Starting server...")
        server_err = (logs_dir / "server.err").open("w")

        server_proc = subprocess.Popen(
            [str(server_bin), str(server_dir)],
            stdout=subprocess.PIPE,  # parse exports from stdout
            stderr=server_err,
            text=True,
            bufsize=1,
        )

        addr, port, captured = wait_for_server_env(server_proc, timeout_s=3.0)

        if not addr:
            addr = os.environ.get("SERVER_ADDRESS", os.uname().nodename)
        if not port:
            port = os.environ.get("SERVER_PORT")

        if not port:
            print("ERROR: Could not determine SERVER_PORT.")
            print("Server output so far:\n", captured)
            sys.exit(2)

        print(f"  SERVER_ADDRESS={addr}")
        print(f"  SERVER_PORT={port}")

        # 2) Start client (mount)
        print("[2/6] Starting client (mounting FUSE)...")
        env = os.environ.copy()
        env["SERVER_ADDRESS"] = addr
        env["SERVER_PORT"] = str(port)

        client_out = (logs_dir / "client.out").open("w")
        client_err = (logs_dir / "client.err").open("w")

        client_proc = subprocess.Popen(
            [str(client_bin), "-s", "-f", "-o", "direct_io", str(cache_dir), str(mount_dir)],
            stdout=client_out,
            stderr=client_err,
            env=env,
            text=True,
            bufsize=1,
        )

        if not wait_for_mount(mount_dir, timeout_s=5.0):
            print("ERROR: mount did not become active.")
            sys.exit(3)

        print("  mount is live ✅")

        # 3) Create file (forces getattr + mknod + open + release)
        print("[3/6] Testing create (mknod)...")
        test_file = mount_dir / "a.txt"
        fd = os.open(str(test_file), os.O_CREAT | os.O_RDWR, 0o644)
        os.close(fd)

        # verify server file exists
        server_expected = Path(str(server_dir) + "/a.txt")
        assert server_expected.exists(), f"Server did not create {server_expected}"
        print("  [ok] create file -> exists on server ✅")

        # 4) Open+close
        print("[4/6] Testing open+close...")
        fd = os.open(str(test_file), os.O_RDONLY)
        os.close(fd)
        print("  [ok] open+close ✅")

        print("\n[5/6] ✅ DONE: your implemented parts look alive.")

    finally:
        print("[6/6] Cleaning up...")
        try:
            fusermount_u(mount_dir)
        except Exception:
            pass
        terminate_proc(client_proc)
        terminate_proc(server_proc)
        print("Logs saved in:", logs_dir)

if __name__ == "__main__":
    main()
