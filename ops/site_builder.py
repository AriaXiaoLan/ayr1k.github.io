"""Isolated build, static validation and atomic current/previous pointer sets."""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import urllib.parse
import uuid

from amber_sync import (PRODUCTS, WEBSITE, SyncError, atomic_json, confined,
                        digest, key, log, require, sync_dir, sync_tree)

NODE = "24.22.1"
PNPM = "11.25.0"
COMMIT = re.compile(r"[a-f0-9]{40}\Z")


def command(argv, cwd, env=None, timeout=300):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SyncError("BUILD_COMMAND_TIMEOUT_OR_UNAVAILABLE") from exc
    # Do not dump dependency script output: it can contain personal paths or credentials.
    if result.returncode:
        (cwd / "command-failure.log").write_bytes(result.stdout[-1024 * 1024:])
        raise SyncError("BUILD_COMMAND_FAILED")
    return result.stdout


def extract_source(data, destination):
    """Git archives only: no symlinks, hardlinks, devices, traversal or metadata paths."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        total = 0
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in member.name,
                    "SOURCE_PATH_ESCAPE")
            require(all(p not in (".git", "node_modules", "dist") for p in path.parts),
                    "UNEXPECTED_SOURCE_CONTENT")
            require(member.isdir() or member.isfile(), "SOURCE_LINK_OR_SPECIAL_FILE")
            total += member.size
            require(total < 512 * 1024 ** 2, "SOURCE_TOO_LARGE")
            target = confined(destination, str(path))
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)


def source_pins(source):
    catalog = json.loads((source / "src/download-catalog.json").read_text())
    require(set(catalog) == set(PRODUCTS), "INVALID_DOWNLOAD_CATALOG")
    pins = {product: {entry["version"]} for product, entry in catalog.items()}
    for product, entry in catalog.items():
        require(entry["repo"] == PRODUCTS[product], "CATALOG_PRODUCT_MISMATCH")
        key(entry["version"])
    # Protect literal historical release links elsewhere in the site as well.
    for path in (source / "src").rglob("*"):
        if path.is_file():
            for version in re.findall(r"(?:/v|\bv)(\d+\.\d+\.\d+)\b", path.read_text(encoding="utf-8")):
                for product in PRODUCTS:
                    pins[product].add(version)
    return {p: sorted(versions, key=key) for p, versions in pins.items()}


class References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for attribute in ("src", "href", "poster"):
            if attribute in attrs:
                self.urls.append(attrs[attribute])
        if attrs.get("srcset"):
            self.urls.extend(item.strip().split()[0] for item in attrs["srcset"].split(","))


def validate_static(directory):
    require((directory / "index.html").is_file(), "STATIC_INDEX_MISSING")
    count = 0
    for path in directory.rglob("*"):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), "STATIC_SPECIAL_FILE")
        if not path.is_file():
            continue
        count += 1
        require(count <= 10000, "TOO_MANY_STATIC_FILES")
        require(path.suffix.lower() not in (".php", ".py", ".sh", ".pem", ".key"),
                "NON_STATIC_OR_SECRET_FILE")
        require(path.relative_to(directory).parts[0] not in ("downloads", "mirror", "private", ".git"),
                "RESERVED_STATIC_PATH")
        urls = []
        if path.suffix == ".html":
            parser = References()
            parser.feed(path.read_text(encoding="utf-8"))
            urls.extend(parser.urls)
        if path.suffix in (".css", ".js"):
            text = path.read_text(encoding="utf-8")
            urls.extend(re.findall(r"url\(\s*['\"]?([^)'\"\s]+)", text))
            # Vite emitted imports and root-relative public asset references.
            urls.extend(url for url in re.findall(r"['\"]((?:\./|/)?(?:assets|themes)/[^'\"<>]+)['\"]", text)
                        if PurePosixPath(urllib.parse.urlsplit(url).path).suffix)
            if path.suffix == ".js":
                urls.extend(re.findall(r"(?:from\s*|import\s*)['\"](\./[^'\"]+)['\"]", text))
        for url in urls:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path or url.startswith("#"):
                continue
            relative = urllib.parse.unquote(parsed.path)
            target = directory / relative.lstrip("/") if relative.startswith("/") else path.parent / relative
            require(target.resolve().is_relative_to(directory.resolve()) and target.is_file() and
                    not target.is_symlink(), "STATIC_REFERENCE_MISSING")
    return count


def deployment(engine, name):
    require(COMMIT.fullmatch(name), "INVALID_DEPLOYMENT_NAME")
    path = confined(engine.root, "public/sites/" + name)
    data = json.loads((path / "_amber-deployment.json").read_text())
    require(data.get("commit") == name and set(data.get("pins", {})) == set(PRODUCTS),
            "INVALID_DEPLOYMENT_METADATA")
    for versions in data["pins"].values():
        for version in versions:
            key(version)
    return path, data


def active_pair(engine):
    active = engine.public / "active"
    if not active.is_symlink():
        require(not active.exists(), "UNMANAGED_ACTIVE_DIRECTORY")
        return None, None
    link = os.readlink(active)
    require(re.fullmatch(r"site-sets/[a-f0-9]{32}", link), "UNSAFE_ACTIVE_LINK")
    pointer_set = confined(engine.root, "public/" + link)
    result = []
    for name in ("current", "previous"):
        path = pointer_set / name
        if not path.is_symlink():
            require(not path.exists() and name == "previous", "INVALID_SITE_POINTER")
            result.append(None)
            continue
        target = os.readlink(path)
        require(re.fullmatch(r"../../sites/[a-f0-9]{40}", target), "UNSAFE_SITE_POINTER")
        commit = target.rsplit("/", 1)[1]
        deployment(engine, commit)
        result.append(commit)
    return tuple(result)


def page_pins(engine):
    pins = {p: set() for p in PRODUCTS}
    for name in active_pair(engine):
        if name:
            _, data = deployment(engine, name)
            for product, versions in data["pins"].items():
                pins[product].update(versions)
    return pins


def switch(engine, current, previous):
    require(os.name == "posix", "POSIX_ATOMIC_SYMLINKS_REQUIRED")
    for commit in (current, previous):
        if commit:
            path, _ = deployment(engine, commit)
            validate_static(path)
    # A generation contains BOTH pointers. One atomic rename commits the pair.
    generation = engine.public / "site-sets" / uuid.uuid4().hex
    generation.mkdir(mode=0o755)
    os.symlink("../../sites/" + current, generation / "current")
    if previous:
        os.symlink("../../sites/" + previous, generation / "previous")
    sync_dir(generation)
    link = engine.public / ("active-" + uuid.uuid4().hex)
    os.symlink("site-sets/" + generation.name, link)
    os.replace(link, engine.public / "active")
    sync_dir(engine.public)


def rollback(engine):
    current, previous = active_pair(engine)
    require(current and previous and current != previous, "NO_ROLLBACK_AVAILABLE")
    switch(engine, previous, current)
    # Pin prevents a scheduled run immediately undoing an operator rollback.
    engine.state["website"] = {"commit": previous, "paused": True}
    engine.save()
    log("website_rollback", commit=previous, paused=True)


def publish_site(engine, source, static, commit):
    require(COMMIT.fullmatch(commit), "INVALID_MAIN_COMMIT")
    validate_static(static)
    require(not (static / "_amber-deployment.json").exists(), "RESERVED_DEPLOYMENT_METADATA")
    current, _ = active_pair(engine)
    destination = confined(engine.root, "public/sites/" + commit)
    require(not destination.exists(), "DEPLOYMENT_ALREADY_EXISTS")
    engine.space(sum(p.stat().st_size for p in static.rglob("*") if p.is_file()))
    candidate = engine.private / "staging" / uuid.uuid4().hex
    shutil.copytree(static, candidate, symlinks=False)
    # Retain old hashed assets so a tab opened before the swap still works.
    if current:
        old_assets = engine.public / "sites" / current / "assets"
        if old_assets.is_dir():
            for old in old_assets.rglob("*"):
                require(not old.is_symlink(), "OLD_ASSET_SYMLINK")
                if old.is_file():
                    target = candidate / "assets" / old.relative_to(old_assets)
                    if target.exists():
                        require(digest(target) == digest(old), "STATIC_ASSET_NAME_COLLISION")
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(old, target)
    metadata = {"schema": 1, "commit": commit, "pins": source_pins(source)}
    atomic_json(candidate / "_amber-deployment.json", metadata, 0o644)
    validate_static(candidate)
    for path in candidate.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    candidate.chmod(0o755)
    sync_tree(candidate)
    os.replace(candidate, destination)
    sync_dir(destination.parent)
    switch(engine, commit, current)
    engine.state["website"] = {"commit": commit, "paused": False}
    engine.save()
    log("website_published", commit=commit, previous=current)


def sandbox_build(engine, work):
    require(os.name == "posix", "LINUX_BUILDER_REQUIRED")
    tools = Path(engine.config["tools"])
    require(tools.is_absolute() and not tools.is_symlink() and
            (tools / "node/bin/node").is_file() and (tools / "pnpm/bin/pnpm.cjs").is_file(),
            "PINNED_TOOLS_MISSING")
    # Only the disposable build tree is writable. No state, mirrors, SSH, user
    # home, certificate keys or credentials are visible to repository scripts.
    prefix = ["bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
              "--unshare-ipc", "--unshare-uts", "--unshare-cgroup", "--cap-drop", "ALL",
              "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
              "--symlink", "usr/sbin", "/sbin", "--symlink", "usr/lib", "/lib",
              "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
              "--tmpfs", "/tmp", "--dir", "/etc", "--dir", "/etc/ssl",
              "--ro-bind", "/etc/ssl/certs", "/etc/ssl/certs",
              "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
              "--ro-bind", "/etc/hosts", "/etc/hosts",
              "--ro-bind", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
              "--ro-bind", tools.as_posix(), "/tools",
              "--bind", work.as_posix(), "/work", "--chdir", "/work",
              "--clearenv", "--setenv", "PATH", "/tools/node/bin:/usr/bin:/bin",
              "--setenv", "HOME", "/work/.home", "--setenv", "CI", "true"]
    require(command(prefix + ["node", "--version"], work).decode().strip() == "v" + NODE,
            "NODE_VERSION_MISMATCH")
    pnpm = ["node", "/tools/pnpm/bin/pnpm.cjs"]
    require(command(prefix + pnpm + ["--version"], work).decode().strip() == PNPM,
            "PNPM_VERSION_MISMATCH")
    package = json.loads((work / "package.json").read_text())
    require(package.get("packageManager") == "pnpm@" + PNPM and
            (work / ".node-version").read_text().strip() == NODE,
            "REPOSITORY_TOOL_PIN_MISMATCH")
    require((work / "pnpm-lock.yaml").is_file(), "LOCKFILE_MISSING")
    lock_hash = digest(work / "pnpm-lock.yaml")
    command(prefix + pnpm + ["install", "--frozen-lockfile", "--ignore-scripts", "--store-dir", "/work/.store"],
            work, timeout=600)
    command(prefix + pnpm + ["build"], work, timeout=300)
    command(prefix + pnpm + ["test:sites"], work, timeout=120)
    command(prefix + pnpm + ["test:mirror"], work, timeout=120)
    require(digest(work / "pnpm-lock.yaml") == lock_hash, "BUILD_CHANGED_LOCKFILE")


def sync_website(engine):
    require(not (engine.state.get("website") or {}).get("paused"), "WEBSITE_PAUSED_AFTER_ROLLBACK")
    head = engine.api.json(f"/repos/{WEBSITE}/branches/main")["commit"]["sha"]
    require(COMMIT.fullmatch(head), "INVALID_MAIN_COMMIT")
    current, _ = active_pair(engine)
    if current == head:
        validate_static(engine.public / "sites" / current)
        return
    if current:
        comparison = engine.api.json(f"/repos/{WEBSITE}/compare/{current}...{head}")
        require(comparison.get("status") == "ahead", "WEBSITE_MAIN_REWIND_OR_DIVERGENCE")
    existing = engine.public / "sites" / head
    if existing.exists():
        # Recovery of a candidate whose rename completed before the pointer swap.
        path, _ = deployment(engine, head)
        validate_static(path)
        switch(engine, head, current)
        engine.state["website"] = {"commit": head, "paused": False}
        engine.save()
        return
    engine.space(engine.config.get("build_budget_bytes", 2 * 1024 ** 3))
    work = engine.private / "staging" / uuid.uuid4().hex
    work.mkdir(mode=0o700)
    repository = work / "repository"
    repository.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(work / "empty-home"), "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_GLOBAL": "/dev/null"}
    command(["git", "init", "--bare"], repository, env)
    command(["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "fetch", "--depth=1",
             "https://github.com/" + WEBSITE + ".git", head], repository, env, timeout=180)
    actual = command(["git", "rev-parse", "FETCH_HEAD"], repository, env).decode().strip()
    require(actual == head, "SOURCE_COMMIT_MISMATCH")
    archive = command(["git", "archive", "--format=tar", head], repository, env)
    source = work / "source"
    source.mkdir()
    extract_source(archive, source)
    pins = source_pins(source)
    sandbox_build(engine, source)
    require(source_pins(source) == pins, "BUILD_CHANGED_DOWNLOAD_CATALOG")
    publish_site(engine, source, source / "dist/client", head)
