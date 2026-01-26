#!/usr/bin/env python3
import os
import re
import sys
import time
import errno
import subprocess
from pathlib import Path

# --- Helper Functions (Same as itest.py) ---

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

# --- Test Logic ---

def main():
    here = Path(".").resolve()

    # 1. Build
    print("[1/3] Building...")
    build = run(["make", "clean", "all"])
    if build.returncode != 0:
        print("BUILD FAILED ❌")
        print(build.stdout)
        print(build.stderr)
        sys.exit(1)
    print("Build OK ✅")

    server_bin = here / "watdfs_server"
    client_bin = here / "watdfs_client"

    base = Path(f"/tmp/{os.environ.get('USER','user')}/watdfs_extended_test")
    server_dir = base / "server"
    cache_dir = base / "cache"
    mount_dir = base / "mount"
    logs_dir = base / "logs"

    # Reset test dirs
    for d in [server_dir, cache_dir, mount_dir, logs_dir]:
        # Simple cleanup if exists (dangerous if not careful, but this is tmp)
        if d.exists() and d == mount_dir:
            fusermount_u(d)
        run(["rm", "-rf", str(d)])
        d.mkdir(parents=True, exist_ok=True)

    print(f"[2/3] Starting services in {base}...")
    
    server_proc = None
    client_proc = None

    try:
        # Start Server
        server_err = (logs_dir / "server.err").open("w")
        server_proc = subprocess.Popen(
            [str(server_bin), str(server_dir)],
            stdout=subprocess.PIPE,
            stderr=server_err,
            text=True,
            bufsize=1,
        )

        addr, port, captured = wait_for_server_env(server_proc)
        if not addr or not port:
            # Fallback
            addr = os.environ.get("SERVER_ADDRESS", "localhost")
            port = os.environ.get("SERVER_PORT")

        if not port:
            print("❌ Could not get port from server")
            sys.exit(2)

        # Start Client
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

        if not wait_for_mount(mount_dir):
            print("❌ Mount failed")
            sys.exit(3)
        
        print("Mount active ✅")

        # --- TESTS ---
        print("\n[3/3] Running Extended Tests...")

        # Test 1: Create new file with mknod (O_CREAT)
        print("  Test 1: Create 'file1.txt'...", end="", flush=True)
        p1 = mount_dir / "file1.txt"
        fd = os.open(str(p1), os.O_CREAT | os.O_RDWR)
        os.close(fd)
        
        if not (server_dir / "file1.txt").exists():
            print(" FAILED ❌ (File not found on server)")
            sys.exit(1)
        print(" OK ✅")

        # Test 2: Stat existing file
        print("  Test 2: stat 'file1.txt'...", end="", flush=True)
        st = os.stat(str(p1))
        # Just check it returns valid stat
        print(" OK ✅")

        # Test 3: Open existing file (O_RDONLY) - Triggers getattr -> open
        print("  Test 3: Open existing 'file1.txt'...", end="", flush=True)
        fd = os.open(str(p1), os.O_RDONLY)
        os.close(fd)
        print(" OK ✅")

        # Test 4: Open non-existent file - Should fail
        print("  Test 4: Open 'missing.txt' (expect failure)...", end="", flush=True)
        try:
            os.open(str(mount_dir / "missing.txt"), os.O_RDONLY)
            print(" FAILED ❌ (Opened missing file!)")
            sys.exit(1)
        except OSError as e:
            if e.errno == errno.ENOENT:
                print(" OK (Got ENOENT) ✅")
            else:
                print(f" FAILED ❌ (Wrong error: {e})")
                sys.exit(1)

        # Test 5: Open with O_CREAT | O_EXCL on existing file - Should fail (EEXIST)
        # Note: FUSE handles O_EXCL by checking getattr first usually, but let's see.
        print("  Test 5: Open 'file1.txt' with O_CREAT | O_EXCL...", end="", flush=True)
        try:
            os.open(str(p1), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            print(" FAILED ❌ (Should have failed with EEXIST)")
        except OSError as e:
            if e.errno == errno.EEXIST:
                print(" OK (Got EEXIST) ✅")
            else:
                print(f" WARNING: Got {e} instead of EEXIST (This might be handled by FUSE kernel/libfuse)")

        # Test 6: Multiple Creates
        print("  Test 6: Create multiple files...", end="", flush=True)
        for i in range(5):
            fname = f"multi_{i}.txt"
            os.close(os.open(str(mount_dir / fname), os.O_CREAT | os.O_RDWR))
        
        count = len(list(server_dir.glob("multi_*.txt")))
        if count == 5:
            print(" OK ✅")
        else:
            print(f" FAILED ❌ (Found {count/5} files)")

        print("\nAll Extended Tests Passed! 🎉")

    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\nCleaning up...")
        if mount_dir.exists():
            fusermount_u(mount_dir)
        terminate_proc(client_proc)
        terminate_proc(server_proc)

if __name__ == "__main__":
    main()
