# Logic Pro 11.2.2 on macOS 27 - launch fix

Unofficial, minimal compatibility patch for a startup failure seen with **Logic Pro 11.2.2 build 6387** on **macOS 27**.

The observed crash happens before Logic finishes launching:

```text
Termination Reason: Namespace DYLD, Code 4, Symbol missing
Symbol not found: _BNNSGraphGetSize
Referenced from: MAMachineLearning.framework
Expected in: Accelerate.framework
```

This repository contains a **launch-only fix**. It does not modify Logic licensing or trial state, does not touch `/System`, does not disable SIP, and never overwrites the original `/Applications/Logic Pro.app`.

## Tested case

- Logic Pro 11.2.2
- build 6387
- `MAMachineLearning` SHA-256: `b7a4e954e202a605af48dc10f963de075def2ecdf4d1c239a7e5022eb3f125da`
- macOS 27.x
- Apple Silicon / ARM64 was runtime-tested successfully

The script also contains guarded x86_64 edits, but Intel runtime behavior has not been independently tested.

## One script, two modes

There is only one entry point:

```text
Logic11-macOS27-LaunchFix.command
```

On the first run it creates a separate Logic copy, patches it, ad-hoc signs the modified framework, verifies the signed universal Mach-O, and launches it.

If the patched copy already exists, running the same script again automatically switches to verification mode. You can also force verification-only behavior with `--verify-only`.

## Usage

Keep the original Logic Pro at `/Applications/Logic Pro.app`, then run:

```bash
chmod +x Logic11-macOS27-LaunchFix.command
./Logic11-macOS27-LaunchFix.command
```

Default output:

```text
~/Desktop/Logic Pro 11.2.2 macOS27 LaunchFix.app
```

Useful options:

```bash
./Logic11-macOS27-LaunchFix.command --verify-only
./Logic11-macOS27-LaunchFix.command --no-launch
```

You can override the destination path:

```bash
LOGIC_PATCH_DEST="$HOME/Applications/Logic Pro 11 LaunchFix.app" \
  ./Logic11-macOS27-LaunchFix.command
```

## What the patch changes

The script is deliberately restricted to the exact tested Logic build. It verifies the original binary hash and expected bytes before applying any edit.

It then:

- neutralizes the obsolete direct `BNNSGraphGetSize` call;
- bypasses the serializer branch that depends on the removed API;
- changes the legacy import to a weak import;
- marks the corresponding symbol reference weak;
- ad-hoc signs only the modified `MAMachineLearning.framework` in the copied app.

Before signing, guarded absolute offsets are safe because the exact source binary is verified. After `codesign`, verification switches to **architecture-relative offsets inside each fat Mach-O slice**, because signing can rewrite the file layout/signature area.

## Limitations

This is intentionally a **minimal startup fix**, not a complete legacy-BNNS compatibility layer. Normal launch/playback can work while ML-heavy features may still require an adapter for the newer BNNS Graph / GraphContext API.

Features worth testing separately include Stem Splitter, Mastering Assistant, ChromaGlow, pitch/model-based processing, and other functionality backed by `MAMachineLearning.framework`.

## Safety

- The original Logic app is never patched in place.
- The script aborts on a version, build, hash, or byte mismatch.
- SIP does not need to be disabled.
- No Apple system framework is replaced.
- No licensing or trial code is modified.

Keep the original Logic app and backups of important projects.

## Related research

The macOS 27 BNNS transition and a broader compatibility adapter have also been researched publicly here:

- https://github.com/NewtonPuff/logic-pro-11-macos-27-bnns-fix

That repository did not expose a LICENSE file when this repository was prepared. This repository therefore does **not** vendor or copy its compatibility adapter source. See [ATTRIBUTION.md](ATTRIBUTION.md).

## Disclaimer

Unofficial project. Not affiliated with or supported by Apple Inc. Logic Pro, macOS, Accelerate and BNNS are Apple technologies/products/trademarks.
