# Changelog

All notable changes to this project are documented here.

## 1.2.0 — 2026-09-23

- Prepared the project for public release.
- Added `diagnose` for `/tmp/LogicBNNSCompat-v4.log`.
- Documented successful runtime execution of BNNS GraphContexts with 45/46/47 arguments on the validated system.
- Clarified upstream adapter attribution and licensing boundaries.
- Added CI, issue template, contribution guidance, and repository hygiene files.
- Kept the validated BNNS adapter and binary compatibility logic unchanged.

## 1.1.0 — 2026-09-23

- Resolved the active macOS SDK with `xcrun --sdk macosx --show-sdk-path`.
- Added explicit `-isysroot` to the adapter build.
- Added preflight checks for required SDK headers.
- Fixed `fatal error: 'dlfcn.h' file not found` when Apple Clang did not inherit an SDK root.

## 1.0.0 — 2026-09-23

- Initial fail-closed installer.
- Exact Logic build and SHA-256 validation.
- Architecture-relative fat Mach-O patch manifest.
- Transactional app copy and publish-on-success behavior.
- Pinned upstream adapter release checksum.
- Ad-hoc signing and post-sign verification.
- JSON receipts and guarded removal.
