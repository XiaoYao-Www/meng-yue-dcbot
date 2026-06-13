# 1. 使用輕量化的 Python 3.12
FROM python:3.12-slim

# 2. 設定工作目錄
WORKDIR /app

# 3. 安裝最新版 Poetry (支援 Poetry 2.0+)
RUN pip install --no-cache-dir poetry

# 4. 複製依賴設定檔與 lock 檔
COPY pyproject.toml poetry.lock* ./

# 5. 設定 poetry 不要建立虛擬環境，並安裝依賴
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# 6. 複製其餘程式碼
COPY . .

# 7. 啟動程式
CMD ["python", "main.py"]