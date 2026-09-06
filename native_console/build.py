"""Fetch cryptographically pinned OpenCode inputs, apply local patches, and build.

Only explicitly selected build inputs are fetched. No model, service, or device
operations are performed. Existing modified upstream files fail closed.
"""
import argparse
import concurrent.futures
import hashlib
import http.client
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def git_hash(data, kind="blob"):
    return hashlib.sha1(kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path escapes source: {relative}")
    return path


def verify_tree(entries, expected):
    directories = {"": []}
    hashes = {"": expected}
    seen = set()
    for entry in entries:
        path = PurePosixPath(entry["path"])
        if path.is_absolute() or ".." in path.parts or str(path) in seen:
            raise ValueError("Invalid or duplicated Git path")
        seen.add(str(path))
        parent = str(path.parent) if str(path.parent) != "." else ""
        directories.setdefault(parent, []).append(entry)
        if entry["type"] == "tree":
            directories.setdefault(str(path), [])
            hashes[str(path)] = entry["sha"]
    for directory, children in directories.items():
        payload = b""
        for entry in sorted(children, key=lambda e: (PurePosixPath(e["path"]).name + ("/" if e["type"] == "tree" else "")).encode()):
            mode = entry["mode"].lstrip("0")
            payload += mode.encode() + b" " + PurePosixPath(entry["path"]).name.encode() + b"\0" + bytes.fromhex(entry["sha"])
        if git_hash(payload, "tree") != hashes.get(directory):
            raise ValueError(f"Git tree verification failed: {directory}")


def read_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "MediaFlow-native-build", "Accept": "application/vnd.github+json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except (OSError, urllib.error.URLError, http.client.HTTPException):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def fetch_blob(url, expected, target):
    content = read_url(url)
    if git_hash(content) != expected:
        raise ValueError(f"Git blob verification failed: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return content


def apply_edits(content, edits):
    for edit in edits:
        if content.count(edit["old"]) != 1:
            raise ValueError("Patch context must match exactly once")
        content = content.replace(edit["old"], edit["new"], 1)
    return content


def materialize(root, relative, link):
    path = inside(root, relative)
    target = inside(root, str(PurePosixPath(relative).parent / link))
    if not target.is_file():
        raise ValueError(f"Missing symlink target: {relative}")
    content = target.read_bytes()
    if path.is_file() and path.read_bytes() not in (link.encode(), content):
        raise ValueError(f"Local materialized symlink drift: {relative}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def needed(entry, packages):
    if entry["type"] != "blob":
        return False
    parts = PurePosixPath(entry["path"]).parts
    if parts[-1] == "package.json" or len(parts) == 1 or parts[0] in {"patches", "vendor"}:
        return True
    if parts[0] != "packages" or parts[1] not in packages:
        return False
    if parts[1] == "desktop":
        return "i18n" in parts or parts[-1] == "tsconfig.json"
    if parts[1] == "opencode":
        return parts[-1] == "tsconfig.json"
    return True


def prepare(source, cache):
    pin = json.loads((HERE / "upstream.json").read_text())
    cache.mkdir(parents=True, exist_ok=True)
    tree_file = cache / "tree.json"
    if not tree_file.exists():
        tree_file.write_bytes(read_url(f"https://api.github.com/repos/{pin['repository']}/git/trees/{pin['commit']}?recursive=1"))
    tree = json.loads(tree_file.read_text())
    if tree.get("truncated") is not False:
        raise ValueError("Incomplete upstream tree")
    verify_tree(tree["tree"], pin["tree"])
    edits = json.loads((HERE / "branding.json").read_text(encoding="utf-8"))
    files = [entry for entry in tree["tree"] if needed(entry, pin["packages"])]
    links = []

    def fetch(entry):
        relative = entry["path"]
        path = inside(source, relative)
        original = cache / "blobs" / entry["sha"]
        existing = path.read_bytes() if path.is_file() else None
        if original.is_file():
            content = original.read_bytes()
            if git_hash(content) != entry["sha"]:
                raise ValueError(f"Corrupt source cache: {relative}")
        elif existing is not None and git_hash(existing) == entry["sha"]:
            content = existing
        else:
            content = fetch_blob(f"https://raw.githubusercontent.com/{pin['repository']}/{pin['commit']}/{relative}", entry["sha"], original)
        if entry["mode"] == "120000":
            links.append((relative, content.decode()))
            return
        try:
            patched = apply_edits(content.decode("utf-8"), edits[relative]).encode("utf-8") if relative in edits else content
        except ValueError as error:
            raise ValueError(f"Patch rejected for {relative}: {error}") from error
        if existing is not None and existing not in (content, patched):
            raise ValueError(f"Local source drift; preserve and inspect before rebuilding: {relative}")
        if relative in edits:
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(content)
        if existing != patched:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(patched)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(fetch, files))
    for relative, link in links:
        materialize(source, relative, link)
    for file in (HERE / "overlay").rglob("*"):
        if file.is_file():
            target = inside(source, file.relative_to(HERE / "overlay"))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
    for name in ("bootstrap.ts", "migration.ts"):
        target = source / "packages/app/src/mediaflow" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "integration" / name, target)
    for relative in ("packages/app/public/licenses/opencode-MIT.txt", "packages/app/src/mediaflow/opencode-MIT.txt"):
        target = inside(source, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / "LICENSE.opencode", target)
    shutil.copyfile(ROOT / "control_console/public/favicon.svg", source / "packages/app/public/mediaflow-icon.svg")
    print(f"Verified {len(files)} pinned upstream files; branding and integration prepared.", flush=True)
    return pin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bun", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--install", action="store_true", help="Install frozen Bun lockfile (no lifecycle scripts)")
    parser.add_argument("--check", action="store_true", help="Run native unit tests and typecheck before build")
    args = parser.parse_args()
    source = ROOT / "opencode-source"
    pin = prepare(source, ROOT / "work/native-source-cache")
    bun = str(args.bun.resolve())
    version = subprocess.check_output([bun, "--version"], text=True).strip()
    if version != pin["bun"]:
        raise ValueError(f"Expected Bun {pin['bun']}; got {version}")
    if args.prepare_only:
        return
    if args.install:
        subprocess.run([bun, "install", "--frozen-lockfile", "--ignore-scripts"], cwd=source, check=True)
    app = source / "packages/app"
    if args.check:
        subprocess.run([bun, "run", "test:unit"], cwd=app, check=True)
        subprocess.run([bun, "run", "typecheck"], cwd=app, check=True)
    subprocess.run([bun, "run", "build"], cwd=app, check=True)
    # Keep an existing published build untouched; parent/release owner publishes.
    print(f"Build ready at {app / 'dist'}; publication is a separate explicit step.")


if __name__ == "__main__":
    main()
