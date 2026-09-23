# Logic Pro 11.2.2 on macOS 27 — BNNS Launch Fix

A transparent, fail-closed compatibility installer for the Logic Pro 11.2.2 startup failure on macOS 27 caused by the BNNS Graph ABI transition in Apple's Accelerate framework.

> **Status:** validated on Logic Pro 11.2.2 (build 6387), macOS 27.0 (26A428), Apple Silicon, SIP enabled.

## The problem

On the affected configuration, Logic Pro terminates during dynamic linking before the UI appears:

```text
Termination Reason: Namespace DYLD, Code 4, Symbol missing
Symbol not found: _BNNSGraphGetSize
Referenced from: .../MAMachineLearning.framework/.../MAMachineLearning
Expected in: .../Accelerate.framework
```

Logic Pro 11.2.2 expects an older BNNS Graph ABI. The tested macOS 27 runtime no longer exposes the legacy interface in the form expected by Logic's `MAMachineLearning.framework`.

This project creates a **separate patched copy** of Logic Pro and bridges those legacy BNNS calls to the current BNNS Graph / GraphContext API.

## Safety model

The installer is intentionally conservative:

- never modifies `/Applications/Logic Pro.app`;
- never modifies `/System` or the Signed System Volume;
- does not replace `Accelerate.framework`;
- does not disable SIP;
- does not alter licensing, receipts, accounts, or trial state;
- validates the exact Logic version, build, source SHA-256, and every binary patch site;
- parses the fat Mach-O and applies architecture-relative patches instead of blind file offsets;
- verifies all original bytes before writing anything;
- performs the work in a hidden temporary app copy;
- publishes the final `.app` only after patching, adapter compilation, signing, and verification succeed;
- writes a JSON receipt next to the patched app;
- refuses to delete anything with `remove` unless the receipt matches.

Your original Logic installation remains the rollback path.

## Supported target

| Component | Validated value |
| --- | --- |
| Logic Pro | 11.2.2 |
| Logic build | 6387 |
| `MAMachineLearning` SHA-256 | `b7a4e954e202a605af48dc10f963de075def2ecdf4d1c239a7e5022eb3f125da` |
| macOS | 27.0 |
| macOS build | 26A428 |
| Host | Apple Silicon / arm64 |
| SIP | enabled |

A different Logic binary fails closed. A different macOS build also fails closed by default; `--allow-untested-os` exists for deliberate testing, but the BNNS runtime symbols are still checked.

## Runtime validation

The compatibility path has been exercised with Logic ML workloads using the current BNNS GraphContext implementation. On the validated system, the adapter successfully created and executed graph contexts with 45, 46, and 47 arguments, including ChromaGlow, with successful `BNNSGraphContextExecute_v2` calls.

Example runtime evidence:

```text
adapter alpha4 loaded; Tensor GraphContext bridge resolved
registered Tensor BNNS graph context: args=47 workspace=5695872
entering first Tensor GraphContextExecute: args=47 ... shadow=0 undersized=0
first Tensor BNNSGraphContextExecute_v2 call succeeded
```

This is compatibility work around an undocumented/version-specific ABI boundary, not an Apple-supported configuration. Future Logic or macOS builds require fresh validation.

## Quick start

```bash
git clone https://github.com/starPaw/logic-pro-11-macos27-launch-fix.git
cd logic-pro-11-macos27-launch-fix
```

### 1. Preflight only

```bash
./Logic27-BNNS.command doctor
```

This validates the host, SDK, Logic build, source hash, BNNS runtime symbols, and all ten binary patch sites. It changes nothing.

### 2. Full dry run

```bash
./Logic27-BNNS.command apply --dry-run
```

This additionally downloads the pinned upstream compatibility-adapter release, verifies its SHA-256, extracts the adapter source, compiles it against the active macOS SDK, and checks the required exports. Logic is still not copied or modified.

### 3. Create the patched copy

```bash
./Logic27-BNNS.command apply
```

Default output:

```text
~/Desktop/Logic Pro 11 BNNS Patched.app
```

The original remains at:

```text
/Applications/Logic Pro.app
```

### 4. Verify later

```bash
./Logic27-BNNS.command verify
```

### 5. Inspect BNNS runtime diagnostics

```bash
./Logic27-BNNS.command diagnose
```

The current upstream adapter writes to:

```text
/tmp/LogicBNNSCompat-v4.log
```

To show more lines:

```bash
./Logic27-BNNS.command diagnose --lines 200
```

### 6. Remove only the patched copy

```bash
./Logic27-BNNS.command remove
```

`remove` requires the matching CleanPatch receipt and explicitly refuses the original Logic app.

## What is patched

The exact byte-level specification lives in [`manifest.json`](manifest.json). There are five validated edits in each Mach-O slice (`x86_64` and `arm64`):

1. neutralize the direct legacy `BNNSGraphGetSize` call;
2. neutralize the obsolete serialization branch;
3. mark the chained legacy import weak;
4. set the Mach-O `N_WEAK_REF` symbol flag;
5. redirect MAMachineLearning's private BNNS loader to `@loader_path/BNNSCompat.dylib`.

After the ten edits, the pre-sign `MAMachineLearning` binary must match:

```text
646cd2dc6f1c14621333e35ca51074e8d5005cfe7144c9608ceffe4038d7376a
```

If it does not, the temporary copy is discarded.

## Compatibility adapter

This repository does **not** redistribute the upstream `BNNSCompat.c` implementation.

At apply time, the installer downloads the pinned release asset from:

- upstream: `NewtonPuff/logic-pro-11-macos-27-bnns-fix`
- release: `logic-11.2.2-macos27`
- pinned asset SHA-256: `0a88f151b90b274c48e39f539316cd21665b0610a7de0f09bf3b1d1f15654161`

The complete release ZIP is verified before any adapter source is extracted or compiled. You can also provide a locally reviewed adapter source:

```bash
./Logic27-BNNS.command apply --adapter-source /path/to/BNNSCompat-v4.c
```

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for attribution and licensing boundaries.

## Toolchain

The adapter is built locally with Apple Clang. The installer explicitly resolves the active SDK:

```bash
xcrun --sdk macosx --show-sdk-path
```

and passes it as an explicit sysroot:

```text
-isysroot <resolved-macOS-SDK>
```

The preflight checks for the headers required by the adapter, including `dlfcn.h` and Accelerate headers.

## Commands

```text
Logic27-BNNS.command doctor
Logic27-BNNS.command apply [--dry-run] [--adapter-source PATH]
Logic27-BNNS.command verify
Logic27-BNNS.command diagnose [--lines N]
Logic27-BNNS.command remove
```

Global options such as `--dest` must appear before the subcommand:

```bash
./Logic27-BNNS.command --dest "$HOME/Applications/Logic Pro 11 BNNS Patched.app" apply
```

## Tests

The repository contains offline structural tests for:

- fat Mach-O parsing;
- all ten manifest patch sites;
- patched-state verification;
- fail-closed rejection on a one-byte mismatch;
- explicit macOS SDK sysroot handling.

Run:

```bash
python3 test_core.py
```

## Project scope

This project intentionally focuses on making the compatibility installation **auditable, transactional, and reversible**. It does not attempt to replace Apple's BNNS implementation or silently support unknown Logic builds.

If Apple ships a Logic or macOS update that restores compatibility, prefer the official update rather than carrying this patch forward.

## Attribution

The BNNS compatibility-adapter strategy and runtime adapter are derived from the work in [`NewtonPuff/logic-pro-11-macos-27-bnns-fix`](https://github.com/NewtonPuff/logic-pro-11-macos-27-bnns-fix).

This repository adds a separate fail-closed installer, architecture-relative patch manifest, SDK/toolchain checks, transactional publishing, receipts, verification, diagnostics, and tests. The upstream adapter source is fetched from its original release rather than republished here.

## License

The installer, manifest, tests, documentation, and repository-owned files are licensed under the MIT License. Third-party material fetched at runtime is **not** covered by this repository's MIT license; see [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
