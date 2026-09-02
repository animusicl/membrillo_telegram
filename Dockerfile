FROM python:3.11-slim

# System deps mínimos
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Crear directorio de trabajo
WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY memory.py llm.py telegram_bot.py ./

# Variables de entorno
ENV PYTHONUNBUFFERED=1

# Puerto (Telegram polling usa este puerto por defecto)
EXPOSE 8080

# Health check para Render
HEALTHCHECK CMD curl -f http://localhost:8080/health || exit 1

# Comando de entrada
CMD ["python", "telegram_bot.py"]