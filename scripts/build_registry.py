#!/usr/bin/env python3
"""
build_registry.py — Presto 模板注册表构建脚本

子命令：
  discover  搜索 GitHub 上所有 presto-template topic 的仓库
  extract   下载二进制并提取 manifest / example / typst 源码
  compile   用 Typst CLI 将 .typ 编译为 SVG 预览
  index     汇总 manifest 生成 registry.json

环境变量：
  GITHUB_TOKEN       GitHub API token（避免 rate limit）
  FORCE_REBUILD      设为 "true" 强制重建所有模板
"""

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ─── 常量 ───────────────────────────────────────────────────────────────

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
TEMPLATES_DIR = ROOT_DIR / "templates"
REGISTRY_JSON = ROOT_DIR / "registry.json"
DISCOVER_JSON = OUTPUT_DIR / "discovered.json"

GITHUB_API = "https://api.github.com"
TOPIC = "presto-template"

# 官方模板所在仓库（它们不通过 topic 发现，而是硬编码）
OFFICIAL_REPO = "Presto-io/Presto"
OFFICIAL_TEMPLATES = [
    {"name": "gongwen", "cmd_path": "cmd/gongwen"},
    {"name": "jiaoan-shicao", "cmd_path": "cmd/jiaoan-shicao"},
]

# 安全限制
MAX_MANIFEST_SIZE = 1 * 1024 * 1024   # 1 MB
MAX_EXAMPLE_SIZE = 1 * 1024 * 1024    # 1 MB
MAX_TYPST_SIZE = 10 * 1024 * 1024     # 10 MB
EXEC_TIMEOUT = 30                      # 秒

# 分类 label 映射
CATEGORY_LABELS = {
    "government": {"zh": "政务", "en": "Government"},
    "education":  {"zh": "教育", "en": "Education"},
    "business":   {"zh": "商务", "en": "Business"},
    "academic":   {"zh": "学术", "en": "Academic"},
    "legal":      {"zh": "法务", "en": "Legal"},
    "resume":     {"zh": "简历", "en": "Resume"},
    "creative":   {"zh": "创意", "en": "Creative"},
    "other":      {"zh": "其他", "en": "Other"},
}

# ─── 辅助函数 ────────────────────────────────────────────────────────────


def github_headers():
    """返回带认证的 GitHub API 请求头。"""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_current_platform():
    """返回当前运行平台的 os-arch 标识。"""
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine, machine)
    return os_name, arch


def find_binary_asset(assets, template_name, target_os, target_arch):
    """从 Release assets 中找到匹配平台的二进制文件。"""
    suffix = ".exe" if target_os == "windows" else ""
    expected = f"presto-template-{template_name}-{target_os}-{target_arch}{suffix}"
    for asset in assets:
        if asset["name"] == expected:
            return asset
    return None


def load_existing_registry():
    """加载已有的 registry.json，如果不存在则返回空结构。"""
    if REGISTRY_JSON.exists():
        with open(REGISTRY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "updatedAt": "", "categories": [], "templates": []}


def safe_run(cmd, input_data=None, timeout=EXEC_TIMEOUT):
    """安全地运行子进程，带超时和大小限制。"""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    try:
        result = subprocess.run(
            cmd,
            input=input_data,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return result
    except subprocess.TimeoutExpired:
        print(f"  ⚠ 命令超时 ({timeout}s): {' '.join(str(c) for c in cmd)}")
        return None
    except Exception as e:
        print(f"  ⚠ 命令执行失败: {e}")
        return None


# ─── discover 子命令 ─────────────────────────────────────────────────────


def cmd_discover(args):
    """搜索 GitHub 上所有 presto-template topic 的仓库。"""
    print("=== Discover templates ===")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    existing = load_existing_registry()
    existing_versions = {}
    for t in existing.get("templates", []):
        existing_versions[t["name"]] = t.get("version", "")

    force = os.environ.get("FORCE_REBUILD", "").lower() == "true" or getattr(args, "force", False)
    discovered = []

    # 1. 官方模板（从 Presto-io/Presto 仓库的 Release 获取）
    print(f"\n检查官方仓库: {OFFICIAL_REPO}")
    for tmpl in OFFICIAL_TEMPLATES:
        name = tmpl["name"]
        print(f"  检查官方模板: {name}")
        release_url = f"{GITHUB_API}/repos/{OFFICIAL_REPO}/releases/latest"
        resp = requests.get(release_url, headers=github_headers())
        if resp.status_code != 200:
            print(f"    ⚠ 无法获取 Release 信息: HTTP {resp.status_code}")
            continue

        release = resp.json()
        tag = release.get("tag_name", "")
        version = tag.lstrip("v")
        published_at = release.get("published_at", "")

        if not force and existing_versions.get(name) == version:
            print(f"    版本未变 ({version})，跳过")
            continue

        discovered.append({
            "name": name,
            "repo": OFFICIAL_REPO,
            "owner": "Presto-io",
            "version": version,
            "tag": tag,
            "published_at": published_at,
            "assets": release.get("assets", []),
            "html_url": f"https://github.com/{OFFICIAL_REPO}",
            "is_official": True,
        })
        print(f"    发现新版本: {version}")

    # 2. 社区模板（通过 topic 搜索）
    print(f"\n搜索 GitHub topic: {TOPIC}")
    search_url = f"{GITHUB_API}/search/repositories"
    params = {"q": f"topic:{TOPIC}", "sort": "updated", "per_page": 100}
    resp = requests.get(search_url, headers=github_headers(), params=params)

    if resp.status_code == 200:
        repos = resp.json().get("items", [])
        print(f"  找到 {len(repos)} 个仓库")

        for repo in repos:
            full_name = repo["full_name"]
            owner = repo["owner"]["login"]

            # 跳过官方仓库（已在上面处理）
            if full_name == OFFICIAL_REPO:
                continue

            print(f"  检查: {full_name}")
            release_url = f"{GITHUB_API}/repos/{full_name}/releases/latest"
            rel_resp = requests.get(release_url, headers=github_headers())
            if rel_resp.status_code != 200:
                print(f"    ⚠ 无 Release，跳过")
                continue

            release = rel_resp.json()
            tag = release.get("tag_name", "")
            version = tag.lstrip("v")
            published_at = release.get("published_at", "")

            # 从 assets 中推测模板名（从二进制文件名提取）
            template_name = None
            for asset in release.get("assets", []):
                aname = asset["name"]
                if aname.startswith("presto-template-"):
                    # presto-template-{name}-{os}-{arch}
                    parts = aname.replace(".exe", "").split("-")
                    # 去掉 presto-template- 前缀和 -{os}-{arch} 后缀
                    if len(parts) >= 5:
                        template_name = "-".join(parts[2:-2])
                        break

            if not template_name:
                print(f"    ⚠ 未找到符合命名规范的二进制，跳过")
                continue

            if not force and existing_versions.get(template_name) == version:
                print(f"    版本未变 ({version})，跳过")
                continue

            discovered.append({
                "name": template_name,
                "repo": full_name,
                "owner": owner,
                "version": version,
                "tag": tag,
                "published_at": published_at,
                "assets": release.get("assets", []),
                "html_url": repo["html_url"],
                "is_official": False,
            })
            print(f"    发现: {template_name} v{version}")
    else:
        print(f"  ⚠ 搜索失败: HTTP {resp.status_code}")

    # 写入发现结果
    with open(DISCOVER_JSON, "w", encoding="utf-8") as f:
        json.dump(discovered, f, ensure_ascii=False, indent=2)
    print(f"\n共发现 {len(discovered)} 个需要更新的模板")


# ─── extract 子命令 ──────────────────────────────────────────────────────


def cmd_extract(args):
    """下载二进制并提取 manifest / example / typst 源码。"""
    print("=== Extract template data ===")

    if not DISCOVER_JSON.exists():
        print("⚠ 未找到 discovered.json，请先运行 discover")
        sys.exit(1)

    with open(DISCOVER_JSON, "r", encoding="utf-8") as f:
        discovered = json.load(f)

    if not discovered:
        print("没有需要更新的模板")
        return

    current_os, current_arch = get_current_platform()
    print(f"当前平台: {current_os}-{current_arch}")

    for tmpl in discovered:
        name = tmpl["name"]
        print(f"\n处理模板: {name}")

        out_dir = OUTPUT_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. 找到当前平台的二进制
        asset = find_binary_asset(tmpl["assets"], name, current_os, current_arch)
        if not asset:
            print(f"  ⚠ 未找到 {current_os}-{current_arch} 的二进制")
            continue

        # 2. 下载二进制
        print(f"  下载: {asset['name']}")
        download_url = asset["browser_download_url"]
        resp = requests.get(download_url, headers=github_headers(), stream=True)
        if resp.status_code != 200:
            print(f"  ⚠ 下载失败: HTTP {resp.status_code}")
            continue

        binary_path = out_dir / f"presto-template-{name}"
        with open(binary_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # 设置执行权限
        binary_path.chmod(binary_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  二进制大小: {binary_path.stat().st_size / 1024:.1f} KB")

        # 3. 运行 --manifest
        print("  提取 manifest.json ...")
        result = safe_run([str(binary_path), "--manifest"])
        if result and result.returncode == 0:
            manifest_data = result.stdout
            if len(manifest_data) > MAX_MANIFEST_SIZE:
                print(f"  ⚠ manifest 超出大小限制 ({len(manifest_data)} bytes)")
                continue
            (out_dir / "manifest.json").write_bytes(manifest_data)
        else:
            print(f"  ⚠ --manifest 失败")
            if result:
                print(f"    stderr: {result.stderr.decode('utf-8', errors='replace')[:500]}")
            continue

        # 4. 运行 --example
        print("  提取 example.md ...")
        result = safe_run([str(binary_path), "--example"])
        if result and result.returncode == 0:
            example_data = result.stdout
            if len(example_data) > MAX_EXAMPLE_SIZE:
                print(f"  ⚠ example 超出大小限制 ({len(example_data)} bytes)")
                continue
            (out_dir / "example.md").write_bytes(example_data)
        else:
            print(f"  ⚠ --example 失败")
            continue

        # 5. 管道转换：cat example.md | ./binary → output.typ
        print("  生成 output.typ ...")
        result = safe_run([str(binary_path)], input_data=example_data)
        if result and result.returncode == 0:
            typst_data = result.stdout
            if len(typst_data) > MAX_TYPST_SIZE:
                print(f"  ⚠ typst 输出超出大小限制 ({len(typst_data)} bytes)")
                continue
            (out_dir / "output.typ").write_bytes(typst_data)
        else:
            print(f"  ⚠ 管道转换失败")
            if result:
                print(f"    stderr: {result.stderr.decode('utf-8', errors='replace')[:500]}")
            continue

        # 6. 获取 README.md
        print("  获取 README.md ...")
        repo = tmpl["repo"]
        # 官方模板的 README 在子目录下
        if tmpl.get("is_official"):
            for official in OFFICIAL_TEMPLATES:
                if official["name"] == name:
                    readme_path = official["cmd_path"]
                    break
            readme_url = f"{GITHUB_API}/repos/{repo}/contents/{readme_path}/README.md"
        else:
            readme_url = f"{GITHUB_API}/repos/{repo}/readme"

        resp = requests.get(readme_url, headers=github_headers())
        if resp.status_code == 200:
            readme_data = resp.json()
            import base64
            content = base64.b64decode(readme_data.get("content", "")).decode("utf-8")
            (out_dir / "README.md").write_text(content, encoding="utf-8")
        else:
            # 如果子目录没有 README，尝试仓库根目录
            resp = requests.get(f"{GITHUB_API}/repos/{repo}/readme", headers=github_headers())
            if resp.status_code == 200:
                readme_data = resp.json()
                import base64
                content = base64.b64decode(readme_data.get("content", "")).decode("utf-8")
                (out_dir / "README.md").write_text(content, encoding="utf-8")
            else:
                print(f"  ⚠ 无法获取 README")
                (out_dir / "README.md").write_text(f"# {name}\n", encoding="utf-8")

        # 7. 保存元数据
        meta = {
            "name": name,
            "repo": repo,
            "owner": tmpl["owner"],
            "version": tmpl["version"],
            "tag": tmpl["tag"],
            "published_at": tmpl["published_at"],
            "html_url": tmpl["html_url"],
            "is_official": tmpl.get("is_official", False),
        }
        with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 清理二进制（不传到下一个 Job）
        binary_path.unlink(missing_ok=True)

        print(f"  完成 ✓")

    # ─── Hero 分帧（仅 gongwen）───────────────────────────────────
    gongwen_dir = OUTPUT_DIR / "gongwen"
    example_file = gongwen_dir / "example.md"
    binary_path_gongwen = gongwen_dir / "presto-template-gongwen"

    if example_file.exists():
        print("\n=== 生成 Hero 分帧 SVG 源码 ===")
        example_text = example_file.read_text(encoding="utf-8")

        # 重新下载二进制用于 hero 帧生成
        for tmpl in discovered:
            if tmpl["name"] == "gongwen":
                asset = find_binary_asset(tmpl["assets"], "gongwen", current_os, current_arch)
                if asset:
                    resp = requests.get(asset["browser_download_url"], headers=github_headers(), stream=True)
                    if resp.status_code == 200:
                        with open(binary_path_gongwen, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                        binary_path_gongwen.chmod(
                            binary_path_gongwen.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
                        )
                break

        if binary_path_gongwen.exists():
            frames = generate_hero_frames(example_text)
            for i, frame_md in enumerate(frames):
                print(f"  生成 hero-frame-{i} ...")
                result = safe_run(
                    [str(binary_path_gongwen)],
                    input_data=frame_md.encode("utf-8"),
                )
                if result and result.returncode == 0:
                    (gongwen_dir / f"hero-frame-{i}.typ").write_bytes(result.stdout)
                else:
                    print(f"    ⚠ hero-frame-{i} 生成失败")

            binary_path_gongwen.unlink(missing_ok=True)


def generate_hero_frames(example_md):
    """将 example.md 截取为 4 个递增的帧。"""
    lines = example_md.split("\n")
    frames = []

    # 找到 frontmatter 的结束位置
    fm_start = -1
    fm_end = -1
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if fm_start < 0:
                fm_start = i
            else:
                fm_end = i
                break

    if fm_end < 0:
        # 没有 frontmatter，返回完整文档作为唯一帧
        return [example_md]

    # frame-0: 仅 frontmatter
    frames.append("\n".join(lines[: fm_end + 1]) + "\n")

    # 找 body 部分的关键行
    body_lines = lines[fm_end + 1 :]
    title_end = 0
    first_para_end = 0
    found_content = False

    for i, line in enumerate(body_lines):
        stripped = line.strip()
        if not stripped:
            if found_content and first_para_end == 0:
                first_para_end = i
            continue
        found_content = True
        if title_end == 0:
            title_end = i + 1

    if first_para_end == 0:
        first_para_end = len(body_lines)

    # frame-1: frontmatter + 标题行
    frame1_lines = lines[: fm_end + 1] + body_lines[: max(title_end, 1)]
    frames.append("\n".join(frame1_lines) + "\n")

    # frame-2: frontmatter + 标题 + 主送单位 + 第一段
    cut2 = min(first_para_end + 2, len(body_lines))
    frame2_lines = lines[: fm_end + 1] + body_lines[:cut2]
    frames.append("\n".join(frame2_lines) + "\n")

    # frame-3: 完整文档
    frames.append(example_md)

    return frames


# ─── compile 子命令 ──────────────────────────────────────────────────────


def cmd_compile(args):
    """用 Typst CLI 编译 .typ 文件为 SVG 预览。"""
    print("=== Compile SVGs ===")

    font_path = getattr(args, "font_path", "fonts/")

    # 确认 typst 可用
    try:
        result = subprocess.run(["typst", "--version"], capture_output=True, text=True)
        print(f"Typst 版本: {result.stdout.strip()}")
    except FileNotFoundError:
        print("⚠ 未找到 typst CLI，请先安装")
        sys.exit(1)

    deploy_dir = OUTPUT_DIR / "deploy"

    for tmpl_dir in sorted(OUTPUT_DIR.iterdir()):
        if not tmpl_dir.is_dir() or tmpl_dir.name == "deploy":
            continue

        name = tmpl_dir.name
        typ_file = tmpl_dir / "output.typ"
        if not typ_file.exists():
            continue

        print(f"\n编译: {name}")
        target_dir = deploy_dir / name
        target_dir.mkdir(parents=True, exist_ok=True)

        # 编译主预览 SVG（多页输出 preview-{n}.svg）
        svg_pattern = str(target_dir / "preview-{n}.svg")
        result = subprocess.run(
            ["typst", "compile", "--font-path", font_path, str(typ_file), svg_pattern],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  ⚠ 编译失败: {result.stderr[:500]}")
        else:
            # 统计生成了多少页
            svgs = list(target_dir.glob("preview-*.svg"))
            print(f"  生成 {len(svgs)} 页预览 SVG")

            # 重命名：Typst 输出 preview-1.svg, preview-2.svg（1-indexed）
            # 如果 Typst 输出的是 0-indexed，需要重命名
            # Typst 0.14+ 使用 {n} 占位符时从 1 开始

        # 编译 Hero 分帧 SVG（仅 gongwen）
        for hero_typ in sorted(tmpl_dir.glob("hero-frame-*.typ")):
            frame_name = hero_typ.stem  # e.g. hero-frame-0
            hero_svg = target_dir / f"{frame_name}.svg"
            result = subprocess.run(
                [
                    "typst", "compile", "--font-path", font_path,
                    str(hero_typ), str(hero_svg),
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"  ⚠ {frame_name} 编译失败: {result.stderr[:300]}")
            else:
                print(f"  {frame_name}.svg ✓")

        # 复制其他文件到 deploy 目录
        for fname in ["manifest.json", "README.md", "example.md"]:
            src = tmpl_dir / fname
            if src.exists():
                shutil.copy2(src, target_dir / fname)

    print("\n编译完成")


# ─── index 子命令 ────────────────────────────────────────────────────────


def cmd_index(args):
    """汇总所有模板的 manifest.json，生成 registry.json 索引。"""
    print("=== Generate registry index ===")

    deploy_dir = OUTPUT_DIR / "deploy"
    templates = []
    categories_seen = set()

    # 从 deploy 目录读取
    if deploy_dir.exists():
        for tmpl_dir in sorted(deploy_dir.iterdir()):
            if not tmpl_dir.is_dir():
                continue
            manifest_file = tmpl_dir / "manifest.json"
            if not manifest_file.exists():
                continue

            with open(manifest_file, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            # 从 meta.json 读取额外信息
            meta_file = OUTPUT_DIR / tmpl_dir.name / "meta.json"
            meta = {}
            if meta_file.exists():
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)

            # 判断信任级别
            owner = meta.get("owner", "")
            if owner == "Presto-io":
                trust = "official"
            else:
                trust = "community"

            category = manifest.get("category", "other")
            categories_seen.add(category)

            templates.append({
                "name": manifest.get("name", tmpl_dir.name),
                "displayName": manifest.get("displayName", ""),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", ""),
                "author": manifest.get("author", ""),
                "category": category,
                "keywords": manifest.get("keywords", []),
                "license": manifest.get("license", ""),
                "trust": trust,
                "publishedAt": meta.get("published_at", ""),
                "repository": meta.get("html_url", ""),
            })

    # 合并已有的 registry 中未更新的模板
    existing = load_existing_registry()
    updated_names = {t["name"] for t in templates}
    for existing_tmpl in existing.get("templates", []):
        if existing_tmpl["name"] not in updated_names:
            templates.append(existing_tmpl)
            categories_seen.add(existing_tmpl.get("category", "other"))

    # 构建分类列表（只包含有模板的分类）
    categories = []
    for cat_id, labels in CATEGORY_LABELS.items():
        if cat_id in categories_seen:
            categories.append({"id": cat_id, "label": labels})

    # 生成 registry.json
    registry = {
        "version": 1,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categories": categories,
        "templates": sorted(templates, key=lambda t: t["name"]),
    }

    # 写到 deploy 目录
    deploy_dir.mkdir(parents=True, exist_ok=True)
    registry_deploy = deploy_dir / "registry.json"
    with open(registry_deploy, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    print(f"registry.json 写入: {registry_deploy}")

    # 同时更新仓库根目录的 registry.json
    with open(REGISTRY_JSON, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    print(f"registry.json 写入: {REGISTRY_JSON}")

    # 复制到 templates/ 目录（供仓库内引用）
    if deploy_dir.exists():
        for tmpl_dir in sorted(deploy_dir.iterdir()):
            if not tmpl_dir.is_dir():
                continue
            target = TEMPLATES_DIR / tmpl_dir.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(tmpl_dir, target)
            print(f"  同步: templates/{tmpl_dir.name}/")

    print(f"\n索引完成: {len(templates)} 个模板, {len(categories)} 个分类")


# ─── 主入口 ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Presto 模板注册表构建脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            子命令：
              discover   搜索 GitHub 上的模板仓库
              extract    下载二进制并提取数据
              compile    用 Typst CLI 编译 SVG
              index      生成 registry.json 索引
        """),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # discover
    p_discover = subparsers.add_parser("discover", help="搜索 GitHub 上的模板仓库")
    p_discover.add_argument("--force", action="store_true", help="强制重建所有模板")

    # extract
    p_extract = subparsers.add_parser("extract", help="下载二进制并提取数据")

    # compile
    p_compile = subparsers.add_parser("compile", help="用 Typst CLI 编译 SVG")
    p_compile.add_argument("--font-path", default="fonts/", help="字体路径")

    # index
    p_index = subparsers.add_parser("index", help="生成 registry.json 索引")

    args = parser.parse_args()

    commands = {
        "discover": cmd_discover,
        "extract": cmd_extract,
        "compile": cmd_compile,
        "index": cmd_index,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
