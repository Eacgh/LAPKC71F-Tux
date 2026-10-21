#!/usr/bin/env python3
"""Quick ITE 8291 keyboard test.

Run after the installer (the udev rule gives wheel access), or with sudo:

    python3 tools/test-keyboard.py
    sudo python3 tools/test-keyboard.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nuc_center.ite import IteKeyboard  # noqa: E402


def main() -> int:
    keyboard = IteKeyboard()
    print(f"ITE 8291: {keyboard.path or 'not found'}")
    if not keyboard.available:
        print("找不到 048d:6006 ITE Device(8291)。")
        return 1
    print(f"当前亮度: {keyboard.state().get('brightness')}/50")
    for name, rgb in (
        ("红", (255, 0, 0)),
        ("绿", (0, 255, 0)),
        ("蓝", (0, 0, 255)),
        ("青", (0, 200, 255)),
    ):
        print(f"设置为{name}色，亮度 4 级")
        print(keyboard.apply(rgb=rgb, level=4))
        time.sleep(1.2)
    print("完成。键盘应停在青色。")
    keyboard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
