# REA native engine status

Verified 2026-10-15: Ghidra provider is available. No Coremail executable was opened, executed, or analyzed by this preparation task. No Codex configuration was changed.

## Process environment

```powershell
$env:GHIDRA_INSTALL_DIR = 'D:\workspace\redocoreemail\.tools\ghidra_12.1.4_PUBLIC'
$env:JAVA_HOME = 'D:\Program Files\Java\jdk-21.0.9'
$env:REA_ANALYSIS_PROVIDER = 'ghidra'
node 'D:\workspace\redocoreemail\.npm-cache\_npx\af752b90495482d7\node_modules\rea-agents\scripts\rea.mjs' doctor --client codex --json
node 'D:\workspace\redocoreemail\.npm-cache\_npx\af752b90495482d7\node_modules\rea-agents\scripts\rea.mjs' providers --json
```

These are process-local settings. An already running MCP server does not acquire them automatically; the root task controls any shared registration changes.

## Installation and integrity

- Installed official Ghidra 12.1.4 from the [NSA GitHub release](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.4_build).
- Asset: `ghidra_12.1.4_PUBLIC_20260921.zip`, 569,649,598 bytes.
- SHA-256 checked against the official release/API: `ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db`.
- Archive retained in `.tools`; extracted distribution is `.tools/ghidra_12.1.4_PUBLIC`.
- Existing Java retained: full 64-bit Oracle JDK 21.0.9, including `jdk.compiler`.
- Existing Node: 24.19.0. Existing REA CLI: 6.5.0, skill version 42.

## Verification evidence

Saved complete outputs:

- `.tools/native-engine-doctor.json`
- `.tools/native-engine-providers.json`

The final doctor reports Ghidra `configured:true`, `available:true`, `providerVersion:12.1.4`. Configuration, platform, architecture, application properties, release version, headless launcher, native decompiler, Java, and Windows native authority all pass. Native authority reports verified Job Object, private DACL, and reparse-safe handle controls with local NTFS only.

`healthy:true` is scoped to the requested Codex client. `environment_healthy:false` reflects unrelated absent Hopper, IDA, JADX, and other client registrations; it does not invalidate the passing Ghidra inspection. Providers also reports Ghidra available with `REA_ANALYSIS_PROVIDER=ghidra`.

Default sandbox execution caused Node's Java `spawnSync` to return `EPERM`, although direct Java execution worked. Running the read-only doctor/providers command with `require_escalated` resolved this and passed Java/native checks. If a subsequent CLI native call falsely reports missing Java, check the subprocess restriction before reinstalling tools.

No full import/decompilation smoke test was run: the root task requested engine readiness only and owns the CMClient analysis session. Thus provider readiness is verified; target import success remains to be established separately.

## Supported boundary and usage limits

The [REA Windows Ghidra guide](https://github.com/morluto/rea/blob/main/docs/windows-ghidra-p0.md) describes an experimental, read-only Windows x64 lane. REA 6.5.0's installed policy verifies Ghidra 12.1.4 and accepts 12.1.x. Java must satisfy the installed distribution's declared range; this release accepts full x64 JDK 21 or newer. Python/PyGhidra are not required.

The installed provider advertises native x86/x64 PE applications and DLLs plus explicitly selected raw 6502 images. Managed PE uses a separate provider. Database mutations/annotations, GUI state, and general feature parity are unavailable. Follow the installed 6.5.0 provider inventory if upstream `main` documents broader support.

Targets and temporary runtimes must use supported fixed local NTFS paths; reparse paths, alternate streams, UNC paths, and ambiguous coordinates are refused. Do not broaden into mutable or sensitive target paths. Static imports use immutable private snapshots rather than executing the analyzed target. Ghidra cold imports can exceed short MCP timeouts; the guide notes an existing 330-second startup deadline. Keep one owned native analysis session, use focused queries, and close it when complete.
