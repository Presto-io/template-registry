#!/usr/bin/env python3
"""
download_fonts.py — 下载编译 SVG 预览所需的开源字体

仅下载 openSource=true 的字体。商业字体（如方正小标宋）需手动获取。
CI 环境中已通过 Dockerfile 或 apt 安装 Noto CJK 字体族。
"""

from pathlib import Path

import hashlib

import requests

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# 开源字体下载列表
# 格式: (文件名, 下载 URL, SHA256 hash)
OPEN_SOURCE_FONTS = [
    # Noto Serif CJK（思源宋体）— CI 中已通过 apt 安装，此处作为备选
    # ("NotoSerifCJKsc-Regular.otf",
    #  "https://github.com/notofonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf",
    #  "expected_sha256_hash_here"),
]


def main():
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    if not OPEN_SOURCE_FONTS:
        print("当前无需额外下载字体（CI 环境已通过 apt 安装 Noto CJK）")
        return

    for filename, url, expected_sha256 in OPEN_SOURCE_FONTS:
        target = FONTS_DIR / filename
        if target.exists():
            print(f"已存在: {filename}")
            continue

        print(f"下载: {filename}")
        resp = requests.get(url, stream=True)
        if resp.status_code != 200:
            print(f"  ⚠ 下载失败: HTTP {resp.status_code}")
            continue

        sha256 = hashlib.sha256()
        with open(target, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                sha256.update(chunk)

        actual_hash = sha256.hexdigest()
        if expected_sha256 and actual_hash != expected_sha256:
            print(f"  !! SHA256 验证失败!")
            print(f"     期望: {expected_sha256}")
            print(f"     实际: {actual_hash}")
            target.unlink()
            continue

        print(f"  大小: {target.stat().st_size / 1024:.1f} KB")
        if expected_sha256:
            print(f"  SHA256 验证通过")

    print("字体下载完成")


if __name__ == "__main__":
    main()
