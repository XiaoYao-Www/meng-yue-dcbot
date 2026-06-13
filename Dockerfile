FROM python:3.12-slim

# 設定環境變數
ENV POETRY_HOME=/opt/poetry
ENV PATH=$POETRY_HOME/bin:$PATH

# 安裝 poetry
RUN apt-get update && apt-get install -y curl && \
    curl -sSL https://install.python-poetry.org | python3 - && \
    apt-get clean

WORKDIR /app

# 複製設定檔
COPY pyproject.toml ./

# 安裝依賴 (不建立虛擬環境，直接裝在系統中，方便容器執行)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# 複製程式碼
COPY . .

CMD ["python", "main.py"]