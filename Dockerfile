# 基于现有的基础镜像
FROM docker.m.daocloud.io/langchain/langgraph-api:3.13

# 设置工作目录
WORKDIR /deps/app_final_test

# 安装 uv
RUN pip install --no-cache-dir \
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
    uv

# 复制项目文件
COPY . .

# 安装项目依赖（使用锁定版本确保可重复构建）
RUN uv pip install --system \
    --index-strategy unsafe-best-match \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.lock.txt

# 安装项目本身为可编辑包（不重复解析依赖，避免覆盖 requirements_1.txt 锁定版本）
RUN uv pip install --system --no-deps \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --extra-index-url https://mirrors.aliyun.com/pypi/simple/ \
    -e .

# 设置环境变量
ENV PYTHONPATH=/deps/app_final_test:/deps/app_final_test/src
