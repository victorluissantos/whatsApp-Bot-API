<p align="right">
  <a href="README.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-united-states_1f1fa-1f1f8.png" alt="English" width="30"/></a>
  <a href="README.pt-br.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-brazil_1f1e7-1f1f7.png" alt="Português" width="30"/></a>
  <a href="README.es.md"><img src="https://em-content.zobj.net/thumbs/120/apple/354/flag-spain_1f1ea-1f1f8.png" alt="Español" width="30"/></a>
</p>

# WhatsApp Bot API - FastAPI

Este proyecto fue migrado de Flask a FastAPI para mejorar el rendimiento y proporcionar documentación automática de la API.
Ahora también utiliza RabbitMQ para la cola de envío asíncrono de mensajes, manteniendo en MongoDB el historial de estados y la configuración del webhook.

## Cambios Principales

- **Framework**: Migrado de Flask a FastAPI
- **Archivo principal**: `main.py` (anteriormente `app.py`)
- **Documentación**: Swagger UI automática disponible en `/docs`
- **Validación**: Modelos Pydantic para validación de datos
- **Rendimiento**: Mejor rendimiento con FastAPI
- **Mensajería asíncrona**: Cola RabbitMQ para envíos asíncronos (`/sendMessageAsync`) con estado persistido en MongoDB

## Endpoints Disponibles

### GET /
- Página inicial con documentación de la API

### POST /sendText
- Envía mensaje de texto por WhatsApp
- Parámetros: `phone` (string, máx 22 caracteres), `text` (string, máx 800 caracteres)

### GET /sendText
- Envía mensaje de texto por WhatsApp (método GET)
- Parámetros: `phone` (string, máx 22 caracteres), `text` (string, máx 800 caracteres)

### POST /sendMultText
- Envía mensaje con URL por WhatsApp
- Parámetros: `phone` (string, máx 22 caracteres), `text` (string, máx 800 caracteres)

### GET /sendMultText
- Envía mensaje con URL por WhatsApp (método GET)
- Parámetros: `phone` (string, máx 22 caracteres), `text` (string, máx 800 caracteres)

## Pantallas del Sistema

### Pantalla Home

![Home del Sistema](assets/home.png)

### Pantalla Swagger

Accede a la documentación interactiva de la API (Swagger UI) en [`/docs`](http://localhost:8000/docs):

![Swagger UI](assets/swagger.png)

## Documentación de la API

Accede a `/docs` para ver la documentación interactiva de la API (Swagger UI).

## Ejecución

### Con Docker Compose
```bash
docker compose up --build
```

### Localmente
```bash
pip install -r IaC/flask/requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Depuracion Visual (VNC)

- Por defecto, Selenium se ejecuta oculto (`headless`) en `main.py` con:
  - `WINDOW_SHOW_DEBUG = False`
- Para mostrar la ventana del navegador durante la depuracion, cambialo a:
  - `WINDOW_SHOW_DEBUG = True`
- Despues del cambio, recrea y reinicia los contenedores:

```bash
docker compose down
docker compose up -d --build
```

### Verificar el puerto VNC

El servidor VNC se expone en el puerto **5914** del host (ve `5914:5914` en `docker-compose.yml`). Con los contenedores en marcha, comprueba que el puerto esté en escucha antes de abrir el visor:

```bash
ss -tln | grep 5914
```

O prueba la conectividad TCP:

```bash
nc -zv 127.0.0.1 5914
```

Con Docker Compose también puedes mostrar el mapeo publicado del puerto del servicio:

```bash
docker compose port fastapi 5914
```

- Para abrir la ventana del contenedor por VNC:

```bash
gvncviewer 127.0.0.1:5914
```

- Si usas TigerVNC:

```bash
vncviewer 127.0.0.1:5914
```

- Cuando el cliente VNC solicite la contrasena, use:
  - `V0oiye3R`

## Estructura del Proyecto

```
├── main.py                 # Aplicación principal FastAPI
├── datasource/            # Módulos de datos
├── static/               # Archivos estáticos
├── templates/            # Plantillas HTML
├── IaC/flask/           # Configuración Docker
│   ├── Dockerfile
│   ├── requirements.txt
│   └── entry_point.sh
└── docker-compose.yml
```

## Variables de Entorno

Configura las siguientes variables en el archivo `.env`:

- `MONGOUSER` (o `MONGO_USER` en el código): usuario de MongoDB — `docker-compose` usa `MONGOUSER`
- `MONGOPASSWORD`: contraseña de MongoDB
- `MONGODB`: nombre de la base de datos
- `FASTAPIPORT`: puerto de la aplicación (por defecto: 8000)
- `FASTAPINAME`: nombre del contenedor (por defecto: fastapi-app)
- `RABBITMQ_USER`: usuario de RabbitMQ
- `RABBITMQ_PASS`: contraseña de RabbitMQ
- `TZ`: zona horaria del contenedor ([nombre IANA](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)). Afecta el sistema, Python y Chrome/WhatsApp Web. Si no se define, Compose usa `UTC`.

### Zona horaria (Docker y WhatsApp Web)

La imagen del servicio FastAPI incluye `tzdata` y pasa `TZ` desde tu `.env` (ver `docker-compose.yml`). Sin eso, el contenedor suele usar **UTC** y las horas en WhatsApp Web pueden desfasarse respecto a tu hora local.

1. Define `TZ` en `.env` según tu región (ejemplos en la tabla).
2. Reinicia el stack: `docker compose down && docker compose up -d --build` (reconstruye una vez tras actualizar el proyecto que añade `tzdata`).

**Cómo identificar la zona**

- Lista completa: [Wikipedia — zonas tz](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (columna *TZ identifier*).
- Mapa / reloj mundial: [timeanddate.com](https://www.timeanddate.com/worldclock/).

| Región | Valor de `TZ` (IANA) |
|--------|----------------------|
| Brasil (Brasilia) | `America/Sao_Paulo` |
| Portugal | `Europe/Lisbon` |
| España (península) | `Europe/Madrid` |
| Reino Unido | `Europe/London` |
| EE. UU. — este | `America/New_York` |
| EE. UU. — oeste | `America/Los_Angeles` |
| UTC | `UTC` |

### Configuración Inicial

1. Copia el archivo de ejemplo:
```bash
cp env.example .env
```

2. Edita el archivo `.env` con tus configuraciones:
```bash
nano .env
```

3. Ejecuta el proyecto:
```bash
docker compose up --build
```

---

## Contacto

Desarrollado por **Victor Luis Santos**  
[LinkedIn](https://br.linkedin.com/in/victor-luis-santos)