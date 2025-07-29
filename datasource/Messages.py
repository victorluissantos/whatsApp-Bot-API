from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from datetime import datetime
import time
import re


class Run:

    def __init__(self, browser=None, mongo=None, env=None):
        self.navegador = browser
        self.mongo = mongo
        self.env = env

    def getMessages(self, navegador, phone):
        """
        Abre a conversa de um contato específico pelo número de telefone
        e extrai as mensagens da conversa
        
        Args:
            navegador: Instância do webdriver
            phone: Número de telefone do contato
            
        Returns:
            dict: Dicionário com informações das mensagens ou erro
        """
        try:
            print(f"Iniciando busca de mensagens para o telefone: {phone}")
            
            # Limpar qualquer estado anterior (igual ao syncSendText)
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)

            # Clica no botão 'Nova conversa' usando seletor CSS (igual ao syncSendText)
            # Tentar diferentes variações de idioma e seletores
            nova_conversa_selectors = [
                'button[title="Nova conversa"][aria-label="Nova conversa"]',
                'button[title="New chat"][aria-label="New chat"]',
                'button[aria-label="Nova conversa"]',
                'button[aria-label="New chat"]',
                'button[title="Nova conversa"]',
                'button[title="New chat"]',
                'button[data-testid="chat"]',
                'button[data-testid="new-chat"]',
                'button[data-testid="new-chat-button"]',
                'button[aria-label*="chat"]',
                'button[title*="chat"]',
                'button[aria-label*="conversa"]',
                'button[title*="conversa"]'
            ]
            
            btn_nova_conversa = None
            for selector in nova_conversa_selectors:
                try:
                    btn_nova_conversa = navegador.find_element(By.CSS_SELECTOR, selector)
                    print(f"Botão 'Nova conversa' encontrado com seletor: {selector}")
                    break
                except NoSuchElementException:
                    continue
            
            if not btn_nova_conversa:
                # Tentar encontrar por XPath como fallback
                xpath_selectors = [
                    '//button[@aria-label="Nova conversa"]',
                    '//button[@aria-label="New chat"]',
                    '//button[@title="Nova conversa"]',
                    '//button[@title="New chat"]',
                    '//button[contains(@aria-label, "chat")]',
                    '//button[contains(@title, "chat")]',
                    '//button[contains(@aria-label, "conversa")]',
                    '//button[contains(@title, "conversa")]'
                ]
                
                for xpath_selector in xpath_selectors:
                    try:
                        btn_nova_conversa = navegador.find_element(By.XPATH, xpath_selector)
                        print(f"Botão 'Nova conversa' encontrado com XPath: {xpath_selector}")
                        break
                    except NoSuchElementException:
                        continue
            
            if not btn_nova_conversa:
                print("Botão 'Nova conversa' não encontrado")
                return {
                    "success": False,
                    "error": "Botão 'Nova conversa' não encontrado",
                    "phone": phone
                }
            
            btn_nova_conversa.click()
            time.sleep(0.5)
            print("Botão 'Nova conversa' clicado")

            # Campo de busca de contato/conversa (tentar diferentes idiomas)
            campo_busca_selectors = [
                'div[contenteditable="true"][aria-label="Pesquisar nome ou número"]',
                'div[contenteditable="true"][aria-label="Search name or number"]',
                'div[contenteditable="true"][aria-label="Search"]',
                'div[contenteditable="true"][data-testid="chat-list-search"]',
                'div[contenteditable="true"]'
            ]
            
            campo_busca = None
            for selector in campo_busca_selectors:
                try:
                    campo_busca = navegador.find_element(By.CSS_SELECTOR, selector)
                    print(f"Campo de busca encontrado com seletor: {selector}")
                    break
                except NoSuchElementException:
                    continue
            
            if not campo_busca:
                print("Campo de busca não encontrado")
                return {
                    "success": False,
                    "error": "Campo de busca não encontrado",
                    "phone": phone
                }
            
            campo_busca.clear()
            time.sleep(0.2)
            telefone = '+55'+phone
            campo_busca.send_keys(telefone)
            time.sleep(1)
            campo_busca.send_keys(Keys.RETURN)
            time.sleep(1)
            print(f"Telefone {telefone} digitado no campo de busca")

            # Aguardar até que a conversa carregue (tentar diferentes seletores)
            conversation_loaded = False
            selectors = [
                '//div[@data-testid="conversation-panel-wrapper"]',
                '//div[@data-testid="conversation-panel-messages"]',
                '//div[contains(@class, "conversation")]',
                '//div[contains(@class, "chat")]',
                '//div[@data-testid="chat-list"]',
                '//div[@data-testid="conversation-panel"]',
                '//div[contains(@class, "messages")]',
                '//div[@data-testid="conversation-panel-wrapper"]//div[contains(@class, "messages")]',
                '//div[@data-testid="conversation-panel"]//div[contains(@class, "messages")]'
            ]
            
            # Aguardar mais tempo para a conversa carregar
            time.sleep(3)
            
            for selector in selectors:
                try:
                    WebDriverWait(navegador, 15).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    conversation_loaded = True
                    print(f"Conversa carregada com seletor: {selector}")
                    break
                except TimeoutException:
                    continue
            
            if not conversation_loaded:
                # Tentar verificar se há algum erro ou mensagem de "não encontrado"
                try:
                    error_elements = navegador.find_elements(By.XPATH, '//div[contains(text(), "não encontrado") or contains(text(), "not found") or contains(text(), "inválido") or contains(text(), "invalid")]')
                    if error_elements:
                        error_text = error_elements[0].text
                        print(f"Erro encontrado: {error_text}")
                        return {
                            "success": False,
                            "error": f"Contato não encontrado: {error_text}",
                            "phone": phone
                        }
                except:
                    pass
                
                print("Conversa não carregou")
                return {
                    "success": False,
                    "error": "Conversa não encontrada ou não foi possível carregar",
                    "phone": phone
                }
            
            # Verificar se o chat existe e obter nome do contato
            try:
                # Tentar encontrar o nome do contato
                contact_name = navegador.find_element(By.XPATH, '//span[@data-testid="conversation-title"]')
                contact_name_text = contact_name.text
                print(f"Nome do contato encontrado: {contact_name_text}")
            except NoSuchElementException:
                contact_name_text = f"Contato {phone}"
                print(f"Nome do contato não encontrado, usando padrão: {contact_name_text}")
            
            # Aguardar um pouco para as mensagens carregarem
            time.sleep(3)
            print("Aguardando carregamento das mensagens...")
            
            # Tentar encontrar as mensagens
            try:
                # Tentar diferentes seletores para o container de mensagens (independente do idioma)
                container_selectors = [
                    '//div[@data-testid="conversation-panel-messages"]',
                    '//div[contains(@class, "messages")]',
                    '//div[contains(@class, "chat")]',
                    '//div[@data-testid="chat-list"]',
                    '//div[@data-testid="conversation-panel"]//div[contains(@class, "messages")]',
                    '//div[@data-testid="conversation-panel-wrapper"]//div[contains(@class, "messages")]',
                    '//div[contains(@class, "conversation")]//div[contains(@class, "messages")]'
                ]
                
                messages_container = None
                for container_selector in container_selectors:
                    try:
                        messages_container = navegador.find_element(By.XPATH, container_selector)
                        print(f"Container de mensagens encontrado com seletor: {container_selector}")
                        break
                    except NoSuchElementException:
                        continue
                
                if not messages_container:
                    print("Container de mensagens não encontrado")
                    return {
                        "success": False,
                        "error": "Container de mensagens não encontrado",
                        "phone": phone,
                        "contact_name": contact_name_text
                    }
                
                # Encontrar todas as mensagens usando múltiplos seletores
                message_elements = []
                
                # Tentar diferentes seletores para encontrar mensagens
                selectors = [
                    './/div[contains(@class, "message-in") or contains(@class, "message-out")]',
                    './/div[contains(@data-testid, "msg-container")]',
                    './/div[contains(@class, "copyable-text")]',
                    './/div[contains(@class, "message")]'
                ]
                
                for selector in selectors:
                    try:
                        elements = messages_container.find_elements(By.XPATH, selector)
                        if elements:
                            message_elements = elements
                            print(f"Mensagens encontradas com seletor: {selector}, quantidade: {len(elements)}")
                            break
                    except:
                        continue
                
                if not message_elements:
                    print("Nenhuma mensagem encontrada")
                    return {
                        "success": True,
                        "contact_name": contact_name_text,
                        "phone": phone,
                        "messages": [],
                        "total_messages": 0
                    }
                
                messages = []
                
                # Processar as últimas 20 mensagens
                for msg_element in message_elements[-20:]:
                    try:
                        # Extrair o texto da mensagem
                        message_text = ""
                        text_selectors = [
                            './/span[@dir="ltr"]',
                            './/div[contains(@class, "copyable-text")]//span',
                            './/span[contains(@class, "selectable-text")]',
                            './/div[contains(@class, "text")]//span'
                        ]
                        
                        for text_selector in text_selectors:
                            try:
                                text_element = msg_element.find_element(By.XPATH, text_selector)
                                message_text = text_element.text.strip()
                                if message_text:
                                    break
                            except NoSuchElementException:
                                continue
                        
                        if not message_text:
                            message_text = "Mensagem não legível"
                        
                        # Determinar origem (enviada ou recebida)
                        origem = "recebida"  # padrão
                        try:
                            # Verificar se é mensagem enviada
                            if "message-out" in msg_element.get_attribute("class"):
                                origem = "enviada"
                            elif "message-in" in msg_element.get_attribute("class"):
                                origem = "recebida"
                            else:
                                # Tentar outros métodos para determinar origem
                                parent_classes = msg_element.get_attribute("class") or ""
                                if "outgoing" in parent_classes or "sent" in parent_classes:
                                    origem = "enviada"
                                elif "incoming" in parent_classes or "received" in parent_classes:
                                    origem = "recebida"
                        except:
                            origem = "recebida"  # padrão se não conseguir determinar
                        
                        # Extrair data/hora da mensagem
                        data = "Horário não disponível"
                        timestamp_selectors = [
                            './/span[@data-testid="msg-meta"]',
                            './/span[contains(@class, "timestamp")]',
                            './/span[contains(@class, "time")]',
                            './/div[contains(@class, "meta")]//span'
                        ]
                        
                        for timestamp_selector in timestamp_selectors:
                            try:
                                timestamp_element = msg_element.find_element(By.XPATH, timestamp_selector)
                                data = timestamp_element.text.strip()
                                if data:
                                    break
                            except NoSuchElementException:
                                continue
                        
                        # Criar objeto da mensagem com os campos corretos
                        message_info = {
                            "message": message_text,
                            "data": data,
                            "origem": origem
                        }
                        
                        messages.append(message_info)
                        
                    except Exception as e:
                        # Se houver erro ao processar uma mensagem, continuar com a próxima
                        print(f"Erro ao processar mensagem: {str(e)}")
                        continue
                
                print(f"Total de mensagens processadas: {len(messages)}")
                return {
                    "success": True,
                    "contact_name": contact_name_text,
                    "phone": phone,
                    "messages": messages,
                    "total_messages": len(messages)
                }
                
            except NoSuchElementException as e:
                print(f"Erro ao encontrar mensagens: {str(e)}")
                return {
                    "success": False,
                    "error": "Não foi possível encontrar mensagens neste chat",
                    "phone": phone,
                    "contact_name": contact_name_text
                }
                
        except NoSuchElementException as e:
            print(f"Elemento não encontrado: {str(e)}")
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            return {
                "success": False,
                "error": f"Elemento não encontrado - conversa inválida: {str(e)}",
                "phone": phone
            }
        except Exception as e:
            print(f"Erro geral: {str(e)}")
            return {
                "success": False,
                "error": f"Erro ao abrir conversa: {str(e)}",
                "phone": phone
            } 