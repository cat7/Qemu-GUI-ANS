# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Qemu-system-ppc ANS GUI.
#
# Build it with the python.org framework Python (universal2, with tkinter):
#
#   /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 \
#       -m PyInstaller --noconfirm QemuGUI.spec
#
# By hand it is the same thing:   pyinstaller --noconfirm QemuGUI.spec
#
# The Python doing the packaging decides what the result runs on. It must be
# a universal2 build with a working tkinter, or the .app will only run on the
# machine that built it: Homebrew's Python is arch-specific and ships no
# tkinter, Apple's /usr/bin/python3 is arm64e and is not redistributable.
#
# Put the result into the folder that holds qemu-system-ppc:
# "dist/Qemu-system-ppc ANS GUI.app" on macOS, the single
# "dist/Qemu-system-ppc ANS GUI.exe" on Windows. The program works that folder out
# itself (paths.resolve_install_dir walks up out of the .app), and keeps
# Machines/ beside the application, never inside the bundle, which is
# read-only.

import os
import sys

# universal2 is a macOS notion, and PyInstaller refuses it elsewhere.
# QEMUGUI_TARGET_ARCH=arm64 builds for this machine only.
TARGET_ARCH = (os.environ.get('QEMUGUI_TARGET_ARCH') or 'universal2') if sys.platform == 'darwin' else None

a = Analysis(
    ['qemu_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.filedialog',
                   'tkinter.messagebox', 'tkinter.simpledialog', 'tkinter.font'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == 'win32':
    # One self-contained .exe; it unpacks itself to a temp folder at start.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name='Qemu-system-ppc ANS GUI',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name='Qemu-system-ppc ANS GUI',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        target_arch=TARGET_ARCH,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name='Qemu-system-ppc ANS GUI',
    )
    app = BUNDLE(
        coll,
        name='Qemu-system-ppc ANS GUI.app',
        icon=None,
        bundle_identifier='org.cat7.qemu-gui-ans',
        info_plist={
            'CFBundleName': 'Qemu-system-ppc ANS GUI',
            'CFBundleDisplayName': 'Qemu-system-ppc ANS GUI',
            'CFBundleShortVersionString': '1.0',
            'CFBundleVersion': '1.0',
            # Without this the window is drawn at 1x and looks blurred on a
            # Retina screen.
            'NSHighResolutionCapable': True,
            # What the python.org universal2 build itself requires.
            'LSMinimumSystemVersion': '10.13',
            # One window, no document types, and it must not keep running with
            # no windows open.
            'LSApplicationCategoryType': 'public.app-category.utilities',
        },
    )
