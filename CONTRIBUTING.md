# Contributing

Contributions are welcome, especially reproducible compatibility reports for newer Logic Pro or macOS builds.

## Before proposing a new supported build

Please include:

- exact Logic Pro version and build;
- macOS version and build;
- Mac architecture;
- SHA-256 of `MAMachineLearning`;
- the original dyld/crash symptom;
- byte-level evidence for every proposed patch site;
- runtime evidence from the compatibility adapter log;
- confirmation that the original Logic app remains untouched.

Do not expand version support by changing only version strings or hashes. A new build requires fresh binary validation and runtime testing.

## Development checks

Run the offline structural test:

```bash
python3 test_core.py
```

On macOS, also run:

```bash
./Logic27-BNNS.command doctor
./Logic27-BNNS.command apply --dry-run
```

Use a separate patched app copy for runtime tests.

## Third-party adapter

Do not copy the upstream `BNNSCompat.c` into this repository unless its licensing status explicitly permits redistribution. The installer is designed to fetch the pinned upstream release instead.
