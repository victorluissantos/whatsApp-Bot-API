from fastapi import FastAPI, Request, HTTPException, Query, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Literal, Annotated
import asyncio
import json
import os
import signal
from decouple import Config, RepositoryEnv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import threading
import logging
import time
import base64
from urllib.parse import urlencode
from io import BytesIO
from PIL import Image
from bson import json_util
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from datasource import Mongo, AutoBoot, Chats, Whats, Messages
from datasource import async_send_queue as async_queue
from datasource import unread_pane_cache
from datasource import triggers as triggers_store
from datasource import trigger_engine
from datasource.app_timezone import get_timezone_name, now_local
from datasource import trigger_matcher

# Configuração de logging
logging.basicConfig(level=logging.DEBUG)

# Inicialização do FastAPI
app = FastAPI(
    title="WhatsApp Bot API",
    description="API para automação do WhatsApp usando Selenium",
    version="1.0.2",
    openapi_tags=[
        {
            "name": "Status",
            "description": "Endpoints para verificar o status da conexão do WhatsApp"
        },
        {
            "name": "Mensagens",
            "description": "Endpoints para enviar e obter mensagens via WhatsApp"
        },
        {
            "name": "Chats",
            "description": "Endpoints para gerenciar e obter informações dos chats"
        },
        {
            "name": "Perfil",
            "description": "Endpoints para obter informações do perfil do WhatsApp"
        },
        {
            "name": "Sistema",
            "description": "Endpoints para controle do sistema e screenshots"
        },
        {
            "name": "Webhook",
            "description": "URL única (POST /webhook/delivery): recebe todos os eventos de integração; use o campo event no JSON",
        },
        {
            "name": "Triggers",
            "description": "Gatilhos de autoatendimento (CRUD; execução automática em etapa futura)",
        },
    ]
)

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuração de templates e arquivos estáticos
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuração do ambiente
env = Config(RepositoryEnv('.env'))
mgd = Mongo.MongoDBConnector(env)

# Variáveis globais
navegador = None
selenium_thread = None
# Por padrão mantém o Chrome oculto (headless). Defina True para debug visual via VNC.
WINDOW_SHOW_DEBUG = True # set to True for debug visual via VNC

# Um único envio por vez (síncrono ou worker da fila) para não concorrer no Selenium
whatsapp_send_lock = threading.Lock()
_queue_worker_stop = threading.Event()
# Transição para sessão logada: aciona clique no filtro "Não lidas" / "Unread".
_prev_logged_in_for_unread_filter = False

# Modelos Pydantic para validação
class SendMessageRequest(BaseModel):
    phone: str = Field(..., max_length=22, description="Número de telefone")
    message: str = Field(..., max_length=800, description="Texto da mensagem")
    unic_sent: bool = Field(False, description="Evita envio duplicado para o mesmo número")
    unRead: bool = Field(False, description="Marca o chat como não lido após o envio (Ctrl+Alt+Shift+U)")

class ChatInfo(BaseModel):
    name: str = Field(..., description="Nome do contato")
    phone: Optional[str] = Field(None, description="Número de telefone do contato")
    lastMessage: str = Field(..., description="Última mensagem da conversa")
    dateTime: str = Field(..., description="Data e hora da última mensagem")
    photo: Optional[str] = Field(None, description="URL da foto do contato")
    unreadCount: str = Field(..., description="Número de mensagens não lidas")

class GetChatsResponse(BaseModel):
    success: bool = Field(..., description="Indica se a operação foi bem-sucedida")
    chats: list[ChatInfo] = Field(..., description="Lista de chats")
    total: int = Field(..., description="Total de chats retornados")
    limit: int = Field(..., description="Limite usado na consulta")

class MessageInfo(BaseModel):
    message: str = Field(..., description="Texto da mensagem")
    data: str = Field(..., description="Data/hora da mensagem")
    origem: str = Field(..., description="Origem da mensagem (enviada/recebida)")

class GetMessagesResponse(BaseModel):
    success: bool = Field(..., description="Indica se a operação foi bem-sucedida")
    contact_name: str = Field(..., description="Nome do contato")
    phone: str = Field(..., description="Número de telefone")
    messages: list[MessageInfo] = Field(..., description="Lista de mensagens")
    total_messages: int = Field(..., description="Total de mensagens retornadas")


class DeliveryWebhookRequest(BaseModel):
    url: str = Field(
        ...,
        max_length=2048,
        description="URL única: recebe POST para fila assíncrona (event=async_message_delivered) e lista não lidas (event=unread_chat_list_changed)",
    )


class DeliveryWebhookResponse(BaseModel):
    success: bool = Field(..., description="Operação bem-sucedida")
    url: Optional[str] = Field(None, description="URL configurada (vazia se removida)")


class SendMessageAsyncResponse(BaseModel):
    success: bool = Field(..., description="Mensagem aceita na fila")
    queued: bool = Field(True, description="Indica enfileiramento assíncrono")
    job_id: str = Field(..., description="Identificador do trabalho na fila")
    webhook_configured: bool = Field(..., description="Há webhook para notificar ao enviar")
    webhook_config_missing: bool = Field(
        ...,
        description="True se não há webhook: envio ocorrerá na fila sem callback",
    )
    message: str = Field(..., description="Mensagem informativa para o cliente")


class SendQueueJobItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_id: str = Field(..., description="Identificador do job")
    phone: str = Field(..., description="Telefone de destino")
    message: str = Field(..., description="Texto enfileirado")
    unic_sent: bool = Field(False, description="Flag unic_sent no envio")
    unRead: bool = Field(False, description="Marca o chat como não lido após o envio")
    status: str = Field(..., description="pending | processing | sent | failed | cancelled | deleted")
    created_at: Optional[str] = Field(None, description="UTC ISO quando entrou na fila")
    started_at: Optional[str] = Field(None, description="UTC ISO quando o worker começou")
    processed_at: Optional[str] = Field(None, description="UTC ISO quando finalizou")
    result: Optional[str] = Field(None, description="Resultado do envio (ex.: Enviado) ou erro")
    deleted_at: Optional[str] = Field(None, description="UTC ISO quando soft-deleted")


class SendQueueListResponse(BaseModel):
    success: bool = Field(..., description="Consulta ok")
    items: list[SendQueueJobItem] = Field(..., description="Jobs da página (mais recentes primeiro)")
    total: int = Field(..., description="Total de documentos na fila")
    page: int = Field(..., description="Página atual (1-based)")
    page_size: int = Field(..., description="Itens por página")
    total_pages: int = Field(..., description="Total de páginas")


UniqueScope = Literal["minute", "hour", "day", "week", "month", "year", "forever"]


class TriggerScheduleModel(BaseModel):
    days_of_week: list[int] = Field(
        default=[0, 1, 2, 3, 4, 5, 6],
        description="0=segunda … 6=domingo",
    )
    all_day: bool = Field(True, description="Sem janela de horário")
    time_start: str = Field("09:00", description="HH:MM (se all_day=false)")
    time_end: str = Field("18:00", description="HH:MM (se all_day=false)")


class TriggerUniqueModel(BaseModel):
    enabled: bool = Field(False, description="Limitar repetições")
    scope: UniqueScope = Field("day", description="Escopo da unicidade")


class TriggerCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    pattern: str = Field(..., min_length=1, max_length=500)
    case_sensitive: bool = False
    reply_message: str = Field(..., min_length=1, max_length=800)
    enabled: bool = True
    schedule: TriggerScheduleModel = Field(default_factory=TriggerScheduleModel)
    unique: TriggerUniqueModel = Field(default_factory=TriggerUniqueModel)


class TriggerUpdateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    pattern: str = Field(..., min_length=1, max_length=500)
    case_sensitive: bool = False
    reply_message: str = Field(..., min_length=1, max_length=800)
    enabled: bool = True
    schedule: TriggerScheduleModel = Field(default_factory=TriggerScheduleModel)
    unique: TriggerUniqueModel = Field(default_factory=TriggerUniqueModel)


class TriggerResponse(BaseModel):
    id: str
    name: str
    pattern: str
    case_sensitive: bool
    reply_message: str
    enabled: bool
    schedule: TriggerScheduleModel
    unique: TriggerUniqueModel
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TriggerListResponse(BaseModel):
    success: bool = True
    items: list[TriggerResponse]
    total: int


class TriggerEnabledRequest(BaseModel):
    enabled: bool


class TriggerTestMatchRequest(BaseModel):
    pattern: str = Field(..., max_length=500, description="Expressão: LIKE %, AND, OR, IN(a,b,c)")
    message: str = Field(..., max_length=2000, description="Texto da mensagem recebida para testar")
    case_sensitive: bool = False


class TriggerTestMatchResponse(BaseModel):
    matches: bool
    pattern: str
    message: str


TRIGGER_DAY_OPTIONS = [
    {"value": 0, "label": "Seg"},
    {"value": 1, "label": "Ter"},
    {"value": 2, "label": "Qua"},
    {"value": 3, "label": "Qui"},
    {"value": 4, "label": "Sex"},
    {"value": 5, "label": "Sáb"},
    {"value": 6, "label": "Dom"},
]

TRIGGER_UNIQUE_SCOPE_OPTIONS = [
    {"value": "minute", "label": "No minuto"},
    {"value": "hour", "label": "Na hora"},
    {"value": "day", "label": "No dia"},
    {"value": "week", "label": "Na semana"},
    {"value": "month", "label": "No mês"},
    {"value": "year", "label": "No ano"},
    {"value": "forever", "label": "Eterna (só uma vez)"},
]


def _trigger_to_response(doc: dict) -> TriggerResponse:
    return TriggerResponse(**doc)


def _payload_from_trigger_body(body: TriggerCreateRequest | TriggerUpdateRequest) -> dict:
    return {
        "name": body.name,
        "pattern": body.pattern,
        "case_sensitive": body.case_sensitive,
        "reply_message": body.reply_message,
        "enabled": body.enabled,
        "schedule": triggers_store.normalize_schedule(body.schedule.model_dump()),
        "unique": triggers_store.normalize_unique(body.unique.model_dump()),
    }


def _form_to_trigger_payload(
    name: str,
    pattern: str,
    reply_message: str,
    case_sensitive: Optional[str],
    days_of_week: list[str],
    all_day: Optional[str],
    time_start: str,
    time_end: str,
    unique_enabled: Optional[str],
    unique_scope: str,
    enabled: Optional[str],
) -> dict:
    days = [int(d) for d in days_of_week if str(d).isdigit()]
    return {
        "name": name,
        "pattern": pattern,
        "case_sensitive": case_sensitive is not None,
        "reply_message": reply_message,
        "enabled": enabled is not None,
        "schedule": triggers_store.normalize_schedule(
            {
                "days_of_week": days,
                "all_day": all_day is not None,
                "time_start": time_start,
                "time_end": time_end,
            }
        ),
        "unique": triggers_store.normalize_unique(
            {"enabled": unique_enabled is not None, "scope": unique_scope}
        ),
    }


def _default_trigger_form() -> dict:
    return {
        "name": "",
        "pattern": "",
        "case_sensitive": False,
        "reply_message": "",
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "all_day": True,
        "time_start": "09:00",
        "time_end": "18:00",
        "unique_enabled": False,
        "unique_scope": "day",
        "enabled": True,
    }


def _trigger_doc_to_form(doc: dict) -> dict:
    schedule = doc.get("schedule") or {}
    unique = doc.get("unique") or {}
    return {
        "name": doc.get("name", ""),
        "pattern": doc.get("pattern", ""),
        "case_sensitive": bool(doc.get("case_sensitive")),
        "reply_message": doc.get("reply_message", ""),
        "days_of_week": schedule.get("days_of_week") or [0, 1, 2, 3, 4, 5, 6],
        "all_day": bool(schedule.get("all_day", True)),
        "time_start": schedule.get("time_start", "09:00"),
        "time_end": schedule.get("time_end", "18:00"),
        "unique_enabled": bool(unique.get("enabled")),
        "unique_scope": unique.get("scope", "day"),
        "enabled": bool(doc.get("enabled", True)),
    }


def _enrich_trigger_rows(items: list[dict]) -> list[dict]:
    rows = []
    for item in items:
        row = dict(item)
        row["schedule_summary"] = triggers_store.format_schedule_summary(item.get("schedule") or {})
        row["unique_summary"] = triggers_store.format_unique_summary(item.get("unique") or {})
        rows.append(row)
    return rows

def iniciar_selenium():
    global navegador
    if navegador:
        try:
            navegador.quit()
        except Exception as e:
            logging.error(f'Erro ao fechar instância anterior: {str(e)}')
    
    import uuid
    import os
    
    # Cria um diretório único para cada instância
    user_data_dir = f'chrome-data-{uuid.uuid4().hex[:8]}'
    os.makedirs(user_data_dir, exist_ok=True)
    
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
    chrome_options.add_argument('--disable-gpu')
    if not WINDOW_SHOW_DEBUG:
        chrome_options.add_argument('--headless')
    else:
        chrome_options.add_argument('--window-size=1366,768')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--disable-plugins')
    chrome_options.add_argument('--disable-images')
    chrome_options.add_argument('--disable-javascript')
    chrome_options.add_argument('--disable-web-security')
    chrome_options.add_argument('--allow-running-insecure-content')
    chrome_options.add_argument('--disable-features=VizDisplayCompositor')
    
    try:
        navegador = webdriver.Chrome(options=chrome_options)
        navegador.get("https://web.whatsapp.com")
        logging.info(f'Selenium iniciado com sucesso. User data dir: {user_data_dir}')
    except Exception as e:
        logging.error(f'Erro ao iniciar Selenium: {str(e)}')
        # Tenta limpar o diretório se houver erro
        try:
            import shutil
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except:
            pass
        raise e

def obter_navegador():
    while navegador is None:
        time.sleep(1)
    return navegador


def _run_message_queue_worker():
    """Consome 1 job por vez do RabbitMQ e processa envio no WhatsApp."""
    while not _queue_worker_stop.is_set():
        delivery_tag = None
        time.sleep(0.35)
        if not whatsapp_send_lock.acquire(blocking=False):
            continue
        try:
            navegador_local = obter_navegador()
            whats = Whats.Run()
            if not whats.isLogado(navegador_local):
                continue
            job, delivery_tag = async_queue.get_next_rabbit_job()
            if not job or delivery_tag is None:
                continue
            job_id = str(job.get("job_id", "")).strip()
            phone = str(job.get("phone", "")).strip()
            message = str(job.get("message", ""))
            unic_sent = bool(job.get("unic_sent", False))
            unRead = bool(job.get("unRead", False))
            if not job_id or not phone or not message:
                async_queue.ack_rabbit_job(delivery_tag)
                logging.warning("Job inválido recebido do RabbitMQ: %s", job)
                continue
            try:
                if not async_queue.mark_job_processing(mgd, job_id):
                    current = async_queue.get_job_status(mgd, job_id)
                    logging.info(
                        "Job %s ignorado na fila (status=%s); removendo do RabbitMQ",
                        job_id,
                        current,
                    )
                    async_queue.ack_rabbit_job(delivery_tag)
                    continue
                w = AutoBoot.WhatsAppBot(navegador_local, mgd)
                result = w.syncSendText(phone, message, unic_sent=unic_sent, unRead=unRead)
                ok = result == "Enviado"
                async_queue.finalize_job(mgd, job_id, ok, result)
                hook = async_queue.get_delivery_webhook_url(mgd)
                if hook:
                    payload = {
                        "event": "async_message_delivered",
                        "job_id": job_id,
                        "phone": phone,
                        "success": ok,
                        "result": result,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                    async_queue.notify_delivery_webhook(hook, payload)
                async_queue.ack_rabbit_job(delivery_tag)
            except Exception as e:
                logging.exception("Erro ao processar job da fila %s", job_id)
                async_queue.finalize_job(mgd, job_id, False, str(e))
                hook = async_queue.get_delivery_webhook_url(mgd)
                if hook:
                    async_queue.notify_delivery_webhook(
                        hook,
                        {
                            "event": "async_message_delivered",
                            "job_id": job_id,
                            "phone": phone,
                            "success": False,
                            "result": str(e),
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                    )
                # Em erro de processamento, marca como failed e remove da fila.
                async_queue.ack_rabbit_job(delivery_tag)
        except Exception:
            logging.exception("Erro no consumidor RabbitMQ; job será reencaminhado se já recebido")
            try:
                if delivery_tag is not None:
                    async_queue.nack_rabbit_job(delivery_tag, requeue=True)
            except Exception:
                pass
        finally:
            whatsapp_send_lock.release()


def _run_unread_filter_watcher():
    """
    Quando o QR termina e a sessão fica logada, tenta clicar no filtro por texto
    (Não lidas / Unread / …). Repete tentativas com lock curto para não travar envios.
    """
    global _prev_logged_in_for_unread_filter
    whats = Whats.Run()
    while True:
        time.sleep(1.5)
        try:
            nav = obter_navegador()
        except Exception:
            continue
        try:
            logged = whats.isLogado(nav)
        except Exception:
            logged = False
        if logged and not _prev_logged_in_for_unread_filter:
            deadline = time.time() + 120
            clicked = False
            while time.time() < deadline:
                if whatsapp_send_lock.acquire(timeout=10):
                    try:
                        if whats.try_click_unread_filter_once(nav):
                            clicked = True
                            break
                    finally:
                        whatsapp_send_lock.release()
                time.sleep(1.2)
            if not clicked:
                logging.warning(
                    "Após login, não foi possível clicar em Não lidas/Unread em até 120s "
                    "(UI diferente ou filtro indisponível)."
                )
        _prev_logged_in_for_unread_filter = logged


def _run_unread_pane_cache_watcher():
    """
    A cada UNREAD_PANE_POLL_SECONDS (default 5), lê #pane-side se a sessão estiver logada.
    Só adquire whatsapp_send_lock quando livre (nunca compete com envio). Se o snapshot mudar,
    atualiza o cache e chama o webhook de não lidas, se configurado.
    """
    whats = Whats.Run()
    chats_runner = Chats.Run()
    try:
        interval = float(os.environ.get("UNREAD_PANE_POLL_SECONDS", "5") or "5")
    except ValueError:
        interval = 5.0
    interval = max(1.0, interval)
    try:
        max_chats = int(os.environ.get("UNREAD_PANE_MAX_CHATS", "100") or "100")
    except ValueError:
        max_chats = 100
    max_chats = max(1, min(max_chats, 200))

    while True:
        try:
            nav = obter_navegador()
        except Exception:
            time.sleep(interval)
            continue
        try:
            logged = whats.isLogado(nav)
        except Exception:
            logged = False
        if not logged:
            unread_pane_cache.clear_cache()
            trigger_engine.reset_baseline()
            time.sleep(interval)
            continue
        if not whatsapp_send_lock.acquire(blocking=False):
            time.sleep(interval)
            continue
        try:
            old_chats, _ = unread_pane_cache.get_snapshot()
            raw = chats_runner.getUnreadChatsFromPaneSide(nav, limit=max_chats)
            if not isinstance(raw, list):
                time.sleep(interval)
                continue
            fp = unread_pane_cache.fingerprint_for_chats(raw)
            pane_changed = unread_pane_cache.update_if_changed(raw, fp)
            # Soft-delete/unique liberado: reavalia mesmo com pane estável.
            force_triggers = trigger_engine.consume_force_recalc()
            if pane_changed:
                url = async_queue.get_delivery_webhook_url(mgd)
                if url:
                    async_queue.notify_delivery_webhook(
                        url,
                        {
                            "event": "unread_chat_list_changed",
                            "timestamp": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                            "chats": raw,
                            "total": len(raw),
                        },
                    )
            # Sempre avalia triggers quando há unread (ou force). Unique/dedup
            # impedem reenvio; antes o motor só rodava se o fingerprint mudasse,
            # então soft-delete + mensagem parada nunca re-disparava.
            has_unread = any(
                str(c.get("unreadCount") or "0").strip() not in ("", "0")
                for c in raw
            )
            if pane_changed or force_triggers or has_unread:
                try:
                    stats = trigger_engine.process_unread_changes(
                        mgd, old_chats, raw, nav=nav
                    )
                    if stats.get("queued") or force_triggers:
                        logging.info(
                            "Triggers: %s (pane_changed=%s force=%s unread=%s)",
                            stats,
                            pane_changed,
                            force_triggers,
                            has_unread,
                        )
                except Exception:
                    logging.exception("Erro no motor de triggers")
        except Exception:
            logging.exception("Erro no watcher de cache #pane-side (não lidas)")
        finally:
            whatsapp_send_lock.release()
        time.sleep(interval)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "active_nav": "home"})


@app.get("/mongo", response_class=HTMLResponse, include_in_schema=False)
async def mongo_manage_page(
    request: Request,
    msg: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    collections_info: list[dict] = []
    try:
        names = sorted(mgd.db.list_collection_names())
        for name in names:
            try:
                count = mgd.db[name].count_documents({})
            except Exception:
                count = 0
            collections_info.append({"name": name, "count": count})
    except Exception as e:
        error = error or f"Erro ao listar collections: {e}"
    return templates.TemplateResponse(
        "mongo_manage.html",
        {
            "request": request,
            "active_nav": "mongo",
            "collections": collections_info,
            "message": msg,
            "error": error,
        },
    )


@app.get("/mongo/export", include_in_schema=False)
async def mongo_export_db():
    try:
        payload = {
            "schema_version": 1,
            "db_name": mgd.db.name,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "collections": {},
        }
        for name in sorted(mgd.db.list_collection_names()):
            docs = list(mgd.db[name].find({}))
            payload["collections"][name] = docs
        filename = f"mongo-backup-{payload['db_name']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        return Response(
            content=json_util.dumps(payload, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        params = urlencode({"error": f"Erro ao exportar DB: {e}"})
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)


@app.post("/mongo/clear", include_in_schema=False)
async def mongo_clear_db(collection_name: str = Form("")):
    target = (collection_name or "").strip()
    try:
        if target:
            if target not in mgd.db.list_collection_names():
                params = urlencode({"error": f"Collection '{target}' não encontrada"})
                return RedirectResponse(url=f"/mongo?{params}", status_code=303)
            deleted = mgd.db[target].delete_many({}).deleted_count
            params = urlencode({"msg": f"Collection '{target}' zerada com sucesso ({deleted} documento(s) removido(s))"})
            return RedirectResponse(url=f"/mongo?{params}", status_code=303)

        total_deleted = 0
        collection_count = 0
        for name in mgd.db.list_collection_names():
            deleted = mgd.db[name].delete_many({}).deleted_count
            total_deleted += deleted
            collection_count += 1
        params = urlencode(
            {
                "msg": (
                    f"Banco limpo com sucesso: {collection_count} collection(s) zerada(s), "
                    f"{total_deleted} documento(s) removido(s)"
                )
            }
        )
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)
    except Exception as e:
        params = urlencode({"error": f"Erro ao limpar DB: {e}"})
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)


@app.post("/mongo/import", include_in_schema=False)
async def mongo_import_db(
    file: UploadFile = File(...),
    collection_name: str = Form(""),
    mode: str = Form("replace"),
):
    target = (collection_name or "").strip()
    mode_clean = (mode or "replace").strip().lower()
    if mode_clean not in ("replace", "append"):
        params = urlencode({"error": "Modo inválido. Use replace ou append"})
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)

    try:
        raw = await file.read()
        payload = json_util.loads(raw.decode("utf-8"))

        source_collection = ""
        docs: list[dict] = []
        if isinstance(payload, list):
            docs = payload
        elif isinstance(payload, dict) and isinstance(payload.get("collections"), dict):
            collections_map = payload["collections"]
            available = sorted(collections_map.keys())
            if target:
                if target not in collections_map:
                    params = urlencode({"error": f"Collection '{target}' não existe no arquivo importado"})
                    return RedirectResponse(url=f"/mongo?{params}", status_code=303)
                source_collection = target
            elif len(available) == 1:
                source_collection = available[0]
            else:
                params = urlencode(
                    {"error": "Arquivo possui múltiplas collections. Informe o nome da collection para importar"}
                )
                return RedirectResponse(url=f"/mongo?{params}", status_code=303)
            docs = collections_map.get(source_collection) or []
        elif isinstance(payload, dict) and isinstance(payload.get("docs"), list):
            docs = payload["docs"]
            source_collection = str(payload.get("collection") or "").strip()
        else:
            params = urlencode({"error": "Formato de arquivo incompatível para importação"})
            return RedirectResponse(url=f"/mongo?{params}", status_code=303)

        if not isinstance(docs, list):
            params = urlencode({"error": "Formato inválido: lista de documentos ausente"})
            return RedirectResponse(url=f"/mongo?{params}", status_code=303)

        destination = target or source_collection
        if not destination:
            params = urlencode({"error": "Informe a collection de destino para importar"})
            return RedirectResponse(url=f"/mongo?{params}", status_code=303)
        coll = mgd.db[destination]

        if mode_clean == "replace":
            coll.delete_many({})
        inserted = 0
        if docs:
            result = coll.insert_many(docs, ordered=False)
            inserted = len(result.inserted_ids)

        params = urlencode(
            {
                "msg": (
                    f"Importação concluída na collection '{destination}': "
                    f"{inserted} documento(s) importado(s) (modo {mode_clean})"
                )
            }
        )
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)
    except json.JSONDecodeError:
        params = urlencode({"error": "Arquivo JSON inválido"})
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)
    except Exception as e:
        params = urlencode({"error": f"Erro ao importar DB: {e}"})
        return RedirectResponse(url=f"/mongo?{params}", status_code=303)


# --- Triggers: telas HTML (rotas estáticas antes de /triggers/{id}) ---

@app.get("/triggers", response_class=HTMLResponse, include_in_schema=False)
async def triggers_list_page(
    request: Request,
    msg: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    items = _enrich_trigger_rows(triggers_store.list_triggers(mgd))
    return templates.TemplateResponse(
        "triggers_list.html",
        {
            "request": request,
            "active_nav": "triggers",
            "triggers": items,
            "message": msg,
            "error": error,
        },
    )


@app.get("/triggers/new", response_class=HTMLResponse, include_in_schema=False)
async def triggers_new_page(request: Request):
    return templates.TemplateResponse(
        "triggers_form.html",
        {
            "request": request,
            "active_nav": "triggers",
            "is_edit": False,
            "trigger": None,
            "form": _default_trigger_form(),
            "day_options": TRIGGER_DAY_OPTIONS,
            "unique_scopes": TRIGGER_UNIQUE_SCOPE_OPTIONS,
            "error": None,
        },
    )


@app.post("/triggers/new", response_class=HTMLResponse, include_in_schema=False)
async def triggers_create_submit(
    request: Request,
    name: str = Form(...),
    pattern: str = Form(...),
    reply_message: str = Form(...),
    case_sensitive: Optional[str] = Form(None),
    days_of_week: Annotated[list[str], Form()] = [],
    all_day: Optional[str] = Form(None),
    time_start: str = Form("09:00"),
    time_end: str = Form("18:00"),
    unique_enabled: Optional[str] = Form(None),
    unique_scope: str = Form("day"),
    enabled: Optional[str] = Form(None),
):
    form_data = _form_to_trigger_payload(
        name, pattern, reply_message, case_sensitive, days_of_week,
        all_day, time_start, time_end, unique_enabled, unique_scope, enabled,
    )
    try:
        triggers_store.create_trigger(mgd, form_data)
    except trigger_matcher.PatternSyntaxError as e:
        error_msg = f"Padrão inválido: {e}"
        status = 400
    except Exception as e:
        error_msg = f"Erro ao criar trigger: {e}"
        status = 400
    else:
        return RedirectResponse(url="/triggers?msg=Trigger+cadastrado+com+sucesso", status_code=303)

    return templates.TemplateResponse(
        "triggers_form.html",
        {
            "request": request,
            "active_nav": "triggers",
            "is_edit": False,
            "trigger": None,
            "form": {
                "name": name,
                "pattern": pattern,
                "case_sensitive": case_sensitive is not None,
                "reply_message": reply_message,
                "days_of_week": [int(d) for d in days_of_week if str(d).isdigit()],
                "all_day": all_day is not None,
                "time_start": time_start,
                "time_end": time_end,
                "unique_enabled": unique_enabled is not None,
                "unique_scope": unique_scope,
                "enabled": enabled is not None,
            },
            "day_options": TRIGGER_DAY_OPTIONS,
            "unique_scopes": TRIGGER_UNIQUE_SCOPE_OPTIONS,
            "error": error_msg,
        },
        status_code=status,
    )


@app.get("/triggers/export", include_in_schema=False)
async def triggers_export_download():
    payload = triggers_store.export_triggers(mgd)
    filename = f"triggers-{time.strftime('%Y%m%d-%H%M%S')}.json"
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/triggers/import", include_in_schema=False)
async def triggers_import_upload(
    file: UploadFile = File(...),
    mode: str = Form("merge"),
):
    try:
        raw = await file.read()
        payload = json.loads(raw.decode("utf-8"))
        result = triggers_store.import_triggers(mgd, payload, mode=mode)
    except json.JSONDecodeError:
        return RedirectResponse(
            url="/triggers?error=Arquivo+JSON+inv%C3%A1lido",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/triggers?error={str(e).replace(' ', '+')}",
            status_code=303,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/triggers?error=Erro+ao+importar%3A+{str(e).replace(' ', '+')}",
            status_code=303,
        )

    msg = f"Importação concluída: {result['imported']} criado(s)"
    if result["skipped"]:
        msg += f", {result['skipped']} ignorado(s) (nome duplicado)"
    if result["errors"]:
        msg += f", {len(result['errors'])} erro(s)"
    return RedirectResponse(url=f"/triggers?msg={msg.replace(' ', '+')}", status_code=303)


@app.post("/triggers/test-match", tags=["Triggers"], response_model=TriggerTestMatchResponse)
async def api_test_trigger_match(body: TriggerTestMatchRequest):
    try:
        matches = trigger_matcher.matches_pattern(body.message, body.pattern, body.case_sensitive)
    except trigger_matcher.PatternSyntaxError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TriggerTestMatchResponse(
        matches=matches,
        pattern=body.pattern,
        message=body.message,
    )


@app.get("/triggers/{trigger_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def triggers_edit_page(request: Request, trigger_id: str):
    doc = triggers_store.get_trigger(mgd, trigger_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return templates.TemplateResponse(
        "triggers_form.html",
        {
            "request": request,
            "active_nav": "triggers",
            "is_edit": True,
            "trigger": doc,
            "form": _trigger_doc_to_form(doc),
            "day_options": TRIGGER_DAY_OPTIONS,
            "unique_scopes": TRIGGER_UNIQUE_SCOPE_OPTIONS,
            "error": None,
        },
    )


@app.post("/triggers/{trigger_id}/edit", response_class=HTMLResponse, include_in_schema=False)
async def triggers_edit_submit(
    request: Request,
    trigger_id: str,
    name: str = Form(...),
    pattern: str = Form(...),
    reply_message: str = Form(...),
    case_sensitive: Optional[str] = Form(None),
    days_of_week: Annotated[list[str], Form()] = [],
    all_day: Optional[str] = Form(None),
    time_start: str = Form("09:00"),
    time_end: str = Form("18:00"),
    unique_enabled: Optional[str] = Form(None),
    unique_scope: str = Form("day"),
    enabled: Optional[str] = Form(None),
):
    form_data = _form_to_trigger_payload(
        name, pattern, reply_message, case_sensitive, days_of_week,
        all_day, time_start, time_end, unique_enabled, unique_scope, enabled,
    )
    try:
        doc = triggers_store.update_trigger(mgd, trigger_id, form_data)
    except trigger_matcher.PatternSyntaxError as e:
        return templates.TemplateResponse(
            "triggers_form.html",
            {
                "request": request,
                "active_nav": "triggers",
                "is_edit": True,
                "trigger": {"id": trigger_id},
                "form": {
                    "name": name,
                    "pattern": pattern,
                    "case_sensitive": case_sensitive is not None,
                    "reply_message": reply_message,
                    "days_of_week": [int(d) for d in days_of_week if str(d).isdigit()],
                    "all_day": all_day is not None,
                    "time_start": time_start,
                    "time_end": time_end,
                    "unique_enabled": unique_enabled is not None,
                    "unique_scope": unique_scope,
                    "enabled": enabled is not None,
                },
                "day_options": TRIGGER_DAY_OPTIONS,
                "unique_scopes": TRIGGER_UNIQUE_SCOPE_OPTIONS,
                "error": f"Padrão inválido: {e}",
            },
            status_code=400,
        )
    if not doc:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return RedirectResponse(url="/triggers?msg=Trigger+atualizado+com+sucesso", status_code=303)


@app.post("/triggers/{trigger_id}/delete", include_in_schema=False)
async def triggers_delete_submit(trigger_id: str):
    if not triggers_store.delete_trigger(mgd, trigger_id):
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return RedirectResponse(url="/triggers?msg=Trigger+exclu%C3%ADdo", status_code=303)


@app.post("/triggers/deactivate", include_in_schema=False)
async def triggers_deactivate_submit(
    trigger_ids: Annotated[list[str], Form()] = [],
):
    result = triggers_store.set_triggers_enabled_bulk(mgd, trigger_ids, False)
    if result["matched"] == 0:
        params = urlencode({"error": "Nenhum trigger válido foi selecionado para inativar"})
        return RedirectResponse(url=f"/triggers?{params}", status_code=303)

    msg = f"{result['modified']} trigger(s) inativado(s)"
    unchanged = result["matched"] - result["modified"]
    if unchanged > 0:
        msg += f"; {unchanged} já estava(m) inativo(s)"
    params = urlencode({"msg": msg})
    return RedirectResponse(url=f"/triggers?{params}", status_code=303)


# --- Triggers: API JSON (Swagger) ---

@app.get("/triggers/all", tags=["Triggers"], response_model=TriggerListResponse)
async def api_list_triggers(enabled_only: bool = Query(False)):
    items = triggers_store.list_triggers(mgd, enabled_only=enabled_only)
    return TriggerListResponse(
        items=[_trigger_to_response(i) for i in items],
        total=len(items),
    )


@app.get("/triggers/export/json", tags=["Triggers"])
async def api_export_triggers():
    return triggers_store.export_triggers(mgd)


class TriggerImportRequest(BaseModel):
    schema_version: int = 1
    triggers: list[dict]


class TriggerImportResponse(BaseModel):
    success: bool
    imported: int
    skipped: int
    total: int
    errors: list[str]


@app.post("/triggers/import/json", tags=["Triggers"], response_model=TriggerImportResponse)
async def api_import_triggers(body: TriggerImportRequest, mode: str = Query("merge")):
    try:
        result = triggers_store.import_triggers(mgd, body.model_dump(), mode=mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return TriggerImportResponse(success=True, **result)


@app.post("/triggers/create", tags=["Triggers"], response_model=TriggerResponse, status_code=201)
async def api_create_trigger(body: TriggerCreateRequest):
    try:
        doc = triggers_store.create_trigger(mgd, _payload_from_trigger_body(body))
    except trigger_matcher.PatternSyntaxError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _trigger_to_response(doc)


@app.get("/triggers/{trigger_id}", tags=["Triggers"], response_model=TriggerResponse)
async def api_get_trigger(trigger_id: str):
    if trigger_id in triggers_store.RESERVED_TRIGGER_IDS:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    doc = triggers_store.get_trigger(mgd, trigger_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return _trigger_to_response(doc)


@app.put("/triggers/{trigger_id}", tags=["Triggers"], response_model=TriggerResponse)
async def api_update_trigger(trigger_id: str, body: TriggerUpdateRequest):
    try:
        doc = triggers_store.update_trigger(mgd, trigger_id, _payload_from_trigger_body(body))
    except trigger_matcher.PatternSyntaxError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not doc:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return _trigger_to_response(doc)


@app.delete("/triggers/{trigger_id}", tags=["Triggers"])
async def api_delete_trigger(trigger_id: str):
    if not triggers_store.delete_trigger(mgd, trigger_id):
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return {"success": True, "message": "Trigger excluído"}


@app.patch("/triggers/{trigger_id}/enabled", tags=["Triggers"], response_model=TriggerResponse)
async def api_set_trigger_enabled(trigger_id: str, body: TriggerEnabledRequest):
    doc = triggers_store.set_trigger_enabled(mgd, trigger_id, body.enabled)
    if not doc:
        raise HTTPException(status_code=404, detail="Trigger não encontrado")
    return _trigger_to_response(doc)


@app.get("/status", tags=["Status"])
async def get_status():
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    # Verifica se está logado usando o método isLogado da classe Whats
    if whats.isLogado(navegador_local):
        return {"connected": True, "qrCode": None}
    else:
        # Não está logado, tenta pegar o QR Code
        try:
            qr_base64_full = whats.getQrCode(navegador_local)  # já retorna 'data:image/png;base64,...'
            return {"connected": False, "qrCode": qr_base64_full}
        except Exception as e:
            return {"connected": False, "qrCode": None, "error": f"QR code não encontrado: {str(e)}"}

@app.get("/profile", tags=["Perfil"])
async def get_profile():
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    # Verifica se está logado
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        profile = whats.getProfile(navegador_local)
        return {"success": True, "profile": profile}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter perfil: {str(e)}")

@app.get("/system/timezone", tags=["Sistema"])
async def system_timezone():
    """Hora local do container — mesma referência usada pelos triggers (faixa de horário, unique por dia)."""
    local = now_local()
    return {
        "timezone": get_timezone_name(),
        "local_now": local.isoformat(),
        "utc_offset": local.strftime("%z"),
        "weekday": local.weekday(),
        "weekday_label": ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"][local.weekday()],
        "hint": "Defina TZ=America/Sao_Paulo no .env e reinicie o compose.",
    }


@app.get("/screenshot", tags=["Sistema"])
async def get_screenshot():
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    try:
        screenshot_path = whats.getScreenShot(navegador_local)
        
        # Converte para base64
        import base64
        with open(screenshot_path, 'rb') as f:
            screenshot_data = f.read()
        screenshot_base64 = base64.b64encode(screenshot_data).decode('utf-8')
        
        return {
            "success": True,
            "screenshot": f"data:image/png;base64,{screenshot_base64}",
            "path": screenshot_path
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao capturar screenshot: {str(e)}")

@app.post("/sendMessage", tags=["Mensagens"])
async def send_message(request: SendMessageRequest):
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        with whatsapp_send_lock:
            w = AutoBoot.WhatsAppBot(navegador_local, mgd)
            result = w.syncSendText(
                request.phone, request.message, unic_sent=request.unic_sent, unRead=request.unRead
            )
        if result == 'Enviado':
            return {"success": True, "phone": request.phone, "message": "Mensagem enviada com sucesso"}
        else:
            return {"success": False, "phone": request.phone, "message": f"Erro ao enviar mensagem: {result}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

@app.get("/sendMessage", tags=["Mensagens"])
async def send_message_get(phone: str, message: str, unic_sent: bool = False, unRead: bool = False):
    if not phone or not message or len(phone) > 22 or len(message) > 800:
        raise HTTPException(status_code=400, detail="Parâmetros inválidos")
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        with whatsapp_send_lock:
            w = AutoBoot.WhatsAppBot(navegador_local, mgd)
            result = w.syncSendText(phone, message, unic_sent=unic_sent, unRead=unRead)
        if result == 'Enviado':
            return {"success": True, "phone": phone, "message": "Mensagem enviada com sucesso"}
        else:
            return {"success": False, "phone": phone, "message": f"Erro ao enviar mensagem: {result}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/webhook/delivery", tags=["Webhook"], response_model=DeliveryWebhookResponse)
async def set_delivery_webhook(body: DeliveryWebhookRequest):
    """Registra a URL única de integração: fila assíncrona e mudanças na lista de não lidas (campo `event` no body)."""
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL do webhook é obrigatória")
    async_queue.set_delivery_webhook_url(mgd, url)
    return DeliveryWebhookResponse(success=True, url=url)


@app.get("/webhook/delivery", tags=["Webhook"])
async def get_delivery_webhook():
    url = async_queue.get_delivery_webhook_url(mgd)
    return {"success": True, "configured": bool(url), "url": url}


@app.delete("/webhook/delivery", tags=["Webhook"], response_model=DeliveryWebhookResponse)
async def delete_delivery_webhook():
    async_queue.clear_delivery_webhook(mgd)
    return DeliveryWebhookResponse(success=True, url=None)


@app.post("/sendMessageAsync", tags=["Mensagens"], response_model=SendMessageAsyncResponse)
async def send_message_async(request: SendMessageRequest):
    """
    Enfileira o envio no MongoDB. O worker envia quando não houver outro envio em andamento
    (endpoint síncrono ou outro item da fila) e a sessão estiver logada. Se a URL única estiver
    configurada (POST /webhook/delivery), recebe um POST com event=async_message_delivered ao terminar.
    """
    obter_navegador()

    hook = async_queue.get_delivery_webhook_url(mgd)
    webhook_ok = bool(hook)
    try:
        job_id = async_queue.enqueue_job(
            mgd, request.phone, request.message, request.unic_sent, request.unRead
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao enfileirar mensagem no RabbitMQ: {str(e)}")

    if webhook_ok:
        msg = "Mensagem enfileirada; a URL configurada em POST /webhook/delivery receberá POST ao concluir o envio."
    else:
        msg = (
            "Mensagem enfileirada para envio assíncrono. Não há URL em POST /webhook/delivery; "
            "o envio ocorre pela fila sem notificação HTTP (nem para fila nem para lista não lidas)."
        )

    return SendMessageAsyncResponse(
        success=True,
        queued=True,
        job_id=job_id,
        webhook_configured=webhook_ok,
        webhook_config_missing=not webhook_ok,
        message=msg,
    )


@app.get("/send-queue", response_class=HTMLResponse, include_in_schema=False)
async def send_queue_page(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    phone: Optional[str] = Query(None),
    message: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    msg: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
):
    raw, total = async_queue.list_queue_jobs_desc(
        mgd,
        page,
        page_size,
        phone=phone,
        message=message,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    filters = {
        "phone": phone or "",
        "message": message or "",
        "status": status or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "page_size": page_size,
    }
    return templates.TemplateResponse(
        "send_queue.html",
        {
            "request": request,
            "active_nav": "send_queue",
            "items": raw,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "filters": filters,
            "message": msg,
            "error": error,
        },
    )


def _send_queue_redirect_params(
    page: int,
    page_size: int,
    phone: str,
    message: str,
    status: str,
    date_from: str,
    date_to: str,
    ok: bool,
    detail: str,
) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "page": max(1, page),
        "page_size": max(1, min(100, page_size)),
    }
    if phone.strip():
        params["phone"] = phone.strip()
    if message.strip():
        params["message"] = message.strip()
    if status.strip():
        params["status"] = status.strip()
    if date_from.strip():
        params["date_from"] = date_from.strip()
    if date_to.strip():
        params["date_to"] = date_to.strip()
    params["msg" if ok else "error"] = detail
    return params


@app.post("/send-queue/{job_id}/cancel", include_in_schema=False)
async def send_queue_cancel_form(
    job_id: str,
    page: int = Form(1),
    page_size: int = Form(20),
    phone: str = Form(""),
    message: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
):
    ok, detail = async_queue.cancel_job(mgd, job_id)
    params = _send_queue_redirect_params(
        page, page_size, phone, message, status, date_from, date_to, ok, detail
    )
    return RedirectResponse(url=f"/send-queue?{urlencode(params)}", status_code=303)


@app.post("/send-queue/{job_id}/resend", include_in_schema=False)
async def send_queue_resend_form(
    job_id: str,
    page: int = Form(1),
    page_size: int = Form(20),
    phone: str = Form(""),
    message: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
):
    ok, detail = async_queue.resend_job(mgd, job_id)
    params = _send_queue_redirect_params(
        page, page_size, phone, message, status, date_from, date_to, ok, detail
    )
    return RedirectResponse(url=f"/send-queue?{urlencode(params)}", status_code=303)


@app.post("/send-queue/{job_id}/delete", include_in_schema=False)
async def send_queue_delete_form(
    job_id: str,
    page: int = Form(1),
    page_size: int = Form(20),
    phone: str = Form(""),
    message: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
):
    ok, detail = async_queue.delete_job(mgd, job_id)
    params = _send_queue_redirect_params(
        page, page_size, phone, message, status, date_from, date_to, ok, detail
    )
    return RedirectResponse(url=f"/send-queue?{urlencode(params)}", status_code=303)


@app.post("/send-queue/delete-batch", include_in_schema=False)
async def send_queue_delete_batch_form(
    job_ids: list[str] = Form([]),
    page: int = Form(1),
    page_size: int = Form(20),
    phone: str = Form(""),
    message: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
):
    cleaned_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in job_ids:
        job_id = str(raw_id or "").strip()
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)
        cleaned_ids.append(job_id)

    if not cleaned_ids:
        params = _send_queue_redirect_params(
            page,
            page_size,
            phone,
            message,
            status,
            date_from,
            date_to,
            False,
            "Nenhum job selecionado para exclusão",
        )
        return RedirectResponse(url=f"/send-queue?{urlencode(params)}", status_code=303)

    deleted_count = 0
    errors: list[str] = []
    for job_id in cleaned_ids:
        ok, detail = async_queue.delete_job(mgd, job_id)
        if ok:
            deleted_count += 1
            continue
        errors.append(f"{job_id}: {detail}")

    if errors and deleted_count == 0:
        detail = "Falha ao excluir os jobs selecionados: " + "; ".join(errors[:3])
        if len(errors) > 3:
            detail += f" (+{len(errors) - 3} erro(s))"
        ok = False
    elif errors:
        detail = (
            f"{deleted_count} job(s) excluído(s) com sucesso; "
            f"{len(errors)} falharam."
        )
        ok = True
    else:
        detail = f"{deleted_count} job(s) excluído(s) com sucesso"
        ok = True

    params = _send_queue_redirect_params(
        page, page_size, phone, message, status, date_from, date_to, ok, detail
    )
    return RedirectResponse(url=f"/send-queue?{urlencode(params)}", status_code=303)


@app.get("/getSendQueue", tags=["Mensagens"], response_model=SendQueueListResponse)
async def get_send_queue(
    page: int = Query(1, ge=1, description="Página (começa em 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Itens por página (padrão 10)"),
    phone: Optional[str] = Query(None, description="Filtrar por telefone (contém)"),
    message: Optional[str] = Query(None, description="Filtrar por texto da mensagem (contém, case insensitive)"),
    status: Optional[str] = Query(None, description="Filtrar por status"),
    date_from: Optional[str] = Query(None, description="Data inicial (YYYY-MM-DD ou ISO)"),
    date_to: Optional[str] = Query(None, description="Data final (YYYY-MM-DD ou ISO)"),
):
    """
    Lista entradas da fila de envio assíncrono, da mais recente para a mais antiga (`created_at` DESC).
    """
    raw, total = async_queue.list_queue_jobs_desc(
        mgd,
        page,
        page_size,
        phone=phone,
        message=message,
        status=status,
        date_from=date_from,
        date_to=date_to,
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    items = [SendQueueJobItem.model_validate(r) for r in raw]
    return SendQueueListResponse(
        success=True,
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


class SendQueueCancelResponse(BaseModel):
    success: bool = Field(..., description="Cancelamento aplicado")
    job_id: str = Field(..., description="ID do job")
    message: str = Field(..., description="Detalhe do resultado")


class SendQueueDeleteResponse(BaseModel):
    success: bool = Field(..., description="Soft-delete aplicado")
    job_id: str = Field(..., description="ID do job")
    message: str = Field(..., description="Detalhe do resultado")


@app.post("/sendQueue/{job_id}/cancel", tags=["Mensagens"], response_model=SendQueueCancelResponse)
async def cancel_send_queue_job(job_id: str):
    """Cancela um job pendente para que não seja enviado pelo worker."""
    ok, detail = async_queue.cancel_job(mgd, job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return SendQueueCancelResponse(success=True, job_id=job_id, message=detail)


@app.post("/sendQueue/{job_id}/delete", tags=["Mensagens"], response_model=SendQueueDeleteResponse)
async def delete_send_queue_job(job_id: str):
    """Soft-delete: marca o job como deleted. Dedup/worker/triggers passam a ignorá-lo."""
    ok, detail = async_queue.delete_job(mgd, job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=detail)
    return SendQueueDeleteResponse(success=True, job_id=job_id, message=detail)


@app.get("/getChats", tags=["Chats"], response_model=GetChatsResponse)
async def get_chats(limit: int = Query(10, ge=1, le=50, description="Número de chats a retornar (1-50)")):
    """
    Obtém a lista de chats do WhatsApp Web.

    Este endpoint retorna uma lista dos chats mais recentes do WhatsApp Web,
    incluindo informações como nome do contato, telefone, última mensagem,
    data/hora, foto do perfil e número de mensagens não lidas.

    Args:
        limit (int): O número máximo de chats a serem retornados (entre 1 e 50).

    Returns:
        GetChatsResponse: Um objeto contendo a lista de chats, total e limite usado.

    Raises:
        HTTPException: Se o WhatsApp não estiver conectado ou ocorrer erro interno.
    """
    # Validação do parâmetro limit
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="O parâmetro 'limit' deve estar entre 1 e 50")
    
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        chats = Chats.Run()
        
        # Tentar primeiro o método original
        print("Tentando método original...")
        chat_list = chats.getAllChats(navegador_local, limit)
        
        # Se retornar lista vazia, tentar o método alternativo
        if isinstance(chat_list, list) and len(chat_list) == 0:
            print("Método original retornou lista vazia, tentando método alternativo...")
            chat_list = chats.getAllChatsAlternative(navegador_local, limit)
        
        if isinstance(chat_list, str) and "error" in chat_list.lower():
            raise HTTPException(status_code=500, detail=f"Erro ao obter chats: {chat_list}")
        
        return {
            "success": True,
            "chats": chat_list,
            "total": len(chat_list),
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.get("/getChatsUnread", tags=["Chats"], response_model=GetChatsResponse)
async def get_chats_unread(
    limit: int = Query(50, ge=1, le=200, description="Máximo de itens retornados do cache"),
):
    """
    Lista de chats não lidas conforme o último snapshot do #pane-side (filtro Unread).
    Não acessa o DOM: usa apenas o cache atualizado pelo processo em background.
    """
    chats_full, _ts = unread_pane_cache.get_snapshot()
    sliced = chats_full[:limit]
    return {
        "success": True,
        "chats": sliced,
        "total": len(sliced),
        "limit": limit,
    }


@app.get("/getMessage", tags=["Mensagens"], response_model=GetMessagesResponse)
async def get_message(phone: str = Query(..., description="Número de telefone do contato")):
    """
    Obtém as mensagens de um chat específico pelo número de telefone.

    Este endpoint retorna as mensagens de um chat específico do WhatsApp Web,
    incluindo informações como texto da mensagem, tipo (enviada/recebida),
    horário e informações do contato.

    Args:
        phone (str): O número de telefone do contato.

    Returns:
        GetMessagesResponse: Um objeto contendo as mensagens do chat.

    Raises:
        HTTPException: Se o WhatsApp não estiver conectado ou ocorrer erro interno.
    """
    # Validação do parâmetro phone
    if not phone or len(phone) > 22:
        raise HTTPException(status_code=400, detail="O parâmetro 'phone' é obrigatório e deve ter no máximo 22 caracteres")
    
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        with whatsapp_send_lock:
            messages = Messages.Run()
            result = messages.getMessages(navegador_local, phone)

        if not isinstance(result, dict):
            raise HTTPException(
                status_code=500,
                detail=f"Resposta inválida de getMessages: {type(result).__name__}",
            )

        if result.get("success"):
            return {
                "success": True,
                "contact_name": result.get("contact_name") or f"Contato {phone}",
                "phone": result.get("phone") or phone,
                "messages": result.get("messages") or [],
                "total_messages": int(result.get("total_messages") or 0),
            }

        raise HTTPException(
            status_code=404,
            detail=result.get("error") or "Erro ao obter mensagens",
        )
    except HTTPException:
        raise
    except Exception as e:
        err_text = str(e).strip() or repr(e)
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno: {type(e).__name__}: {err_text}",
        )


@app.post("/reset", tags=["Sistema"])
async def reset_whatsapp():
    navegador_local = obter_navegador()
    whats = Whats.Run()
    try:
        whats.resetPage(navegador_local)
        return {"success": True, "message": "WhatsApp Web recarregado com Ctrl+Shift+R"}
    except Exception as e:
        return {"success": False, "message": f"Erro ao recarregar: {str(e)}"}


def _do_container_restart():
    time.sleep(1)
    # Encerra o uvicorn; com `restart: always` o Docker sobe de novo.
    try:
        os.kill(os.getpid(), signal.SIGTERM)
    except Exception:
        os._exit(0)


@app.post("/restartContainer", tags=["Sistema"])
async def restart_container(background_tasks: BackgroundTasks):
    # BackgroundTasks (não create_task): create_task era cancelado ao fim do request.
    background_tasks.add_task(_do_container_restart)
    return {
        "success": True,
        "message": "Container será reiniciado em instantes. Aguarde alguns segundos e recarregue a página.",
    }

# Eventos de inicialização
@app.on_event("startup")
async def startup_event():
    global selenium_thread
    async_queue.ensure_queue_indexes(mgd)
    triggers_store.ensure_indexes(mgd)
    async_queue.ensure_rabbit_topology()
    logging.info(
        "Fuso horário da aplicação: %s | agora local: %s",
        get_timezone_name(),
        now_local().isoformat(),
    )
    selenium_thread = threading.Thread(target=iniciar_selenium)
    selenium_thread.start()
    worker = threading.Thread(target=_run_message_queue_worker, name="wa-async-queue", daemon=True)
    worker.start()
    unread_watcher = threading.Thread(
        target=_run_unread_filter_watcher, name="wa-unread-filter", daemon=True
    )
    unread_watcher.start()
    pane_cache_watcher = threading.Thread(
        target=_run_unread_pane_cache_watcher, name="wa-unread-pane-cache", daemon=True
    )
    pane_cache_watcher.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)