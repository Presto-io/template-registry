# template-registry — AI 开发提示词

> 本文件是 `Presto-io/template-registry` 仓库的 CLAUDE.md。
> 这是一个待创建的新仓库。

---

## 仓库职责

自动发现、收录社区模板，生成静态索引和预览资源，推送到 `registry-deploy` 仓库供 Cloudflare Pages 部署。

## 共享规范

- 扩展生态规范：`extension-spec.md`
- 架构设计：`Presto-architecture.md` 第四节

---

## 仓库结构

```
template-registry/
  templates/                          ← CI 生成的输出（也存在仓库中）
    gongwen/
      manifest.json
      README.md
      example.md
      preview-1.svg
      preview-2.svg
    jiaoan-shicao/
      manifest.json
      README.md
      example.md
      preview-1.svg
  registry.json                       ← CI 生成的索引
  scripts/
    build_registry.py                 ← 主构建脚本
    download_fonts.py                 ← 字体下载辅助脚本
  fonts/                              ← 编译 SVG 所需的字体
    .gitkeep
  Dockerfile                          ← 沙箱镜像（Typst CLI + 基础字体）
  .github/workflows/
    update-registry.yml               ← CI 主流程
  CLAUDE.md                           ← 本文件
  README.md
```

---

## CI 管线设计

### 触发条件

```yaml
on:
  schedule:
    - cron: '0 */6 * * *'            # 每 6 小时
  workflow_dispatch:
    inputs:
      force_rebuild:
        description: '强制重建所有模板'
        type: boolean
        default: false
```

### 双 Job 安全隔离

**Job 1: extract（低权限）**

```yaml
jobs:
  extract:
    runs-on: ubuntu-latest
    permissions:
      contents: read                   # 只读，不传 secrets
    steps:
      - uses: actions/checkout@v4

      # 搜索 GitHub 上所有 presto-template topic 的仓库
      - name: Discover templates
        run: python scripts/build_registry.py discover

      # 对每个新/更新的模板：
      # 1. 下载当前平台（linux-amd64）的二进制
      # 2. 运行 ./binary --manifest → manifest.json
      # 3. 运行 ./binary --example → example.md
      # 4. 运行 cat example.md | ./binary → output.typ
      # 5. 从模板仓库获取 README.md
      - name: Extract template data
        run: python scripts/build_registry.py extract

      - uses: actions/upload-artifact@v4
        with:
          name: extracted-data
          path: output/
```

**Job 2: compile-and-deploy（高权限）**

```yaml
  compile-and-deploy:
    needs: extract
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4

      - uses: actions/download-artifact@v4
        with:
          name: extracted-data
          path: output/

      # 用 Typst CLI（可信）编译 SVG
      - name: Install Typst CLI
        run: |
          curl -fsSL https://typst.community/typst-install/install.sh | sh
          echo "$HOME/.local/bin" >> $GITHUB_PATH

      # 对每个模板的 output.typ 编译 SVG
      - name: Compile SVGs
        run: python scripts/build_registry.py compile --font-path fonts/

      # 生成 registry.json 索引
      - name: Generate registry index
        run: python scripts/build_registry.py index

      # 推送到 registry-deploy 仓库
      - name: Deploy to registry-deploy
        uses: cpina/github-action-push-to-another-repository@main
        env:
          SSH_DEPLOY_KEY: ${{ secrets.REGISTRY_DEPLOY_KEY }}
        with:
          source-directory: output/deploy/
          destination-github-username: Presto-io
          destination-repository-name: registry-deploy
          target-directory: templates/
          commit-message: "chore: update template registry"
```

### build_registry.py 主要功能

#### `discover` 子命令

```python
# 搜索 GitHub 上的模板仓库
# GET https://api.github.com/search/repositories?q=topic:presto-template&sort=updated
# 对比 registry.json 中已有版本，筛选出需要更新的
```

#### `extract` 子命令

```python
# 对每个需要更新的模板：
# 1. GET /repos/{owner}/{repo}/releases/latest → 找 linux-amd64 二进制
# 2. 下载二进制，设执行权限
# 3. 运行 --manifest, --example，管道转换
# 4. 获取仓库 README.md
# 5. 输出到 output/{name}/ 目录
```

安全注意事项：
- 二进制在无 secrets 的环境中运行
- 执行超时 30 秒
- 限制输出大小（manifest < 1MB, example < 1MB, typst < 10MB）

#### `compile` 子命令

```python
# 对每个模板的 output.typ：
# typst compile --font-path fonts/ output.typ preview-{page}.svg
# 收集所有页面的 SVG
```

#### `index` 子命令

```python
# 读取所有模板的 manifest.json
# 生成 registry.json：
# - categories: 从所有模板的 category 去重
# - templates: 精简索引（不含 requiredFonts, frontmatterSchema 等大字段）
# - trust: 根据仓库 owner 判断（Presto-io → official，其他 → community）
# - updatedAt: 当前时间
```

#### 增量检测

```python
# 读取当前 registry.json
# 对比每个模板的 version 与最新 Release tag
# 只处理版本号变化的模板
# force_rebuild=true 时处理所有模板
```

---

## registry.json 生成规则

```json
{
  "version": 1,
  "updatedAt": "ISO 8601",
  "categories": [
    // 从所有模板的 category 去重，附带中英文 label
    // 只包含有模板的分类
  ],
  "templates": [
    {
      "name": "manifest.name",
      "displayName": "manifest.displayName",
      "description": "manifest.description",
      "version": "manifest.version",
      "author": "manifest.author",
      "category": "manifest.category",
      "keywords": "manifest.keywords",
      "license": "manifest.license",
      "trust": "official | verified | community",
      "publishedAt": "release.published_at",
      "repository": "repo.html_url"
    }
  ]
}
```

trust 判断规则：
- `repo.owner.login === 'Presto-io'` → `official`
- 未来有签名验证 → `verified`
- 其他 → `community`

---

## 分类 label 映射

```python
CATEGORY_LABELS = {
    'government': {'zh': '政务', 'en': 'Government'},
    'education':  {'zh': '教育', 'en': 'Education'},
    'business':   {'zh': '商务', 'en': 'Business'},
    'academic':   {'zh': '学术', 'en': 'Academic'},
    'legal':      {'zh': '法务', 'en': 'Legal'},
    'resume':     {'zh': '简历', 'en': 'Resume'},
    'creative':   {'zh': '创意', 'en': 'Creative'},
    'other':      {'zh': '其他', 'en': 'Other'},
}
```

---

## Hero 分帧 SVG

为官网 Hero 动画生成分帧 SVG（仅对 `gongwen` 模板）：

```python
# 截取 example.md 不同长度：
# frame-0: 仅 frontmatter（--- ... ---）
# frame-1: frontmatter + 标题行
# frame-2: frontmatter + 标题 + 主送单位 + 第一段
# frame-3: 完整 example.md
#
# 每个版本分别：
# cat truncated.md | ./binary → output.typ
# typst compile output.typ → hero-frame-{n}.svg
```

输出到 `templates/gongwen/hero-frame-{0..3}.svg`。

---

## 将来扩展

此仓库的模式将被 `plugin-registry` 和 `agent-skill-registry` 复用。共享 CI workflow 在 `Presto-io/.github` 组织仓库中。

各 registry 的差异：
- topic 不同（`presto-template` vs `presto-plugin` vs `presto-agent-skill`）
- 推送的目标目录不同（`templates/` vs `plugins/` vs `agent-skills/`）
- 模板需要编译 SVG，插件可能不需要

---

## 注意事项

- Python 脚本使用 `requests` 库访问 GitHub API
- 需要 `GITHUB_TOKEN` 环境变量避免 rate limit
- Typst CLI 版本应与 Presto 使用的版本一致（当前 0.14.2）
- SVG 中的字体如果缺失，Typst 会 fallback，但预览效果可能不理想
- 所有生成的文件都应该 commit 到仓库（便于 diff 和回溯）
- 完成任务后立即 commit，消息用中文
