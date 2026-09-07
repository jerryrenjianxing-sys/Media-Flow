# MediaFlow native console build

## dev.28 local navigation correction

Management anchors explicitly open externally so the native router cannot treat
`/manage` as a project directory. The gateway redirects management paths to the
owned console on port3001, with a matching exact API CORS origin. The management
root no longer mounts the retired React chat; its settings and native return
links are independent. This narrow correction does not restore or change the
native conversation core. See `docs/dev28-navigation-repair.md` for scope,
red/green browser tests, and remaining acceptance limits.

The application is the native OpenCode Solid frontend, not an iframe or a
replacement chat renderer. Upstream sessions, projects, tools, providers, models,
terminal, and both native settings variants remain upstream implementations.

## Provenance and reproducibility

`upstream.json` pins OpenCode **1.18.29**, commit
`16747470f976aca3d362ad730bcd3fe82ecc2c9a`, Git tree
`6d8cc725d9c0945d7259b78e2f60cdec6c493a26`, from
<https://github.com/anomalyco/opencode>, and Bun **1.3.14**.
The original MIT notice is retained in `LICENSE.opencode`, the built public
license asset, and the About panel. Upstream endorsement is not implied.

`build.py` obtains recursive tree metadata from GitHub, reconstructs and verifies
every Git tree hash against the pinned root, and checks each selected Git blob
hash before use. Git symlinks are materialized as files only within the source
root, allowing builds without Windows symlink privileges. The selected input set
matches the native app's workspace dependency closure: root/lock/patch/vendor
files, workspace manifests, the listed frontend/shared packages, desktop locale
inputs, and the CLI TypeScript project descriptor. CLI/runtime builds are not
part of this package. An interrupted download is retried at most three times;
invalid hashes or patch drift fail closed. No arbitrary installer is downloaded.

`branding.json` is an exact-context patch against verified upstream bytes. Each
replacement must match once. `overlay/` holds only new local files; it is not a
forked copy of the upstream app. The build copies `integration/bootstrap.ts` and
`integration/migration.ts` from their tracked canonical locations. The existing
MediaFlow M icon is copied from `control_console/public/favicon.svg`.

Fetched source (`opencode-source/`), verified metadata/blob cache
(`work/native-source-cache/`), dependencies, and generated output stay untracked.
Rebuilding accepts pristine or exactly patched source, but refuses to overwrite
unexpected local upstream edits. Preserve those edits before resolving drift.

## Commands

From the repository root, with a Python 3.11+ interpreter and the pinned Bun:

```powershell
python native_console/build_tests.py
python native_console/build.py --bun /absolute/path/to/bun.exe --prepare-only
python native_console/build.py --bun /absolute/path/to/bun.exe --install --check
```

`--install` uses the upstream frozen lockfile and disables lifecycle scripts.
Omit it when dependencies are already installed. `--check` runs the native unit
suite and package typecheck before Vite builds. No services are started, stopped,
or published by this command. Output is `opencode-source/packages/app/dist/`.
The release owner separately copies that build into `native_console/dist/`.
Keep the previous published build for rollback. Build inputs are pinned;
byte-for-byte output identity across operating systems is not claimed.

## Local customization boundary

- Default Chinese applies only when no existing locale is stored. Existing
  locale and theme preferences remain native and are not reset.
- The default MediaFlow palette has light/dark teal variants. Native theme
  selection is retained, including user-selected upstream themes.
- Vite assets use `/_native/`; the default backend is same-origin `/opencode`.
  Parent-owned gateway/runtime integration supplies those routes.
  The upstream protocol probe, current-client factory and health polling preserve
  generic server subpaths. The scoped transport adapter only prefixes the URL;
  request bodies, headers, abort signals and response/event streams are preserved.
  Native PTY URL construction also normalizes a trailing slash.
- The shared native titlebar adds branding, `/manage`, and explicit emergency
  stop. Stop requests confirm first, use the existing `PUT /api/automation-stop`
  with `{stopped:true}`, and never retry a write automatically. A missing or
  unsuccessful receipt is unknown, never a fabricated successful stop. This
  pauses automation and requests running task stops; it does not close MuMu.
- The no-project New Session action opens the existing native project chooser.
  No custom session or chat state is introduced.
- About in both settings variants includes the complete upstream MIT license,
  disclaimer, and retained legacy-draft conflict/retry guidance.
  It reads the existing `/api/agent/status` migration receipt and offers the
  explicit `/api/agent/native-migration/retry` action when retryable. A failed
  read leaves settings and Manage usable; writes are never automatically retried.

New MediaFlow labels have explicit English fallback entries in every upstream
locale to preserve native locale-key parity; Chinese labels are translated.

Update the pin, inspect the upstream license and dependency closure, revise only
necessary exact-context edits, then rerun Python tests, native unit tests,
typecheck, build, and rendered acceptance. Do not make the patch permissive to
silence upstream drift. Browser and live-system acceptance belong to the release
owner; build checks do not authorize model calls or device operations.
