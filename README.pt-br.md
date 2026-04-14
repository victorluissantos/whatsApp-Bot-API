<p align="right">
  <a href="README.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-united-states_1f1fa-1f1f8.png" alt="English" width="30"/></a>
  <a href="README.pt-br.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-brazil_1f1e7-1f1f7.png" alt="Português" width="30"/></a>
  <a href="README.es.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-spain_1f1ea-1f1f8.png" alt="Español" width="30"/></a>
</p>

# WhatsApp Bot API - FastAPI

Este projeto foi migrado de Flask para FastAPI para melhorar a performance e fornecer documentação automática da API.
Agora também utiliza RabbitMQ para fila de envio assíncrono de mensagens, mantendo no MongoDB o histórico de status e a configuração de webhook.

## Principais Mudanças

- **Framework**: Migrado de Flask para FastAPI
- **Arquivo principal**: `main.py` (anteriormente `app.py`)
- **Documentação**: Swagger UI automática disponível em `/docs`
- **Validação**: Modelos Pydantic para validação de dados
- **Performance**: Melhor performance com FastAPI
- **Mensageria assíncrona**: Fila RabbitMQ para envios assíncronos (`/sendMessageAsync`) com status persistido no MongoDB

## Endpoints Disponíveis

### GET /
- Página inicial com documentação da API

### POST /sendText
- Envia mensagem de texto via WhatsApp
- Parâmetros: `phone` (string, max 22 chars), `text` (string, max 800 chars)

### GET /sendText
- Envia mensagem de texto via WhatsApp (método GET)
- Parâmetros: `phone` (string, max 22 chars), `text` (string, max 800 chars)

### POST /sendMultText
- Envia mensagem com URL via WhatsApp
- Parâmetros: `phone` (string, max 22 chars), `text` (string, max 800 chars)

### GET /sendMultText
- Envia mensagem com URL via WhatsApp (método GET)
- Parâmetros: `phone` (string, max 22 chars), `text` (string, max 800 chars)

## Telas do Sistema

### Tela Home

![Home do Sistema](assets/home.png)

### Tela Swagger

Acesse a documentação interativa da API (Swagger UI) em [`/docs`](http://localhost:8000/docs):

![Swagger UI](assets/swagger.png)

## Documentação da API

Acesse `/docs` para ver a documentação interativa da API (Swagger UI).

## Execução

### Com Docker Compose
```bash
docker compose up --build
```

### Localmente
```bash
pip install -r IaC/flask/requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Debug Visual (VNC)

- Por padrão, o Selenium roda oculto (`headless`) no `main.py` com:
  - `WINDOW_SHOW_DEBUG = False`
- Para mostrar a janela do navegador durante o debug, altere para:
  - `WINDOW_SHOW_DEBUG = True`
- Após alterar, recrie e reinicie os containers:

```bash
docker compose down
docker compose up -d --build
```

### Verificar a porta do VNC

O servidor VNC é exposto na porta **5914** do host (veja `5914:5914` no `docker-compose.yml`). Com os containers no ar, confira se a porta está em escuta antes de abrir o visualizador:

```bash
ss -tln | grep 5914
```

Ou teste a conectividade TCP:

```bash
nc -zv 127.0.0.1 5914
```

Com Docker Compose, você também pode exibir o mapeamento publicado da porta do serviço:

```bash
docker compose port fastapi 5914
```

- Para abrir a janela do container via VNC:

```bash
gvncviewer 127.0.0.1:5914
```

- Se estiver usando TigerVNC:

```bash
vncviewer 127.0.0.1:5914
```

- Quando o cliente VNC solicitar a senha, use:
  - `V0oiye3R`

## Estrutura do Projeto

```
├── main.py                 # Aplicação FastAPI principal
├── datasource/            # Módulos de dados
├── static/               # Arquivos estáticos
├── templates/            # Templates HTML
├── IaC/flask/           # Configurações Docker
│   ├── Dockerfile
│   ├── requirements.txt
│   └── entry_point.sh
└── docker-compose.yml
```

## Variáveis de Ambiente

Configure as seguintes variáveis no arquivo `.env`:

- `MONGOUSER` (ou `MONGO_USER` no código): usuário do MongoDB — o `docker-compose` usa `MONGOUSER`
- `MONGOPASSWORD`: senha do MongoDB
- `MONGODB`: nome do banco de dados
- `FASTAPIPORT`: porta da aplicação (padrão: 8000)
- `FASTAPINAME`: nome do container (padrão: fastapi-app)
- `RABBITMQ_USER`: usuário do RabbitMQ
- `RABBITMQ_PASS`: senha do RabbitMQ
- `TZ`: fuso horário do container ([nome IANA](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)). Afeta o sistema, o Python e o Chrome/WhatsApp Web. Se não definir, o Compose usa `UTC`.

### Fuso horário (Docker e WhatsApp Web)

A imagem do serviço FastAPI inclui `tzdata` e repassa `TZ` do `.env` (veja `docker-compose.yml`). Sem isso, o container costuma ficar em **UTC**, e os horários nas mensagens do WhatsApp Web podem parecer deslocados em relação ao seu fuso local (não é “inglês do Chrome” em si — é o relógio do container).

1. Defina `TZ` no `.env` conforme sua região (exemplos na tabela abaixo).
2. Reinicie o stack: `docker compose down && docker compose up -d --build` (faça rebuild uma vez após atualizar o projeto que adicionou `tzdata`).

**Como descobrir o identificador**

- Lista completa: [Wikipedia — fusos tz](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (use a coluna *TZ identifier* no `TZ`).
- Mapa / relógio mundial: [timeanddate.com](https://www.timeanddate.com/worldclock/) (confira o nome IANA correspondente na lista acima).

| Região / caso | Valor de `TZ` (IANA) |
|---------------|----------------------|
| Brasil (Brasília) | `America/Sao_Paulo` |
| Portugal | `Europe/Lisbon` |
| Espanha (península) | `Europe/Madrid` |
| Reino Unido | `Europe/London` |
| EUA — leste | `America/New_York` |
| EUA — oeste | `America/Los_Angeles` |
| UTC | `UTC` |

### Configuração Inicial

1. Copie o arquivo de exemplo:
```bash
cp env.example .env
```

2. Edite o arquivo `.env` com suas configurações:
```bash
nano .env
```

3. Execute o projeto:
```bash
docker compose up --build
```

---

## Contato

Desenvolvido por **Victor Luis Santos**  
[LinkedIn](https://br.linkedin.com/in/victor-luis-santos)
