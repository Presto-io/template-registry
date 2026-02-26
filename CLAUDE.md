# template-registry — AI 开发指南

> 组织级规则见 `../Presto-homepage/docs/ai-guide.md`
> 扩展生态规范见 `../Presto-homepage/docs/specs/extension-spec.md`
> 架构设计见 `../Presto-homepage/docs/specs/Presto-architecture.md` 第四节
> Verified 模板方案见 `../Presto-homepage/docs/specs/verified-templates-design.md`

自动发现、收录社区模板，生成静态索引和预览资源，推送到 `registry-deploy` 仓库供 Cloudflare Pages 部署。

## 技术栈

- Python（用 `uv` 管理）+ GitHub Actions
- Typst CLI 0.14.2（编译 SVG 预览）

## 仓库结构

```
templates/              ← CI 生成的模板资源（manifest、README、preview SVG）
verified-templates.json ← verified 模板收录配置（repo + 锁定 tag）
registry.json           ← CI 生成的索引
scripts/
  build_registry.py     ← 主构建脚本（discover / extract / compile / index / build）
  download_fonts.py     ← 字体下载辅助
fonts/                  ← 编译 SVG 所需字体
Dockerfile              ← 沙箱镜像
.github/workflows/
  update-registry.yml   ← CI 主流程
  check-versions.yml    ← cron 版本检测，自动提 PR
  build-verified.yml    ← verified 模板编译
```

## CI 关键约束

- **双 Job 安全隔离**：extract（低权限，运行不可信二进制）→ compile-and-deploy（高权限）
- 二进制执行超时 30 秒，输出大小限制（manifest < 1MB, example < 1MB, typst < 10MB）
- **增量检测**：只处理版本号变化的模板，`force_rebuild=true` 时处理所有
- 推送到 `registry-deploy` 的 `templates/` 子目录，使用 `PRESTO_PAT`

## trust 判断规则

- `repo.owner.login === 'Presto-io'` → `official`
- 模板在 `verified-templates.json` 中且已编译 → `verified`
- 其他 → `community`

## Verified 模板编译

`build_registry.py build` 子命令：从源码交叉编译 6 平台二进制。

- 安全约束：`CGO_ENABLED=0`、`--network none`（编译阶段禁止联网）、`--read-only`、5 分钟超时、50MB 产物限制
- 编译失败则跳过，不影响其他模板

## Hero 分帧 SVG

为官网 Hero 动画生成分帧 SVG（仅对 `gongwen` 模板），截取 example.md 不同长度编译为 `hero-frame-{0..3}.svg`。

## 注意事项

- Python 脚本使用 `requests` 库访问 GitHub API
- 需要 `GITHUB_TOKEN` 环境变量避免 rate limit
- Typst CLI 版本应与 Presto 使用的版本一致（当前 0.14.2）
- SVG 中的字体如果缺失，Typst 会 fallback，但预览效果可能不理想
- 所有生成的文件都应该 commit 到仓库（便于 diff 和回溯）
