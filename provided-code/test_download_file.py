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

# --- Test Logic for download_file ---

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

    base = Path(f"/tmp/{os.environ.get('USER','user')}/watdfs_download_test")
    server_dir = base / "server"
    cache_dir = base / "cache"
    mount_dir = base / "mount"
    logs_dir = base / "logs"

    # Reset test dirs
    for d in [server_dir, cache_dir, mount_dir, logs_dir]:
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

        # --- TEST download_file ---
        print("\n[3/3] Testing download_file...")

        # Put a file on the server directly
        test_file = "test_download.txt"
        test_content = "This is test content for download_file."
        server_file = server_dir / test_file
        with server_file.open("w") as f:
            f.write(test_content)
        print(f"  Placed file '{test_file}' on server with content: {test_content}")

        # Now, read the file from the mount point (this should trigger download_file)
        mount_file = mount_dir / test_file
        print(f"  Reading file from mount: {mount_file}")
        read_result = run(["cat", str(mount_file)])
        if read_result.returncode != 0:
            print(f"❌ Failed to read file from mount: {read_result.stderr}")
            sys.exit(4)
        
        read_content = read_result.stdout.strip()
        if read_content != test_content:
            print(f"❌ Content mismatch: expected '{test_content}', got '{read_content}'")
            sys.exit(5)
        print("  File read successfully from mount ✅")

        # Check if the file was downloaded to cache
        cache_file = cache_dir / test_file
        if not cache_file.exists():
            print(f"❌ File not found in cache: {cache_file}")
            sys.exit(6)
        
        with cache_file.open("r") as f:
            cache_content = f.read().strip()
        if cache_content != test_content:
            print(f"❌ Cache content mismatch: expected '{test_content}', got '{cache_content}'")
            sys.exit(7)
        
        print(f"  File successfully downloaded to cache: {cache_file} ✅")
        print("\ndownload_file test PASSED! 🎉")

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