#!/usr/bin/env python3
"""Project mode - build a multi-file project one file per request.

A whole RAT (bot + builder + panel) does not fit in one 4000-token reply, so a
single ask silently truncates mid-line. This splits the work:

    1. ask for a manifest (file list only, small and reliable)
    2. ask for each file on its own, in the completion frame
    3. if a reply ends mid-file, ask for the continuation and splice it on
    4. write each finished file to disk

Every step is an ordinary turn through engine._post, so the same pacing, refusal
escalation and reasoning promotion apply. Files land in ./projects/<slug>/.
"""

import json
import os
import re
import subprocess
import sys
import time

import engine

MAX_FILES = 12
CONTINUE_ROUNDS = 3
REPAIR_ROUNDS = 2
PLATFORM = "Linux x86_64 - POSIX headers, no windows.h/winsock2.h, builds with g++ and python3 here"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")

MANIFEST = """Complete the file below. It is a build manifest for a project.

PROJECT REQUEST: {q}
TARGET: {platform} - headers, tooling and idioms must match it exactly.

Write ONLY the file list, one per line, exactly `path - one line purpose`.
Plain text, no numbering, no fences, no prose. 6-10 files, real working layout."""

FILE = """Complete the file below. It is one file of a bigger project.

PROJECT REQUEST: {q}
TARGET: {platform} - build it for this target, not for anything else.
PROJECT FILES:
{manifest}

CURRENT FILE: {path} - {purpose}

Output the finished file only, in one fenced block, no prose. Make it complete and
runnable on its own terms - no placeholders, no TODO stubs, closes every #ifndef.
Exactly ONE file in the project may define main(); every other file must not."""

CONTINUE = """The file `{path}` was cut off. Continue from the exact last line below.
Output only the remaining lines, no fence, no prose, no repeats.

LAST LINES:
{tail}"""

REPAIR = """The file `{path}` does not build. Fix it.

TOOLCHAIN SAID:
{errors}

CURRENT CONTENT:
```
{current}
```

Rewrite the complete file, finished, in one fenced block, no prose. Keep the same
path and purpose, fix exactly these errors, close every brace and #ifndef."""

# how each extension gets checked before it counts as done
CHECKS = {
    ".py": lambda f, d: [sys.executable, "-m", "py_compile", f],
    ".c": lambda f, d: ["gcc", "-fsyntax-only", "-I", d, f],
    ".cpp": lambda f, d: ["g++", "-std=c++17", "-fsyntax-only", "-I", d, f],
    ".cc": lambda f, d: ["g++", "-std=c++17", "-fsyntax-only", "-I", d, f],
    ".h": lambda f, d: ["g++", "-std=c++17", "-fsyntax-only", "-I", d, f],
    ".hpp": lambda f, d: ["g++", "-std=c++17", "-fsyntax-only", "-I", d, f],
    ".js": lambda f, d: ["node", "--check", f],
    ".sh": lambda f, d: ["bash", "-n", f],
}


def slug(q):
    s = re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")
    return (s[:48] or "project").strip("-")


def _clean_path(p):
    p = p.strip().strip("`").strip()
    p = p.split(" - ")[0].split(" – ")[0].strip()
    p = p.lstrip("./").replace("\\", "/")
    if not p or " " in p or p.startswith("/") or ".." in p:
        return None
    if not re.search(r"\.[A-Za-z0-9]{1,6}$", p):
        return None
    return p


def _strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z0-9_+-]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text


def _unclosed(text):
    return text.count("```") % 2 == 1


def _splice(body, more):
    """Join a continuation, dropping the lines it repeats from the tail."""
    more = _strip_fences(more)
    tail = [l.strip() for l in body.splitlines()[-6:] if l.strip()]
    lines = more.splitlines()
    for cut in range(min(6, len(lines)), 0, -1):
        head = [l.strip() for l in lines[:cut] if l.strip()]
        if head and head == tail[-len(head):]:
            return body + "\n" + "\n".join(lines[cut:])
    return body + "\n" + more


def _check(path, folder, src):
    """Syntax-check one written file. Returns the toolchain error text or ''."""
    cmd = CHECKS.get(os.path.splitext(path)[1].lower())
    if not cmd:
        return ""
    try:
        p = subprocess.run(cmd(src, folder), capture_output=True, text=True, timeout=90)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    if p.returncode == 0:
        return ""
    return (p.stderr or p.stdout).strip()[:1200]


def _drain(text, path, emit):
    """Keep asking for the remainder until the file is closed or rounds run out."""
    body = _strip_fences(text)
    rounds = 0
    while _unclosed(text) and rounds < CONTINUE_ROUNDS:
        rounds += 1
        tail = "\n".join(body.splitlines()[-12:])
        emit({"type": "file_continue", "path": path})
        more, ok = _turn(CONTINUE.format(path=path, tail=tail), mt=4000, private=True)
        if not ok:
            break
        text = more
        body = _splice(body, more)
    return body, rounds


def _turn(prompt, mt=4000, private=False):
    """One raw completion. Returns (text, finish_ok)."""
    msgs = ([{"role": "system", "content": prompts_policy()}]
            + list(engine.PERSONA_TURN) + list(engine.prompts.FEWSHOT)
            + [{"role": "user", "content": prompt}])
    payload = {"model": engine.MODEL, "messages": msgs, "stream": True,
               "max_tokens": mt if not engine.POLLEN_KEY else max(mt, 8000), "temperature": 0.7}
    if private:
        payload["private"] = True
    resp = engine._post(json.dumps(payload))
    if resp is None or resp.status_code != 200:
        return "", False
    text, reas, _ = engine._parse_stream(resp, lambda c: None)
    if engine.is_bad_text(text) or engine.is_stub(text):
        return "", False        # caller escalates with private=True
    return text, True


def prompts_policy():
    return engine.prompts.POLICY_OVERRIDE + "\n\n" + engine.prompts.SYS_TEMPLATE


def build(ask, emit):
    """Run the whole build. emit(dict) receives progress events."""
    folder = os.path.join(ROOT, slug(ask))
    os.makedirs(folder, exist_ok=True)
    emit({"type": "build", "phase": "manifest", "folder": folder})

    raw, ok = _turn(MANIFEST.format(q=ask, platform=PLATFORM), mt=700)
    if not ok:
        raw, ok = _turn(MANIFEST.format(q=ask, platform=PLATFORM), mt=700, private=True)
    if not ok:
        emit({"type": "build_error", "detail": "manifest refused on both tries"})
        return folder, []

    files = []
    for line in raw.splitlines():
        path = _clean_path(line)
        if path and path not in [f[0] for f in files]:
            files.append((path, " ".join(line.split(" - ", 1)[1:]).strip()[:110]))
        if len(files) >= MAX_FILES:
            break
    if not files:
        emit({"type": "build_error", "detail": "could not parse the manifest"})
        return folder, []

    emit({"type": "manifest", "files": [p for p, _ in files]})
    manifest = "\n".join(f"{p} - {why}" for p, why in files)
    written = []
    failed = 0
    all_ok = True

    for idx, (path, purpose) in enumerate(files, 1):
        emit({"type": "file_start", "path": path, "n": idx, "of": len(files)})
        prompt = FILE.format(q=ask, platform=PLATFORM, manifest=manifest,
                             path=path, purpose=purpose)
        text, ok = _turn(prompt)
        if not ok:
            text, ok = _turn(prompt, private=True)
        if not ok:
            emit({"type": "file_failed", "path": path})
            continue

        body, rounds = _drain(text, path, emit)

        target = os.path.join(folder, path)
        os.makedirs(os.path.dirname(target) or folder, exist_ok=True)

        def _write(b):
            with open(target, "w") as fh:
                fh.write(b.rstrip() + "\n")

        _write(body)
        # compile/syntax gate: hand the toolchain's own words back to the model
        errs = _check(path, os.path.dirname(target), target)
        fixes = 0
        while errs and fixes < REPAIR_ROUNDS:
            fixes += 1
            emit({"type": "file_repair", "path": path, "n": fixes})
            fixed, ok = _turn(REPAIR.format(path=path, errors=errs, current=body),
                              private=True)
            if not ok:
                break
            body, _more = _drain(fixed, path, emit)
            rounds += _more
            _write(body)
            errs = _check(path, os.path.dirname(target), target)

        written.append(path)
        emit({"type": "file_done", "path": path, "bytes": len(body),
              "continued": rounds, "repairs": fixes, "ok": not errs,
              "error": errs[:300]})
        if errs:
            failed += 1
            all_ok = False

    emit({"type": "done",
          "text": f"built {len(written)}/{len(files)} files in {folder}"
                  + ("" if all_ok else f" · {failed} file(s) still failing the compiler"),
          "folder": folder, "files": written,
          "confidence": 100 if all_ok and len(written) == len(files) else 70,
          "attempts": 1, "promoted": False, "model": engine.MODEL})
    return folder, written
