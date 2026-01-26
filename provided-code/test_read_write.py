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

    base = Path(f"/tmp/{os.environ.get('USER','user')}/watdfs_rw_test")
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

        # --- READ/WRITE TESTS ---
        print("\n[3/3] Running Read/Write Tests...")

        # Test 1: Basic write and read
        print("  Test 1: Basic write and read...", end="", flush=True)
        test_file = mount_dir / "basic.txt"
        test_data = b"Hello, WatDFS!"
        
        with open(test_file, "wb") as f:
            f.write(test_data)
        
        with open(test_file, "rb") as f:
            read_data = f.read()
        
        if read_data != test_data:
            print(f" FAILED ❌ (Expected {test_data}, got {read_data})")
            sys.exit(1)
        print(" OK ✅")

        # Test 2: Write and read with os.open/os.write/os.read
        print("  Test 2: Low-level write/read...", end="", flush=True)
        test_file2 = mount_dir / "lowlevel.txt"
        test_data2 = b"Low-level I/O test"
        
        fd = os.open(str(test_file2), os.O_CREAT | os.O_RDWR)
        bytes_written = os.write(fd, test_data2)
        os.close(fd)
        
        if bytes_written != len(test_data2):
            print(f" FAILED ❌ (Wrote {bytes_written} bytes, expected {len(test_data2)})")
            sys.exit(1)
        
        fd = os.open(str(test_file2), os.O_RDONLY)
        read_data2 = os.read(fd, len(test_data2))
        os.close(fd)
        
        if read_data2 != test_data2:
            print(f" FAILED ❌ (Expected {test_data2}, got {read_data2})")
            sys.exit(1)
        print(" OK ✅")

        # Test 3: Overwrite existing data
        print("  Test 3: Overwrite data...", end="", flush=True)
        original_data = b"Original content here"
        new_data = b"New content"
        
        test_file3 = mount_dir / "overwrite.txt"
        with open(test_file3, "wb") as f:
            f.write(original_data)
        
        # Overwrite from beginning
        with open(test_file3, "r+b") as f:
            f.write(new_data)
        
        with open(test_file3, "rb") as f:
            result = f.read()
        
        expected = new_data + original_data[len(new_data):]
        if result != expected:
            print(f" FAILED ❌ (Expected {expected}, got {result})")
            sys.exit(1)
        print(" OK ✅")

        # Test 4: Write at specific offset
        print("  Test 4: Write at offset...", end="", flush=True)
        test_file4 = mount_dir / "offset.txt"
        base_data = b"0123456789"
        insert_data = b"ABC"
        
        with open(test_file4, "wb") as f:
            f.write(base_data)
        
        # Write at offset 3
        with open(test_file4, "r+b") as f:
            f.seek(3)
            f.write(insert_data)
        
        with open(test_file4, "rb") as f:
            result = f.read()
        
        expected = b"012ABC6789"
        if result != expected:
            print(f" FAILED ❌ (Expected {expected}, got {result})")
            sys.exit(1)
        print(" OK ✅")

        # Test 5: Read from specific offset
        print("  Test 5: Read from offset...", end="", flush=True)
        test_file5 = mount_dir / "read_offset.txt"
        full_data = b"ABCDEFGHIJKLMNOP"
        
        with open(test_file5, "wb") as f:
            f.write(full_data)
        
        # Read from offset 5, length 6
        with open(test_file5, "rb") as f:
            f.seek(5)
            partial = f.read(6)
        
        expected = b"FGHIJK"
        if partial != expected:
            print(f" FAILED ❌ (Expected {expected}, got {partial})")
            sys.exit(1)
        print(" OK ✅")

        # Test 6: Large data (test chunking)
        print("  Test 6: Large data (chunking)...", end="", flush=True)
        test_file6 = mount_dir / "large.txt"
        # Create data larger than MAX_ARRAY_LEN (65535)
        large_data = b"X" * 100000
        
        with open(test_file6, "wb") as f:
            f.write(large_data)
        
        with open(test_file6, "rb") as f:
            read_large = f.read()
        
        if read_large != large_data:
            print(f" FAILED ❌ (Data mismatch, expected {len(large_data)} bytes, got {len(read_large)})")
            sys.exit(1)
        print(" OK ✅")

        # Test 7: Binary data
        print("  Test 7: Binary data...", end="", flush=True)
        test_file7 = mount_dir / "binary.dat"
        # Create binary data with all byte values
        binary_data = bytes(range(256)) * 10  # 2560 bytes of all possible byte values
        
        with open(test_file7, "wb") as f:
            f.write(binary_data)
        
        with open(test_file7, "rb") as f:
            read_binary = f.read()
        
        if read_binary != binary_data:
            print(f" FAILED ❌ (Binary data mismatch)")
            sys.exit(1)
        print(" OK ✅")

        # Test 8: Empty file
        print("  Test 8: Empty file...", end="", flush=True)
        test_file8 = mount_dir / "empty.txt"
        
        # Create empty file
        with open(test_file8, "wb") as f:
            pass  # Write nothing
        
        with open(test_file8, "rb") as f:
            empty_read = f.read()
        
        if empty_read != b"":
            print(f" FAILED ❌ (Expected empty, got {empty_read})")
            sys.exit(1)
        print(" OK ✅")

        # Test 9: Read beyond EOF
        print("  Test 9: Read beyond EOF...", end="", flush=True)
        test_file9 = mount_dir / "eof.txt"
        eof_data = b"Short content"
        
        with open(test_file9, "wb") as f:
            f.write(eof_data)
        
        # Try to read more than available
        with open(test_file9, "rb") as f:
            result = f.read(1000)  # Try to read 1000 bytes from 13-byte file
        
        if result != eof_data:
            print(f" FAILED ❌ (Expected {eof_data}, got {result})")
            sys.exit(1)
        print(" OK ✅")

        # Test 10: Multiple files persistence
        print("  Test 10: Multiple files persistence...", end="", flush=True)
        files_data = {
            "file_a.txt": b"Content A",
            "file_b.txt": b"Content B", 
            "file_c.txt": b"Content C"
        }
        
        # Write all files
        for filename, data in files_data.items():
            with open(mount_dir / filename, "wb") as f:
                f.write(data)
        
        # Read all files back and verify
        for filename, expected_data in files_data.items():
            with open(mount_dir / filename, "rb") as f:
                actual_data = f.read()
            if actual_data != expected_data:
                print(f" FAILED ❌ ({filename}: expected {expected_data}, got {actual_data})")
                sys.exit(1)
            
            # Also verify on server side
            server_file = server_dir / filename
            if not server_file.exists():
                print(f" FAILED ❌ ({filename} not found on server)")
                sys.exit(1)
        
        print(" OK ✅")

        print("\n🎉 All Read/Write Tests Passed!")

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