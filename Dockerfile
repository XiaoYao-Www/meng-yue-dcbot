# 1. 使用輕量化的 Python 3.12 映像檔
FROM python:3.12-slim-bookworm

# 2. 安裝官方 uv 執行檔
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 3. 設定 Python 與 uv 環境變數
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 4. 建立非 root 使用者（安全最佳實踐）
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --gid 1001 appuser

# 5. 設定工作目錄
WORKDIR /app

# 6. 建立空的 data/ 目錄供使用者掛載資料庫與檔案
RUN mkdir -p /app/data && chown -R appuser:appgroup /app/data

# 7. 先複製依賴設定與 lockfile，利用快取層安裝依賴
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 8. 複製專案原始碼（受 .dockerignore 保護）
COPY . .

# 9. 將 uv 建立的虛擬環境加入 PATH
ENV PATH="/app/.venv/bin:$PATH"

USER appuser

# 10. 啟動機器人
CMD ["python", "main.py"]