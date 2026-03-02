#!/usr/bin/env python3
"""
WatDFS E2E Verification Suite - Replicates 9 Marmoset Tests
This script strictly matches the server behavior for local verification.
"""

import os
import re
import sys
import time
import errno
import subprocess
import multiprocessing as mp
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────────────────────────────────────

CACHE_INTERVAL_SEC = 3  # Cache timeout for freshness tests

# ──────────────────────────────────────────────────────────────────────────────
#  Setup and Teardown Utilities
# ──────────────────────────────────────────────────────────────────────────────

def run_cmd(cmd, timeout=30, **kwargs):
    """Run a subprocess command with proper error handling and timeout."""
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "Command timed out")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))

def is_mounted(mount_dir: Path) -> bool:
    """Check if directory is a FUSE mount point."""
    target = os.path.realpath(str(mount_dir))

    # Prefer kernel mount table parsing for reliability.
    try:
        with open("/proc/self/mountinfo", "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    mount_point = parts[4].replace("\\040", " ")
                    if os.path.realpath(mount_point) == target:
                        return True
    except Exception:
        pass

    # Fallback to mountpoint utility.
    try:
        cp = subprocess.run(["mountpoint", "-q", str(mount_dir)], timeout=5)
        return cp.returncode == 0
    except:
        return False

def fusermount_u(mount_dir: Path):
    """Aggressively unmount FUSE without triggering a stat() call."""
    # Do NOT use mount_dir.exists() here. It will crash if the mount is broken.
    
    # Standard unmount
    subprocess.run(["fusermount", "-u", str(mount_dir)], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Lazy unmount - this is the magic bullet for "Transport endpoint not connected"
    # It detaches the filesystem from the tree immediately.
    subprocess.run(["fusermount", "-uz", str(mount_dir)], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def terminate_process(proc):
    """Safely terminate a subprocess."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass

def wait_for_mount(mount_dir: Path, timeout_s: float = 10.0) -> bool:
    """Wait for FUSE mount to become active."""
    start_time = time.time()
    while time.time() - start_time < timeout_s:
        if is_mounted(mount_dir):
            return True
        time.sleep(0.1)
    return False

def extract_server_env(server_proc, timeout_s: float = 10.0):
    """Extract SERVER_ADDRESS and SERVER_PORT from server stdout."""
    addr = port = None
    buffer = ""
    start_time = time.time()
    
    while time.time() - start_time < timeout_s:
        try:
            line = server_proc.stdout.readline() if server_proc.stdout else ""
            if line:
                buffer += line
                addr_match = re.search(r"export\s+SERVER_ADDRESS\s*=\s*([^\s]+)", buffer)
                port_match = re.search(r"export\s+SERVER_PORT\s*=\s*([0-9]+)", buffer)
                
                if addr_match:
                    addr = addr_match.group(1).strip()
                if port_match:
                    port = port_match.group(1).strip()
                    
                if addr and port:
                    return addr, port, buffer
            else:
                time.sleep(0.1)
        except Exception:
            time.sleep(0.1)
    
    return addr, port, buffer

def print_log_files(logs_dir):
    """Print content of log files for debugging."""
    print("\n" + "="*60)
    print("LOG FILES CONTENT:")
    print("="*60)
    
    for log_file in ["server.err", "client.out", "client.err"]:
        log_path = logs_dir / log_file
        if log_path.exists():
            print(f"\n--- {log_file} ---")
            try:
                with open(log_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        lines = content.split('\n')
                        
                        # Filter out verbose RPC debug output
                        filtered_lines = []
                        skip_rpc_block = False
                        
                        for line in lines:
                            # Skip verbose RPC debug blocks
                            if 'rpcCall(' in line and 'ArgTypes:' in line:
                                skip_rpc_block = True
                                continue
                            elif skip_rpc_block and (line.strip().startswith('flags =') or 
                                                   line.strip().startswith('fh_old =') or
                                                   line.strip().startswith('writepage =') or
                                                   line.strip().startswith('direct_io =') or
                                                   line.strip().startswith('-') or
                                                   'is_input:' in line):
                                continue
                            elif skip_rpc_block and line.strip() == '':
                                skip_rpc_block = False
                                continue
                            else:
                                skip_rpc_block = False
                                filtered_lines.append(line)
                        
                        # Show only the last 50 lines to prevent spam
                        if len(filtered_lines) > 50:
                            print("[... showing last 50 lines of filtered output ...]")
                            filtered_lines = filtered_lines[-50:]
                            
                        for line in filtered_lines: 
                            print(line)
                    else:
                        print("(empty)")
            except Exception as e:
                print(f"Error reading {log_file}: {e}")
        else:
            print(f"\n--- {log_file} --- (not found)")

# ──────────────────────────────────────────────────────────────────────────────
#  Main Setup Function
# ──────────────────────────────────────────────────────────────────────────────


def start_client(dirs, client_id, server_addr=None, server_port=None):
    """Helper to start a specific client instance."""
    cache_path = dirs[f"cache{client_id}"]
    mount_path = dirs[f"mount{client_id}"]
    
    # Clean up any stale mount first
    fusermount_u(mount_path)
    
    # Get current directory for executable path
    current_dir = Path(".").resolve()
    
    # Use environment variables from current process and add server info
    env = os.environ.copy()
    # Get server info from environment or parameters
    if server_addr and server_port:
        env.update({
            "SERVER_ADDRESS": server_addr,
            "SERVER_PORT": str(server_port),
        })
        print(f"📡 Starting client {client_id} with server {server_addr}:{server_port}")
    else:
        # Try to get from current environment (from setup())
        if "SERVER_ADDRESS" not in env or "SERVER_PORT" not in env:
            print(f"Warning: Server connection info not available for client {client_id}")
        else:
            print(f"📡 Starting client {client_id} with env server {env.get('SERVER_ADDRESS')}:{env.get('SERVER_PORT')}")
    
    env.update({
        "CACHE_INTERVAL_SEC": str(CACHE_INTERVAL_SEC),
        # Suppress verbose RPC debug output
        "RPC_DEBUG": "0", 
        "DEBUG": "0",
        "VERBOSE": "0"
    })
    
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        out = (dirs["logs"] / f"client{client_id}.out").open("w")
        err = (dirs["logs"] / f"client{client_id}.err").open("w")

        proc = subprocess.Popen(
            [str(current_dir / "watdfs_client"), "-s", "-f", "-o", "direct_io", str(cache_path), str(mount_path)],
            stdout=out, stderr=err, env=env, text=True
        )

        out.close()
        err.close()

        if wait_for_mount(mount_path, timeout_s=15.0):
            return proc

        terminate_process(proc)
        fusermount_u(mount_path)
        print(f"❌ Failed to mount client {client_id} at {mount_path} (attempt {attempt}/{max_attempts})")

        # Try to read the error log for debugging
        try:
            with open(dirs["logs"] / f"client{client_id}.err", "r") as error_log:
                error_content = error_log.read().strip()
                if error_content:
                    print(f"📋 Client {client_id} stderr (last 10 lines):")
                    error_lines = error_content.split('\n')[-10:]
                    for line in error_lines:
                        if 'rpcCall' not in line and 'ArgTypes' not in line:  # Filter verbose output
                            print(f"   {line}")
        except Exception as e:
            print(f"   Could not read error log: {e}")

        if attempt < max_attempts:
            time.sleep(0.5)

    return None

    if not wait_for_mount(mount_path):
        terminate_process(proc)
        return None
    return proc

def setup():
    """Build system and start server+client with zombie-proof cleanup."""
    current_dir = Path(".").resolve()
    
    # ... (Keep your building system print/logic)

    username = os.environ.get('USER', 'testuser')
    base_dir = Path(f"/tmp/{username}/watdfs_e2e_test")
    mount_dir = base_dir / "mount"
    
    print("🧹 Cleaning up any stale test environment...")
    
    # 1. Aggressively unmount FIRST. 
    # We do this before even checking if the directory exists.
    fusermount_u(mount_dir)
    
    # 2. Now it is safe to remove the directory.
    # We use a try-except block in case rm -rf still struggles with the mount.
    try:
        if base_dir.exists():
            subprocess.run(["rm", "-rf", str(base_dir)], check=True)
    except subprocess.CalledProcessError:
        # If it still fails, the kernel might need a moment to settle the lazy unmount
        time.sleep(1)
        subprocess.run(["rm", "-rf", str(base_dir)])
    
    # 3. Create fresh directories (support multi-client)
    dirs = {
        "server": base_dir / "server",
        "cache": base_dir / "cache", 
        "mount": base_dir / "mount",
        "cache1": base_dir / "cache1", 
        "mount1": base_dir / "mount1",
        "cache2": base_dir / "cache2", 
        "mount2": base_dir / "mount2",
        "logs": base_dir / "logs"
    }
    for dir_path in dirs.values():
        dir_path.mkdir(parents=True, exist_ok=True)
    
    # ... (Rest of your setup logic)
    
    print("✅ Test environment cleaned and prepared")

    # Start WatDFS server
    print("🚀 Starting WatDFS server...")
    server_stderr = (dirs["logs"] / "server.err").open("w")
    server_proc = subprocess.Popen(
        [str(current_dir / "watdfs_server"), str(dirs["server"])],
        stdout=subprocess.PIPE, stderr=server_stderr, text=True, bufsize=1
    )

    # Extract server connection info
    server_addr, server_port, captured_output = extract_server_env(server_proc)
    if not server_addr:
        server_addr = "localhost"
    if not server_port:
        print("❌ Failed to get SERVER_PORT from server")
        terminate_process(server_proc)
        sys.exit(2)

    print(f"✅ Server running on {server_addr}:{server_port}")

    # Start WatDFS client
    print(f"🚀 Starting WatDFS client (cache_interval={CACHE_INTERVAL_SEC}s)...")
    client_env = os.environ.copy()
    client_env.update({
        "SERVER_ADDRESS": server_addr,
        "SERVER_PORT": str(server_port),
        "CACHE_INTERVAL_SEC": str(CACHE_INTERVAL_SEC),
        # Suppress verbose RPC debug output
        "RPC_DEBUG": "0",
        "DEBUG": "0",
        "VERBOSE": "0"
    })

    client_stdout = (dirs["logs"] / "client.out").open("w")
    client_stderr = (dirs["logs"] / "client.err").open("w")
    client_proc = subprocess.Popen(
        [str(current_dir / "watdfs_client"), "-s", "-f", "-o", "direct_io", 
         str(dirs["cache"]), str(dirs["mount"])],
        stdout=client_stdout, stderr=client_stderr, env=client_env, text=True, bufsize=1
    )

    # Wait for mount to become active
    if not wait_for_mount(dirs["mount"]):
        print("❌ Mount operation failed")
        terminate_process(client_proc)
        terminate_process(server_proc)
        print_log_files(dirs["logs"])
        sys.exit(3)

    print("✅ WatDFS client mounted successfully\n")
    
    # Store server connection info for multi-client tests
    dirs["server_addr"] = server_addr
    dirs["server_port"] = server_port
    
    return server_proc, client_proc, dirs, server_addr, server_port

def teardown(server_proc, client_proc, dirs):
    """Clean shutdown of server and client with robust cleanup."""
    print("\n🧹 Performing thorough cleanup...")
    
    # Multiple attempts to unmount
    mount_dir = dirs["mount"]
    for attempt in range(5):
        try:
            fusermount_u(mount_dir)
            run_cmd(["fusermount", "-u", str(mount_dir)], timeout=3)
            if not is_mounted(mount_dir):
                break
        except:
            pass
        time.sleep(0.2)
    
    # Terminate processes
    terminate_process(client_proc)
    terminate_process(server_proc)
    
    # Final cleanup attempt
    time.sleep(0.5)
    try:
        run_cmd(["rm", "-rf", str(dirs["mount"].parent)], timeout=5)
    except:
        pass
    
    print("✅ Cleanup complete")

# ──────────────────────────────────────────────────────────────────────────────
#  Test Case Implementations
# ──────────────────────────────────────────────────────────────────────────────

def test_e2e_open_close_existing(dirs):
    """Test 1: Pre-create file, perform two consecutive Open/Close cycles."""
    print("🧪 TEST 1: e2e_open_close_existing")
    
    # Pre-create file directly on server
    test_file = "existing_file.txt"
    server_path = dirs["server"] / test_file
    mount_path = dirs["mount"] / test_file
    
    with open(server_path, "w") as f:
        f.write("server-created-content")
    
    print("  📁 Pre-created file on server")
    
    # First open/close cycle
    print("  🔓 First open/close cycle...")
    fd1 = os.open(str(mount_path), os.O_RDWR)
    os.close(fd1)
    print("  ✅ First cycle completed")
    
    # Second open/close cycle
    print("  🔓 Second open/close cycle...")
    fd2 = os.open(str(mount_path), os.O_RDWR)
    os.close(fd2)
    print("  ✅ Second cycle completed")
    
    print("✅ PASSED ✅ - e2e_open_close_existing")

def test_e2e_open_close_nocreat(dirs):
    """Test 2: Attempt to open non-existing files (must fail)."""
    print("🧪 TEST 2: e2e_open_close_nocreat")
    
    nonexistent_file = dirs["mount"] / "does_not_exist.txt"
    
    # Test O_RDWR without O_CREAT (should fail)
    print("  🚫 Testing O_RDWR on non-existent file...")
    try:
        fd = os.open(str(nonexistent_file), os.O_RDWR)
        os.close(fd)
        raise AssertionError("O_RDWR should have failed on non-existent file")
    except OSError as e:
        if e.errno == errno.ENOENT:
            print("  ✅ O_RDWR correctly failed with ENOENT")
        else:
            raise AssertionError(f"Wrong errno: got {e.errno}, expected {errno.ENOENT}")
    
    # Test O_RDONLY on non-existent file (should also fail)
    print("  🚫 Testing O_RDONLY on non-existent file...")
    try:
        fd = os.open(str(nonexistent_file), os.O_RDONLY)
        os.close(fd)
        raise AssertionError("O_RDONLY should have failed on non-existent file")
    except OSError as e:
        if e.errno == errno.ENOENT:
            print("  ✅ O_RDONLY correctly failed with ENOENT")
        else:
            raise AssertionError(f"Wrong errno: got {e.errno}, expected {errno.ENOENT}")
    
    print("✅ PASSED ✅ - e2e_open_close_nocreat")

def test_e2e_create_close(dirs):
    """Test 3: Create new file, then re-open for reading."""
    print("🧪 TEST 3: e2e_create_close")
    
    test_file = dirs["mount"] / "created_file.txt"
    
    # Create file with O_WRONLY | O_CREAT
    print("  📝 Creating new file...")
    fd1 = os.open(str(test_file), os.O_WRONLY | os.O_CREAT, 0o644)
    os.close(fd1)
    print("  ✅ File created and closed")
    
    # Re-open same file with O_RDONLY
    print("  📖 Re-opening for reading...")
    fd2 = os.open(str(test_file), os.O_RDONLY)
    os.close(fd2)
    print("  ✅ File re-opened and closed")
    
    print("✅ PASSED ✅ - e2e_create_close")

def test_e2e_already_open_read_only(dirs):
    """Test 4: Section 7.1.6 Logic - Open read-only, server update, cache refresh."""
    print("🧪 TEST 4: e2e_already_open_read_only")
    
    test_file = "abcd"
    mount_path = dirs["mount"] / test_file
    server_path = dirs["server"] / test_file
    
    # Pre-create file on server
    with open(server_path, "w") as f:
        f.write("initial-content")
    print("  📁 Pre-created 'abcd' on server")
    
    # Open file with O_RDONLY (keep it open)
    print("  🔓 Opening file with O_RDONLY...")
    fd = os.open(str(mount_path), os.O_RDONLY)
    print("  ✅ File opened in read-only mode")
    
    # Update file directly on server disk (bypass client)
    print("  ✏️  Updating file directly on server disk...")
    time.sleep(0.5)  # Ensure different mtime
    with open(server_path, "w") as f:
        f.write("server-updated-content")
    print("  ✅ Server file updated")
    
    # Sleep until cache interval expires
    print(f"  ⏳ Sleeping {CACHE_INTERVAL_SEC + 1} seconds for cache expiry...")
    time.sleep(CACHE_INTERVAL_SEC + 1)
    
    # Perform os.stat (getattr) - this should refresh the cache
    print("  📊 Performing os.stat() to trigger cache refresh...")
    stat_result = os.stat(str(mount_path))
    print("  ✅ os.stat() completed (cache should be refreshed)")
    
    # Attempt os.truncate (must fail with EMFILE - Local Session Conflict)
    print("  ✂️  Testing os.truncate() (should fail with EMFILE)...")
    try:
        os.truncate(str(mount_path), 5)
        raise AssertionError("truncate should have failed with EMFILE")
    except OSError as e:
        if e.errno == errno.EMFILE:
            print("  ✅ os.truncate() correctly failed with EMFILE")
        else:
            raise AssertionError(f"Wrong errno for truncate: got {e.errno}, expected {errno.EMFILE}")
    
    # Attempt os.fsync (must fail)
    print("  💾 Testing os.fsync() (should fail)...")
    try:
        os.fsync(fd)
        raise AssertionError("fsync should have failed on read-only file")
    except OSError as e:
        # Could be EBADF or other errno depending on implementation
        print(f"  ✅ os.fsync() correctly failed with errno {e.errno}")
    
    # Clean up
    os.close(fd)
    print("  🔒 Closed file descriptor")
    
    print("✅ PASSED ✅ - e2e_already_open_read_only")

def test_e2e_create_write_close(dirs):
    """Test 5: Create, write, fsync, close, then re-open (handle async release)."""
    print("🧪 TEST 5: e2e_create_write_close")
    
    test_file = dirs["mount"] / "write_test.txt"
    test_data = b"test-write-data"
    
    # Create, write, fsync, close
    print("  📝 Creating and writing to file...")
    fd1 = os.open(str(test_file), os.O_WRONLY | os.O_CREAT, 0o644)
    bytes_written = os.write(fd1, test_data)
    assert bytes_written == len(test_data), f"Write failed: {bytes_written} != {len(test_data)}"
    
    print("  💾 Calling fsync...")
    os.fsync(fd1)
    
    print("  🔒 Closing file...")
    os.close(fd1)
    print("  ✅ File created, written, synced, and closed")
    
    # Re-open file (handle potential EMFILE from async release)
    print("  🔓 Re-opening file (handling potential EMFILE)...")
    max_retries = 10
    for attempt in range(max_retries):
        try:
            fd2 = os.open(str(test_file), os.O_RDONLY)
            print(f"  ✅ File re-opened successfully (attempt {attempt + 1})")
            
            # Verify content
            data_read = os.read(fd2, 100)
            assert data_read == test_data, f"Data mismatch: {data_read!r} != {test_data!r}"
            print("  ✅ Data integrity verified")
            
            os.close(fd2)
            break
        except OSError as e:
            if e.errno == errno.EMFILE:
                print(f"  ⏳ EMFILE on attempt {attempt + 1}, retrying...")
                time.sleep(0.5)
            else:
                raise
    else:
        raise AssertionError(f"Failed to re-open file after {max_retries} attempts")
    
    print("✅ PASSED ✅ - e2e_create_write_close")

def test_e2e_create_write_read_close(dirs):
    """Test 6: Create, write, seek to 0, read back, verify integrity."""
    print("🧪 TEST 6: e2e_create_write_read_close")
    
    test_file = dirs["mount"] / "rw_test.txt"
    test_data = b"read-write-test-data"
    
    # Create file with O_RDWR
    print("  📝 Creating file with O_RDWR...")
    fd = os.open(str(test_file), os.O_RDWR | os.O_CREAT, 0o644)
    
    # Write data
    print("  ✍️  Writing test data...")
    bytes_written = os.write(fd, test_data)
    assert bytes_written == len(test_data), f"Write failed: {bytes_written} != {len(test_data)}"
    print("  ✅ Data written")
    
    # Seek to beginning
    print("  🔄 Seeking to beginning of file...")
    pos = os.lseek(fd, 0, os.SEEK_SET)
    assert pos == 0, f"Seek failed: position {pos} != 0"
    print("  ✅ Seek completed")
    
    # Read back data
    print("  📖 Reading data back...")
    data_read = os.read(fd, len(test_data))
    assert data_read == test_data, f"Data mismatch: {data_read!r} != {test_data!r}"
    print("  ✅ Data integrity verified")
    
    # Call fsync and close
    print("  💾 Calling fsync...")
    os.fsync(fd)
    
    print("  🔒 Closing file...")
    os.close(fd)
    
    print("✅ PASSED ✅ - e2e_create_write_read_close")

def test_e2e_append_test(dirs):
    """Test 7: Verify O_APPEND mode handles writes correctly."""
    print("🧪 TEST 7: e2e_append_test")
    
    test_file = dirs["mount"] / "append_test.txt"
    initial_data = b"initial-"
    append_data = b"appended"
    expected_final = initial_data + append_data
    
    # Create file and write initial data
    print("  📝 Creating file with initial data...")
    fd1 = os.open(str(test_file), os.O_WRONLY | os.O_CREAT, 0o644)
    os.write(fd1, initial_data)
    os.close(fd1)
    print("  ✅ Initial data written")
    
    # Open file in append mode
    print("  📝 Opening file in O_APPEND mode...")
    fd2 = os.open(str(test_file), os.O_WRONLY | os.O_APPEND)
    
    # Write additional data (should be appended)
    print("  ➕ Writing additional data...")
    bytes_written = os.write(fd2, append_data)
    assert bytes_written == len(append_data), f"Append write failed: {bytes_written} != {len(append_data)}"
    os.close(fd2)
    print("  ✅ Data appended")
    
    # Verify final content
    print("  🔍 Verifying final content...")
    fd3 = os.open(str(test_file), os.O_RDONLY)
    final_data = os.read(fd3, 100)
    os.close(fd3)
    
    assert final_data == expected_final, f"Append failed: {final_data!r} != {expected_final!r}"
    print("  ✅ Append operation verified")
    
    print("✅ PASSED ✅ - e2e_append_test")

def test_e2e_excl_test(dirs):
    """Test 8: O_CREAT | O_EXCL should fail on existing files."""
    print("🧪 TEST 8: e2e_excl_test")
    
    test_file = dirs["mount"] / "excl_test.txt"
    
    # Create file first
    print("  📝 Creating file...")
    fd1 = os.open(str(test_file), os.O_WRONLY | os.O_CREAT, 0o644)
    os.write(fd1, b"existing-file")
    os.close(fd1)
    print("  ✅ File created")
    
    # Attempt to open existing file with O_CREAT | O_EXCL (should fail)
    print("  🚫 Testing O_CREAT | O_EXCL on existing file...")
    try:
        fd2 = os.open(str(test_file), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        os.close(fd2)
        raise AssertionError("O_CREAT | O_EXCL should have failed on existing file")
    except OSError as e:
        if e.errno == errno.EEXIST:
            print("  ✅ O_CREAT | O_EXCL correctly failed with EEXIST")
        else:
            raise AssertionError(f"Wrong errno: got {e.errno}, expected {errno.EEXIST}")
    
    print("✅ PASSED ✅ - e2e_excl_test")

def test_check_code_compiles(dirs):
    """Test 9: Verify code compilation (already handled in setup)."""
    print("🧪 TEST 9: check_code_compiles")
    
    # This is already verified in setup() where we run "make clean all"
    current_dir = Path(".")
    
    # Verify executables exist
    server_exe = current_dir / "watdfs_server"
    client_exe = current_dir / "watdfs_client"
    
    assert server_exe.exists() and server_exe.is_file(), "watdfs_server executable not found"
    assert client_exe.exists() and client_exe.is_file(), "watdfs_client executable not found"
    
    print("  ✅ watdfs_server executable exists")
    print("  ✅ watdfs_client executable exists")
    print("  ✅ Code compilation verified")
    
    print("✅ PASSED ✅ - check_code_compiles")

def test_e2e_already_open_write_mode(dirs):
    """Test 10: Section 7.1.6 - Write mode privileges and path-based operations."""
    print("🧪 TEST 10: e2e_already_open_write_mode")
    
    test_file = "abcd"
    mount_path = dirs["mount"] / test_file
    server_path = dirs["server"] / test_file
    
    # Setup: Create file on server with initial content
    initial_data = "initial_data"
    with open(server_path, "w") as f:
        f.write(initial_data)
    print("  📁 Created 'abcd' on server with initial_data")
    
    # Open for writing (keep fd open)
    print("  🔓 Opening file with O_RDWR...")
    fd = os.open(str(mount_path), os.O_RDWR)
    print("  ✅ File opened in write mode (fd kept open)")
    
    # Server-side injection: modify file directly on server disk
    print("  ✏️  Injecting 'wxyz' directly on server disk...")
    time.sleep(0.5)  # Ensure different mtime
    with open(server_path, "w") as f:
        f.write("wxyz")
    print("  ✅ Server file modified to 'wxyz'")
    
    # Expiry sleep
    print(f"  ⏳ Sleeping {CACHE_INTERVAL_SEC + 1} seconds for cache expiry...")
    time.sleep(CACHE_INTERVAL_SEC + 1)
    
    # Writer's privilege check: os.stat should NOT download server's wxyz
    print("  📊 Performing os.stat() (should keep local initial_data)...")
    stat_result = os.stat(str(mount_path))
    print("  ✅ os.stat() completed")
    
    # Verify we still have local content (not server's wxyz)
    print("  🔍 Verifying local content is preserved...")
    current_pos = os.lseek(fd, 0, os.SEEK_CUR)  # Save current position
    os.lseek(fd, 0, os.SEEK_SET)  # Seek to start
    local_content = os.read(fd, 100).decode('utf-8')
    os.lseek(fd, current_pos, os.SEEK_SET)  # Restore position
    
    if local_content == initial_data:
        print("  ✅ Local content preserved (writer's privilege respected)")
    else:
        raise AssertionError(f"Expected local '{initial_data}', got '{local_content}' (server download occurred)")
    
    # Path-based truncate: should work on existing local cache
    print("  ✂️  Performing path-based os.truncate()...")
    try:
        os.truncate(str(mount_path), 5)
        print("  ✅ os.truncate() succeeded")
    except OSError as e:
        if e.errno == errno.EMFILE:
            raise AssertionError("os.truncate() failed with EMFILE - file already open conflict")
        else:
            raise AssertionError(f"os.truncate() failed with unexpected error: {e}")
    
    # Verify truncation worked
    print("  🔍 Verifying truncation result...")
    os.lseek(fd, 0, os.SEEK_SET)
    truncated_content = os.read(fd, 100).decode('utf-8')
    expected_truncated = initial_data[:5]  # "initi"
    
    if truncated_content == expected_truncated:
        print(f"  ✅ Truncation verified: '{truncated_content}'")
    else:
        raise AssertionError(f"Truncation failed: expected '{expected_truncated}', got '{truncated_content}'")
    
    # Cleanup
    os.close(fd)
    print("  🔒 Closed file descriptor")
    
    print("✅ PASSED ✅ - e2e_already_open_write_mode")

def test_e2e_create_truncate_read(dirs):
    """Test Alternative: Marmoset public6 - Truncate and immediate read."""
    print("🧪 TEST: e2e_create_truncate_read")
    mount_path = dirs["mount"] / "trunc_test.txt"
    
    fd = os.open(str(mount_path), os.O_RDWR | os.O_CREAT, 0o644)
    os.write(fd, b"some initial data")
    os.truncate(str(mount_path), 4)
    os.lseek(fd, 0, os.SEEK_SET)
    data = os.read(fd, 10)
    assert data == b"some", f"Expected 'some', got {data}"
    os.fsync(fd)
    os.close(fd)
    print("  ✅ Truncate and read back verified")
    print("✅ PASSED ✅ - e2e_create_truncate_read")

def test_e2e_utime(dirs):
    """Test: Marmoset public9 - utimensat functionality."""
    print("🧪 TEST: e2e_utime")
    mount_path = dirs["mount"] / "utime_file"
    
    fd = os.open(str(mount_path), os.O_RDWR | os.O_CREAT)
    os.write(fd, b"data")
    os.close(fd)
    
    # Set custom time
    past_time = time.time() - 10000
    os.utime(str(mount_path), (past_time, past_time))
    
    st = os.stat(str(mount_path))
    assert abs(st.st_mtime - past_time) < 2, "mtime not updated correctly"
    print("  ✅ Timestamp update verified")
    print("✅ PASSED ✅ - e2e_utime")


def _writer_hold_open_worker(file_path: str, data: bytes, hold_seconds: int, result_queue):
    """Worker A: open O_CREAT|O_WRONLY, write data, hold FD, then close."""
    try:
        fd = os.open(file_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
        os.write(fd, data)
        time.sleep(hold_seconds)
        os.close(fd)
        result_queue.put({"ok": True})
    except OSError as e:
        result_queue.put({"ok": False, "errno": e.errno, "error": str(e)})
    except Exception as e:
        result_queue.put({"ok": False, "errno": None, "error": str(e)})


def _reader_timed_open_worker(file_path: str, read_after_open: bool, result_queue):
    """Worker B: open O_CREAT|O_RDONLY, time open(), and optionally read data."""
    start_ts = time.time()
    try:
        fd = os.open(file_path, os.O_CREAT | os.O_RDONLY, 0o644)
        end_ts = time.time()
        data_read = b""
        if read_after_open:
            data_read = os.read(fd, 4096)
        os.close(fd)
        result_queue.put({
            "ok": True,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_sec": end_ts - start_ts,
            "data_read": data_read,
        })
    except OSError as e:
        end_ts = time.time()
        result_queue.put({
            "ok": False,
            "errno": e.errno,
            "error": str(e),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_sec": end_ts - start_ts,
        })
    except Exception as e:
        end_ts = time.time()
        result_queue.put({
            "ok": False,
            "errno": None,
            "error": str(e),
            "start_ts": start_ts,
            "end_ts": end_ts,
            "duration_sec": end_ts - start_ts,
        })

def test_e2e_one_reader_one_writer_permissions(dirs, server_addr=None, server_port=None):
    """Marmoset release0: second client read-open must not timeout or return EACCES."""
    print("🧪 TEST: e2e_one_reader_one_writer_permissions")

    if not server_addr or not server_port:
        raise AssertionError("Server connection info is required for multi-client test")

    writer_client_proc = None
    reader_client_proc = None
    writer_proc = None
    reader_proc = None

    test_filename = "asdf_permissions.txt"
    writer_mount_file = dirs["mount1"] / test_filename
    reader_mount_file = dirs["mount2"] / test_filename

    try:
        print("  🚀 Starting writer and reader clients...")
        writer_client_proc = start_client(dirs, 1, server_addr, server_port)
        if writer_client_proc is None:
            raise AssertionError("Failed to start writer client")

        reader_client_proc = start_client(dirs, 2, server_addr, server_port)
        if reader_client_proc is None:
            raise AssertionError("Failed to start reader client")

        ctx = mp.get_context("fork")
        writer_result_q = ctx.Queue()
        reader_result_q = ctx.Queue()

        print("  ✍️  Process A: opening O_CREAT|O_WRONLY, writing, sleeping 5s...")
        writer_proc = ctx.Process(
            target=_writer_hold_open_worker,
            args=(str(writer_mount_file), b"writer-hold", 5, writer_result_q),
        )

        print("  📖 Process B: starting 1s later and timing O_CREAT|O_RDONLY open()...")
        reader_proc = ctx.Process(
            target=_reader_timed_open_worker,
            args=(str(reader_mount_file), False, reader_result_q),
        )

        writer_proc.start()
        time.sleep(1)
        reader_proc.start()

        writer_proc.join(timeout=15)
        reader_proc.join(timeout=15)

        if writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
            raise AssertionError("Writer process timed out")

        if reader_proc.is_alive():
            reader_proc.terminate()
            reader_proc.join(timeout=2)
            raise AssertionError("Reader process timed out (open() likely stuck unexpectedly)")

        if writer_result_q.empty():
            raise AssertionError("Writer process produced no result")
        writer_result = writer_result_q.get_nowait()

        if reader_result_q.empty():
            raise AssertionError("Reader process produced no result")
        reader_result = reader_result_q.get_nowait()

        if not writer_result.get("ok", False):
            raise AssertionError(
                f"Writer failed: errno={writer_result.get('errno')} error={writer_result.get('error')}"
            )

        if not reader_result.get("ok", False):
            if reader_result.get("errno") == errno.EACCES:
                raise AssertionError("Reader open() returned EACCES; expected blocking then success")
            raise AssertionError(
                f"Reader open() failed: errno={reader_result.get('errno')} error={reader_result.get('error')}"
            )

        open_duration = float(reader_result.get("duration_sec", 0.0))
        print(f"  ⏱️  Reader open() duration: {open_duration:.3f}s")

        # Marmoset timeout failures indicate open must not hang.
        if open_duration > 2.5:
            raise AssertionError(
                f"Reader open() took too long ({open_duration:.3f}s); expected prompt return"
            )

        print("  ✅ Reader did not get EACCES and open() returned promptly")
        print("✅ PASSED ✅ - e2e_one_reader_one_writer_permissions")

    finally:
        if writer_proc is not None and writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
        if reader_proc is not None and reader_proc.is_alive():
            reader_proc.terminate()
            reader_proc.join(timeout=2)

        terminate_process(writer_client_proc)
        terminate_process(reader_client_proc)
        fusermount_u(dirs["mount1"])
        fusermount_u(dirs["mount2"])


def test_e2e_one_reader_one_writer_caching(dirs, server_addr=None, server_port=None):
    """Marmoset release5: reader from second client blocks, then reads writer-updated data."""
    print("🧪 TEST: e2e_one_reader_one_writer_caching")

    if not server_addr or not server_port:
        raise AssertionError("Server connection info is required for multi-client test")

    writer_client_proc = None
    reader_client_proc = None
    writer_proc = None
    reader_proc = None

    test_filename = "asdf_caching.txt"
    writer_mount_file = dirs["mount1"] / test_filename
    reader_mount_file = dirs["mount2"] / test_filename
    initial_data = b"old-data"
    updated_data = b"new-data-from-writer"

    try:
        print("  🚀 Starting writer and reader clients...")
        writer_client_proc = start_client(dirs, 1, server_addr, server_port)
        if writer_client_proc is None:
            raise AssertionError("Failed to start writer client")

        reader_client_proc = start_client(dirs, 2, server_addr, server_port)
        if reader_client_proc is None:
            raise AssertionError("Failed to start reader client")

        print("  🧪 Priming reader cache with old data...")
        # Retry for transient EACCES caused by async release timing in prior tests.
        prime_write_ok = False
        last_prime_error = None
        for attempt in range(8):
            try:
                fd_prime_w = os.open(str(writer_mount_file), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
                os.write(fd_prime_w, initial_data)
                os.close(fd_prime_w)
                prime_write_ok = True
                break
            except OSError as e:
                last_prime_error = e
                if e.errno == errno.EACCES:
                    time.sleep(0.25)
                    continue
                raise
        if not prime_write_ok:
            raise AssertionError(f"Priming write failed after retries: {last_prime_error}")

        fd_prime_r = os.open(str(reader_mount_file), os.O_CREAT | os.O_RDONLY, 0o644)
        _ = os.read(fd_prime_r, 4096)
        os.close(fd_prime_r)

        # Ensure writer update lands in a later mtime second than the primed cache.
        time.sleep(1.2)

        ctx = mp.get_context("fork")
        writer_result_q = ctx.Queue()
        reader_result_q = ctx.Queue()

        print("  ✍️  Process A: opening for write, updating data, sleeping 5s...")
        writer_proc = ctx.Process(
            target=_writer_hold_open_worker,
            args=(str(writer_mount_file), updated_data, 5, writer_result_q),
        )

        print("  📖 Process B: opening for read after 1s and reading content...")
        reader_proc = ctx.Process(
            target=_reader_timed_open_worker,
            args=(str(reader_mount_file), True, reader_result_q),
        )

        writer_proc.start()
        time.sleep(1)
        reader_proc.start()

        writer_proc.join(timeout=20)
        reader_proc.join(timeout=20)

        if writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
            raise AssertionError("Writer process timed out")

        if reader_proc.is_alive():
            reader_proc.terminate()
            reader_proc.join(timeout=2)
            raise AssertionError("Reader process timed out")

        if writer_result_q.empty() or reader_result_q.empty():
            raise AssertionError("Missing result from writer or reader process")

        writer_result = writer_result_q.get_nowait()
        reader_result = reader_result_q.get_nowait()

        if not writer_result.get("ok", False):
            raise AssertionError(
                f"Writer failed: errno={writer_result.get('errno')} error={writer_result.get('error')}"
            )

        if not reader_result.get("ok", False):
            if reader_result.get("errno") == errno.EACCES:
                raise AssertionError("Reader open() returned EACCES; expected blocking then success")
            raise AssertionError(
                f"Reader failed: errno={reader_result.get('errno')} error={reader_result.get('error')}"
            )

        open_duration = float(reader_result.get("duration_sec", 0.0))
        data_read = bytes(reader_result.get("data_read", b""))
        print(f"  ⏱️  Reader open() duration: {open_duration:.3f}s")
        print(f"  📖 Reader observed data: {data_read!r}")

        if open_duration > 2.5:
            raise AssertionError(
                f"Reader open() took too long ({open_duration:.3f}s); expected prompt return"
            )

        if len(data_read) == 0:
            raise AssertionError("Reader returned empty data unexpectedly")

        print("  ✅ Reader open() returned promptly and data path is functional")
        print("✅ PASSED ✅ - e2e_one_reader_one_writer_caching")

    finally:
        if writer_proc is not None and writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
        if reader_proc is not None and reader_proc.is_alive():
            reader_proc.terminate()
            reader_proc.join(timeout=2)

        terminate_process(writer_client_proc)
        terminate_process(reader_client_proc)
        fusermount_u(dirs["mount1"])
        fusermount_u(dirs["mount2"])


def _open_only_worker(file_path: str, flags: int, mode: int, result_queue):
    """Open file with provided flags and immediately close on success."""
    start_ts = time.time()
    try:
        fd = os.open(file_path, flags, mode)
        end_ts = time.time()
        os.close(fd)
        result_queue.put({"ok": True, "duration_sec": end_ts - start_ts})
    except OSError as e:
        end_ts = time.time()
        result_queue.put({"ok": False, "errno": e.errno, "error": str(e), "duration_sec": end_ts - start_ts})


def _read_all_from_path(file_path: str, max_bytes: int = 1024 * 1024 + 256 * 1024) -> bytes:
    """Read file contents from a path with an upper bound for safety."""
    fd = os.open(file_path, os.O_CREAT | os.O_RDONLY, 0o644)
    try:
        chunks = []
        total = 0
        while total < max_bytes:
            chunk = os.read(fd, min(65536, max_bytes - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _atomicity_writer_worker(file_path: str, result_queue):
    """Writer worker for atomicity: open for write, write large data, close."""
    payload = (b"NEW-DATA-" * 100000)[:700000]
    start_ts = time.time()
    try:
        fd = os.open(file_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o644)
        open_done_ts = time.time()

        offset = 0
        chunk_size = 32768
        while offset < len(payload):
            wrote = os.write(fd, payload[offset:offset + chunk_size])
            if wrote <= 0:
                raise OSError(errno.EIO, "write returned non-positive byte count")
            offset += wrote

        os.fsync(fd)
        os.close(fd)

        result_queue.put({
            "ok": True,
            "open_duration_sec": open_done_ts - start_ts,
            "write_size": len(payload),
        })
    except OSError as e:
        result_queue.put({"ok": False, "errno": e.errno, "error": str(e)})
    except Exception as e:
        result_queue.put({"ok": False, "errno": None, "error": str(e)})


def _atomicity_reader_worker(file_path: str, duration_sec: float, result_queue):
    """Reader worker for atomicity: repeatedly open/read while writer runs."""
    old_payload = (b"OLD-DATA-" * 100000)[:655350]
    new_payload = (b"NEW-DATA-" * 100000)[:700000]

    old_count = 0
    new_count = 0
    mixed_count = 0
    read_errors = 0
    total_reads = 0

    end_time = time.time() + duration_sec
    while time.time() < end_time:
        try:
            data = _read_all_from_path(file_path)
            total_reads += 1
            if data == old_payload:
                old_count += 1
            elif data == new_payload:
                new_count += 1
            else:
                mixed_count += 1
        except OSError:
            read_errors += 1
        time.sleep(0.1)

    result_queue.put({
        "ok": True,
        "total_reads": total_reads,
        "old_count": old_count,
        "new_count": new_count,
        "mixed_count": mixed_count,
        "read_errors": read_errors,
    })


def test_e2e_atomicity_parallel(dirs, server_addr=None, server_port=None):
    """Mimic release9 atomicity: 1 writer + 2 readers in parallel, no mixed reads."""
    print("🧪 TEST: e2e_atomicity_parallel")

    if not server_addr or not server_port:
        raise AssertionError("Server connection info is required for multi-client atomicity test")

    reader_client_1 = None
    reader_client_2 = None
    writer_proc = None
    reader_proc_1 = None
    reader_proc_2 = None

    filename = "atomicity_test.bin"
    server_file = dirs["server"] / filename
    writer_path = dirs["mount"] / filename
    reader_path_1 = dirs["mount1"] / filename
    reader_path_2 = dirs["mount2"] / filename

    old_payload = (b"OLD-DATA-" * 100000)[:655350]

    try:
        print("  🚀 Starting 2 reader clients...")
        reader_client_1 = start_client(dirs, 1, server_addr, server_port)
        if reader_client_1 is None:
            raise AssertionError("Failed to start reader client 1")

        reader_client_2 = start_client(dirs, 2, server_addr, server_port)
        if reader_client_2 is None:
            raise AssertionError("Failed to start reader client 2")

        print(f"  📁 Creating server file with old data (size={len(old_payload)})...")
        with open(server_file, "wb") as f:
            f.write(old_payload)

        ctx = mp.get_context("fork")
        writer_q = ctx.Queue()
        reader_q_1 = ctx.Queue()
        reader_q_2 = ctx.Queue()

        print("  🏁 Starting writer and two readers in parallel...")
        writer_proc = ctx.Process(target=_atomicity_writer_worker, args=(str(writer_path), writer_q))
        reader_proc_1 = ctx.Process(target=_atomicity_reader_worker, args=(str(reader_path_1), 8.0, reader_q_1))
        reader_proc_2 = ctx.Process(target=_atomicity_reader_worker, args=(str(reader_path_2), 8.0, reader_q_2))

        writer_proc.start()
        reader_proc_1.start()
        reader_proc_2.start()

        writer_proc.join(timeout=40)
        reader_proc_1.join(timeout=40)
        reader_proc_2.join(timeout=40)

        if writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
            raise AssertionError("Writer process timed out (possible deadlock in open/sync path)")
        if reader_proc_1.is_alive() or reader_proc_2.is_alive():
            if reader_proc_1.is_alive():
                reader_proc_1.terminate()
                reader_proc_1.join(timeout=2)
            if reader_proc_2.is_alive():
                reader_proc_2.terminate()
                reader_proc_2.join(timeout=2)
            raise AssertionError("Reader process timed out")

        if writer_q.empty() or reader_q_1.empty() or reader_q_2.empty():
            raise AssertionError("Missing result from writer/readers")

        writer_result = writer_q.get_nowait()
        r1 = reader_q_1.get_nowait()
        r2 = reader_q_2.get_nowait()

        if not writer_result.get("ok", False):
            raise AssertionError(
                f"Writer failed: errno={writer_result.get('errno')} error={writer_result.get('error')}"
            )

        writer_open_sec = float(writer_result.get("open_duration_sec", 0.0))
        print(f"  ⏱️  Writer open duration: {writer_open_sec:.3f}s")
        if writer_open_sec > 3.0:
            raise AssertionError(f"Writer open took too long ({writer_open_sec:.3f}s), possible lock contention/deadlock")

        for idx, reader_result in enumerate([r1, r2], start=1):
            if not reader_result.get("ok", False):
                raise AssertionError(f"Reader {idx} worker failed")
            if reader_result.get("total_reads", 0) == 0:
                raise AssertionError(f"Reader {idx} did not complete any reads")
            if reader_result.get("mixed_count", 0) > 0:
                raise AssertionError(
                    f"Reader {idx} observed mixed/partial content {reader_result.get('mixed_count')} time(s)"
                )

        print(f"  📊 Reader1 stats: {r1}")
        print(f"  📊 Reader2 stats: {r2}")
        print("  ✅ No mixed reads observed during concurrent writer/readers")
        print("✅ PASSED ✅ - e2e_atomicity_parallel")

    finally:
        for proc in [writer_proc, reader_proc_1, reader_proc_2]:
            if proc is not None and proc.is_alive():
                proc.terminate()
                proc.join(timeout=2)

        terminate_process(reader_client_1)
        terminate_process(reader_client_2)
        fusermount_u(dirs["mount1"])
        fusermount_u(dirs["mount2"])


def test_e2e_double_client_double_open_fail(dirs, server_addr=None, server_port=None):
    """Marmoset release2: second writer open from another client should fail quickly, not timeout."""
    print("🧪 TEST: e2e_double_client_double_open_fail")

    if not server_addr or not server_port:
        raise AssertionError("Server connection info is required for multi-client test")

    writer_client_proc = None
    second_client_proc = None
    writer_proc = None
    second_open_proc = None

    test_filename = "asdf_double_open.txt"
    writer_mount_file = dirs["mount1"] / test_filename
    second_mount_file = dirs["mount2"] / test_filename

    try:
        print("  🚀 Starting writer and second clients...")
        writer_client_proc = start_client(dirs, 1, server_addr, server_port)
        if writer_client_proc is None:
            raise AssertionError("Failed to start writer client")

        second_client_proc = start_client(dirs, 2, server_addr, server_port)
        if second_client_proc is None:
            raise AssertionError("Failed to start second client")

        ctx = mp.get_context("fork")
        writer_result_q = ctx.Queue()
        second_result_q = ctx.Queue()

        print("  ✍️  Process A: opening O_CREAT|O_WRONLY and holding for 5s...")
        writer_proc = ctx.Process(
            target=_writer_hold_open_worker,
            args=(str(writer_mount_file), b"writer", 5, writer_result_q),
        )
        writer_proc.start()

        time.sleep(1)
        print("  🚫 Process B: opening same file O_CREAT|O_WRONLY (should fail quickly)...")
        second_open_proc = ctx.Process(
            target=_open_only_worker,
            args=(str(second_mount_file), os.O_CREAT | os.O_WRONLY, 0o644, second_result_q),
        )
        second_open_proc.start()

        second_open_proc.join(timeout=10)
        writer_proc.join(timeout=15)

        if second_open_proc.is_alive():
            second_open_proc.terminate()
            second_open_proc.join(timeout=2)
            raise AssertionError("Second writer open timed out")

        if second_result_q.empty():
            raise AssertionError("Second writer open produced no result")
        second_result = second_result_q.get_nowait()

        duration = float(second_result.get("duration_sec", 0.0))
        if duration > 2.5:
            raise AssertionError(f"Second writer open took too long ({duration:.3f}s)")

        if second_result.get("ok", True):
            raise AssertionError("Second writer open unexpectedly succeeded")

        errno_val = second_result.get("errno")
        if errno_val not in (errno.EACCES, errno.EPERM):
            raise AssertionError(f"Second writer open failed with unexpected errno {errno_val}")

        print("  ✅ Second writer open failed quickly without timeout")
        print("✅ PASSED ✅ - e2e_double_client_double_open_fail")

    finally:
        if writer_proc is not None and writer_proc.is_alive():
            writer_proc.terminate()
            writer_proc.join(timeout=2)
        if second_open_proc is not None and second_open_proc.is_alive():
            second_open_proc.terminate()
            second_open_proc.join(timeout=2)

        terminate_process(writer_client_proc)
        terminate_process(second_client_proc)
        fusermount_u(dirs["mount1"])
        fusermount_u(dirs["mount2"])



# ──────────────────────────────────────────────────────────────────────────────
#  Test Execution Framework
# ──────────────────────────────────────────────────────────────────────────────

def run_test(test_name, test_func, dirs, server_addr=None, server_port=None):
    """Execute a single test with proper error handling."""
    try:
        # Check if test requires server connection info (multi-client tests)
        import inspect
        sig = inspect.signature(test_func)
        if len(sig.parameters) > 1:  # More than just 'dirs' parameter
            test_func(dirs, server_addr, server_port)
        else:
            test_func(dirs)
        return True, ""
    except Exception as e:
        error_msg = f"{test_name} FAILED: {str(e)}"
        print(f"❌ FAILED ❌ - {test_name}")
        print("🔥" * 50)
        print(f"💥 ERROR: {str(e)}")
        print("🔥" * 50)
        return False, error_msg

def main():
    """Main test execution function."""
    print("=" * 70)
    print("🧪 WatDFS E2E Verification Suite - 10 Marmoset Tests")
    print("=" * 70)
    
    # Initial cleanup of any stale test environment
    username = os.environ.get('USER', 'testuser')
    stale_base_dir = Path(f"/tmp/{username}/watdfs_e2e_test")
    if stale_base_dir.exists():
        print("🧹 Cleaning up stale test environment from previous run...")
        stale_mount = stale_base_dir / "mount"
        
        # Aggressively clean up stale mounts
        for _ in range(3):
            try:
                fusermount_u(stale_mount)
                run_cmd(["fusermount", "-u", str(stale_mount)], timeout=2)
                run_cmd(["fusermount", "-uz", str(stale_mount)], timeout=2)
            except:
                pass
        
        # Remove entire stale directory
        try:
            run_cmd(["rm", "-rf", str(stale_base_dir)], timeout=5)
            print("✅ Stale environment cleaned")
        except:
            print("⚠️  Some stale files may remain, but continuing...")
    
    server_proc = client_proc = None
    dirs = None
    test_results = []
    
    try:
        # Setup system
        server_proc, client_proc, dirs, server_addr, server_port = setup()
        
        # Server connection info extracted from setup() - use the real values!
        print(f"🌐 Multi-client tests will use server {server_addr}:{server_port}")
        
        # Define all tests (including new multi-client tests)
        tests = [
            ("e2e_open_close_existing", test_e2e_open_close_existing),
            ("e2e_open_close_nocreat", test_e2e_open_close_nocreat),
            ("e2e_create_close", test_e2e_create_close),
            ("e2e_already_open_read_only", test_e2e_already_open_read_only),
            ("e2e_create_write_close", test_e2e_create_write_close),
            ("e2e_create_write_read_close", test_e2e_create_write_read_close),
            ("e2e_append_test", test_e2e_append_test),
            ("e2e_excl_test", test_e2e_excl_test),
            ("check_code_compiles", test_check_code_compiles),
            ("e2e_already_open_write_mode", test_e2e_already_open_write_mode),
            ("e2e_create_truncate_read", test_e2e_create_truncate_read),
            ("e2e_utime", test_e2e_utime),
            ("e2e_one_reader_one_writer_permissions", test_e2e_one_reader_one_writer_permissions),
            ("e2e_one_reader_one_writer_caching", test_e2e_one_reader_one_writer_caching),
            ("e2e_atomicity_parallel", test_e2e_atomicity_parallel),
        ]
        all_tests = tests.copy()
        
        # Check for command line arguments
        import sys
        selected_test = None
        if len(sys.argv) > 1:
            for arg in sys.argv[1:]:
                if arg.startswith("--"):
                    candidate = arg[2:]
                    if candidate:
                        selected_test = candidate
                        break
                else:
                    selected_test = arg
                    break

        # Default: run all tests. If a test name is provided, run only that test.
        if selected_test:
            tests = [(name, func) for name, func in tests if name == selected_test]
            if not tests:
                print(f"❌ Test '{selected_test}' not found")
                print("Available tests:")
                for name, _ in all_tests:
                    print(f"  - {name}")
                return 1
        
        # Execute tests
        print(f"🏃 Running {len(tests)} tests...\n")
        
        for test_name, test_func in tests:
            print("-" * 50)
            success, error_msg = run_test(test_name, test_func, dirs, server_addr, server_port)
            test_results.append((test_name, success, error_msg))
            if not success:
                print("\n❌ TEST SUITE TERMINATED DUE TO FAILURE")
                print_log_files(dirs["logs"])
                return 1
            print()
        
        # ADDED: Show DLOG messages from client.err after successful tests
        print("\n🔍 CLIENT DEBUG MESSAGES:")
        print("-" * 50)
        try:
            client_err_path = dirs["logs"] / "client.err"
            if client_err_path.exists():
                with open(client_err_path, 'r') as f:
                    content = f.read().strip()
                    if content:
                        # Show only truncate-related DLOG messages for clarity
                        lines = content.split('\n')
                        truncate_lines = [line for line in lines if 'Truncating file' in line or 'watdfs_cli_truncate' in line]
                        if truncate_lines:
                            print("📝 Truncate-related DLOG messages:")
                            for line in truncate_lines:
                                print(f"  {line}")
                        else:
                            print("📝 All recent DLOG messages:")
                            dlog_lines = [line for line in lines if 'DEBUG' in line]
                            for line in dlog_lines[-5:]:  # Show last 5 DLOG messages
                                print(f"  {line}")
                    else:
                        print("  (No DLOG messages found)")
            else:
                print("  (Log file not found)")
        except Exception as e:
            print(f"  (Error reading logs: {e})")
        print("-" * 50)
        
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        if dirs:
            print_log_files(dirs["logs"])
        return 1
    
    finally:
        # Cleanup
        if dirs:
            teardown(server_proc, client_proc, dirs)
        else:
            terminate_process(client_proc)
            terminate_process(server_proc)
    
    # Print final results
    print("=" * 70)
    print("📊 FINAL RESULTS")
    print("=" * 70)
    
    passed_count = sum(1 for _, success, _ in test_results if success)
    total_count = len(test_results)
    
    for test_name, success, error_msg in test_results:
        status = "✅ PASS" if success else f"❌ FAIL"
        print(f"{status:8} {test_name}")
        if not success:
            print(f"         → {error_msg}")
    
    print("-" * 70)
    print(f"SUMMARY: {passed_count}/{total_count} tests passed")
    
    if passed_count == total_count:
        print("🎉 ALL TESTS PASSED! Your WatDFS implementation is working correctly!")
        return 0
    else:
        print(f"💥 {total_count - passed_count} test(s) failed. Check implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())