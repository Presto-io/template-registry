# 安全审查报告

> 审查日期：2025-02-25
> 审查范围：`template-registry` 全仓库代码
> 审查文件：`scripts/build_registry.py`、`scripts/download_fonts.py`、`.github/workflows/update-registry.yml`、`Dockerfile`

## 总览

| 严重程度 | 数量 |
|----------|------|
| CRITICAL | 1    |
| HIGH     | 3    |
| MEDIUM   | 3    |
| LOW      | 3    |

---

## CRITICAL-1: 下载的二进制在执行前未校验 SHA256

**位置**：`scripts/build_registry.py` L347-384

代码下载了 `SHA256SUMS` 文件并解析了哈希值，但**从未实际校验下载的二进制文件**。SHA256 仅被写入 `meta.json` 供分发使用。这意味着：

- MITM 攻击可替换二进制而不被检测
- GitHub release 被篡改后无法发现
- `download_sha256sums()` + `parse_sha256sums()` 做了解析工作，但 `cmd_extract()` 中没有任何 `hashlib.sha256` 调用

```python
# 当前：下载后直接设执行权限并运行
binary_path.chmod(...)
result = safe_run([str(binary_path), "--manifest"])

# 缺失：应在 chmod 之前校验
import hashlib
actual = hashlib.sha256(binary_path.read_bytes()).hexdigest()
if actual != expected_hash:
    raise SecurityError(...)
```

---

## HIGH-1: 不可信二进制无网络隔离

**位置**：`scripts/build_registry.py` L158-175

`safe_run()` 仅限制了 `PATH` 环境变量和超时时间，但二进制仍以 CI runner 用户权限运行，具有**完整的网络访问能力**：

- 恶意二进制可以将 runner 文件系统的内容外传到攻击者服务器
- 可以下载并执行额外 payload
- 可以扫描内部网络

虽然 Job 1 (`extract`) 没有传入 secrets，这是一个好的设计，但 `GITHUB_TOKEN` 在 `discover` 步骤设置后，可能仍存留在进程环境中被后续步骤的二进制读取。

**建议**：使用 Docker 容器或 `unshare --net` 来隔离不可信二进制的网络访问。

---

## HIGH-2: 自托管 Runner 上编译不可信 `.typ` 文件

**位置**：`.github/workflows/update-registry.yml` L53-56

Job 2 在 `[self-hosted, macOS, ARM64]` runner 上编译由不可信二进制生成的 `.typ` 文件。Typst 的 `#read()` 函数可以读取 runner 上的任意文件。由于自托管 runner：

- **持久化状态**（不像 GitHub-hosted runner 每次销毁）
- **拥有 `PRESTO_PAT` secret**（虽然 Typst 无法直接读取环境变量，但可以读取 `~/.config/` 等位置的凭据文件）
- 恶意 `.typ` 可以通过 `#read("/etc/passwd")` 等方式将敏感信息嵌入生成的 SVG 中

---

## HIGH-3: `GITHUB_TOKEN` 可能泄露给不可信二进制

**位置**：`.github/workflows/update-registry.yml` L33-42

`discover` 步骤设置了 `GITHUB_TOKEN` 环境变量，而 `extract` 步骤也同样设置了它。虽然 `safe_run()` 用 `env={"PATH": ...}` 覆盖了环境变量，**但这仅适用于 `safe_run()` 内的子进程**。

```yaml
- name: Extract template data
  env:
    GITHUB_TOKEN: ${{ github.token }}   # ← 传给了整个 step
  run: python scripts/build_registry.py extract
```

`safe_run()` 中 `env={"PATH": "..."}` 确实阻止了二进制直接读取环境变量，但：
- 二进制可以读取 `/proc/1/environ`（Linux）获取父进程环境
- 或通过 `/proc/self/fd/` 等途径间接获取

---

## MEDIUM-1: 模板名未做路径安全校验

**位置**：`scripts/build_registry.py` L89-104, L342

`extract_template_names()` 从 asset 文件名中提取模板名，然后直接用于构建文件路径：

```python
out_dir = OUTPUT_DIR / name   # name 来自 asset 文件名
```

虽然 GitHub asset 名称不允许 `/`，但模板名未做显式校验（如禁止 `..`、`.`）。如果将来数据源扩展，这会成为路径穿越漏洞。

---

## MEDIUM-2: `compile` 命令的 Typst 调用无超时和沙箱

**位置**：`scripts/build_registry.py` L566-572

与 `safe_run()` 不同，`cmd_compile()` 中的 `subprocess.run()` 调用**没有超时限制**，也没有限制环境变量：

```python
result = subprocess.run(
    ["typst", "compile", "--font-path", font_path, str(typ_file), svg_pattern],
    capture_output=True,
    text=True,
    # ← 无 timeout，无 env 限制
)
```

恶意 `.typ` 文件可能导致 Typst 无限循环或消耗大量资源。

---

## MEDIUM-3: `curl | sh` 安装 uv 未固定版本

**位置**：`.github/workflows/update-registry.yml` L63-67

```yaml
curl -LsSf https://astral.sh/uv/install.sh | sh
```

uv 安装脚本未固定版本。如果 `astral.sh` 被入侵，可以注入恶意代码到**拥有 `contents: write` 权限和 `PRESTO_PAT` secret** 的 Job 2 中。相比之下，Typst 版本已正确固定。

---

## LOW-1: 缺少 `.gitignore`

仓库没有 `.gitignore` 文件。可能导致：
- `output/` 目录中的临时二进制文件被意外提交
- `scripts/__pycache__/` 已出现在未跟踪文件中
- `.env` 文件如果被创建会被意外提交

---

## LOW-2: `download_fonts.py` 无完整性校验

**位置**：`scripts/download_fonts.py` L38

字体下载无哈希校验。当前下载列表为空所以风险为零，但如果将来添加字体，应同时添加 SHA256 校验。

---

## LOW-3: Dockerfile 依赖未固定版本

**位置**：`Dockerfile` L39

```dockerfile
RUN pip3 install --break-system-packages requests
```

`requests` 版本未固定，存在供应链攻击风险。建议使用 `requests==2.32.3` 等固定版本。

---

## 安全设计亮点

项目已经做对的地方：

1. **双 Job 隔离** — 不可信二进制在低权限 Job 中运行，secrets 仅在 Job 2 中使用
2. **`safe_run()` 环境清洗** — `env={"PATH": "..."}` 阻止了大多数环境变量泄露
3. **超时和大小限制** — 30 秒超时 + 1MB/10MB 大小限制防止资源耗尽
4. **不使用 `shell=True`** — 所有 `subprocess.run()` 调用都使用列表形式，避免了 shell 注入
5. **Dockerfile 非 root 用户** — sandbox 用户限制了容器内权限
6. **Typst 版本固定** — 编译工具链版本可控

---

## 修复优先级建议

| 优先级 | 问题 | 修复难度 |
|--------|------|----------|
| P0 | SHA256 校验缺失 | 低 — 添加 `hashlib` 校验即可 |
| P1 | 不可信二进制网络隔离 | 中 — 需要 Docker 或 `unshare` |
| P1 | 自托管 Runner 编译不可信 `.typ` | 中 — 可用 Docker 隔离或限制 Typst `#read` |
| P1 | `GITHUB_TOKEN` 泄露风险 | 低 — `extract` 步骤移除 `GITHUB_TOKEN` 环境变量 |
| P2 | 模板名路径校验 | 低 — 添加正则白名单 |
| P2 | `compile` 无超时 | 低 — 添加 `timeout` 参数 |
| P2 | `curl \| sh` 未固定版本 | 低 — 固定 uv 版本 |

---

## 修复记录（2026-02-25）

| 问题 | 状态 | 修复方式 |
|------|------|----------|
| CRITICAL-1: SHA256 校验缺失 | 已修复 | `verify_sha256()` 在 chmod 后、执行前校验二进制哈希 |
| HIGH-1: 不可信二进制网络隔离 | 已修复 | Linux 上 `safe_run()` 使用 `unshare --net` 隔离网络 |
| HIGH-2: 自托管 Runner 编译不可信 `.typ` | 已修复 | compile 步骤改为 `docker run --network none --read-only` 沙箱执行，runner 迁移至 NAS6 (x86_64 Linux) |
| HIGH-3: `GITHUB_TOKEN` 泄露 | 已修复 | 移除 extract 步骤的 `GITHUB_TOKEN`，`fetch_readme()` 迁移至 discover 阶段 |
| MEDIUM-1: 模板名路径穿越 | 已修复 | 添加 `VALID_TEMPLATE_NAME` 正则白名单 `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$` |
| MEDIUM-2: compile 无超时/沙箱 | 已修复 | 添加 120s 超时 + `env={"PATH": ...}` 环境隔离 |
| MEDIUM-3: uv 未固定版本 | 已修复 | 固定为 `uv/0.10.6` |
| LOW-1: 缺少 `.gitignore` | 已修复 | 创建 `.gitignore`（排除 output/、__pycache__/、.env 等） |
| LOW-2: 字体下载无完整性校验 | 已修复 | 添加 SHA256 验证框架（当前下载列表为空） |
| LOW-3: Dockerfile requests 未固定 | 已修复 | 固定为 `requests==2.32.5` |
