FROM python:3.12-slim

# 設定必要的環境變數
ENV POETRY_HOME=/opt/poetry
ENV POETRY_VERSION=1.8.2
ENV PATH=$POETRY_HOME/bin:$PATH

# 安裝 curl 以便下載 poetry，並安裝 poetry
RUN apt-get update && apt-get install -y curl && \
    curl -sSL https://install.python-poetry.org | python3 - && \
    apt-get purge -y --auto-remove curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 複製設定檔
COPY pyproject.toml poetry.lock* ./

# 設定 poetry 不要建立虛擬環境 (容器內直接用全域環境即可)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --only main

# 複製程式碼
COPY . .

CMD ["python", "main.py"]