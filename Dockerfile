# 1. 使用輕量化的 Python 3.12
FROM python:3.12-slim

# 強制讓 Python 即時輸出，不要暫存
ENV PYTHONUNBUFFERED=1

# 建立非 root 使用者（安全最佳實踐）
RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --gid 1001 appuser

# 2. 設定工作目錄
WORKDIR /app

# 3. 安裝最新版 Poetry (支援 Poetry 2.0+)
RUN pip install --no-cache-dir poetry

# 4. 複製依賴設定檔與 lock 檔
COPY pyproject.toml poetry.lock* ./

# 建立空的 data/ 目錄供使用者掛載
RUN mkdir -p /app/data && chown appuser:appgroup /app/data

# 5. 設定 poetry 不要建立虛擬環境，並安裝依賴
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# 6. 複製其餘程式碼
COPY . .

USER appuser

# 7. 啟動程式
CMD ["python", "main.py"]