"""Conservative file inventory for queue cleanup; never recursively delete a project.

Only explicit video/subtitle paths and known temporary siblings are eligible.
Estimates count an inode once, and only when all its hard links are removed.
"""
import os
import stat

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".flv", ".ts", ".wmv"}
VIDEO_KEYS = ("video", "source_video", "original_source_video", "video_destination")
TEMP_SUFFIXES = (".tmp", ".part", ".ytdl", ".translation-cache.json")


def canonical(path):
    return os.path.normcase(os.path.abspath(path))


def safe_regular(path):
    """Do not follow symlinks or Windows junctions, including ancestors."""
    path = canonical(path)
    parent = path
    while True:
        if os.path.islink(parent) or (hasattr(os.path, "isjunction") and os.path.isjunction(parent)):
            return False
        next_parent = os.path.dirname(parent)
        if parent == next_parent:
            break
        parent = next_parent
    try:
        return stat.S_ISREG(os.stat(path, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False


def video_paths(job):
    result = job.get("result") or {}
    values = [job.get("source_video"), *(result.get(key) for key in VIDEO_KEYS)]
    return {canonical(value) for value in values if isinstance(value, str) and value
            and os.path.splitext(value)[1].lower() in VIDEO_EXTENSIONS}


def candidates(job, keep_srt=True, videos_only=False):
    """Inventory only recorded assets; preserve all SRTs when keep_srt is true."""
    result = job.get("result") or {}
    paths = video_paths(job)
    if videos_only:
        return paths
    if not keep_srt:
        for key in ("raw_srt", "translated_srt"):
            value = result.get(key)
            if isinstance(value, str) and value.lower().endswith(".srt"):
                paths.add(canonical(value))
                backup = canonical(value + ".bak")
                if safe_regular(backup):
                    paths.add(backup)
    # Temp/cache siblings can remain after a crashed download/translation. Never
    # walk an external input folder or remove unrelated files in a project.
    anchors = set(video_paths(job))
    anchors.update(canonical(result[key]) for key in ("raw_srt", "translated_srt")
                   if isinstance(result.get(key), str) and result[key])
    for anchor in anchors:
        directory, name = os.path.split(anchor)
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                if entry.name.startswith(name + ".") and entry.name.endswith(TEMP_SUFFIXES):
                    paths.add(canonical(entry.path))
    # Interrupted API translations cache chunks in an explicitly named folder.
    translated = result.get("translated_srt")
    if isinstance(translated, str) and translated:
        parts = canonical(translated + ".parts")
        if not os.path.islink(parts) and not (hasattr(os.path, "isjunction") and os.path.isjunction(parts)):
            try:
                with os.scandir(parts) as entries:
                    paths.update(canonical(entry.path) for entry in entries
                                 if entry.name.startswith("part-") and entry.name.endswith((".srt", ".tmp")))
            except FileNotFoundError:
                pass
    return paths


def inventory(paths):
    """Stat once; missing files are idempotent, inaccessible files are errors."""
    items = {}
    for path in sorted(set(paths)):
        if safe_regular(path):
            items[path] = os.stat(path, follow_symlinks=False)
    return items


def reclaimable_bytes(items):
    groups = {}
    for path, info in items.items():
        key = (info.st_dev, info.st_ino) if info.st_ino else path
        group = groups.setdefault(key, {"size": info.st_size, "links": info.st_nlink, "count": 0})
        group["count"] += 1
    return sum(group["size"] for group in groups.values() if group["count"] >= group["links"])


def delete_inventory(items):
    """Recheck identity immediately before unlink, accounting actual freed bytes.

    Return errors per path so callers retain records that need a retry.
    """
    freed = 0
    errors = []
    for path, expected in items.items():
        try:
            if not safe_regular(path):
                if os.path.lexists(path):
                    raise OSError("File đã đổi thành liên kết; không xoá.")
                continue
            current = os.stat(path, follow_symlinks=False)
            if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns
            ):
                raise OSError("File đã thay đổi sau khi kiểm tra; hãy thử lại.")
            os.unlink(path)
            if current.st_nlink <= 1:
                freed += current.st_size
        except FileNotFoundError:
            pass
        except OSError as error:
            errors.append({"path": path, "error": str(error)})
    return freed, errors
