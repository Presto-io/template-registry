# GPG 公钥目录

此目录用于存放社区开发者的 GPG 公钥，用于 `verified` 信任级别的验证。

## 注册公钥

将你的 GPG 公钥导出为 ASCII 格式，命名为 `<github-username>.asc`，提交 PR 到本仓库。

```bash
gpg --armor --export your-email@example.com > keys/<github-username>.asc
```

## 文件格式

```
keys/
├── README.md
├── alice.asc
└── bob.asc
```

## 验证流程

> 暂未实现。未来 CI 将自动验证 Release 签名，通过验证的模板将获得 `verified` 信任级别。
