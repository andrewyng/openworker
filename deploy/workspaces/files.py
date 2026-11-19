"""One bounded file operation. Invoked inside a workspace pod via Kubernetes exec."""
import contextlib
import json
import os
import stat
import sys
import uuid

MAX_BYTES = 128_000
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def parts(path):
    if not isinstance(path, str) or len(path.encode()) > 512:
        raise ValueError()
    result = path.split("/")
    if len(result) > 64 or any(
        part in ("", ".", "..") or part.startswith(".ow-tmp-")
        or "\\" in part or ":" in part or any(ord(c) < 32 for c in part)
        for part in result
    ):
        raise ValueError()
    return result


@contextlib.contextmanager
def parent(root, path, create=False):
    directory = os.dup(root)
    try:
        *parents, name = parts(path)
        for part in parents:
            if create:
                try:
                    os.mkdir(part, mode=0o770, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, DIRECTORY, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, name
    finally:
        os.close(directory)


def read(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ValueError()
        data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError()
        return data.decode("utf-8")


def write(directory, name, text):
    data = text.encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError()
    try:
        if not stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
            raise ValueError()
    except FileNotFoundError:
        pass
    temp = ".ow-tmp-" + uuid.uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o660, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temp, dir_fd=directory)
        except FileNotFoundError:
            pass


def list_files(root):
    files = []
    entries = 0

    def walk(directory, prefix, depth):
        nonlocal entries
        if depth > 64:
            raise ValueError()
        with os.scandir(directory) as iterator:
            for entry in iterator:
                entries += 1
                if entries > 2048:
                    raise ValueError()
                if entry.name.startswith(".ow-tmp-"):
                    continue
                path = prefix + entry.name
                if entry.is_dir(follow_symlinks=False):
                    child = os.open(entry.name, DIRECTORY, dir_fd=directory)
                    try:
                        walk(child, path + "/", depth + 1)
                    finally:
                        os.close(child)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
    walk(root, "", 0)
    if len(files) > 1000 or sum(len(p.encode()) for p in files) > MAX_BYTES:
        raise ValueError()
    return {"files": sorted(files)}


def run(request, root):
    if request["workspace"] != os.environ["WORKSPACE_ID"]:
        raise ValueError()
    op = request["operation"]
    args = op["arguments"]
    if op["name"] == "list_files":
        return list_files(root)
    with parent(root, args["path"], create=op["name"] == "write_file") as (directory, name):
        if op["name"] == "read_file":
            return {"text": read(directory, name), "path": args["path"]}
        if op["name"] == "write_file":
            write(directory, name, args["content"])
            return {"written": True}
        if op["name"] == "edit_file":
            text = read(directory, name)
            old = args["old_text"]
            if not old or text.count(old) != 1:
                raise ValueError()
            write(directory, name, text.replace(old, args["new_text"], 1))
            return {"edited": True}
        raise ValueError()


if __name__ == "__main__":
    try:
        line = sys.stdin.buffer.readline(1_048_577)
        if len(line) > 1_048_576:
            raise ValueError()
        root_fd = os.open("/data", DIRECTORY)
        try:
            print(json.dumps({"result": run(json.loads(line), root_fd)}, ensure_ascii=False))
        finally:
            os.close(root_fd)
    except (OSError, KeyError, TypeError, ValueError):
        print('{"error":"invalid_workspace_request"}')
