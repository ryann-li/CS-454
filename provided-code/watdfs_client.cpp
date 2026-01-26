//
// Starter code for CS 454/654
// You SHOULD change this file
//

#include "watdfs_client.h"
#include "debug.h"
INIT_LOG

#include "rpc.h"
#include <iostream>

// SETUP AND TEARDOWN
void *watdfs_cli_init(struct fuse_conn_info *conn, const char *path_to_cache,
                      time_t cache_interval, int *ret_code)
{
    // TODO: set up the RPC library by calling `rpcClientInit`.
    std::cout << "Initializing RPC Client..." << std::endl;

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

    // TODO Initialize any global state that you require for the assignment and return it.
    // The value that you return here will be passed as userdata in other functions.
    // In A1, you might not need it, so you can return `nullptr`.
    void *userdata = nullptr;

    // TODO: save `path_to_cache` and `cache_interval` (for A3).

    // TODO: set `ret_code` to 0 if everything above succeeded else some appropriate
    // non-zero value.
    *ret_code = 0;
    // Return pointer to global state data.
    return userdata;
}

void watdfs_cli_destroy(void *userdata)
{
    // TODO: clean up your userdata state.
    // TODO: tear down the RPC library by calling `rpcClientDestroy`.
    rpcClientDestroy();
}

// GET FILE ATTRIBUTES
int watdfs_cli_getattr(void *userdata, const char *path, struct stat *statbuf)
{
    // SET UP THE RPC CALL
    DLOG("watdfs_cli_getattr called for '%s'", path);
    std::cout << "watdfs_cli_getattr called for '" << path << "'" << std::endl;

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

    std::cout << "watdfs_cli_getattr RPC call returned " << rpc_ret << ", server ret code: " << ret << std::endl;

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

    return fxn_ret;
}

int watdfs_cli_open(void *userdata, const char *path,
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

int watdfs_cli_release(void *userdata, const char *path,
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

// READ AND WRITE DATA
int watdfs_cli_read(void *userdata, const char *path, char *buf, size_t size,
                    off_t offset, struct fuse_file_info *fi)
{
    // Read size amount of data at offset of file into buf.

    // Remember that size may be greater than the maximum array size of the RPC
    // library.
    int ARG_COUNT = 6;

    void **args = new void *[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;

    // 0,4 STAYS SAME EVERYTIME
    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[4] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
                   (uint)sizeof(struct fuse_file_info);
    args[4] = (void *)fi;

    off_t curr_offset = offset;

    unsigned long max_chunk_size = MAX_ARRAY_LEN;
    unsigned long to_read = size;
    int total = 0;

    while (to_read > 0)
    {
        unsigned long this_chunk_size =
            (to_read > max_chunk_size) ? max_chunk_size : to_read;

        char *chunk_buf = buf + (size - to_read);
        arg_types[1] =
            (1u << ARG_OUTPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)this_chunk_size;
        args[1] = (void *)chunk_buf;

        arg_types[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[2] = (void *)&this_chunk_size;

        arg_types[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[3] = (void *)&curr_offset;

        arg_types[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        int ret_bytes;
        args[5] = (void *)&ret_bytes;

        arg_types[6] = 0;

        int rpc_ret = rpcCall((char *)"read", arg_types, args);
        if (rpc_ret < 0)
        {
            delete[] args;

            return -EINVAL;
        }
        if (ret_bytes < 0)
        {
            delete[] args;

            return ret_bytes;
        }
        total += ret_bytes;
        if (ret_bytes < this_chunk_size)
        {
            // We have read less than we wanted, so we are done.
            break;
        }
        to_read -= this_chunk_size;
        curr_offset += this_chunk_size;
    }

    delete[] args;

    return total;
}

int watdfs_cli_write(void *userdata, const char *path, const char *buf,
                     size_t size, off_t offset, struct fuse_file_info *fi)
{
    // Write size amount of data at offset of file from buf.

    // Remember that size may be greater than the maximum array size of the RPC
    // library.

    int ARG_COUNT = 6;

    void **args = new void *[ARG_COUNT];
    int arg_types[ARG_COUNT + 1];

    int pathlen = strlen(path) + 1;

    arg_types[0] =
        (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) | (uint)pathlen;
    args[0] = (void *)path;

    arg_types[4] = (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
                   (uint)sizeof(struct fuse_file_info);
    args[4] = (void *)fi;

    off_t curr_offset = offset;

    unsigned long max_chunk_size = MAX_ARRAY_LEN;
    unsigned long to_write = size;
    int total = 0;

    while (to_write > 0)
    {
        unsigned long this_chunk_size =
            (to_write > max_chunk_size) ? max_chunk_size : to_write;

        const char *chunk_buf = buf + (size - to_write);
        arg_types[1] =
            (1u << ARG_INPUT) | (1u << ARG_ARRAY) | (ARG_CHAR << 16u) |
            (uint)this_chunk_size;
        args[1] = (void *)chunk_buf;

        arg_types[2] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[2] = (void *)&this_chunk_size;

        arg_types[3] = (1u << ARG_INPUT) | (ARG_LONG << 16u);
        args[3] = (void *)&curr_offset;

        arg_types[5] = (1u << ARG_OUTPUT) | (ARG_INT << 16u);
        int ret_bytes;
        args[5] = (void *)&ret_bytes;

        arg_types[6] = 0;

        int rpc_ret = rpcCall((char *)"write", arg_types, args);
        if (rpc_ret < 0)
        {
            delete[] args;

            return -EINVAL;
        }
        if (ret_bytes < 0)
        {
            delete[] args;

            return ret_bytes;
        }
        total += ret_bytes;
        if (ret_bytes < this_chunk_size)
        {
            // We have written less than we wanted, so we are done.
            break;
        }
        to_write -= this_chunk_size;
        curr_offset += this_chunk_size;
    }

    delete[] args;

    return total;
}
int watdfs_cli_truncate(void *userdata, const char *path, off_t newsize)
{
    // Change the file size to newsize.
    return -ENOSYS;
}

int watdfs_cli_fsync(void *userdata, const char *path,
                     struct fuse_file_info *fi)
{
    // Force a flush of file data.
    return -ENOSYS;
}

// CHANGE METADATA
int watdfs_cli_utimensat(void *userdata, const char *path,
                         const struct timespec ts[2])
{
    // Change file access and modification times.
    return -ENOSYS;
}
