# template-registry

Presto 模板注册表——自动发现、收录社区模板，生成静态索引和预览资源，推送到 `registry-deploy` 仓库供 Cloudflare Pages 部署。

## 工作原理

```
GitHub 模板仓库（presto-template topic）
        │
        ▼
  CI: discover ──→ 搜索 GitHub API，筛选新/更新的模板
        │
        ▼
  CI: extract  ──→ 下载二进制，提取 manifest / example / typst 源码
        │                （低权限 Job，无 secrets）
        ▼
  CI: compile  ──→ Typst CLI 编译 SVG 预览
        │                （高权限 Job，可信代码）
        ▼
  CI: index    ──→ 汇总生成 registry.json
        │
        ├──→ 提交到本仓库 templates/ + registry.json
        └──→ 推送到 registry-deploy → Cloudflare Pages
                    │
                    ▼
           registry.presto.app
```

## 目录结构

```
template-registry/
  templates/                    ← CI 生成的模板数据（含 SVG 预览）
    gongwen/
    jiaoan-shicao/
  registry.json                 ← CI 生成的索引
  scripts/
    build_registry.py           ← 主构建脚本（discover/extract/compile/index）
    download_fonts.py           ← 字体下载辅助脚本
  fonts/                        ← 编译 SVG 所需的字体
  Dockerfile                    ← 沙箱镜像（Typst CLI + 基础字体）
  .github/workflows/
    update-registry.yml         ← CI 主流程
```

## CI 管线

### 双 Job 安全隔离

| Job | 权限 | 职责 |
|-----|------|------|
| `extract` | `contents: read` | 运行社区二进制（无 secrets） |
| `compile-and-deploy` | `contents: write` | Typst 编译 + 推送部署 |

### 触发条件

- **定时**：每 6 小时自动运行
- **手动**：支持 `force_rebuild` 参数强制重建所有模板

## 本地开发

```bash
# 安装依赖
pip install requests

# 搜索模板
export GITHUB_TOKEN="your_token"
python scripts/build_registry.py discover

# 提取数据
python scripts/build_registry.py extract

# 编译 SVG（需要 Typst CLI）
python scripts/build_registry.py compile --font-path fonts/

# 生成索引
python scripts/build_registry.py index
```

### Docker 沙箱

```bash
# 构建镜像
docker build -t presto-typst-sandbox .

# 编译 .typ 文件
docker run --rm -v $(pwd)/output:/work presto-typst-sandbox \
  compile --font-path /fonts input.typ output.svg
```

## 官方模板

当前收录的 Presto 官方模板：

| 名称 | 分类 | 说明 |
|------|------|------|
| `gongwen` | 政务 | 符合 GB/T 9704-2012 标准的类公文排版 |
| `jiaoan-shicao` | 教育 | 教案和实操文档模板 |

官方模板位于 [Presto-io/Presto](https://github.com/Presto-io/Presto) 仓库的 `cmd/gongwen/` 和 `cmd/jiaoan-shicao/` 目录。

## 社区模板

要让你的模板被自动收录：

1. 为仓库添加 `presto-template` topic
2. 创建 GitHub Release，包含 6 平台二进制文件
3. 二进制遵循 [扩展生态规范](extension-spec.md) 中的协议

## 相关文档

- [扩展生态规范](extension-spec.md) — 模板二进制协议、manifest schema、Release 命名规范
