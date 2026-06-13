# 1. 使用輕量化的 Python 3.12
FROM python:3.12-slim

# 2. 設定工作目錄
WORKDIR /app

# 3. 複製專案的所有檔案進去（包含 pyproject.toml 和 main.py）
COPY . .

# 4. 使用原生 pip 直接安裝當前目錄 (.)，它會自動解析 pyproject.toml 並安裝依賴
RUN pip install --no-cache-dir .

# 5. 啟動你的程式
CMD ["python", "main.py"]