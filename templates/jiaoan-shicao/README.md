# Presto Official Templates

Presto 官方免费模板集合。每个模板是一个独立的 Go 程序，遵循 Presto 模板协议（stdin Markdown → stdout Typst）。

默认转换模式必须把 stdin 中的 Markdown 转换为 stdout 中的非空 Typst 源码。转换失败、panic 或生成空白 Typst 都必须写入 stderr 并以非 0 状态退出；`--manifest`、`--example`、`--version`、`--info` 仍按各自协议输出。

## 包含模板

| 模板 | 说明 |
|------|------|
| `gongwen` | 符合 GB/T 9704-2012 标准的类公文排版 |
| `jiaoan-shicao` | 实操教案 Markdown → 标准表格排版 |
| `jiaoan-jihua` | 授课进度计划表 Markdown → 标准表格排版 |

## 快速开始

### 构建

```bash
# 构建所有模板
make build-all

# 构建单个模板
make build NAME=gongwen
make build NAME=jiaoan-jihua
```

### 测试

```bash
make test
```

## Code signing policy

Windows `.exe` release assets follow the [Code signing policy](docs/windows-code-signing.md). Public trusted signing is limited to Presto-io controlled official Windows templates and future Presto-reviewed verified Windows templates.

## Privacy

Presto Official Templates run locally and do not collect or transmit personal data. See the [Privacy Policy](docs/privacy-policy.md).

### 安装到 Presto

```bash
make preview NAME=gongwen
make preview NAME=jiaoan-jihua
```

## 开发者

如果你想开发自己的模板，请参考：
- [CONVENTIONS.md](CONVENTIONS.md) — 模板开发规范
- [presto-template-starter-go](https://github.com/Presto-io/presto-template-starter-go) — Go 脚手架
- [presto-template-starter-rust](https://github.com/Presto-io/presto-template-starter-rust) — Rust 脚手架
- [presto-template-starter-typescript](https://github.com/Presto-io/presto-template-starter-typescript) — TypeScript 脚手架

## 协议

MIT
