#!/usr/bin/env python3
"""
build_registry.py — Presto 模板注册表构建脚本 (v2)

子命令：
  discover  搜索 GitHub 上所有 presto-template topic 的仓库
  extract   下载二进制并提取 manifest / example / typst 源码
  compile   用 Typst CLI 将 .typ 编译为 SVG 预览
  index     汇总 manifest 生成 registry.json (v2)

环境变量：
  GITHUB_TOKEN       GitHub API token（避免 rate limit）
  FORCE_REBUILD      设为 "true" 强制重建所有模板
"""

import argparse
import base64
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import textwrap
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

# 官方模板仓库（monorepo，一个仓库包含多个模板）
OFFICIAL_REPO = "Presto-io/presto-official-templates"

# 支持的平台列表
ALL_PLATFORMS = [
    "darwin-arm64", "darwin-amd64",
    "linux-arm64", "linux-amd64",
    "windows-arm64", "windows-amd64",
]

# 安全限制
MAX_MANIFEST_SIZE = 1 * 1024 * 1024   # 1 MB
MAX_EXAMPLE_SIZE = 1 * 1024 * 1024    # 1 MB
MAX_TYPST_SIZE = 10 * 1024 * 1024     # 10 MB
EXEC_TIMEOUT = 30                      # 秒

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


def extract_template_names(assets):
    """从 release assets 中提取所有唯一的模板名（支持 monorepo）。"""
    KNOWN_OS = {"darwin", "linux", "windows"}
    KNOWN_ARCH = {"amd64", "arm64"}
    names = set()
    for asset in assets:
        aname = asset["name"].replace(".exe", "")
        if not aname.startswith("presto-template-"):
            continue
        parts = aname.split("-")
        # presto-template-<name...>-<os>-<arch>
        if len(parts) >= 5 and parts[-2] in KNOWN_OS and parts[-1] in KNOWN_ARCH:
            name = "-".join(parts[2:-2])
            if name:
                names.add(name)
    return sorted(names)


def parse_sha256sums(content):
    """解析 SHA256SUMS 文件内容，返回 {filename: hash} 映射。"""
    result = {}
    for line in content.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            hash_val = parts[0]
            # 去掉 binary mode 前缀 *
            filename = parts[1].lstrip("*")
            result[filename] = hash_val
    return result


def download_sha256sums(assets):
    """从 release assets 下载并解析 SHA256SUMS 文件。"""
    for asset in assets:
        if asset["name"] == "SHA256SUMS":
            resp = requests.get(asset["browser_download_url"], headers=github_headers())
            if resp.status_code == 200:
                return parse_sha256sums(resp.text)
    return {}


def collect_platforms(assets, template_name, sha256_map):
    """收集模板在所有平台的下载 URL 和 SHA256。"""
    platforms = {}
    for plat in ALL_PLATFORMS:
        os_name, arch = plat.split("-")
        suffix = ".exe" if os_name == "windows" else ""
        expected = f"presto-template-{template_name}-{os_name}-{arch}{suffix}"
        for asset in assets:
            if asset["name"] == expected:
                platforms[plat] = {
                    "url": asset["browser_download_url"],
                    "sha256": sha256_map.get(expected, ""),
                }
                break
    return platforms


def load_existing_registry():
    """加载已有的 registry.json，如果不存在则返回空结构。"""
    if REGISTRY_JSON.exists():
        with open(REGISTRY_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"version": 2, "updatedAt": "", "templates": []}


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


def fetch_readme(repo, template_name):
    """获取模板的 README.md（支持 monorepo 子目录）。"""
    # 尝试多个可能的子目录路径
    paths_to_try = [
        f"templates/{template_name}/README.md",
        f"cmd/{template_name}/README.md",
        f"{template_name}/README.md",
    ]

    for path in paths_to_try:
        url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
        resp = requests.get(url, headers=github_headers())
        if resp.status_code == 200:
            data = resp.json()
            content = base64.b64decode(data.get("content", "")).decode("utf-8")
            return content

    # 回退到仓库根 README
    url = f"{GITHUB_API}/repos/{repo}/readme"
    resp = requests.get(url, headers=github_headers())
    if resp.status_code == 200:
        data = resp.json()
        content = base64.b64decode(data.get("content", "")).decode("utf-8")
        return content

    return f"# {template_name}\n"


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

    def process_release(repo_full_name, owner, release):
        """处理一个仓库的 release，提取所有模板（支持 monorepo）。"""
        tag = release.get("tag_name", "")
        version = tag.lstrip("v")
        published_at = release.get("published_at", "")
        assets = release.get("assets", [])

        template_names = extract_template_names(assets)
        if not template_names:
            print(f"    ⚠ 未找到符合命名规范的二进制")
            return

        for name in template_names:
            if not force and existing_versions.get(name) == version:
                print(f"    {name}: 版本未变 ({version})，跳过")
                continue

            discovered.append({
                "name": name,
                "repo": repo_full_name,
                "owner": owner,
                "version": version,
                "tag": tag,
                "published_at": published_at,
                "assets": assets,
                "html_url": f"https://github.com/{repo_full_name}",
            })
            print(f"    发现: {name} v{version}")

    # 1. 官方模板仓库（monorepo）
    print(f"\n检查官方仓库: {OFFICIAL_REPO}")
    release_url = f"{GITHUB_API}/repos/{OFFICIAL_REPO}/releases/latest"
    resp = requests.get(release_url, headers=github_headers())
    if resp.status_code == 200:
        process_release(OFFICIAL_REPO, "Presto-io", resp.json())
    else:
        print(f"  ⚠ 无法获取 Release 信息: HTTP {resp.status_code}")

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

            process_release(full_name, owner, rel_resp.json())
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

    # SHA256SUMS 缓存（按 repo+tag 缓存，避免同一 release 重复下载）
    sha256_cache = {}

    for tmpl in discovered:
        name = tmpl["name"]
        repo = tmpl["repo"]
        tag = tmpl["tag"]
        print(f"\n处理模板: {name}")

        out_dir = OUTPUT_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)

        assets = tmpl["assets"]

        # 1. 获取 SHA256SUMS（按 release 缓存）
        cache_key = f"{repo}@{tag}"
        if cache_key not in sha256_cache:
            print("  下载 SHA256SUMS ...")
            sha256_cache[cache_key] = download_sha256sums(assets)
            if sha256_cache[cache_key]:
                print(f"    解析到 {len(sha256_cache[cache_key])} 条记录")
            else:
                print("    未找到 SHA256SUMS 文件")
        sha256_map = sha256_cache[cache_key]

        # 2. 收集所有平台的下载信息
        platforms = collect_platforms(assets, name, sha256_map)
        print(f"  平台覆盖: {len(platforms)}/{len(ALL_PLATFORMS)}")

        # 3. 找到当前平台的二进制
        asset = find_binary_asset(assets, name, current_os, current_arch)
        if not asset:
            print(f"  ⚠ 未找到 {current_os}-{current_arch} 的二进制")
            # 仍保存 platforms 信息
            meta = {
                "name": name,
                "repo": repo,
                "owner": tmpl["owner"],
                "version": tmpl["version"],
                "tag": tag,
                "published_at": tmpl["published_at"],
                "html_url": tmpl["html_url"],
                "platforms": platforms,
            }
            with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            continue

        # 4. 下载二进制
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

        # 5. 运行 --manifest
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

        # 6. 运行 --example
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

        # 7. 管道转换：cat example.md | ./binary → output.typ
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

        # 8. 获取 README.md
        print("  获取 README.md ...")
        readme_content = fetch_readme(repo, name)
        (out_dir / "README.md").write_text(readme_content, encoding="utf-8")

        # 9. 保存元数据（包含 platforms）
        meta = {
            "name": name,
            "repo": repo,
            "owner": tmpl["owner"],
            "version": tmpl["version"],
            "tag": tag,
            "published_at": tmpl["published_at"],
            "html_url": tmpl["html_url"],
            "platforms": platforms,
        }
        with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 清理二进制（不传到下一个 Job）
        binary_path.unlink(missing_ok=True)

        print(f"  完成 ✓")

    # ─── Hero 分帧（仅 gongwen）───────────────────────────────────
    generate_hero_source(discovered, current_os, current_arch)


def generate_hero_source(discovered, current_os, current_arch):
    """生成 Hero 分帧 SVG 源码（仅 gongwen）。"""
    gongwen_dir = OUTPUT_DIR / "gongwen"
    example_file = gongwen_dir / "example.md"
    binary_path = gongwen_dir / "presto-template-gongwen"

    if not example_file.exists():
        return

    print("\n=== 生成 Hero 分帧 SVG 源码 ===")
    example_text = example_file.read_text(encoding="utf-8")

    # 重新下载二进制用于 hero 帧生成
    for tmpl in discovered:
        if tmpl["name"] == "gongwen":
            asset = find_binary_asset(tmpl["assets"], "gongwen", current_os, current_arch)
            if asset:
                resp = requests.get(asset["browser_download_url"], headers=github_headers(), stream=True)
                if resp.status_code == 200:
                    with open(binary_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    binary_path.chmod(
                        binary_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
                    )
            break

    if not binary_path.exists():
        return

    frames = generate_hero_frames(example_text)
    for i, frame_md in enumerate(frames):
        print(f"  生成 hero-frame-{i} ...")
        result = safe_run(
            [str(binary_path)],
            input_data=frame_md.encode("utf-8"),
        )
        if result and result.returncode == 0:
            (gongwen_dir / f"hero-frame-{i}.typ").write_bytes(result.stdout)
        else:
            print(f"    ⚠ hero-frame-{i} 生成失败")

    binary_path.unlink(missing_ok=True)


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
    """汇总所有模板的 manifest.json，生成 registry.json v2 索引。"""
    print("=== Generate registry index (v2) ===")

    deploy_dir = OUTPUT_DIR / "deploy"
    templates = []

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

            # trust 判定
            owner = meta.get("owner", "")
            trust = "official" if owner == "Presto-io" else "community"

            # 预览图路径（相对于 templates 根目录，扫描所有页）
            preview_svgs = sorted(tmpl_dir.glob("preview-*.svg"))
            preview_images = [f"{tmpl_dir.name}/{svg.name}" for svg in preview_svgs]

            templates.append({
                "name": manifest.get("name", tmpl_dir.name),
                "displayName": manifest.get("displayName", ""),
                "description": manifest.get("description", ""),
                "version": manifest.get("version", ""),
                "author": manifest.get("author", ""),
                "repo": meta.get("repo", ""),
                "license": manifest.get("license", ""),
                "category": manifest.get("category", ""),
                "keywords": manifest.get("keywords", []),
                "trust": trust,
                "platforms": meta.get("platforms", {}),
                "minPrestoVersion": manifest.get("minPrestoVersion", ""),
                "requiredFonts": manifest.get("requiredFonts", []),
                "previewImages": preview_images,
            })

    # 合并已有的 registry 中未更新的模板
    existing = load_existing_registry()
    updated_names = {t["name"] for t in templates}
    for existing_tmpl in existing.get("templates", []):
        if existing_tmpl["name"] not in updated_names:
            templates.append(existing_tmpl)

    # 生成 v2 registry.json
    registry = {
        "version": 2,
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
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

    print(f"\n索引完成: {len(templates)} 个模板")


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
              index      生成 registry.json v2 索引
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
    p_index = subparsers.add_parser("index", help="生成 registry.json v2 索引")

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
