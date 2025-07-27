from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
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

from datasource import Mongo, AutoBoot, Chats, Whats

# Configuração de logging
logging.basicConfig(level=logging.DEBUG)

# Inicialização do FastAPI
app = FastAPI(
    title="WhatsApp Bot API",
    description="API para automação do WhatsApp usando Selenium",
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Status",
            "description": "Endpoints para verificar o status da conexão do WhatsApp"
        },
        {
            "name": "Mensagens",
            "description": "Endpoints para enviar mensagens via WhatsApp"
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
        }
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
    chrome_options.add_argument('--headless')  # Executar em modo headless
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
        w = AutoBoot.WhatsAppBot(navegador_local, mgd)
        result = w.syncSendText(phone, message, unic_sent=unic_sent)
        if result == 'Enviado':
            return {"success": True, "phone": phone, "message": "Mensagem enviada com sucesso"}
        else:
            return {"success": False, "phone": phone, "message": f"Erro ao enviar mensagem: {result}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")

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
    selenium_thread = threading.Thread(target=iniciar_selenium)
    selenium_thread.start()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)