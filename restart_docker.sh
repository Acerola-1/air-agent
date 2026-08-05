#!/bin/bash

# ============================================
# Docker 项目快速重启脚本
# ============================================

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查命令是否存在
check_command() {
    if ! command -v $1 &> /dev/null; then
        log_error "命令 $1 未找到，请先安装"
        exit 1
    fi
}

# 显示帮助信息
show_help() {
    echo "使用方法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help     显示此帮助信息"
    echo "  -b, --build    重新构建镜像（默认：否）"
    echo "  -l, --logs     启动后显示日志（默认：是）"
    echo "  -f, --force    强制停止容器（默认：否）"
    echo "  -c, --clean    清理未使用的镜像和容器"
    echo "  -t, --test     重启后测试 API"
    echo ""
    echo "示例:"
    echo "  $0              # 快速重启"
    echo "  $0 -b -t        # 重新构建并测试"
    echo "  $0 -c -f        # 清理并强制重启"
}

# 初始化变量
REBUILD=false
SHOW_LOGS=true
FORCE_STOP=false
CLEAN=false
TEST_API=false

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -b|--build)
            REBUILD=true
            shift
            ;;
        -l|--logs)
            SHOW_LOGS=true
            shift
            ;;
        -f|--force)
            FORCE_STOP=true
            shift
            ;;
        -c|--clean)
            CLEAN=true
            shift
            ;;
        -t|--test)
            TEST_API=true
            shift
            ;;
        *)
            log_error "未知选项: $1"
            show_help
            exit 1
            ;;
    esac
done

# 主函数
main() {
    log_info "========================================"
    log_info "   Docker 项目快速重启脚本"
    log_info "========================================"

    # 检查必要命令
    check_command docker

    # 进入项目目录
    PROJECT_DIR="/home/ai/app_final_test"
    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "项目目录不存在: $PROJECT_DIR"
        exit 1
    fi

    cd "$PROJECT_DIR"
    log_info "工作目录: $(pwd)"

    # 清理选项
    if [ "$CLEAN" = true ]; then
        log_info "清理未使用的 Docker 资源..."
        docker system prune -f
        docker volume prune -f
    fi

    # 停止容器
    log_info "停止运行中的容器..."
    if [ "$FORCE_STOP" = true ]; then
        docker compose down --remove-orphans --timeout 10
    else
        docker compose down
    fi

    # 重建镜像选项
    if [ "$REBUILD" = true ]; then
        log_info "重新构建镜像..."
        docker compose build
    fi

    # 启动容器
    log_info "启动容器..."
    docker compose up -d

    # 等待容器启动
    log_info "等待容器启动..."
    sleep 5

    # 检查容器状态
    log_info "检查容器状态..."
    CONTAINER_STATUS=$(docker compose ps --services --filter "status=running")

    if [ -n "$CONTAINER_STATUS" ]; then
        log_success "容器启动成功!"
        echo "运行中的容器:"
        docker compose ps
    else
        log_error "容器启动失败!"
        if [ "$SHOW_LOGS" = true ]; then
            docker compose logs
        fi
        exit 1
    fi

    # 显示端口信息
    log_info "服务端口信息:"
    echo "  - LangGraph API: http://localhost:8125"
    echo "  - 容器内部端口: 8000"

    # 显示日志选项
    if [ "$SHOW_LOGS" = true ]; then
        log_info "显示最后 20 行日志:"
        docker compose logs --tail=20

        log_info "持续查看日志 (按 Ctrl+C 退出)..."
        echo "========================================"
        docker compose logs -f --tail=10 &
        LOG_PID=$!

        # 等待 10 秒后结束日志跟踪
        sleep 10
        kill $LOG_PID 2>/dev/null
    fi

    # 测试 API 选项
    if [ "$TEST_API" = true ]; then
        log_info "测试 API 连接..."

        # 等待 API 完全启动
        sleep 3

        if curl -s http://localhost:8125/ > /dev/null; then
            log_success "API 连接测试成功!"

            # 获取 API 基本信息
            log_info "API 基本信息:"
            curl -s http://localhost:8125/ | head -20
        else
            log_warning "API 连接测试失败，可能需要更多时间启动"
            log_info "查看详细错误:"
            docker compose logs --tail=30
        fi
    fi

    log_success "========================================"
    log_success "重启完成!"
    log_success "========================================"

    # 显示常用命令
    echo ""
    log_info "常用命令:"
    echo "  查看日志:    docker compose logs -f"
    echo "  查看状态:    docker compose ps"
    echo "  进入容器:    docker compose exec langgraph-api bash"
    echo "  停止容器:    docker compose down"
    echo "  重启单个:    docker compose restart langgraph-api"
    echo ""
    log_info "测试 API: curl http://localhost:8125/"
}

# 异常处理
trap 'log_error "脚本执行中断"; exit 1' INT TERM

# 执行主函数
main

exit 0
