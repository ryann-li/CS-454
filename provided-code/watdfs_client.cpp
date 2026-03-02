//
// Starter code for CS 454/654
// You SHOULD change this file
//

#include "watdfs_client.h"
#include "debug.h"
INIT_LOG

#include "rpc.h"
#include <iostream>
#include <map>
#include <string>
#include <algorithm>
#include <cstring>
#include <sys/stat.h>
#include <utime.h>
#include <unistd.h>
#include <fcntl.h>

struct ActiveSession
{
    int local_fd;
    int flags;
    uint64_t server_fh; // server-side file handle returned by
    struct fuse_file_info fi;
};

struct watdfs_cli_state
{
    std::string cache_path;
    time_t cache_interval;

    // Tracks active sessions (for EMFILE and local FDs)
    // Key: path, Value: ActiveSession (local_fd + flags)
    std::map<std::string, ActiveSession> active_sessions;

    // Tracks validation times (Persists even after close)
    // Key: path, Value: Tc
    std::map<std::string, time_t> cache_metadata;
};

int download_file(void *userdata, const char *cache_path, const char *server_path, struct fuse_file_info *fi);
int upload_file(void *userdata, const char *cache_path, const char *server_path, struct fuse_file_info *fi);
int watdfs_cli_release_p1(void *userdata, const char *path, struct fuse_file_info *fi);
int watdfs_cli_open(void *userdata, const char *path, struct fuse_file_info *fi);
int watdfs_cli_release(void *userdata, const char *path, struct fuse_file_info *fi);
static int sync_cache(void *userdata, const char *path, struct fuse_file_info *fi);

// SETUP AND TEARDOWN
void *watdfs_cli_init(struct fuse_conn_info *conn, const char *path_to_cache,
                      time_t cache_interval, int *ret_code)
{
    // TODO: set up the RPC library by calling `rpcClientInit`.
    // std::cout << "Initializing RPC Client..." << std::endl;

    int ret = rpcClientInit();

    if (ret < 0)
    {
        std::cerr << "Failed to initialize RPC Client, return code: " << ret << std::endl;
        // Set ret_code to some appropriate non-zero value.
        *ret_code = -1;
        return nullptr;
    }

    // TODO: check the return code of the `rpcClientInit` call
    // `rpcClientInit` may fail, for example, if an incorrect port was exported.

    // It may be useful to print to stderr or stdout during debugging.
    // Important: Make sure you turn off logging prior to submission!
    // One useful technique is to use pre-processor flags like:
    // # ifdef PRINT_ERR
    // std::cerr << "Failed to initialize RPC Client" << std::endl;
    // #endif
    // Tip: Try using a macro for the above to minimize the debugging code.

    // Allocate and initialize state
    watdfs_cli_state *state = new watdfs_cli_state();
    state->cache_path = path_to_cache;
    state->cache_interval = cache_interval;

    *ret_code = 0;
    return (void *)state; // Return the actual pointer!
}

void watdfs_cli_destroy(void *userdata)
{
    if (userdata)
    {
        delete (watdfs_cli_state *)userdata;
    }
    rpcClientDestroy();
}

// GET FILE ATTRIBUTES (direct server RPC, used internally)
static int rpc_getattr(void *userdata, const char *path, struct stat *statbuf)
{
    // SET UP THE RPC CALL
    DLOG("rpc_getattr called for '%s'", path);
    // std::cout << "rpc_getattr called for '" << path << "'" << std::endl;

    // getattr has 3 arguments.
    int ARG_COUNT = 3;

    // Allocate space for the output arguments.
    void **args = new void *[ARG_COUNT];

    // Allocate the space for arg types, and one extra space for the null
    // array element.
    int arg_types[ARG_COUNT + 1];

    // The path has string length (strlen) + 1 (for the null character).
    int pathlen = strlen(path) + 1;

    // Fill in the arguments
    // The first argument is the path, it is an input only argument, and a char
    // array. The length of the array is the length of the path.
    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    // For arrays the argument is the array pointer, not a pointer to a pointer.
    args[0] = (void *)path;

    // The second argument is the stat structure. This argument is an output
    // only argument, and we treat it as a char array. The length of the array
    // is the size of the stat structure, which we can determine with sizeof.
    arg_types[1] = (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
                   (uint)sizeof(struct stat); // statbuf
    args[1] = (void *)statbuf;

    // The third argument is the return code, an output only argument, which is
    // an integer.
    // TODO: fill in this argument type.
    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);

    // The return code is not an array, so we need to hand args[2] an int*.
    // The int* could be the address of an integer located on the stack, or use
    // a heap allocated integer, in which case it should be freed.
    // TODO: Fill in the argument
    int ret;
    args[2] = (void *)&ret;

    // Finally, the last position of the arg types is 0. There is no
    // corresponding arg.
    arg_types[3] = 0;

    // MAKE THE RPC CALL
    int rpc_ret = rpcCall((char *)"getattr", arg_types, args);

    // std::cout << "watdfs_cli_getattr RPC call returned " << rpc_ret << ", server ret code: " << ret << std::endl;

    // HANDLE THE RETURN
    // The integer value watdfs_cli_getattr will return.
    int fxn_ret = 0;
    if (rpc_ret < 0)
    {
        DLOG("getattr rpc failed with error '%d'", rpc_ret);
        // Something went wrong with the rpcCall, return a sensible return
        // value. In this case lets return, -EINVAL
        fxn_ret = -EINVAL;
    }
    else
    {
        // Our RPC call succeeded. However, it's possible that the return code
        // from the server is not 0, that is it may be -errno. Therefore, we
        // should set our function return value to the retcode from the server.

        // TODO: set the function return value to the return code from the server.
        fxn_ret = ret;
    }

    if (fxn_ret < 0)
    {
        // If the return code of watdfs_cli_getattr is negative (an error), then
        // we need to make sure that the stat structure is filled with 0s. Otherwise,
        // FUSE will be confused by the contradicting return values.
        memset(statbuf, 0, sizeof(struct stat));
    }

    // Clean up the memory we have allocated.
    delete[] args;

    // Finally return the value we got from the server.
    return fxn_ret;
}

// GET FILE ATTRIBUTES (FUSE wrapper: open->stat->release if not already open)
int watdfs_cli_getattr(void *userdata, const char *path, struct stat *statbuf)
{
    DLOG("GETATTR called on %s", path);
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    if (state->active_sessions.count(path_str) > 0)
    {
        ActiveSession &sess = state->active_sessions[path_str];
        // For read-only open files, check freshness so a stale cache is refreshed.
        // (Writers manage their own view; no server refresh needed while writing.)
        if ((sess.flags & O_ACCMODE) == O_RDONLY)
        {
            struct fuse_file_info server_fi;
            memset(&server_fi, 0, sizeof(server_fi));
            server_fi.fh = sess.server_fh;
            server_fi.flags = sess.flags;
            sync_cache(userdata, path, &server_fi);
        }
        // Stat the (possibly just-refreshed) local cache file.
        int ret = stat(full_cache_path.c_str(), statbuf);
        return (ret < 0) ? -errno : 0;
    }
    else
    {
        // File is not open: open -> stat local -> release (treat as read).
        struct fuse_file_info tmp_fi;
        memset(&tmp_fi, 0, sizeof(tmp_fi));
        tmp_fi.flags = O_RDONLY;

        int open_ret = watdfs_cli_open(userdata, path, &tmp_fi);
        if (open_ret < 0)
        {
            // If open fails with -EACCES (file locked by writer), fall back to server RPC
            // to get metadata without needing to open the file
            if (open_ret == -EACCES)
            {
                return rpc_getattr(userdata, path, statbuf);
            }
            // For other errors (e.g., file doesn't exist), return the error
            return open_ret;
        }

        int ret = stat(full_cache_path.c_str(), statbuf);
        int stat_result = (ret < 0) ? -errno : 0;

        watdfs_cli_release(userdata, path, &tmp_fi);
        return stat_result;
    }
}

// TRUNCATE (direct server RPC, used internally by upload_file)
static int rpc_truncate(void *userdata, const char *path, off_t newsize)
{
    int ARG_COUNT = 3;
    void *args[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;
    arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
    args[1] = (void *)&newsize;

    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[2] = (void *)&ret;
    arg_types[3] = 0;

    int rpc_ret = rpcCall((char *)"truncate", arg_types, args);
    return (rpc_ret < 0) ? -EINVAL : ret;
}

// Lock/Unlock RPCs for atomic transfers (Section 7.2.4)
// mode: 0 = RW_READ_LOCK, 1 = RW_WRITE_LOCK
static int rpc_lock(const char *path, int mode)
{
    int ARG_COUNT = 3;
    void *args[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;
    arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] = (1u << ARG_INPUT) | (ARG_INT << 16u);
    args[1] = (void *)&mode;

    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[2] = (void *)&ret;
    arg_types[3] = 0;

    int rpc_ret = rpcCall((char *)"lock", arg_types, args);
    return (rpc_ret < 0) ? -EINVAL : ret;
}

static int rpc_unlock(const char *path, int mode)
{
    int ARG_COUNT = 3;
    void *args[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;
    arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] = (1u << ARG_INPUT) | (ARG_INT << 16u);
    args[1] = (void *)&mode;

    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[2] = (void *)&ret;
    arg_types[3] = 0;

    int rpc_ret = rpcCall((char *)"unlock", arg_types, args);
    return (rpc_ret < 0) ? -EINVAL : ret;
}

// CREATE, OPEN AND CLOSE
int watdfs_cli_mknod(void *userdata, const char *path, mode_t mode, dev_t dev)
{
    // Called to create a file.
    int ARG_COUNT = 4;

    void **args = new void *[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;

    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] = (1u << ARG_INPUT) | (ARG_INT << 16u);
    args[1] = (void *)&mode;

    arg_types[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
    args[2] = (void *)&dev;

    arg_types[3] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[3] = (void *)&ret;

    arg_types[4] = 0;

    int rpc_ret = rpcCall((char *)"mknod", arg_types, args);

    int fxn_ret = 0;
    if (rpc_ret < 0)
    {
        fxn_ret = -EINVAL;
    }
    else
    {
        fxn_ret = ret;
    }

    delete[] args;

    // If creation succeeded, open/release to sync cache (treat as write).
    if (fxn_ret == 0)
    {
        struct fuse_file_info tmp_fi;
        memset(&tmp_fi, 0, sizeof(tmp_fi));
        tmp_fi.flags = O_WRONLY;
        int open_ret = watdfs_cli_open(userdata, path, &tmp_fi);
        if (open_ret == 0)
            watdfs_cli_release(userdata, path, &tmp_fi);
    }

    return fxn_ret;
}

int watdfs_cli_open_p1(void *userdata, const char *path,
                       struct fuse_file_info *fi)
{
    // Called during open.
    // You should fill in fi->fh.
    int ARG_COUNT = 3;

    void **args = new void *[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;

    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] =
        (1u << ARG_INPUT) | (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) |
        (ARG_CHAR << 16u) | (uint)sizeof(struct fuse_file_info);

    args[1] = (void *)fi;

    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[2] = (void *)&ret;

    arg_types[3] = 0;

    int rpc_ret = rpcCall((char *)"open", arg_types, args);

    int fxn_ret = 0;
    if (rpc_ret < 0)
    {
        DLOG("RPC call failed with error: %d", rpc_ret);
        fxn_ret = -EINVAL;
    }
    else
    {
        fxn_ret = ret;
    }

    delete[] args;
    return fxn_ret;
}

// ─── Freshness Helper: sync_cache ────────────────────────────────────────────
// Returns 0 on success, negative on error.
// After this call, the local cache file is guaranteed to be fresh.
static int sync_cache(void *userdata, const char *path, struct fuse_file_info *fi)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    time_t now = time(nullptr);

    // Tier 1: Grace Period — if (now - Tc) < cache_interval, cache is fresh.
    if (state->cache_metadata.count(path_str) > 0)
    {
        time_t Tc = state->cache_metadata[path_str];
        if ((now - Tc) < state->cache_interval)
        {
            return 0;
        }
    }

    // Tier 2: Handshake — grace period expired, ask server for mtime.
    struct stat server_stat;
    int ga_ret = rpc_getattr(userdata, path, &server_stat);
    if (ga_ret < 0)
        return ga_ret;

    time_t T_server = server_stat.st_mtime;

    struct stat local_stat;
    bool in_cache = (stat(full_cache_path.c_str(), &local_stat) == 0);

    if (in_cache)
    {
        time_t T_client = local_stat.st_mtime;
        if (T_client == T_server)
        {
            // Mtimes match — cache is still valid. Just update Tc.
            state->cache_metadata[path_str] = now;
            return 0;
        }
    }

    // Tier 3: Download — cache is stale or missing, fetch from server.
    // download_file handles mtime alignment internally.
    int d_ret = download_file(userdata, full_cache_path.c_str(), path, fi);
    if (d_ret < 0)
        return d_ret;

    state->cache_metadata[path_str] = time(nullptr);
    return 0;
}

int watdfs_cli_open(void *userdata, const char *path, struct fuse_file_info *fi)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    // 1. EMFILE check: file already open on this client?
    if (state->active_sessions.count(path_str) > 0)
    {
        return -EMFILE;
    }

    // 2. Server open RPC (enforces SWMR on server side).
    int server_ret = watdfs_cli_open_p1(userdata, path, fi);
    if (server_ret < 0)
    {
        return server_ret;
    }

    int access_mode = fi->flags & O_ACCMODE;
    struct fuse_file_info sync_fi = *fi;
    bool opened_temp_sync_handle = false;

    // sync_cache may need to read from the server; for write-mode opens,
    // the server fh from open_p1 is not guaranteed to be readable.
    if (access_mode == O_WRONLY || access_mode == O_RDWR)
    {
        memset(&sync_fi, 0, sizeof(sync_fi));
        sync_fi.flags = O_RDONLY;
        int sync_open_ret = watdfs_cli_open_p1(userdata, path, &sync_fi);
        if (sync_open_ret < 0)
        {
            watdfs_cli_release_p1(userdata, path, fi);
            return sync_open_ret;
        }
        opened_temp_sync_handle = true;
    }

    // 3. Freshness: sync_cache ensures local cache file is up-to-date.
    int sc_ret = sync_cache(userdata, path, &sync_fi);

    if (opened_temp_sync_handle)
    {
        watdfs_cli_release_p1(userdata, path, &sync_fi);
    }

    if (sc_ret < 0)
    {
        // Rollback: release on server since we can't proceed locally.
        watdfs_cli_release_p1(userdata, path, fi);
        return sc_ret;
    }

    // 4. Save the server-side FH before opening the local cache file.
    // fi->fh is the server FH here (set by watdfs_cli_open_p1).
    uint64_t server_fh = fi->fh;

    // 5. Open the local cache file with the FUSE-provided flags.
    int local_fd = open(full_cache_path.c_str(), fi->flags);
    if (local_fd < 0)
    {
        int saved_errno = errno;
        // fi->fh is still server_fh here — correct for the release RPC.
        watdfs_cli_release_p1(userdata, path, fi);
        return -saved_errno;
    }

    // 6. Overwrite fi->fh with the local FD (what FUSE will use for reads/writes).
    fi->fh = (uint64_t)local_fd;

    // 7. Update session map (store fd, flags, and server_fh).
    ActiveSession session;
    session.local_fd = local_fd;
    session.flags = fi->flags;
    session.server_fh = server_fh;
    session.fi = *fi; // Copy the contents
    state->active_sessions[path_str] = session;

    return 0;
}

int watdfs_cli_release_p1(void *userdata, const char *path,
                          struct fuse_file_info *fi)
{
    // Called during close, but possibly asynchronously.
    int ARG_COUNT = 3;
    void **args = new void *[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;

    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[1] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
                   (uint)sizeof(struct fuse_file_info);
    args[1] = (void *)fi;

    arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
    int ret;
    args[2] = (void *)&ret;
    arg_types[3] = 0;

    int rpc_ret = rpcCall((char *)"release", arg_types, args);

    int fxn_ret = 0;
    if (rpc_ret < 0)
    {
        fxn_ret = -EINVAL;
    }
    else
    {
        fxn_ret = ret;
    }

    delete[] args;
    return fxn_ret;
}

time_t get_server_last_modified_time(void *userdata, const char *path)
{
    struct stat server_stat;
    int getattr_ret = rpc_getattr(userdata, path, &server_stat);
    if (getattr_ret < 0)
        return -1;
    return server_stat.st_mtime;
}

int compare_client_server_last_modified_time(void *userdata, const char *path)
{
    time_t server_mtime = get_server_last_modified_time(userdata, path);
    if (server_mtime < 0)
        return -1;

    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    struct stat local_stat;
    if (stat(full_cache_path.c_str(), &local_stat) < 0)
        return -1;

    if (server_mtime > local_stat.st_mtime)
        return 1; // Server has newer version
    else if (server_mtime < local_stat.st_mtime)
        return -1; // Client has newer version (shouldn't happen in normal ops)
    else
        return 0; // Both are the same
}

int watdfs_cli_release(void *userdata, const char *path,
                       struct fuse_file_info *fi)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    // Check that the file is actually open.
    if (state->active_sessions.count(path_str) == 0)
    {
        return -EBADF;
    }

    ActiveSession &session = state->active_sessions[path_str];
    int access_mode = session.flags & O_ACCMODE;

    // Build a fi copy carrying the server-side FH for all server RPCs.
    // (fi->fh has been overwritten with the local_fd since watdfs_cli_open.)
    struct fuse_file_info server_fi = *fi;
    server_fi.fh = session.server_fh;

    // 1. If opened in Write or RW mode, upload the file before closing.
    if (access_mode != O_RDONLY)
    {
        int up_ret = upload_file(userdata, full_cache_path.c_str(), path, &server_fi);
        if (up_ret < 0)
        {
            // Still release and close, but propagate upload error.
            watdfs_cli_release_p1(userdata, path, &server_fi);
            close(session.local_fd);
            state->active_sessions.erase(path_str);
            return up_ret;
        }

        // upload_file handles mtime alignment internally.
    }

    // 2. Server release RPC (updates SWMR state on server).
    int server_ret = watdfs_cli_release_p1(userdata, path, &server_fi);

    // 3. Close the local FD.2
    close(session.local_fd);

    // 4. Erase the session, but keep Tc in cache_metadata.
    state->active_sessions.erase(path_str);

    // 5. Update Tc so subsequent opens can use the grace period.
    state->cache_metadata[path_str] = time(nullptr);

    return server_ret;
}

// READ AND WRITE DATA
int watdfs_cli_read(void *userdata, const char *path, char *buf, size_t size,
                    off_t offset, struct fuse_file_info *fi)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);

    bool is_open = (state->active_sessions.count(path_str) > 0);

    if (is_open)
    {
        int access_mode = state->active_sessions[path_str].flags & O_ACCMODE;

        if (access_mode == O_WRONLY || access_mode == O_RDWR)
        {
            // Write/RW mode: skip the network, read from local FD.
            ssize_t bytes = pread(fi->fh, buf, size, offset);
            return (bytes < 0) ? -errno : (int)bytes;
        }
        else
        {
            // Read-only mode: sync cache first, then read locally.
            // sync_cache -> download_file uses fi->fh as a server FD;
            // use server_fh so the server pread targets the right descriptor.
            struct fuse_file_info server_fi = *fi;
            server_fi.fh = state->active_sessions[path_str].server_fh;
            int sc_ret = sync_cache(userdata, path, &server_fi);
            if (sc_ret < 0)
                return sc_ret;

            ssize_t bytes = pread(fi->fh, buf, size, offset);
            return (bytes < 0) ? -errno : (int)bytes;
        }
    }
    else
    {
        // File is not open. Use a local fuse_file_info with O_RDONLY.
        struct fuse_file_info tmp_fi;
        memset(&tmp_fi, 0, sizeof(tmp_fi));
        tmp_fi.flags = O_RDONLY;

        int open_ret = watdfs_cli_open(userdata, path, &tmp_fi);
        if (open_ret < 0)
            return open_ret;

        ssize_t bytes = pread(tmp_fi.fh, buf, size, offset);
        int read_result = (bytes < 0) ? -errno : (int)bytes;

        watdfs_cli_release(userdata, path, &tmp_fi);
        return read_result;
    }
}
int watdfs_cli_write(void *userdata, const char *path, const char *buf,
                     size_t size, off_t offset, struct fuse_file_info *fi)
{
    DLOG("WRITE called on %s, size=%zu, offset=%ld", path, size, offset);
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    // Write locally using pwrite.
    ssize_t bytes = pwrite(fi->fh, buf, size, offset);
    if (bytes < 0)
        return -errno;

    // Freshness check (Section 7.1.5): if grace period expired, do handshake.
    time_t now = time(nullptr);
    time_t Tc = 0;
    if (state->cache_metadata.count(path_str) > 0)
        Tc = state->cache_metadata[path_str];

    if ((now - Tc) >= state->cache_interval)
    {
        // Tier 2: Compare T_client with T_server.
        struct stat server_stat;
        int ga_ret = rpc_getattr(userdata, path, &server_stat);
        bool need_upload = true;

        if (ga_ret == 0)
        {
            struct stat local_stat;
            if (stat(full_cache_path.c_str(), &local_stat) == 0)
            {
                if (local_stat.st_mtime == server_stat.st_mtime)
                {
                    // Handshake passes: mtimes match, just renew Tc.
                    need_upload = false;
                }
            }
        }

        if (need_upload)
        {
            struct fuse_file_info server_fi = *fi;
            server_fi.fh = state->active_sessions[path_str].server_fh;
            upload_file(userdata, full_cache_path.c_str(), path, &server_fi);
        }
        state->cache_metadata[path_str] = time(nullptr);
    }

    return (int)bytes;
}

int check_and_upload_if_expired(void *userdata, const char *path, struct fuse_file_info *fi)
{
    time_t now = time(nullptr);
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path); // Use string only for map lookups
    std::string full_cache_path = state->cache_path + path_str;

    time_t Tc = 0;
    if (state->cache_metadata.count(path_str) > 0)
        Tc = state->cache_metadata[path_str];

    if ((now - Tc) >= state->cache_interval)
    {
        struct stat server_stat;
        // 1. Use 'path' (the const char*) not 'path_str'
        int ga_ret = rpc_getattr(userdata, path, &server_stat);

        bool need_upload = true;
        if (ga_ret == 0)
        {
            struct stat local_stat;
            if (stat(full_cache_path.c_str(), &local_stat) == 0)
            {
                if (local_stat.st_mtime == server_stat.st_mtime)
                {
                    need_upload = false;
                }
            }
        }

        int ret = 0;
        if (need_upload)
        {
            // 2. Build the server_fi but don't use pointers to stack memory
            struct fuse_file_info server_fi = *fi;
            server_fi.fh = state->active_sessions[path_str].server_fh;

            // 3. Call upload_file using 'path' (const char*)
            // 4. Update 'ret' without redeclaring it (no 'int')
            // ret = upload_file(userdata, full_cache_path.c_str(), path, &server_fi);
            ret = -1;
        }

        state->cache_metadata[path_str] = time(nullptr);
        return ret;
    }
    return 0;
}

int watdfs_cli_truncate(void *userdata, const char *path, off_t newsize)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    if (state->active_sessions.count(path_str) > 0)
    {
        // log
        DLOG("1. Truncating file %s to size %ld", path, newsize);
        ActiveSession &session = state->active_sessions[path_str];
        if ((session.flags & O_ACCMODE) == O_RDONLY)
            return -EMFILE;

        // 1. Truncate locally
        int ret = truncate(full_cache_path.c_str(), newsize);
        if (ret < 0)
            return -errno;

        // 2. IMPORTANT: Check freshness and UPLOAD if interval expired
        // This ensures the server eventually sees the truncation.
        check_and_upload_if_expired(userdata, path, &session.fi);

        return 0;
    }
    else
    {
        // Path-based logic for closed files: Open -> Truncate -> Release (Uploads)
        DLOG("2. Truncating file %s to size %ld", path, newsize);
        struct fuse_file_info tmp_fi;
        memset(&tmp_fi, 0, sizeof(tmp_fi));
        tmp_fi.flags = O_WRONLY;

        int open_ret = watdfs_cli_open(userdata, path, &tmp_fi);
        if (open_ret < 0)
            return open_ret;

        int ret = truncate(full_cache_path.c_str(), newsize);
        int result = (ret < 0) ? -errno : 0;

        watdfs_cli_release(userdata, path, &tmp_fi); // This triggers the server sync
        return result;
    }
}

int watdfs_cli_ftruncate(void *userdata, const char *path, off_t newsize,
                         struct fuse_file_info *fi)
{
    DLOG("ftruncate called on %s, newsize=%ld", path, newsize);

    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);

    // For ftruncate, we already have an open file handle
    if (fi == NULL)
    {
        DLOG("ftruncate: fi is NULL");
        return -EBADF;
    }

    // Get session for this file handle using the path
    auto session_iter = state->active_sessions.find(path_str);
    if (session_iter == state->active_sessions.end())
    {
        DLOG("ftruncate: No active session for path %s", path);
        return -EBADF;
    }

    ActiveSession &session = session_iter->second;

    // Use ftruncate on the local file descriptor
    int result = ftruncate(session.local_fd, newsize);
    if (result < 0)
    {
        DLOG("ftruncate failed on local cache: %s", strerror(errno));
        return -errno;
    }

    DLOG("ftruncate succeeded on %s", path);
    return 0;
}

int watdfs_cli_fsync(void *userdata, const char *path,
                     struct fuse_file_info *fi)
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    // fsync only makes sense if the file is open.
    if (state->active_sessions.count(path_str) == 0)
    {
        return -EBADF;
    }

    ActiveSession &session = state->active_sessions[path_str];
    int access_mode = session.flags & O_ACCMODE;

    if (access_mode == O_RDONLY)
    {
        // Read-only file: nothing to flush.
        return -EBADF;
    }

    // Writable file: upload immediately and update Tc.
    struct fuse_file_info server_fi = *fi;
    server_fi.fh = session.server_fh;
    int up_ret = upload_file(userdata, full_cache_path.c_str(), path, &server_fi);
    if (up_ret < 0)
        return up_ret;

    state->cache_metadata[path_str] = time(nullptr);
    return 0;
}

// CHANGE METADATA
int watdfs_cli_utimensat(void *userdata, const char *path,
                         const struct timespec ts[2])
{
    watdfs_cli_state *state = (watdfs_cli_state *)userdata;
    std::string path_str(path);
    std::string full_cache_path = state->cache_path + path_str;

    if (state->active_sessions.count(path_str) > 0)
    {
        // 1. Check if we have permission to write (Section 7.1.6)
        ActiveSession &sess = state->active_sessions[path_str];
        if ((sess.flags & O_ACCMODE) == O_RDONLY)
        {
            return -EMFILE;
        }

        // 2. Set timestamps on local cache file
        int ret = utimensat(AT_FDCWD, full_cache_path.c_str(), ts, 0);
        if (ret < 0)
            return -errno;

        // 3. Freshness Check (Section 7.1.5)
        time_t now = time(nullptr);
        time_t Tc = state->cache_metadata[path_str];

        if ((now - Tc) >= state->cache_interval)
        {
            // Use the server-side FH we stored during open
            struct fuse_file_info server_fi;
            memset(&server_fi, 0, sizeof(server_fi));
            server_fi.fh = sess.server_fh;
            server_fi.flags = sess.flags;

            // This will now push the data AND the new timestamps (due to your upload_file fix)
            upload_file(userdata, full_cache_path.c_str(), path, &server_fi);

            // Renew the lease
            state->cache_metadata[path_str] = time(nullptr);
        }

        return 0;
    }
    else
    {
        // File is not open: open(WRONLY) -> utimensat local -> release.
        struct fuse_file_info tmp_fi;
        memset(&tmp_fi, 0, sizeof(tmp_fi));
        tmp_fi.flags = O_WRONLY;

        int open_ret = watdfs_cli_open(userdata, path, &tmp_fi);
        if (open_ret < 0)
            return open_ret;

        int ret = utimensat(AT_FDCWD, full_cache_path.c_str(), ts, 0);
        int ut_result = (ret < 0) ? -errno : 0;

        watdfs_cli_release(userdata, path, &tmp_fi);
        return ut_result;
    }
}

int download_file(void *userdata, const char *cache_path, const char *server_path, struct fuse_file_info *fi)
{
    // Acquire READ lock for atomic transfer.
    rpc_lock(server_path, 0); // 0 = RW_READ_LOCK

    struct stat statbuf;
    int getattr_ret = rpc_getattr(userdata, server_path, &statbuf);
    if (getattr_ret < 0)
    {
        rpc_unlock(server_path, 0);
        return getattr_ret;
    }

    int local_fd = open(cache_path, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (local_fd < 0)
    {
        rpc_unlock(server_path, 0);
        return -errno;
    }

    char buf[MAX_ARRAY_LEN];
    long long offset = 0;
    int bytes_read = 0;

    while (offset < statbuf.st_size)
    {
        size_t to_read = std::min((long long)MAX_ARRAY_LEN, statbuf.st_size - offset);

        // RPC call to read from server
        int ARG_COUNT = 6;
        void *args[ARG_COUNT];
        int arg_types[ARG_COUNT + 1];

        int pathlen = strlen(server_path) + 1;
        arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
        args[0] = (void *)server_path;

        arg_types[1] = (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)to_read;
        args[1] = (void *)buf;

        arg_types[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[2] = (void *)&to_read;

        arg_types[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[3] = (void *)&offset;

        arg_types[4] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)sizeof(struct fuse_file_info);
        args[4] = (void *)fi;

        arg_types[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        int ret_bytes;
        args[5] = (void *)&ret_bytes;

        arg_types[6] = 0;

        int rpc_ret = rpcCall((char *)"read", arg_types, args);
        if (rpc_ret < 0)
        {
            close(local_fd);
            rpc_unlock(server_path, 0);
            return -EINVAL;
        }
        if (ret_bytes < 0)
        {
            close(local_fd);
            rpc_unlock(server_path, 0);
            return ret_bytes;
        }
        bytes_read = ret_bytes;

        if (bytes_read < 0)
        {
            close(local_fd);
            rpc_unlock(server_path, 0);
            return bytes_read;
        }

        // 5. Write the chunk to the local disk
        if (write(local_fd, buf, bytes_read) < bytes_read)
        {
            close(local_fd);
            rpc_unlock(server_path, 0);
            return -errno;
        }

        offset += bytes_read;
    }

    close(local_fd);

    // Align local mtime with server's mtime for freshness comparisons.
    struct utimbuf ut;
    ut.actime = statbuf.st_atime;
    ut.modtime = statbuf.st_mtime;
    utime(cache_path, &ut);

    // Release READ lock.
    rpc_unlock(server_path, 0);

    return 0;
}

int upload_file(void *userdata, const char *cache_path, const char *server_path, struct fuse_file_info *fi)
{
    struct stat statbuf;
    if (stat(cache_path, &statbuf) < 0)
        return -errno;

    // Acquire WRITE lock for atomic transfer.
    rpc_lock(server_path, 1); // 1 = RW_WRITE_LOCK

    // Truncate server file first to handle case where local file is smaller.
    rpc_truncate(userdata, server_path, 0);

    char buf[MAX_ARRAY_LEN];
    long long offset = 0;
    int bytes_written = 0;

    while (offset < statbuf.st_size)
    {
        size_t to_write = std::min((long long)MAX_ARRAY_LEN, statbuf.st_size - offset);

        int local_fd = open(cache_path, O_RDONLY);
        if (local_fd < 0)
        {
            rpc_unlock(server_path, 1);
            return -errno;
        }

        if (pread(local_fd, buf, to_write, offset) < 0)
        {
            close(local_fd);
            rpc_unlock(server_path, 1);
            return -errno;
        }
        close(local_fd);

        // RPC call to write to server
        int ARG_COUNT = 6;
        void *args[ARG_COUNT];
        int arg_types[ARG_COUNT + 1];

        int pathlen = strlen(server_path) + 1;
        arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
        args[0] = (void *)server_path;

        arg_types[1] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)to_write;
        args[1] = (void *)buf;

        arg_types[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[2] = (void *)&to_write;

        arg_types[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[3] = (void *)&offset;

        arg_types[4] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)sizeof(struct fuse_file_info);
        args[4] = (void *)fi;

        arg_types[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        int ret_bytes;
        args[5] = (void *)&ret_bytes;

        arg_types[6] = 0;

        int rpc_ret = rpcCall((char *)"write", arg_types, args);
        if (rpc_ret < 0)
        {
            rpc_unlock(server_path, 1);
            return -EINVAL;
        }
        if (ret_bytes < 0)
        {
            rpc_unlock(server_path, 1);
            return ret_bytes;
        }
        bytes_written = ret_bytes;

        offset += bytes_written;
    }

    // Preserve local file timestamps on the server (for utimens compatibility)
    struct stat local_stat;
    if (stat(cache_path, &local_stat) == 0)
    {
        // Use server-side utimensat to set the server file's timestamps
        // to match what the local cache file has (which includes utimens changes)
        struct timespec ts[2];
        ts[0].tv_sec = local_stat.st_atime;
        ts[0].tv_nsec = 0;
        ts[1].tv_sec = local_stat.st_mtime;
        ts[1].tv_nsec = 0;

        // RPC call to set server timestamps
        int ARG_COUNT = 3;
        void *args[ARG_COUNT];
        int arg_types[ARG_COUNT + 1];

        int pathlen = strlen(server_path) + 1;
        arg_types[0] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
        args[0] = (void *)server_path;

        arg_types[1] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)(sizeof(struct timespec) * 2);
        args[1] = (void *)ts;

        arg_types[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        int ret_utimens;
        args[2] = (void *)&ret_utimens;
        arg_types[3] = 0;

        rpcCall((char *)"utimensat", arg_types, args);
    }

    // Release WRITE lock.
    rpc_unlock(server_path, 1);

    return 0;
}