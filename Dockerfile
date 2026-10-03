FROM python:3.13-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependências básicas
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Copia requirements
COPY requirements.txt .

# Instala Python + Playwright
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Cria local dos navegadores
RUN mkdir -p /ms-playwright

# Instala Chromium + todas as dependências necessárias
RUN PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    python -m playwright install --with-deps chromium

# Copia o projeto
COPY . .

# Porta do Railway
EXPOSE 8080

# Inicia o Flask pelo Gunicorn
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 4 --timeout 120 app:app"]