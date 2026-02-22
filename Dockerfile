# Typst CLI 沙箱镜像
# 用于安全地编译社区模板的 .typ 文件为 SVG 预览
#
# 构建：docker build -t presto-typst-sandbox .
# 使用：docker run --rm -v $(pwd)/output:/work presto-typst-sandbox \
#         typst compile --font-path /fonts input.typ output.svg

FROM ubuntu:24.04 AS base

ARG TYPST_VERSION=0.14.2

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    fontconfig \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 安装 Typst CLI
RUN curl -fsSL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
    | tar -xJ --strip-components=1 -C /usr/local/bin/ \
    && chmod +x /usr/local/bin/typst \
    && typst --version

# 安装基础中文字体
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# 字体目录（挂载自定义字体）
RUN mkdir -p /fonts
COPY fonts/ /fonts/

# Python 依赖
RUN pip3 install --break-system-packages requests

# 工作目录
WORKDIR /work

# 非 root 用户（安全）
RUN useradd -m -s /bin/bash sandbox
USER sandbox

ENTRYPOINT ["typst"]
CMD ["--help"]
