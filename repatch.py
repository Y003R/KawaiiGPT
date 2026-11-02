#!/usr/bin/env python3
"""Re-apply this fork's patches after the upstream author updates kawai.py.

kawai.py upstream is a 2.4k-line monolith, so the fork is carried as a three-way
merge instead of loose edits:

    patches/base.py     upstream kawai.py exactly as it was when we forked
    patches/patched.py  our patched kawai.py
    kawai.py            what is on disk right now

    python repatch.py           runs after a pull (git hook does it for you)
    python repatch.py --check    exit code only: 0 clean/done, 1 needs a human

If upstream is untouched since the fork we just restore our copy. If upstream
moved we run `git merge-file` from the stored base, so their new code and our
patches both survive; what cannot be merged automatically is left as normal
conflict markers plus a .rej-style report, and the file still imports.

Never edit kawai.py by hand and forget this: run `python repatch.py --save`
afterwards so the next update merges against your newest work.
"""

import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(HERE, "kawai.py")
BASE = os.path.join(HERE, "patches", "base.py")
PATCHED = os.path.join(HERE, "patches", "patched.py")


def digest(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def read(path):
    with open(path, "r", newline="") as fh:
        return fh.read()


def rel(path):
    return os.path.relpath(path, HERE)


def write(path, text):
    with open(path, "w", newline="\n") as fh:
        fh.write(text)


def main():
    if not (os.path.exists(BASE) and os.path.exists(PATCHED)):
        print("[repatch] patches/base.py or patches/patched.py missing - nothing to do")
        return 0

    if "--save" in sys.argv:
        write(PATCHED, read(LIVE))
        print("[repatch] saved current kawai.py as the new patched baseline")
        return 0

    live, base, patched = read(LIVE), read(BASE), read(PATCHED)
    if live == patched:
        print("[repatch] kawai.py already carries the patches")
        return 0
    if live == base:
        write(LIVE, patched)
        print("[repatch] upstream unchanged - patches restored")
        return 0

    # upstream moved: three-way merge, keep their work and ours.
    # relative paths + cwd keeps argv small (absolute paths tripped E2BIG),
    # -p prints the merge instead of editing in place.
    p = subprocess.run(["git", "merge-file", "-p",
                        "-L", "upstream-new", "-L", "fork-base", "-L", "our-patches",
                        rel(LIVE), rel(BASE), rel(PATCHED)],
                       capture_output=True, text=True, cwd=HERE)
    merged = p.stdout
    if not merged.strip():
        print("[repatch] merge produced nothing - kawai.py left as upstream had it")
        return 1
    write(LIVE, merged)
    conflicts = merged.count("<<<<<<<")
    if p.returncode == 0:
        print("[repatch] upstream changed - merged cleanly, our patches kept")
        return 0
    print(f"[repatch] MERGED WITH {conflicts} CONFLICT(S) - resolve in kawai.py, "
          f"look for <<<<<<< markers")
    print("[repatch] when done: python repatch.py --save")
    return 1


if __name__ == "__main__":
    code = main()
    if "--check" in sys.argv:
        sys.exit(code)
    sys.exit(0 if code == 0 or "--save" in sys.argv else code)
