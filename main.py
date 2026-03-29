from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
import os
from decouple import Config, RepositoryEnv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import threading
import logging
import time
import base64
from io import BytesIO
from PIL import Image
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from datasource import Mongo, AutoBoot, Chats, Whats, Messages
from datasource import async_send_queue as async_queue
from datasource import unread_pane_cache

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
            "description": "Webhooks: entrega da fila assíncrona e alterações na lista de não lidas (cache #pane-side)",
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
    unic_sent: bool = Field(False, description="Evita envio duplicado para o mesmo número")  # Novo nome

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
    url: str = Field(..., max_length=2048, description="URL HTTPS (ou HTTP em dev) que receberá POST ao concluir envio da fila")


class DeliveryWebhookResponse(BaseModel):
    success: bool = Field(..., description="Operação bem-sucedida")
    url: Optional[str] = Field(None, description="URL configurada (vazia se removida)")


class UnreadListWebhookRequest(BaseModel):
    url: str = Field(
        ...,
        max_length=2048,
        description="URL que receberá POST JSON quando a lista de não lidas (cache) mudar",
    )


class UnreadListWebhookResponse(BaseModel):
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
    status: str = Field(..., description="pending | processing | sent | failed")
    created_at: Optional[str] = Field(None, description="UTC ISO quando entrou na fila")
    started_at: Optional[str] = Field(None, description="UTC ISO quando o worker começou")
    processed_at: Optional[str] = Field(None, description="UTC ISO quando finalizou")
    result: Optional[str] = Field(None, description="Resultado do envio (ex.: Enviado) ou erro")


class SendQueueListResponse(BaseModel):
    success: bool = Field(..., description="Consulta ok")
    items: list[SendQueueJobItem] = Field(..., description="Jobs da página (mais recentes primeiro)")
    total: int = Field(..., description="Total de documentos na fila")
    page: int = Field(..., description="Página atual (1-based)")
    page_size: int = Field(..., description="Itens por página")
    total_pages: int = Field(..., description="Total de páginas")

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
    """Máquina de estados simples: se o lock de envio está livre e há jobs pendentes, processa um."""
    while not _queue_worker_stop.is_set():
        time.sleep(0.35)
        if not whatsapp_send_lock.acquire(blocking=False):
            continue
        try:
            navegador_local = obter_navegador()
            whats = Whats.Run()
            if not whats.isLogado(navegador_local):
                continue
            job = async_queue.claim_next_pending_job(mgd)
            if not job:
                continue
            job_id = job["job_id"]
            phone = job["phone"]
            message = job["message"]
            unic_sent = bool(job.get("unic_sent", False))
            try:
                w = AutoBoot.WhatsAppBot(navegador_local, mgd)
                result = w.syncSendText(phone, message, unic_sent=unic_sent)
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
            time.sleep(interval)
            continue
        if not whatsapp_send_lock.acquire(blocking=False):
            time.sleep(interval)
            continue
        try:
            raw = chats_runner.getUnreadChatsFromPaneSide(nav, limit=max_chats)
            if not isinstance(raw, list):
                time.sleep(interval)
                continue
            fp = unread_pane_cache.fingerprint_for_chats(raw)
            if unread_pane_cache.update_if_changed(raw, fp):
                url = async_queue.get_unread_list_webhook_url(mgd)
                if url:
                    async_queue.notify_unread_list_webhook(
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
        except Exception:
            logging.exception("Erro no watcher de cache #pane-side (não lidas)")
        finally:
            whatsapp_send_lock.release()
        time.sleep(interval)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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
            result = w.syncSendText(request.phone, request.message, unic_sent=request.unic_sent)
        if result == 'Enviado':
            return {"success": True, "phone": request.phone, "message": "Mensagem enviada com sucesso"}
        else:
            return {"success": False, "phone": request.phone, "message": f"Erro ao enviar mensagem: {result}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

@app.get("/sendMessage", tags=["Mensagens"])
async def send_message_get(phone: str, message: str, unic_sent: bool = False):
    if not phone or not message or len(phone) > 22 or len(message) > 800:
        raise HTTPException(status_code=400, detail="Parâmetros inválidos")
    navegador_local = obter_navegador()
    whats = Whats.Run()
    
    if not whats.isLogado(navegador_local):
        raise HTTPException(status_code=400, detail="WhatsApp não está conectado")
    
    try:
        with whatsapp_send_lock:
            w = AutoBoot.WhatsAppBot(navegador_local, mgd)
            result = w.syncSendText(phone, message, unic_sent=unic_sent)
        if result == 'Enviado':
            return {"success": True, "phone": phone, "message": "Mensagem enviada com sucesso"}
        else:
            return {"success": False, "phone": phone, "message": f"Erro ao enviar mensagem: {result}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/webhook/delivery", tags=["Webhook"], response_model=DeliveryWebhookResponse)
async def set_delivery_webhook(body: DeliveryWebhookRequest):
    """Registra a URL que receberá POST quando um envio da fila assíncrona for concluído."""
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


@app.post("/webhook/unread-list", tags=["Webhook"], response_model=UnreadListWebhookResponse)
async def set_unread_list_webhook(body: UnreadListWebhookRequest):
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL do webhook é obrigatória")
    async_queue.set_unread_list_webhook_url(mgd, url)
    return UnreadListWebhookResponse(success=True, url=url)


@app.get("/webhook/unread-list", tags=["Webhook"])
async def get_unread_list_webhook():
    url = async_queue.get_unread_list_webhook_url(mgd)
    return {"success": True, "configured": bool(url), "url": url}


@app.delete("/webhook/unread-list", tags=["Webhook"], response_model=UnreadListWebhookResponse)
async def delete_unread_list_webhook():
    async_queue.clear_unread_list_webhook(mgd)
    return UnreadListWebhookResponse(success=True, url=None)


@app.post("/sendMessageAsync", tags=["Mensagens"], response_model=SendMessageAsyncResponse)
async def send_message_async(request: SendMessageRequest):
    """
    Enfileira o envio no MongoDB. O worker envia quando não houver outro envio em andamento
    (endpoint síncrono ou outro item da fila) e a sessão estiver logada. Se houver webhook configurado,
    ele recebe um POST ao terminar.
    """
    obter_navegador()

    hook = async_queue.get_delivery_webhook_url(mgd)
    webhook_ok = bool(hook)
    job_id = async_queue.enqueue_job(mgd, request.phone, request.message, request.unic_sent)

    if webhook_ok:
        msg = "Mensagem enfileirada; você será notificado no webhook quando o envio for concluído."
    else:
        msg = (
            "Mensagem enfileirada para envio assíncrono. Atenção: não há webhook de entrega configurado "
            "(POST /webhook/delivery); o envio será feito pela fila, mas você não receberá notificação automática ao concluir."
        )

    return SendMessageAsyncResponse(
        success=True,
        queued=True,
        job_id=job_id,
        webhook_configured=webhook_ok,
        webhook_config_missing=not webhook_ok,
        message=msg,
    )


@app.get("/getSendQueue", tags=["Mensagens"], response_model=SendQueueListResponse)
async def get_send_queue(
    page: int = Query(1, ge=1, description="Página (começa em 1)"),
    page_size: int = Query(10, ge=1, le=100, description="Itens por página (padrão 10)"),
):
    """
    Lista entradas da fila de envio assíncrono, da mais recente para a mais antiga (`created_at` DESC).
    """
    raw, total = async_queue.list_queue_jobs_desc(mgd, page, page_size)
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
        messages = Messages.Run()
        result = messages.getMessages(navegador_local, phone)
        
        if result.get("success"):
            return result
        else:
            raise HTTPException(status_code=404, detail=result.get("error", "Erro ao obter mensagens"))
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")


@app.post("/reset", tags=["Sistema"])
async def reset_whatsapp():
    navegador_local = obter_navegador()
    whats = Whats.Run()
    try:
        whats.resetPage(navegador_local)
        return {"success": True, "message": "WhatsApp Web recarregado com Ctrl+Shift+R"}
    except Exception as e:
        return {"success": False, "message": f"Erro ao recarregar: {str(e)}"}

# Eventos de inicialização
@app.on_event("startup")
async def startup_event():
    global selenium_thread
    async_queue.ensure_queue_indexes(mgd)
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