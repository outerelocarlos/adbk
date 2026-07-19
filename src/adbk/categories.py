"""Default category definitions.

These are sensible, broadly-useful starting points. Everything here is
overridable and extendable through ``[[category]]`` tables in
``backup-config.toml`` -- the app-specific entries (WhatsApp, Telegram, games,
emulators) are just common examples to edit for your own device.

Paths that do not exist on a given phone are simply reported as missing and
skipped, so listing extra candidates is harmless.
"""

from __future__ import annotations

from adbk.models import Category

DEFAULT_CATEGORIES: tuple[Category, ...] = (
    # --- Shared-storage user folders -----------------------------------------
    Category("Documents", ("/sdcard/Documents",), "User documents"),
    Category("Downloads", ("/sdcard/Download",), "Downloaded files"),
    Category("Camera & Pictures", ("/sdcard/DCIM", "/sdcard/Pictures"), "Photos and screenshots"),
    Category("Videos", ("/sdcard/Movies",), "Videos"),
    Category(
        "Music & Audio",
        ("/sdcard/Music", "/sdcard/Podcasts", "/sdcard/Audiobooks"),
        "Music and other audio",
    ),
    Category(
        "Ringtones, notifications & alarms",
        ("/sdcard/Ringtones", "/sdcard/Notifications", "/sdcard/Alarms"),
        "Custom sound files placed in Ringtones/, Notifications/ and Alarms/",
    ),
    Category("Voice recordings", ("/sdcard/Recordings",), "Voice recorder output"),
    Category(
        "Manga & readers",
        ("/sdcard/Tachiyomi", "/sdcard/Mihon", "/sdcard/Aniyomi"),
        "Tachiyomi/Mihon/Aniyomi backups, settings and downloads",
    ),
    Category("ROMs", ("/sdcard/ROMs",), "Emulator ROMs on shared storage"),

    # --- Messaging apps -------------------------------------------------------
    Category(
        "WhatsApp",
        ("/sdcard/Android/media/com.whatsapp", "/sdcard/Android/media/com.whatsapp.w4b"),
        "WhatsApp media + encrypted chat backups (msgstore .crypt14/.crypt15)",
    ),
    Category(
        "Telegram",
        (
            "/sdcard/Android/data/org.thunderdog.challegram",  # Telegram X
            "/sdcard/Android/data/org.telegram.messenger",     # Telegram
            "/sdcard/Android/media/org.telegram.messenger",
        ),
        "Telegram / Telegram X data and media",
    ),
    Category(
        "App exports & configs",
        (
            "/sdcard/Signal", "/sdcard/nova_backups", "/sdcard/backups",
            "/sdcard/.mixplorer", "/sdcard/MiXplorer",  # MiXplorer bookmarks/themes/settings
        ),
        "App-generated exports/configs on shared storage (Signal, Nova, MiXplorer, ...)",
    ),

    # --- Game & app data ------------------------------------------------------
    # Targeted native-game saves (edit for your own titles). These live under
    # Android/data, which is readable via adb on many devices (e.g. Samsung).
    Category(
        "Game saves",
        (
            "/sdcard/Android/data/jp.nippon1.disgaearefine",
            "/sdcard/Android/data/com.TeamCherry.HollowKnight",
            "/sdcard/Android/data/com.square_enix.android_googleplay.subarashikikonosekai_solo",
            "/sdcard/Android/data/jp.konami.masterduel",
            "/sdcard/Android/data/com.playstack.balatro.android",
        ),
        "Saved games for specific native titles",
    ),
    # Emulator saves/states/BIOS across the common Android emulators. Save-focused
    # sub-folders are used where the parent also holds ROMs/ISOs (PSP, RetroArch,
    # Dolphin); everything else points at the app's Android/data (saves live in
    # files/, caches are filtered out). Missing paths are simply skipped, so this
    # broad list is safe on any device.
    Category(
        "Emulator saves",
        (
            # RetroArch (all cores) -- saves, states, BIOS, configs; not ROMs/cores
            "/sdcard/RetroArch/saves", "/sdcard/RetroArch/states",
            "/sdcard/RetroArch/system", "/sdcard/RetroArch/config",
            "/sdcard/Android/data/com.retroarch/files",
            "/sdcard/Android/data/com.retroarch.aarch64/files",
            # PPSSPP (PSP) -- memstick save/state/system, not the GAME ISOs
            "/sdcard/PSP/SAVEDATA", "/sdcard/PSP/PPSSPP_STATE", "/sdcard/PSP/SYSTEM",
            "/sdcard/Android/data/org.ppsspp.ppsspp/files",
            "/sdcard/Android/data/org.ppsspp.ppssppgold/files",
            # Dolphin (GameCube / Wii) -- memcards, Wii saves, save states
            "/sdcard/dolphin-emu/GC", "/sdcard/dolphin-emu/Wii",
            "/sdcard/dolphin-emu/StateSaves",
            "/sdcard/Android/data/org.dolphinemu.dolphinemu/files",
            # PS2 (AetherSX2 / NetherSX2)
            "/sdcard/Android/data/xyz.aethersx2.android/files",
            # PS1 (DuckStation)
            "/sdcard/Android/data/com.github.stenzek.duckstation/files",
            # Nintendo Switch (yuzu forks) -- saves in nand/, keys, config
            "/sdcard/Android/data/dev.eden.eden_emulator/files",
            "/sdcard/Android/data/org.yuzu.yuzu_emu/files",
            "/sdcard/Android/data/org.yuzu.yuzu_emu.ea/files",
            "/sdcard/Android/data/org.suyu.suyu/files",
            "/sdcard/Android/data/dev.sudachi.sudachi_emu/files",
            "/sdcard/Android/data/org.citron.citron_emu/files",
            # Nintendo 3DS (Citra and forks)
            "/sdcard/citra-emu",
            "/sdcard/Android/data/org.citra.citra_emu/files",
            "/sdcard/Android/data/io.github.lime3ds.android/files",
            "/sdcard/Android/data/io.github.mandarine3ds.mandarine/files",
            "/sdcard/Android/data/io.github.azahar_emu.azahar/files",
            # Nintendo DS (melonDS, DraStic)
            "/sdcard/Android/data/me.magnum.melonds/files",
            "/sdcard/DraStic/backup", "/sdcard/DraStic/savestates",
            "/sdcard/Android/data/com.dsemu.drastic/files",
            # Nintendo 64 (Mupen64Plus AE)
            "/sdcard/Mupen64Plus AE/GameSaves", "/sdcard/Mupen64Plus AE/SlotSaves",
            "/sdcard/Android/data/org.mupen64plusae.v3.alpha/files",
            # Dreamcast (Flycast, Redream)
            "/sdcard/redream/saves", "/sdcard/redream/states",
            "/sdcard/Android/data/com.flycast.emulator/files",
            "/sdcard/Android/data/io.recompiled.redream/files",
            # PS Vita (Vita3K)
            "/sdcard/Android/data/org.vita3k.emulator/files",
            # GBA/GBC/multi (My Boy!, Robert Broglia .emu series)
            "/sdcard/Android/data/com.fastemulator.gba/files",
            "/sdcard/Android/data/com.fastemulator.gbc/files",
            "/sdcard/Android/data/com.explusalpha.GbaGbcEmu/files",
            "/sdcard/Android/data/com.explusalpha.Snes9xEXPlus/files",
            "/sdcard/Android/data/com.explusalpha.MdEmu/files",
            "/sdcard/Android/data/com.explusalpha.NesEmu/files",
        ),
        "Saves, states, memory cards and BIOS across common Android emulators",
    ),
    # Broad catch-all: every app's shared-storage data under Android/data plus
    # game asset files under Android/obb. Selected by default (per "all selected
    # by default"); drill into it to deselect apps whose caches you don't want.
    Category(
        "All app data (Android/data + obb)",
        ("/sdcard/Android/data", "/sdcard/Android/obb"),
        "Every app's Android/data (incl. caches for ~all apps) + Android/obb game files",
    ),
)
