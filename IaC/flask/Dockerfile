FROM python:3.11-slim

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Instalar Google Chrome
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Definir diretório de trabalho
WORKDIR /code

# Copiar requirements e instalar dependências Python
COPY IaC/flask/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação
COPY . .

# Copiar e configurar entry_point.sh
COPY IaC/flask/entry_point.sh /entry_point.sh
RUN chmod +x /entry_point.sh

# Expor porta
EXPOSE 8000

# Comando para executar a aplicação
CMD ["/entry_point.sh"]