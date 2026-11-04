# -*- coding: utf-8 -*-
"""打 Windows 便携版（PyInstaller one-folder）。

    python scripts/build_release.py            # 只构建
    python scripts/build_release.py --zip      # 构建并打包成 dist/Preloader_Rw-<ver>-win64.zip

产物：`dist/Preloader_Rw/Preloader_Rw.exe`，整个目录可拷走直接运行，目标机
不需要装 Python。DA1/DA2 镜像不入包（厂商专有），由用户自行放置。

打包后 GUI 仍按原架构把壳跑在子进程里：exe 以 `--relay` 参数把自己再拉起一次，
由 `preloader_rw/__main__.py` 分派；源码运行则继续走 `python -m preloader_rw.relay`。
"""

import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "Preloader_Rw"

# 运行期按路径读取的资源：命令表、载荷、传输后端（含 dll/exe/inf）。
# 载荷是 importlib 按文件路径加载的，所以 .py 也必须以数据形式进包。
DATA_DIRS = (
    "preloader_rw/profiles",
    "preloader_rw/backends",
    "data",
)
# 载荷逐个文件收，不整个目录搬：`__pycache__` 会跟着目录进来，包不干净。
DATA_GLOBS = ("preloader_rw/payloads/*.py", "preloader_rw/payloads/*.h")
DATA_FILES = ("README.md", "LICENSE")


def build():
    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.exit("当前解释器没有 PySide6，换一个装好的：\n  %s" % sys.executable)

    argv = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--console",
        "--name", NAME,
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(ROOT, "build", "pyinstaller"),
        "--specpath", os.path.join(ROOT, "build"),
        "--collect-submodules", "preloader_rw",
    ]
    sep = ";" if os.name == "nt" else ":"
    for rel in DATA_DIRS:
        argv += ["--add-data", "%s%s%s" % (os.path.join(ROOT, rel), sep, rel)]
    for pattern in DATA_GLOBS:
        for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            argv += ["--add-data", "%s%s%s" % (path, sep, rel)]
    for rel in DATA_FILES:
        argv += ["--add-data", "%s%s%s" % (os.path.join(ROOT, rel), sep, ".")]
    argv.append(os.path.join(ROOT, "preloader_rw", "__main__.py"))

    print("PyInstaller:", " ".join(argv[3:]))
    return subprocess.call(argv, cwd=ROOT)


def make_zip(tag):
    """把 dist/<NAME>/ 压成 zip；跳过 __pycache__ 与 .pyc。

    逐个文件写而不是 make_archive：源码里残留的缓存不该跟着发布包走。
    """
    import zipfile

    src = os.path.join(ROOT, "dist", NAME)
    out = os.path.join(ROOT, "dist", "%s-%s-win64.zip" % (NAME, tag))
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for base, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = os.path.join(base, name)
                rel = os.path.join(NAME, os.path.relpath(path, src))
                zf.write(path, rel.replace(os.sep, "/"))
    print("zip:", out, "(%.1f MB)" % (os.path.getsize(out) / 1024.0 / 1024.0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="构建后打包 zip")
    ap.add_argument("--tag", default="v0.1.0", help="zip 里的版本标记")
    args = ap.parse_args()
    if build() != 0:
        return 1
    if args.zip:
        make_zip(args.tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
