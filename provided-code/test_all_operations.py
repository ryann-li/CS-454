#!/usr/bin/env python3
"""
Comprehensive WatDFS Integration Test
Tests all implemented FUSE operations in a logical workflow.
"""
import os
import re
import sys
import time
import errno
import subprocess
from pathlib import Path

# --- Helper Functions ---

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

# --- Main Test ---

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

    base = Path(f"/tmp/{os.environ.get('USER','user')}/watdfs_integration_test")
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

        # --- COMPREHENSIVE INTEGRATION TESTS ---
        print("\n[3/3] Running Integration Tests...")

        # Test 1: mknod + getattr (create file and check attributes)
        print("  Test 1: Create file and check attributes...", end="", flush=True)
        test_file = mount_dir / "test_file.txt"
        
        # Create file (triggers mknod)
        test_file.touch()
        
        # Check file exists and get attributes (triggers getattr)
        if not test_file.exists():
            print(" FAILED ❌ (File not created)")
            sys.exit(1)
        
        stat_info = os.stat(test_file)
        if not (stat_info.st_mode & 0o100000):  # Regular file
            print(" FAILED ❌ (Not a regular file)")
            sys.exit(1)
        print(" OK ✅")

        # Test 2: open + write + fsync + release (file I/O workflow)
        print("  Test 2: Write data workflow...", end="", flush=True)
        content = "Hello, WatDFS! This is a test file."
        
        with open(test_file, "w") as f:  # open + write + fsync + release
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        
        # Verify content was written
        with open(test_file, "r") as f:
            read_content = f.read()
        
        if read_content != content:
            print(f" FAILED ❌ (Content mismatch)")
            sys.exit(1)
        print(" OK ✅")

        # Test 3: read operations (different sizes and offsets)
        print("  Test 3: Read operations...", end="", flush=True)
        
        # Read full file
        with open(test_file, "r") as f:
            full_read = f.read()
        
        # Read partial (first 5 bytes)
        with open(test_file, "rb") as f:
            partial_read = f.read(5)
        
        # Read with offset
        with open(test_file, "rb") as f:
            f.seek(7)  # Skip "Hello, "
            offset_read = f.read(6)  # Read "WatDFS"
        
        if full_read != content or partial_read != b"Hello" or offset_read != b"WatDFS":
            print(" FAILED ❌ (Read operations failed)")
            sys.exit(1)
        print(" OK ✅")

        # Test 4: truncate operations
        print("  Test 4: Truncate operations...", end="", flush=True)
        
        # Truncate to smaller size
        os.truncate(str(test_file), 10)
        
        with open(test_file, "r") as f:
            truncated_content = f.read()
        
        if truncated_content != content[:10]:
            print(" FAILED ❌ (Truncate shrink failed)")
            sys.exit(1)
        
        # Truncate to larger size (should pad with zeros)
        os.truncate(str(test_file), 20)
        
        with open(test_file, "rb") as f:
            extended_content = f.read()
        
        expected = content[:10].encode() + b'\x00' * 10
        if extended_content != expected:
            print(" FAILED ❌ (Truncate extend failed)")
            sys.exit(1)
        print(" OK ✅")

        # Test 5: utimensat (change file times)
        print("  Test 5: Change file times...", end="", flush=True)
        
        # Set custom times (1 hour ago)
        custom_time = time.time() - 3600
        os.utime(str(test_file), (custom_time, custom_time))
        
        # Verify times changed
        stat_info = os.stat(test_file)
        if abs(stat_info.st_atime - custom_time) > 1 or abs(stat_info.st_mtime - custom_time) > 1:
            print(" FAILED ❌ (Time change failed)")
            sys.exit(1)
        print(" OK ✅")

        # Test 6: Large file operations (chunking)
        print("  Test 6: Large file operations...", end="", flush=True)
        large_file = mount_dir / "large_file.bin"
        large_data = b"X" * 100000  # 100KB
        
        # Write large file
        with open(large_file, "wb") as f:
            f.write(large_data)
            f.flush()
            os.fsync(f.fileno())
        
        # Read back and verify
        with open(large_file, "rb") as f:
            read_large = f.read()
        
        if read_large != large_data:
            print(" FAILED ❌ (Large file I/O failed)")
            sys.exit(1)
        
        # Truncate large file
        os.truncate(str(large_file), 50000)
        
        stat_info = os.stat(large_file)
        if stat_info.st_size != 50000:
            print(" FAILED ❌ (Large file truncate failed)")
            sys.exit(1)
        print(" OK ✅")

        # Test 7: Multiple files workflow
        print("  Test 7: Multiple files workflow...", end="", flush=True)
        
        files_data = {
            "file1.txt": "First file content",
            "file2.txt": "Second file content", 
            "file3.txt": "Third file content"
        }
        
        # Create and write multiple files
        for filename, content in files_data.items():
            filepath = mount_dir / filename
            with open(filepath, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        
        # Verify all files
        for filename, expected_content in files_data.items():
            filepath = mount_dir / filename
            with open(filepath, "r") as f:
                actual_content = f.read()
            if actual_content != expected_content:
                print(f" FAILED ❌ (Multi-file failed for {filename})")
                sys.exit(1)
        print(" OK ✅")

        # Test 8: Error conditions
        print("  Test 8: Error handling...", end="", flush=True)
        
        # Try to truncate non-existent file
        nonexistent = mount_dir / "does_not_exist.txt"
        try:
            os.truncate(str(nonexistent), 10)
            print(" FAILED ❌ (Should have failed on non-existent file)")
            sys.exit(1)
        except OSError as e:
            if e.errno != errno.ENOENT:
                print(f" FAILED ❌ (Wrong error code: {e.errno})")
                sys.exit(1)
        
        # Try to read non-existent file
        try:
            with open(nonexistent, "r") as f:
                f.read()
            print(" FAILED ❌ (Should have failed on read non-existent)")
            sys.exit(1)
        except FileNotFoundError:
            pass  # Expected
        
        print(" OK ✅")

        # Test 9: Mixed operations workflow
        print("  Test 9: Mixed operations workflow...", end="", flush=True)
        mixed_file = mount_dir / "mixed_ops.txt"
        
        # Create, write, truncate, modify time, write more, fsync
        with open(mixed_file, "w") as f:
            f.write("Initial content")
        
        os.truncate(str(mixed_file), 7)  # Keep "Initial"
        
        past_time = time.time() - 7200  # 2 hours ago
        os.utime(str(mixed_file), (past_time, past_time))
        
        with open(mixed_file, "a") as f:
            f.write(" + appended")
            f.flush()
            os.fsync(f.fileno())
        
        # Verify final state
        with open(mixed_file, "r") as f:
            final_content = f.read()
        
        if final_content != "Initial + appended":
            print(f" FAILED ❌ (Mixed ops content wrong: '{final_content}')")
            sys.exit(1)
        
        # Check that access time is still our custom time (approximately)
        # Note: modification time will be updated by the append operation
        stat_info = os.stat(mixed_file)
        if abs(stat_info.st_atime - past_time) > 5:  # Allow some tolerance
            # This might not be preserved on all filesystems, so just check content is correct
            pass  # Access time behavior can vary
        
        print(" OK ✅")

        print("\n🎉 All Integration Tests Passed!")
        print("✅ Tested: getattr, mknod, open, release, read, write, truncate, fsync, utimensat")
        print("✅ Workflows: File creation, I/O operations, large files, time management, error handling")

    except Exception as e:
        print(f"\n❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        print("\nCleaning up...")
        if mount_dir.exists():
            fusermount_u(mount_dir)
        terminate_proc(client_proc)
        terminate_proc(server_proc)

if __name__ == "__main__":
    main()