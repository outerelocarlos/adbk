# Migration notes

Practical notes for moving to a new phone: which kinds of data this tool captures
by copying files, and which apps need their own (non-file-copy) migration step.
General tool behaviour lives in the [README](README.md); this file is the "what
actually happens to my stuff" reference.

## Game saves

The **"Game saves"** category pulls each configured native title's data from
`/sdcard/Android/data/<package>`, which is readable via `adb` on many devices
(no root required on, for example, most Samsung phones).

Whether a given game's save is captured depends on where the game stores it:

| Where the game stores its save | Captured by a file copy? | Notes |
| --- | --- | --- |
| `Android/data/<package>/files/...` on shared storage | yes | The common case for engines like Unity and LÖVE. |
| Account / cloud (Google Play Games, publisher account) | account-bound | Progress follows the account; just sign in on the new phone. |
| Private `/data/data/<package>` (no shared-storage copy) | not without root | See "Delisted or private-save games" below. |

Add your own titles by listing their packages under the **"Game saves"**
`[[category]]` in `backup-config.toml`. Run `backup --dry-run` with the phone
connected to see exactly which paths exist and are readable on your device.

## Emulator saves (device-agnostic)

The **"Emulator saves"** category is universal - it lists the common Android
emulators' save/state/memory-card/BIOS locations, so it works on any phone
(missing paths are simply skipped). It targets **saves, not ROMs/ISOs**.

Covered out of the box:

- **RetroArch** - `RetroArch/{saves,states,system,config}` (+ its `Android/data`)
- **PPSSPP** - `PSP/{SAVEDATA,PPSSPP_STATE,SYSTEM}` (not the `GAME` ISOs)
- **Dolphin** (GC/Wii) - `dolphin-emu/{GC,Wii,StateSaves}`
- **AetherSX2 / NetherSX2** (PS2), **DuckStation** (PS1)
- **Switch** - Eden, yuzu (+EA), suyu, sudachi, citron (`files/nand`, keys, config)
- **3DS** - Citra, Lime3DS, Mandarine, Azahar
- **DS** - melonDS, DraStic (`DraStic/{backup,savestates}`)
- **N64** - Mupen64Plus AE (`GameSaves`, `SlotSaves`)
- **Dreamcast** - Flycast, Redream (`redream/{saves,states}`)
- **PS Vita** - Vita3K
- **My Boy! / GBA.emu / Snes9x EX+ / .emu cores**

Emulator **ROMs/BIOS** that live on shared storage (e.g. `/sdcard/ROMs`) come via
the **ROMs**/**Downloads** categories, not this one. Add your own emulator with a
`[[category]]` entry in `backup-config.toml`.

## Delisted or private-save games

Every installed app is recorded in the manifest, and the APK of any app that did
not come from the store is kept automatically, so most sideloaded games are
already preserved. A **delisted** game is the awkward case: it still reports the
store as its installer, so either back up with `--check-store` (which detects
that the listing is gone) or list its package under `[apps] force_apk` in
`backup-config.toml`. Restore can then reinstall it directly.

The OBB and the save still need the manual steps below. To preserve a game
fully, keep all three pieces:

1. **APK** - pull the installed app package (no root needed):
   ```
   adb pull "$(adb shell pm path <package> | sed 's/package://')" game.apk
   ```
   (an app with split APKs prints several lines; pull each.)
2. **OBB** - `/sdcard/Android/obb/<package>/` (large asset packs), captured by the
   **Android/data + obb** category.
3. **Save** - if it lives in private `/data/data`, it can't be file-copied without
   root. If the app has `ALLOW_BACKUP` and a low `targetSdk` (older games), the
   deprecated `adb backup` can still extract it:
   ```
   adb backup -f game_save.ab <package>
   ```
   (tap **"Back up my data"** on the phone; no password needed).

**Restore on the new phone:** install the APK, copy the OBB back into
`Android/obb/<package>/`, then `adb restore game_save.ab`. If the `.ab` comes out
~0 bytes, `adb backup` was blocked and only root can extract the save.

## Apps that need their own migration (not file-copyable)

The tool copies these apps' **shared-storage** data, but their chat/settings
databases live in private storage. Use each app's own export/transfer:

| App type | What the tool grabs | What you must do yourself |
| --- | --- | --- |
| **WhatsApp** | media + encrypted chat backups under `Android/media/com.whatsapp/.../Databases/msgstore-*.crypt14` | reinstall with the same number -> it restores the local backup (keep your encryption key / cloud backup) |
| **Signal** | the exported `.backup` (once you create it) | Settings -> Chats -> **Chat backups**; note the passphrase |
| **Launcher (e.g. Nova)** | the exported backup file on shared storage | make a fresh backup in the launcher's settings before switching |
| **Telegram** | `Android/data/org.telegram.messenger` / Telegram X media | account-based; chats re-sync on login |
| **Manga reader (Tachiyomi/Mihon)** | the `.tachibk`/`.proto.gz` backup (under `Tachiyomi/`) + downloads | install the reader, restore the backup in-app; it reinstalls the sources/extensions from their repo (extension APKs are deliberately not kept) |
| **Authenticator / 2FA** | nothing - seeds are in private storage | **before wiping the old phone**, use the app's **Transfer accounts -> Export** and scan the QR on the new phone (lose this and you lose your 2FA) |
| **File manager (e.g. MiXplorer)** | its config folder on `/sdcard` (bookmarks, themes) | copy the folder back |

> The authenticator/2FA row is the one thing no file backup can capture - export
> your seeds **before** resetting the old device.

## Junk that is deliberately skipped

Thumbnail/cache/trash folders, `*.tmp`, `.trashed-*`/`.pending-*`, `.nomedia`,
Glide's `.gs_fs0` cache, recycle bins, and anything you add under `[filters]` in
`backup-config.toml`.
