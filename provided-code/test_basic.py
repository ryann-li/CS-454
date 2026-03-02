#!/usr/bin/env python3
"""
WatDFS E2E Correctness Tests
"""

import os
import re
import sys
import time
import errno
import subprocess
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
#  Setup utilities (same as other tests)
# ──────────────────────────────────────────────────────────────────────────────

CACHE_INTERVAL = 2  # shorter for faster tests

def run(cmd, **kwargs):
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)

def is_mounted(mount_dir: Path) -> bool:
    cp = subprocess.run(["mountpoint", "-q", str(mount_dir)])
    return cp.returncode == 0

def fusermount_u(mount_dir: Path):
    subprocess.run(["fusermount", "-u", str(mount_dir)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def terminate_proc(p):
    if p is None or p.poll() is not None:
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

def wait_for_server_env(server_proc, timeout_s: float = 5.0):
    addr = port = None
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

# ──────────────────────────────────────────────────────────────────────────────
#  Test framework
# ──────────────────────────────────────────────────────────────────────────────

results = []

def test(name, func, dirs):
    """Run a single test function and record result."""
    try:
        func(dirs)
        print(f"✅ PASS: {name}")
        results.append((name, True, ""))
    except Exception as e:
        print(f"❌ FAIL: {name} → {e}")
        results.append((name, False, str(e)))

def print_summary():
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    failed = total - passed
    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    if failed:
        print("FAILED tests:")
        for name, p, detail in results:
            if not p:
                print(f"  - {name}: {detail}")
    print(f"{'='*60}")
    return 0 if failed == 0 else 1

def setup():
    """Build, start server+client, return (server_proc, client_proc, dirs)."""
    here = Path(".").resolve()
    
    # Build
    print("[setup] Building...")
    build = run(["make", "clean", "all"])
    if build.returncode != 0:
        print("BUILD FAILED ❌")
        print(build.stdout, build.stderr)
        sys.exit(1)
    print("[setup] Build OK ✅")

    # Dirs
    base = Path(f"/tmp/{os.environ.get('USER', 'user')}/watdfs_e2e_test")
    server_dir = base / "server"
    cache_dir  = base / "cache" 
    mount_dir  = base / "mount"
    logs_dir   = base / "logs"

    for d in [server_dir, cache_dir, mount_dir, logs_dir]:
        if d.exists() and d == mount_dir:
            fusermount_u(d)
        run(["rm", "-rf", str(d)])
        d.mkdir(parents=True, exist_ok=True)

    # Start server
    print("[setup] Starting server...")
    server_err = (logs_dir / "server.err").open("w")
    server_proc = subprocess.Popen(
        [str(here / "watdfs_server"), str(server_dir)],
        stdout=subprocess.PIPE, stderr=server_err, text=True, bufsize=1)

    addr, port, captured = wait_for_server_env(server_proc)
    if not addr:
        addr = "localhost"
    if not port:
        print("❌ No SERVER_PORT")
        terminate_proc(server_proc)
        sys.exit(2)

    # Start client
    print(f"[setup] Starting client (cache_interval={CACHE_INTERVAL}s)...")
    env = os.environ.copy()
    env.update({
        "SERVER_ADDRESS": addr,
        "SERVER_PORT": str(port),
        "CACHE_INTERVAL_SEC": str(CACHE_INTERVAL)
    })

    client_out = (logs_dir / "client.out").open("w")
    client_err = (logs_dir / "client.err").open("w")
    client_proc = subprocess.Popen(
        [str(here / "watdfs_client"), "-s", "-f", "-o", "direct_io", 
         str(cache_dir), str(mount_dir)],
        stdout=client_out, stderr=client_err, env=env, text=True, bufsize=1)

    if not wait_for_mount(mount_dir):
        print("❌ Mount failed")
        terminate_proc(client_proc)
        terminate_proc(server_proc)
        sys.exit(3)

    print("[setup] Mount active ✅\n")
    return server_proc, client_proc, {
        "server": server_dir, "cache": cache_dir, 
        "mount": mount_dir, "logs": logs_dir
    }

def teardown(server_proc, client_proc, mount_dir):
    print("\n[teardown] Cleaning up...")
    fusermount_u(mount_dir)
    time.sleep(0.2)
    terminate_proc(client_proc)
    terminate_proc(server_proc)

# ──────────────────────────────────────────────────────────────────────────────
#  Core correctness tests
# ──────────────────────────────────────────────────────────────────────────────

def test_basic_create_reopen(dirs):
    """Test basic create → close → reopen flow on same file."""
    mount_dir = dirs["mount"]
    server_dir = dirs["server"]
    
    fname = "basic_test.txt" 
    p = str(mount_dir / fname)
    
    # Create file
    fd1 = os.open(p, os.O_CREAT | os.O_RDWR)
    os.write(fd1, b"test-data")
    os.close(fd1)
    
    # Verify server has it
    assert (server_dir / fname).exists(), "File not created on server"
    
    # Reopen same file - this should work
    fd2 = os.open(p, os.O_RDWR) 
    data = os.read(fd2, 100)
    os.close(fd2)
    
    assert data == b"test-data", f"Got {data!r}, expected b'test-data'"

# ──────────────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print("WatDFS E2E Correctness Tests")
    print("=" * 40)
    
    server_proc = client_proc = None
    dirs = None
    
    try:
        server_proc, client_proc, dirs = setup()
        
        # Run all tests  
        test("basic_create_reopen", test_basic_create_reopen, dirs)
        
    except Exception as e:
        print(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if dirs:
            teardown(server_proc, client_proc, dirs["mount"])
        else:
            terminate_proc(client_proc)
            terminate_proc(server_proc)
    
    return print_summary()

if __name__ == "__main__":
    sys.exit(main())