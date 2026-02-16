//
// Starter code for CS 454/654
// You SHOULD change this file
//

#include "rpc.h"
#include "debug.h"
INIT_LOG

#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <cstring>
#include <cstdlib>
#include <iostream>
#include <fuse.h>
#define PRINT_ERR

// Global state server_persist_dir.
char *server_persist_dir = nullptr;

// Important: the server needs to handle multiple concurrent client requests.
// You have to be careful in handling global variables, especially for updating them.
// Hint: use locks before you update any global variable.

// We need to operate on the path relative to the server_persist_dir.
// This function returns a path that appends the given short path to the
// server_persist_dir. The character array is allocated on the heap, therefore
// it should be freed after use.
// Tip: update this function to return a unique_ptr for automatic memory management.
char *get_full_path(char *short_path)
{
    int short_path_len = strlen(short_path);
    int dir_len = strlen(server_persist_dir);
    int full_len = dir_len + short_path_len + 1;

    char *full_path = (char *)malloc(full_len);

    // First fill in the directory.
    strcpy(full_path, server_persist_dir);
    // Then append the path.
    strcat(full_path, short_path);
    DLOG("Full path: %s\n", full_path);

    return full_path;
}

// The server implementation of getattr.
int watdfs_getattr(int *argTypes, void **args)
{
    // Get the arguments.
    // The first argument is the path relative to the mountpoint.
    char *short_path = (char *)args[0];
    // The second argument is the stat structure, which should be filled in
    // by this function.
    struct stat *statbuf = (struct stat *)args[1];
    // The third argument is the return code, which should be set be 0 or -errno.
    int *ret = (int *)args[2];

    // Get the local file name, so we call our helper function which appends
    // the server_persist_dir to the given path.
    char *full_path = get_full_path(short_path);

    // Initially we set the return code to be 0.
    *ret = 0;

    // TODO: Make the stat system call, which is the corresponding system call needed
    // to support getattr. You should use the statbuf as an argument to the stat system call.
    (void)statbuf;
    int sys_ret = stat(full_path, statbuf);

    // Let sys_ret be the return code from the stat system call.

    if (sys_ret < 0)
    {
        // If there is an error on the system call, then the return code should
        // be -errno.
        *ret = -errno;
    }

    // Clean up the full path, it was allocated on the heap.
    free(full_path);

    // DLOG("Returning code: %d", *ret);
    //  The RPC call succeeded, so return 0.
    return 0;
}

int watdfs_mknod(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    mode_t *mode = (mode_t *)args[1];
    dev_t *dev = (dev_t *)args[2];
    int *ret = (int *)args[3];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = mknod(full_path, *mode, *dev);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }

    free(full_path);
    return 0;
}

int watdfs_open(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    struct fuse_file_info *fi = (struct fuse_file_info *)args[1];
    int *ret = (int *)args[2];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = open(full_path, fi->flags);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }
    else
    {
        fi->fh = sys_ret;
    }

    free(full_path);
    return 0;
}

int watdfs_release(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    struct fuse_file_info *fi = (struct fuse_file_info *)args[1];
    int *ret = (int *)args[2];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = close(fi->fh);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }

    free(full_path);
    return 0;
}

int watdfs_read(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    char *buf = (char *)args[1];
    size_t size = *(size_t *)args[2];
    off_t offset = *(off_t *)args[3];
    struct fuse_file_info *fi = (struct fuse_file_info *)args[4];
    int *ret = (int *)args[5];

    char *full_path = get_full_path(short_path);

    ssize_t bytes = pread(fi->fh, buf, size, offset);
    if (bytes < 0)
        *ret = -errno;
    else
        *ret = (int)bytes;

    free(full_path);
    return 0;
}

int watdfs_write(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    const char *buf = (const char *)args[1];
    size_t size = *(size_t *)args[2];
    off_t offset = *(off_t *)args[3];
    struct fuse_file_info *fi = (struct fuse_file_info *)args[4];
    int *ret = (int *)args[5];

    char *full_path = get_full_path(short_path);

    ssize_t bytes = pwrite(fi->fh, buf, size, offset);
    if (bytes < 0)
        *ret = -errno;
    else
        *ret = (int)bytes;

    free(full_path);
    return 0;
}

int watdfs_truncate(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    off_t size = *(off_t *)args[1];
    int *ret = (int *)args[2];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = truncate(full_path, size);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }

    free(full_path);
    return 0;
}

int watdfs_fsync(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    struct fuse_file_info *fi = (struct fuse_file_info *)args[1];
    int *ret = (int *)args[2];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = fsync(fi->fh);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }

    free(full_path);
    return 0;
}

int watdfs_utimensat(int *argTypes, void **args)
{
    char *short_path = (char *)args[0];
    struct timespec *tv = (struct timespec *)args[1];
    int *ret = (int *)args[2];

    char *full_path = get_full_path(short_path);

    *ret = 0;

    int sys_ret = utimensat(AT_FDCWD, full_path, tv, 0);
    if (sys_ret < 0)
    {
        *ret = -errno;
    }

    free(full_path);
    return 0;
}

// The main function of the server.
int main(int argc, char *argv[])
{
    // argv[1] should contain the directory where you should store data on the
    // server. If it is not present it is an error, that we cannot recover from.
    if (argc != 2)
    {
// In general, you shouldn't print to stderr or stdout, but it may be
// helpful here for debugging. Important: Make sure you turn off logging
// prior to submission!
// See watdfs_client.cpp for more details
#ifdef PRINT_ERR
        std::cerr << "Usage:" << argv[0] << " server_persist_dir\n";
#endif
        return -1;
    }
    // Store the directory in a global variable.
    server_persist_dir = argv[1];

    // TODO: Initialize the rpc library by calling `rpcServerInit`.
    // Important: `rpcServerInit` prints the 'export SERVER_ADDRESS' and
    // 'export SERVER_PORT' lines. Make sure you *do not* print anything
    // to *stdout* before calling `rpcServerInit`.
    // DLOG("Initializing server...");
    rpcServerInit();

    int ret = 0;
    // TODO: If there is an error with `rpcServerInit`, it maybe useful to have
    // debug-printing here, and then you should return.

    // TODO: Register your functions with the RPC library.
    // Note: The braces are used to limit the scope of `argTypes`, so that you can
    // reuse the variable for multiple registrations. Another way could be to
    // remove the braces and use `argTypes0`, `argTypes1`, etc.
    {
        // There are 3 args for the function (see watdfs_client.cpp for more
        // detail).
        int argTypes[4];
        // First is the path.
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        // The second argument is the statbuf.
        argTypes[1] =
            (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)sizeof(struct stat);

        // The third argument is the retcode.
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        // Finally we fill in the null terminator.
        argTypes[3] = 0;

        // We need to register the function with the types and the name.
        ret = rpcRegister((char *)"getattr", argTypes, watdfs_getattr);
        if (ret < 0)
        {
            // It may be useful to have debug-printing here.
            std::cerr << "Error registering getattr, return code: " << ret << std::endl;
            return ret;
        }
    }

    {
        // detail).
        int argTypes[5];
        // First is the path.
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        // The second argument is the mode.
        argTypes[1] = (1u << ARG_INPUT) | (ARG_INT << 16u);
        // The third argument is the dev.
        argTypes[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        // The fourth argument is the retcode.
        argTypes[3] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        // Finally we fill in the null terminator.
        argTypes[4] = 0;

        // We need to register the function with the types and the name.
        ret = rpcRegister((char *)"mknod", argTypes, watdfs_mknod);
        if (ret < 0)
        {
            // It may be useful to have debug-printing here.
            std::cerr << "Error registering mknod, return code: " << ret << std::endl;
            return ret;
        }
    }

    {
        // detail).
        int argTypes[4];
        // First is the path.
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        // The second argument is the fuse_file_info.
        argTypes[1] =
            (1u << ARG_INPUT) | (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) |
            (ARG_CHAR << 16u) | (uint)sizeof(struct fuse_file_info);

        // The third argument is the retcode.
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        // Finally we fill in the null terminator.
        argTypes[3] = 0;

        // We need to register the function with the types and the name.
        ret = rpcRegister((char *)"open", argTypes, watdfs_open);
        if (ret < 0)
        {
            // It may be useful to have debug-printing here.
            std::cerr << "Error registering open, return code: " << ret << std::endl;
            return ret;
        }
    }

    {
        // detail).
        int argTypes[4];
        // First is the path.
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        // The second argument is the fuse_file_info.
        argTypes[1] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)sizeof(struct fuse_file_info);

        // The third argument is the retcode.
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        // Finally we fill in the null terminator.
        argTypes[3] = 0;

        // We need to register the function with the types and the name.
        ret = rpcRegister((char *)"release", argTypes, watdfs_release);
        if (ret < 0)
        {
            // It may be useful to have debug-printing here.
            std::cerr << "Error registering release, return code: " << ret << std::endl;
            return ret;
        }
    }

    {
        // detail).
        int argTypes[7];
        // First is the path.
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        // The second argument is the buffer.
        argTypes[1] =
            (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)MAX_ARRAY_LEN;
        // The third argument is the size.
        argTypes[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        // The fourth argument is the offset.
        argTypes[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        // The fifth argument is the fuse_file_info.
        argTypes[4] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)sizeof(struct fuse_file_info);
        // The sixth argument is the retcode.
        argTypes[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        argTypes[6] = 0;

        ret = rpcRegister((char *)"read", argTypes, watdfs_read);
        if (ret < 0)
        {
            // It may be useful to have debug-printing here.
            std::cerr << "Error registering read, return code: " << ret << std::endl;
            return ret;
        }
    }

    {
        int argTypes[7];
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        argTypes[1] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)MAX_ARRAY_LEN;
        argTypes[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        argTypes[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        argTypes[4] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)sizeof(struct fuse_file_info);
        argTypes[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        argTypes[6] = 0;
        ret = rpcRegister((char *)"write", argTypes, watdfs_write);
        if (ret < 0)
        {
            std::cerr << "Error registering write, return code: " << ret << std::endl;
            return ret;
        }
    }
    {
        int argTypes[4];
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        argTypes[1] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        argTypes[3] = 0;
        ret = rpcRegister((char *)"truncate", argTypes, watdfs_truncate);
        if (ret < 0)
        {
            std::cerr << "Error registering truncate, return code: " << ret << std::endl;
            return ret;
        }
    }
    {
        int argTypes[4];
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        argTypes[1] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)sizeof(struct fuse_file_info);
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        argTypes[3] = 0;
        ret = rpcRegister((char *)"fsync", argTypes, watdfs_fsync);
        if (ret < 0)
        {
            std::cerr << "Error registering fsync, return code: " << ret << std::endl;
            return ret;
        }
    }
    {
        int argTypes[4];
        argTypes[0] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | 1u;
        argTypes[1] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)(2 * sizeof(struct timespec));
        argTypes[2] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        argTypes[3] = 0;
        ret = rpcRegister((char *)"utimensat", argTypes, watdfs_utimensat);
        if (ret < 0)
        {
            std::cerr << "Error registering utimensat, return code: " << ret << std::endl;
            return ret;
        }
    }

    // TODO: Hand over control to the RPC library by calling `rpcExecute`.
    ret = rpcExecute();

    // rpcExecute could fail, so you may want to have debug-printing here, and
    // then you should return.
    return ret;
}
