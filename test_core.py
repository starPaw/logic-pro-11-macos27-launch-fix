#!/usr/bin/env python3
"""Offline structural tests for CleanPatch core; no macOS frameworks required."""
from pathlib import Path
import importlib.util
import struct

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cleanpatch", HERE / "logic27_patch.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def synthetic_fat():
    total = 0x80000
    data = bytearray(total)
    struct.pack_into(">II", data, 0, m.FAT_MAGIC, 2)
    struct.pack_into(">IIIII", data, 8,
                     int(m.MANIFEST["fat_arches"]["x86_64"]["cpu_type"], 0), 3,
                     0x4000, 0x34000, 14)
    struct.pack_into(">IIIII", data, 28,
                     int(m.MANIFEST["fat_arches"]["arm64"]["cpu_type"], 0), 0,
                     0x40000, 0x34000, 14)
    arches = m.parse_fat_arches(bytes(data))
    for arch_name, meta in m.MANIFEST["fat_arches"].items():
        base, _ = arches[int(meta["cpu_type"], 0)]
        for entry in m.MANIFEST["patches"][arch_name]:
            rel = int(entry["offset"], 0)
            old = m.materialize_patch(entry, "old")
            data[base+rel:base+rel+len(old)] = old
    return data

def main():
    data = synthetic_fat()
    plan = m.patch_plan(bytes(data), "old")
    assert len(plan) == 10
    for p in plan:
        a = p["absolute_offset"]
        data[a:a+len(p["old"])] = p["new"]
    m.patch_plan(bytes(data), "new")

    bad = synthetic_fat()
    first = plan[0]["absolute_offset"]
    bad[first] ^= 0x01
    try:
        m.patch_plan(bytes(bad), "old")
    except m.PatchError:
        pass
    else:
        raise AssertionError("mismatch did not fail closed")

    installer_source = (HERE / "logic27_patch.py").read_text(encoding="utf-8")
    assert '"-isysroot", str(sdk)' in installer_source
    assert '"--sdk", "macosx", "--show-sdk-path"' in installer_source
    assert 'diagnose_command' in installer_source
    assert m.MANIFEST["adapter"]["runtime_log"] == "/tmp/LogicBNNSCompat-v4.log"
    assert m.MANIFEST["tool_version"] == "1.2.0"

    print("PASS: fat parsing, 10-site manifest, patched-state verification, mismatch rejection, explicit macOS SDK sysroot, diagnostics metadata")

if __name__ == "__main__":
    main()
