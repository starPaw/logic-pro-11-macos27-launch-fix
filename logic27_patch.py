#!/usr/bin/env python3
"""
Logic Pro 11.2.2 / macOS 27 BNNS compatibility installer and verifier.

Design goals:
  * fail closed on any target mismatch;
  * never modify /Applications/Logic Pro.app;
  * parse fat Mach-O slices and patch slice-relative sites;
  * fetch the upstream compatibility adapter only from a pinned release asset,
    or accept an explicitly supplied local adapter source;
  * perform all writes in a temporary app copy, verify, sign, then rename it
    into place only after success;
  * keep a machine-readable manifest and sidecar receipt.

This program deliberately does not contain or re-publish the upstream
BNNS compatibility adapter source.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

FAT_MAGIC = 0xCAFEBABE
FAT_MAGIC_64 = 0xCAFEBABF

class PatchError(RuntimeError):
    pass

def info(message: str = "") -> None:
    print(message, flush=True)

def fail(message: str) -> "NoReturn":
    raise PatchError(message)

def run(cmd, *, capture=False, check=True) -> subprocess.CompletedProcess:
    if capture:
        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    else:
        cp = subprocess.run(cmd)
    if check and cp.returncode != 0:
        detail = ""
        if capture:
            detail = (cp.stderr or cp.stdout or "").strip()
        raise PatchError(f"Command failed ({cp.returncode}): {' '.join(map(str, cmd))}"
                         + (f"\n{detail}" if detail else ""))
    return cp

def require_command(name: str) -> str:
    found = shutil.which(name)
    if not found:
        fail(f"Required command not found: {name}")
    return found

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def load_info_plist(app: Path) -> dict:
    p = app / "Contents/Info.plist"
    if not p.is_file():
        fail(f"Info.plist not found: {p}")
    with p.open("rb") as f:
        return plistlib.load(f)

def sw_vers(flag: str) -> str:
    return run(["/usr/bin/sw_vers", flag], capture=True).stdout.strip()

def checked_system(allow_untested_os: bool) -> dict:
    if sys.platform != "darwin":
        fail("This patcher only runs on macOS.")

    version = sw_vers("-productVersion")
    build = sw_vers("-buildVersion")
    machine = platform.machine()
    expected = MANIFEST["tested_system"]

    if machine != expected["host_arch"]:
        fail(f"Tested host architecture is {expected['host_arch']}; found {machine}.")

    if (version, build) != (expected["macos_version"], expected["macos_build"]):
        if not allow_untested_os:
            fail(
                f"Untested macOS: {version} ({build}). "
                f"Validated target is {expected['macos_version']} ({expected['macos_build']}). "
                "Use --allow-untested-os only after reviewing the runtime-symbol checks."
            )

    sip = run(["/usr/bin/csrutil", "status"], capture=True, check=False)
    return {
        "macos_version": version,
        "macos_build": build,
        "host_arch": machine,
        "sip": (sip.stdout or sip.stderr).strip(),
    }

def checked_target(app: Path) -> tuple[Path, dict]:
    app = app.expanduser().resolve()
    if not app.is_dir():
        fail(f"Logic app not found: {app}")

    meta = load_info_plist(app)
    target = MANIFEST["target"]

    actual = {
        "bundle_id": meta.get("CFBundleIdentifier"),
        "version": meta.get("CFBundleShortVersionString"),
        "build": str(meta.get("CFBundleVersion", "")),
    }
    wanted = {
        "bundle_id": target["bundle_id"],
        "version": target["version"],
        "build": target["build"],
    }
    if actual != wanted:
        fail(f"Unsupported Logic build.\nExpected: {wanted}\nFound:    {actual}")

    binary = app / target["binary_rel"]
    if not binary.is_file():
        fail(f"MAMachineLearning binary not found: {binary}")
    digest = sha256_file(binary)
    if digest != target["source_sha256"]:
        fail(
            "MAMachineLearning does not match the exact validated build.\n"
            f"Expected SHA-256: {target['source_sha256']}\n"
            f"Found SHA-256:    {digest}"
        )
    return binary, actual

def parse_fat_arches(data: bytes) -> dict[int, tuple[int, int]]:
    if len(data) < 8:
        fail("Mach-O binary is too small.")
    magic, count = struct.unpack_from(">II", data, 0)
    if magic not in (FAT_MAGIC, FAT_MAGIC_64):
        fail(f"Expected a fat Mach-O, found magic 0x{magic:08x}.")

    entry_fmt = ">IIIII" if magic == FAT_MAGIC else ">IIQQII"
    entry_size = struct.calcsize(entry_fmt)
    pos = 8
    arches: dict[int, tuple[int, int]] = {}
    for _ in range(count):
        if pos + entry_size > len(data):
            fail("Truncated fat Mach-O architecture table.")
        row = struct.unpack_from(entry_fmt, data, pos)
        cpu, offset, size = row[0], row[2], row[3]
        if offset + size > len(data):
            fail(f"Fat Mach-O slice 0x{cpu:x} extends beyond the file.")
        arches[cpu] = (offset, size)
        pos += entry_size
    return arches

def loader_regions() -> tuple[bytes, bytes]:
    old = MANIFEST["adapter"]["legacy_loader_path"].encode() + b"\0"
    new = MANIFEST["adapter"]["install_name"].encode() + b"\0"
    if len(new) > len(old):
        fail("Manifest loader replacement does not fit in the original string region.")
    return old, new + b"\0" * (len(old) - len(new))

def materialize_patch(entry: dict, state: str) -> bytes:
    if entry.get("kind") == "loader_path":
        old, new = loader_regions()
        return old if state == "old" else new
    return bytes.fromhex(entry[state])

def patch_plan(data: bytes, state: str) -> list[dict]:
    arches = parse_fat_arches(data)
    plan = []
    for arch_name, arch_meta in MANIFEST["fat_arches"].items():
        cpu = int(arch_meta["cpu_type"], 0)
        if cpu not in arches:
            fail(f"Required Mach-O slice is missing: {arch_name}")
        base, size = arches[cpu]
        for entry in MANIFEST["patches"][arch_name]:
            rel = int(entry["offset"], 0)
            expected = materialize_patch(entry, state)
            if rel + len(expected) > size:
                fail(f"{arch_name} patch site {entry['offset']} is outside its slice.")
            absolute = base + rel
            got = data[absolute:absolute + len(expected)]
            if got != expected:
                fail(
                    f"{arch_name}: {entry['description']} mismatch at slice+{entry['offset']}.\n"
                    f"Expected: {expected.hex()}\nFound:    {got.hex()}"
                )
            plan.append({
                "arch": arch_name,
                "base": base,
                "relative_offset": rel,
                "absolute_offset": absolute,
                "description": entry["description"],
                "old": materialize_patch(entry, "old"),
                "new": materialize_patch(entry, "new"),
            })

    old_path, new_path = loader_regions()
    if state == "old":
        if data.count(old_path) != 2:
            fail(f"Expected exactly 2 legacy Accelerate loader strings; found {data.count(old_path)}.")
    else:
        bare_new = MANIFEST["adapter"]["install_name"].encode() + b"\0"
        if old_path in data:
            fail("Legacy Accelerate loader string still exists after patching.")
        if data.count(bare_new) != 2:
            fail(f"Expected exactly 2 adapter loader strings; found {data.count(bare_new)}.")
    return plan

def print_plan(plan: list[dict]) -> None:
    info("\nBinary patch manifest (slice-relative):")
    for i, p in enumerate(plan, 1):
        info(
            f"  {i:02d}. {p['arch']:7s} +0x{p['relative_offset']:05x} "
            f"{p['old'].hex()} -> {p['new'].hex()}  {p['description']}"
        )

def apply_binary_patch(path: Path) -> str:
    data = bytearray(path.read_bytes())
    plan = patch_plan(bytes(data), "old")
    print_plan(plan)
    for p in plan:
        a = p["absolute_offset"]
        data[a:a + len(p["old"])] = p["new"]
    path.write_bytes(data)
    patch_plan(bytes(data), "new")
    digest = sha256_bytes(bytes(data))
    expected = MANIFEST["target"]["patched_pre_sign_sha256"]
    if digest != expected:
        fail(
            "Patched pre-sign binary hash mismatch.\n"
            f"Expected: {expected}\nFound:    {digest}"
        )
    return digest

def check_runtime_symbols() -> dict:
    path = MANIFEST["runtime"]["accelerate"]
    try:
        lib = ctypes.CDLL(path)
    except OSError as e:
        fail(f"Could not load Accelerate.framework: {e}")

    missing = []
    for name in MANIFEST["runtime"]["required_modern_symbols"]:
        try:
            getattr(lib, name)
        except AttributeError:
            missing.append(name)
    if missing:
        fail("Required modern BNNS symbols are missing:\n  " + "\n  ".join(missing))

    legacy = MANIFEST["runtime"]["legacy_symbol_expected_missing"]
    legacy_present = True
    try:
        getattr(lib, legacy)
    except AttributeError:
        legacy_present = False

    if legacy_present:
        fail(
            f"Legacy symbol {legacy} is present in this Accelerate.framework. "
            "The observed compatibility patch is not appropriate for this runtime."
        )
    return {"modern_symbols": "ok", "legacy_symbol": f"{legacy}: absent (expected)"}

def macos_sdk_path() -> Path:
    cp = run(
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"],
        capture=True,
        check=False,
    )
    if cp.returncode != 0:
        fail(
            "Could not resolve the active macOS SDK with "
            "`xcrun --sdk macosx --show-sdk-path`."
        )
    sdk = Path(cp.stdout.strip())
    if not sdk.is_dir():
        fail(f"xcrun returned a macOS SDK path that does not exist: {sdk}")

    required_headers = (
        sdk / "usr/include/dlfcn.h",
        sdk / "System/Library/Frameworks/Accelerate.framework/Headers/Accelerate.h",
    )
    missing = [str(p) for p in required_headers if not p.exists()]
    if missing:
        fail(
            "The selected macOS SDK is incomplete for this build. Missing:\n  "
            + "\n  ".join(missing)
        )
    return sdk

def check_tools(download_needed: bool) -> None:
    for cmd in ("xcrun", "clang", "lipo", "nm", "otool", "codesign", "ditto"):
        if cmd == "clang":
            cp = run(["/usr/bin/xcrun", "--find", "clang"], capture=True, check=False)
            if cp.returncode != 0:
                fail("Apple clang not found. Install Xcode Command Line Tools.")
        else:
            require_command(cmd)
    sdk = macos_sdk_path()
    info(f"  macOS SDK: {sdk}")
    if download_needed:
        require_command("curl")

def secure_adapter_source(work: Path, local_source: Path | None) -> tuple[Path, dict]:
    if local_source:
        source = local_source.expanduser().resolve()
        if not source.is_file():
            fail(f"Adapter source not found: {source}")
        copied = work / "BNNSCompat.c"
        shutil.copy2(source, copied)
        return copied, {"origin": str(source), "sha256": sha256_file(copied)}

    adapter = MANIFEST["adapter"]
    archive = work / "upstream-adapter.zip"
    info("Downloading pinned upstream compatibility-adapter release…")
    run([
        require_command("curl"),
        "--fail", "--location", "--silent", "--show-error",
        "--proto", "=https", "--tlsv1.2",
        "-o", str(archive), adapter["release_url"],
    ])
    digest = sha256_file(archive)
    if digest != adapter["release_asset_sha256"]:
        fail(
            "Upstream release asset checksum mismatch.\n"
            f"Expected: {adapter['release_asset_sha256']}\n"
            f"Found:    {digest}"
        )

    with zipfile.ZipFile(archive) as zf:
        candidates = [
            zi for zi in zf.infolist()
            if not zi.is_dir()
            and Path(zi.filename).name.lower().startswith("bnnscompat")
            and zi.filename.lower().endswith(".c")
        ]
        if len(candidates) != 1:
            names = ", ".join(zi.filename for zi in candidates) or "(none)"
            fail(f"Expected exactly one BNNSCompat*.c in pinned archive; found: {names}")
        source_bytes = zf.read(candidates[0])

    source = work / "BNNSCompat.c"
    source.write_bytes(source_bytes)
    return source, {
        "origin": adapter["release_url"],
        "archive_sha256": digest,
        "archive_member": candidates[0].filename,
        "sha256": sha256_file(source),
    }

def build_adapter(source: Path, work: Path) -> Path:
    out = work / "BNNSCompat.dylib"
    clang = run(["/usr/bin/xcrun", "--find", "clang"], capture=True).stdout.strip()
    sdk = macos_sdk_path()
    info(f"Building adapter against macOS SDK: {sdk}")
    run([
        clang,
        "-isysroot", str(sdk),
        "-std=c11", "-O2", "-Wall", "-Wextra", "-fvisibility=hidden",
        "-arch", "arm64", "-arch", "x86_64", "-dynamiclib",
        "-Wl,-install_name,@loader_path/BNNSCompat.dylib",
        "-o", str(out), str(source),
    ])

    archs = run(["/usr/bin/lipo", "-archs", str(out)], capture=True).stdout.split()
    for arch in ("arm64", "x86_64"):
        if arch not in archs:
            fail(f"Compiled adapter is missing {arch}.")

    exports = run(["/usr/bin/nm", "-gU", str(out)], capture=True).stdout.splitlines()
    exported = {line.rsplit(maxsplit=1)[-1] for line in exports if line.strip()}
    missing = [
        name for name in MANIFEST["adapter"]["required_exports"]
        if "_" + name not in exported
    ]
    if missing:
        fail("Adapter is missing Logic-facing exports:\n  " + "\n  ".join(missing))
    return out

def static_no_direct_legacy_call(binary: Path) -> None:
    for arch in ("arm64", "x86_64"):
        cp = run(["/usr/bin/otool", "-arch", arch, "-tvV", str(binary)], capture=True)
        if "symbol stub for: _BNNSGraphGetSize" in cp.stdout:
            fail(f"{arch} still contains an executable direct _BNNSGraphGetSize stub call.")

def verify_signed_copy(app: Path) -> dict:
    target = MANIFEST["target"]
    binary = app / target["binary_rel"]
    framework = app / target["framework_rel"]
    shim = app / target["shim_rel"]
    if not (binary.is_file() and shim.is_file() and framework.is_dir()):
        fail("Patched app is missing MAMachineLearning or BNNSCompat.dylib.")

    data = binary.read_bytes()
    patch_plan(data, "new")
    static_no_direct_legacy_call(binary)

    run(["/usr/bin/codesign", "--verify", "--verbose=2", str(shim)], capture=True)
    run(["/usr/bin/codesign", "--verify", "--verbose=2", str(framework)], capture=True)

    exports = run(["/usr/bin/nm", "-gU", str(shim)], capture=True).stdout
    missing = [s for s in MANIFEST["adapter"]["required_exports"] if f"_{s}" not in exports]
    if missing:
        fail("Signed adapter is missing exports: " + ", ".join(missing))

    return {
        "binary_sha256_post_sign": sha256_file(binary),
        "shim_sha256": sha256_file(shim),
        "framework_signature": "valid",
        "patch_sites": "valid",
    }

def doctor(app: Path, allow_untested_os: bool, *, show_plan=True) -> dict:
    system = checked_system(allow_untested_os)
    check_tools(download_needed=False)
    binary, logic = checked_target(app)
    runtime = check_runtime_symbols()
    plan = patch_plan(binary.read_bytes(), "old")
    if show_plan:
        info("PRE-FLIGHT OK")
        info(f"  macOS: {system['macos_version']} ({system['macos_build']})")
        info(f"  host:  {system['host_arch']}")
        info(f"  Logic: {logic['version']} ({logic['build']})")
        info(f"  source SHA-256: {sha256_file(binary)}")
        info(f"  SIP:   {system['sip']}")
        print_plan(plan)
        info("\nNo files were changed.")
    return {"system": system, "logic": logic, "runtime": runtime, "plan": plan}

def write_receipt(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)

def apply_patch(args) -> None:
    source_app = args.app.expanduser().resolve()
    dest = args.dest.expanduser().resolve()
    if source_app == dest:
        fail("Destination must not be the original Logic application.")
    if dest.exists():
        fail(f"Destination already exists: {dest}")
    if not dest.parent.is_dir():
        fail(f"Destination parent directory does not exist: {dest.parent}")

    preflight = doctor(source_app, args.allow_untested_os, show_plan=True)
    check_tools(download_needed=args.adapter_source is None)

    with tempfile.TemporaryDirectory(prefix="logic27-bnns-build-") as td:
        work = Path(td)
        adapter_source, adapter_meta = secure_adapter_source(work, args.adapter_source)
        info(f"\nAdapter source SHA-256: {adapter_meta['sha256']}")
        adapter = build_adapter(adapter_source, work)
        info("Adapter build + export validation: OK")

        if args.dry_run:
            info("\nDRY RUN COMPLETE — target, runtime, pinned adapter, compiler and exports are valid.")
            info("No Logic application copy was created.")
            return

        partial = dest.parent / f".{dest.name}.partial-{uuid.uuid4().hex[:10]}"
        try:
            info(f"\nCreating transactional copy: {partial}")
            run(["/usr/bin/ditto", str(source_app), str(partial)])

            target = MANIFEST["target"]
            binary = partial / target["binary_rel"]
            framework = partial / target["framework_rel"]
            shim = partial / target["shim_rel"]

            copied_hash = sha256_file(binary)
            if copied_hash != target["source_sha256"]:
                fail("Copied MAMachineLearning hash differs from the validated source.")

            pre_sign_hash = apply_binary_patch(binary)
            info(f"Patched pre-sign SHA-256: {pre_sign_hash}")

            shutil.copy2(adapter, shim)
            os.chmod(shim, 0o755)
            static_no_direct_legacy_call(binary)

            info("Signing compatibility adapter and modified framework…")
            run(["/usr/bin/codesign", "--force", "--sign", "-", str(shim)])
            run(["/usr/bin/codesign", "--verify", "--verbose=2", str(shim)], capture=True)
            run(["/usr/bin/codesign", "--force", "--sign", "-", str(framework)])
            run(["/usr/bin/codesign", "--verify", "--verbose=2", str(framework)], capture=True)

            verification = verify_signed_copy(partial)

            receipt = {
                "schema": 1,
                "tool_id": MANIFEST["tool_id"],
                "created_unix": int(time.time()),
                "source_app": str(source_app),
                "output_app": str(dest),
                "target": MANIFEST["target"],
                "tested_system": MANIFEST["tested_system"],
                "actual_system": preflight["system"],
                "adapter": adapter_meta,
                "verification": verification,
                "manifest_sha256": sha256_file(MANIFEST_PATH),
            }

            os.rename(partial, dest)
            receipt_path = Path(str(dest) + ".logic27-patch.json")
            write_receipt(receipt_path, receipt)

            info("\nSUCCESS")
            info(f"  patched copy: {dest}")
            info(f"  receipt:      {receipt_path}")
            info(f"  original:     {source_app}  (untouched)")
            info("\nLaunch only the patched copy for testing. The original remains your rollback.")
        except Exception:
            if partial.exists():
                shutil.rmtree(partial, ignore_errors=True)
            raise

def verify_command(args) -> None:
    app = args.dest.expanduser().resolve()
    if not app.is_dir():
        fail(f"Patched app not found: {app}")
    result = verify_signed_copy(app)
    info("VERIFY OK")
    for k, v in result.items():
        info(f"  {k}: {v}")

def diagnose_command(args) -> None:
    log_path = Path(MANIFEST["adapter"].get("runtime_log", "/tmp/LogicBNNSCompat-v4.log"))
    info(f"Adapter log: {log_path}")
    if not log_path.is_file():
        info("No adapter log exists yet.")
        info("Launch the patched Logic copy and exercise an ML-backed feature such as ChromaGlow, then run diagnose again.")
        return

    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-args.lines:] if args.lines > 0 else lines
    success = sum("BNNSGraphContextExecute_v2 call succeeded" in line for line in lines)
    errors = [line for line in lines if "ERROR:" in line]
    registered = sum("registered Tensor BNNS graph context" in line for line in lines)

    info(f"Registered graph contexts: {registered}")
    info(f"Successful first executes: {success}")
    info(f"Error lines: {len(errors)}")
    info("\nRecent adapter log:")
    for line in tail:
        info(line)

def remove_command(args) -> None:
    app = args.dest.expanduser().resolve()
    original = args.app.expanduser().resolve()
    if app == original:
        fail("Refusing to remove the original Logic application.")
    receipt = Path(str(app) + ".logic27-patch.json")
    if not receipt.is_file():
        fail(f"Patch receipt is missing; refusing to remove anything: {receipt}")
    meta = json.loads(receipt.read_text(encoding="utf-8"))
    if meta.get("tool_id") != MANIFEST["tool_id"] or Path(meta.get("output_app", "")).resolve() != app:
        fail("Patch receipt does not match this app; refusing to remove anything.")
    if not app.is_dir():
        fail(f"Patched app is already absent: {app}")
    shutil.rmtree(app)
    receipt.unlink()
    info(f"Removed patched copy: {app}")
    info(f"Original remains untouched: {original}")

def parser() -> argparse.ArgumentParser:
    default_app = Path(MANIFEST["target"]["default_app"])
    default_dest = Path.home() / "Desktop/Logic Pro 11 BNNS Patched.app"

    p = argparse.ArgumentParser(
        description="Fail-closed Logic Pro 11.2.2 / macOS 27 BNNS compatibility installer."
    )
    p.add_argument("--app", type=Path, default=default_app,
                   help=f"source Logic app (default: {default_app})")
    p.add_argument("--dest", type=Path, default=default_dest,
                   help=f"patched copy destination (default: {default_dest})")
    p.add_argument("--allow-untested-os", action="store_true",
                   help="permit a macOS version/build other than the validated 27.0 (26A428); runtime symbols are still checked")

    sub = p.add_subparsers(dest="command")
    sub.add_parser("doctor", help="preflight only; never changes Logic")

    ap = sub.add_parser("apply", help="build, patch, verify and publish a separate Logic copy")
    ap.add_argument("--adapter-source", type=Path,
                    help="use a local BNNSCompat*.c instead of downloading the pinned upstream release")
    ap.add_argument("--dry-run", action="store_true",
                    help="run target checks and compile/validate the adapter, but do not copy or patch Logic")

    sub.add_parser("verify", help="verify an already patched copy")
    dp = sub.add_parser("diagnose", help="summarize the BNNS compatibility adapter runtime log")
    dp.add_argument("--lines", type=int, default=60,
                    help="number of recent adapter-log lines to print (default: 60; use 0 for all)")
    sub.add_parser("remove", help="remove only a patched copy that has this tool's matching receipt")
    return p

def main() -> int:
    args = parser().parse_args()
    command = args.command or "doctor"
    try:
        if command == "doctor":
            doctor(args.app, args.allow_untested_os)
        elif command == "apply":
            apply_patch(args)
        elif command == "verify":
            verify_command(args)
        elif command == "diagnose":
            diagnose_command(args)
        elif command == "remove":
            remove_command(args)
        else:
            fail(f"Unknown command: {command}")
        return 0
    except PatchError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCancelled. Temporary patch copy was not published.", file=sys.stderr)
        return 130

if __name__ == "__main__":
    raise SystemExit(main())
