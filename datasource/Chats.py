from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import TimeoutException
from datetime import datetime
import requests
import time


class Run:

    def getUnreadChats(self, navegador):
        try:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)

            chatList = []  # Initialize chatList

            navegador.execute_script(" document.getElementsByTagName('button')[1].click(); ")
            time.sleep(1.5)

            try:

                navegador.execute_script("document.getElementsByTagName('li')[0].click();")
                time.sleep(1)

            except Exception as e:

                webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
                time.sleep(1)

            try:
                # Aguarde até que a lista de chats esteja presente
                lista_conversas_block = WebDriverWait(navegador, 10).until(
                    EC.presence_of_element_located((By.XPATH, '//div[@aria-label="Lista de conversas"]'))
                )

                # Encontrar todas as divs que contêm informações de chat dentro do bloco da lista de conversas
                chat_content_divs = lista_conversas_block.find_elements(By.XPATH, './/div[@role="row"]')

                # Iterar sobre as divs encontradas
                for chat_content_div in chat_content_divs:
                    # Extrair informações usando índices e posições relativas
                    from_value = chat_content_div.find_element(By.XPATH, './/div[1]').text
                    elem = from_value.split('\n')

                    try:
                        line = chat_content_div.find_element(By.XPATH, './/div[2]').text
                        total = line.split('\n')
                        total_value = total[-1] if len(total) >= 3 and total[-1].isdigit() else '0'  # Verifica se o último item da lista é um número
                    except NoSuchElementException:
                        total_value = '0'

                    result = {
                        'referencia': elem[0],
                        'lastMessage': elem[2],
                        'dateTime': elem[1],
                        'total': total_value
                    }

                    chatList.append(result)

                # Clique novamente para fechar o filtro
                filtro = WebDriverWait(navegador, 20).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="side"]/div[1]/div/button'))
                )
                filtro.click()
                time.sleep(1)
                webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()

            except TimeoutException:
                webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
                time.sleep(1)
                # Se o elemento não for encontrado dentro do tempo limite, retorne o chatList vazio
                print("Lista de conversas não encontrada. Retornando chatList vazio.")
                return chatList

            return chatList

        except Exception as e:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)
            return f"An error occurred: {e}"

    def debugWhatsAppStatus(self, navegador):
        """
        Método de debug para verificar o status do WhatsApp
        """
        try:
            print("=== DEBUG: Verificando status do WhatsApp ===")
            
            # Verificar URL
            current_url = navegador.current_url
            print(f"URL atual: {current_url}")
            
            # Verificar se estamos no WhatsApp Web
            if "web.whatsapp.com" not in current_url:
                print("❌ Não estamos no WhatsApp Web!")
                return False
            
            # Verificar se há elementos de carregamento
            try:
                loading_elements = navegador.find_elements(By.XPATH, "//div[contains(@class, 'loading') or contains(@class, 'spinner')]")
                if loading_elements:
                    print(f"⚠️ Elementos de carregamento encontrados: {len(loading_elements)}")
            except:
                pass
            
            # Verificar se há QR code
            try:
                qr_elements = navegador.find_elements(By.XPATH, "//canvas[contains(@class, 'qr') or contains(@class, 'QR')]")
                if qr_elements:
                    print("⚠️ QR Code encontrado - WhatsApp não está logado!")
                    return False
            except:
                pass
            
            # Verificar se há elementos de chat
            try:
                chat_elements = navegador.find_elements(By.XPATH, "//div[@role='listitem']")
                print(f"📱 Elementos de chat encontrados: {len(chat_elements)}")
            except:
                print("❌ Nenhum elemento de chat encontrado")
            
            # Verificar aria-labels
            try:
                aria_elements = navegador.find_elements(By.XPATH, "//div[@aria-label]")
                print(f"🏷️ Elementos com aria-label: {len(aria_elements)}")
                for i, elem in enumerate(aria_elements[:10]):  # Mostrar os primeiros 10
                    aria_label = elem.get_attribute('aria-label')
                    if aria_label:
                        print(f"  {i+1}. {aria_label}")
            except Exception as e:
                print(f"❌ Erro ao buscar aria-labels: {e}")
            
            print("=== FIM DEBUG ===")
            return True
            
        except Exception as e:
            print(f"❌ Erro no debug: {e}")
            return False

    def getAllChats(self, navegador, limit=10):
        try:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)

            # Executar debug primeiro
            self.debugWhatsAppStatus(navegador)

            chatList = []

            # DEBUG: Verificar se estamos na página correta
            current_url = navegador.current_url
            print(f"URL atual: {current_url}")
            
            # DEBUG: Verificar se há elementos na página
            try:
                body = navegador.find_element(By.TAG_NAME, "body")
                print(f"Body encontrado: {body}")
            except Exception as e:
                print(f"Erro ao encontrar body: {e}")
                return chatList

            # DEBUG: Tentar encontrar qualquer div com aria-label
            try:
                all_aria_labels = navegador.find_elements(By.XPATH, "//div[@aria-label]")
                print(f"Total de elementos com aria-label: {len(all_aria_labels)}")
                for i, elem in enumerate(all_aria_labels[:5]):  # Mostrar apenas os primeiros 5
                    aria_label = elem.get_attribute('aria-label')
                    print(f"Elemento {i+1} aria-label: {aria_label}")
            except Exception as e:
                print(f"Erro ao buscar aria-labels: {e}")

            # Aguarde até que a lista de conversas esteja presente - tentar múltiplos idiomas
            lista_conversas_block = None
            chat_list_selectors = [
                '//div[@aria-label="Lista de conversas"]',  # Português
                '//div[@aria-label="Chat list"]',  # Inglês
                '//div[@aria-label="Conversation list"]',  # Inglês alternativo
                '//div[@role="grid" and @aria-label]',  # Genérico
                '//div[contains(@class, "x1y332i5") and @role="grid"]'  # Por classe
            ]
            
            for selector in chat_list_selectors:
                try:
                    print(f"Tentando seletor: {selector}")
                    lista_conversas_block = WebDriverWait(navegador, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    print(f"Lista de conversas encontrada com seletor: {selector}")
                    break
                except TimeoutException:
                    print(f"Seletor não encontrado: {selector}")
                    continue
            
            if not lista_conversas_block:
                print("Nenhum seletor de lista de conversas funcionou")
                return chatList

            # Encontrar todas as divs que contêm informações de chat (role="listitem")
            chat_content_divs = lista_conversas_block.find_elements(By.XPATH, './/div[@role="listitem"]')
            print(f"Total de chats encontrados: {len(chat_content_divs)}")

            # Iterar sobre as divs encontradas até o limite especificado
            for i, chat_content_div in enumerate(chat_content_divs):
                if i >= limit:
                    break
                try:
                    print(f"Processando chat {i+1}...")
                    # Extrair nome/telefone do contato
                    try:
                        # Tentar diferentes seletores para o nome
                        name_selectors = [
                            './/span[@title]',
                            './/span[@dir="auto" and @title]',
                            './/span[contains(@class, "x1iyjqo2") and @title]'
                        ]
                        name = None
                        for selector in name_selectors:
                            try:
                                name_element = chat_content_div.find_element(By.XPATH, selector)
                                name = name_element.get_attribute('title')
                                if name:
                                    print(f"Nome encontrado: {name}")
                                    break
                            except NoSuchElementException:
                                continue
                        if not name:
                            name = "Contato sem nome"
                            print("Nome não encontrado")
                    except Exception as e:
                        name = "Contato sem nome"
                        print(f"Erro ao extrair nome: {e}")

                    # Extrair última mensagem
                    try:
                        message_selectors = [
                            './/span[@class="x78zum5 x1cy8zhl"]//span[@dir="ltr"]',
                            './/span[@class="x78zum5 x1cy8zhl"]//span[@dir="auto"]',
                            './/span[contains(@class, "x1cy8zhl")]//span[@dir="ltr"]',
                            './/span[contains(@class, "x1cy8zhl")]//span[@dir="auto"]'
                        ]
                        last_message = None
                        for selector in message_selectors:
                            try:
                                message_element = chat_content_div.find_element(By.XPATH, selector)
                                last_message = message_element.text.strip()
                                if last_message:
                                    print(f"Mensagem encontrada: {last_message[:50]}...")
                                    break
                            except NoSuchElementException:
                                continue
                        if not last_message:
                            last_message = "Sem mensagem"
                            print("Mensagem não encontrado")
                    except Exception as e:
                        last_message = "Sem mensagem"
                        print(f"Erro ao extrair última mensagem: {e}")

                    # Extrair data/hora
                    try:
                        date_selectors = [
                            './/div[contains(@class, "_ak8i")]',
                            './/div[@class="_ak8i"]',
                            './/div[contains(@class, "x1s688f")]'
                        ]
                        date_time = None
                        for selector in date_selectors:
                            try:
                                date_element = chat_content_div.find_element(By.XPATH, selector)
                                date_time = date_element.text.strip()
                                if date_time:
                                    print(f"Data encontrada: {date_time}")
                                    break
                            except NoSuchElementException:
                                continue
                        if not date_time:
                            date_time = "Data não disponível"
                            print("Data não encontrado")
                    except Exception as e:
                        date_time = "Data não disponível"
                        print(f"Erro ao extrair data/hora: {e}")

                    # Extrair foto do contato
                    try:
                        img_element = chat_content_div.find_element(By.XPATH, './/img[@src]')
                        photo_url = img_element.get_attribute('src')
                        print(f"Foto encontrada: {photo_url[:50]}...")
                    except NoSuchElementException:
                        photo_url = None
                        print("Foto não encontrado")
                    except Exception as e:
                        photo_url = None
                        print(f"Erro ao extrair foto: {e}")

                    # Extrair número de mensagens não lidas - suporte para PT e EN
                    try:
                        unread_selectors = [
                            './/span[contains(@aria-label, "unread message")]//span',  # EN
                            './/span[contains(@aria-label, "mensagem") and contains(@aria-label, "lida")]//span',  # PT
                            './/span[contains(@class, "x184q3qc")]',
                            './/span[@class="x184q3qc"]'
                        ]
                        unread_count = "0"
                        for selector in unread_selectors:
                            try:
                                unread_element = chat_content_div.find_element(By.XPATH, selector)
                                unread_count = unread_element.text.strip()
                                if unread_count:
                                    print(f"Mensagens não lidas: {unread_count}")
                                    break
                            except NoSuchElementException:
                                continue
                    except Exception as e:
                        unread_count = "0"
                        print(f"Erro ao extrair mensagens não lidas: {e}")

                    # Extrair telefone se disponível
                    phone = None
                    if name and name.startswith('+55'):
                        phone = name
                    elif name and any(char.isdigit() for char in name):
                        phone = name

                    result = {
                        'name': name,
                        'phone': phone,
                        'lastMessage': last_message,
                        'dateTime': date_time,
                        'photo': photo_url,
                        'unreadCount': unread_count
                    }

                    chatList.append(result)
                    print(f"Chat {i+1} processado com sucesso")
                except Exception as e:
                    print(f"Erro ao processar chat {i}: {str(e)}")
                    continue

            print(f"Total de chats processados: {len(chatList)}")
            return chatList

        except Exception as e:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)
            print(f"Erro geral: {e}")
            return f"An error occurred: {e}"

    def getAllChatsAlternative(self, navegador, limit=10):
        """
        Método alternativo para obter chats usando uma abordagem diferente
        """
        try:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)

            chatList = []

            # Aguarde até que a lista de conversas esteja presente - tentar múltiplos idiomas
            lista_conversas_block = None
            chat_list_selectors = [
                '//div[@aria-label="Lista de conversas"]',  # Português
                '//div[@aria-label="Chat list"]',  # Inglês
                '//div[@aria-label="Conversation list"]',  # Inglês alternativo
                '//div[@role="grid" and @aria-label]',  # Genérico
                '//div[contains(@class, "x1y332i5") and @role="grid"]'  # Por classe
            ]
            
            for selector in chat_list_selectors:
                try:
                    lista_conversas_block = WebDriverWait(navegador, 5).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    print(f"Lista de conversas encontrada (método alternativo) com seletor: {selector}")
                    break
                except TimeoutException:
                    print(f"Seletor não encontrado (método alternativo): {selector}")
                    continue
            
            if not lista_conversas_block:
                print("Nenhum seletor de lista de conversas funcionou (método alternativo)")
                return chatList

            # Tentar encontrar todos os elementos de chat usando diferentes seletores
            chat_selectors = [
                './/div[@role="listitem"]',
                './/div[contains(@class, "x10l6tqk")]',
                './/div[contains(@class, "xh8yej3")]'
            ]

            chat_content_divs = []
            for selector in chat_selectors:
                try:
                    divs = lista_conversas_block.find_elements(By.XPATH, selector)
                    if divs:
                        chat_content_divs = divs
                        print(f"Chats encontrados com seletor '{selector}': {len(divs)}")
                        break
                except Exception as e:
                    print(f"Erro com seletor '{selector}': {e}")
                    continue

            if not chat_content_divs:
                print("Nenhum chat encontrado com nenhum seletor")
                return chatList

            # Iterar sobre as divs encontradas até o limite especificado
            for i, chat_content_div in enumerate(chat_content_divs):
                if i >= limit:
                    break
                
                try:
                    print(f"Processando chat {i+1} (método alternativo)...")
                    
                    # Extrair nome/telefone do contato - tentar múltiplos seletores
                    name = None
                    name_selectors = [
                        './/span[@title]',
                        './/span[@dir="auto" and @title]',
                        './/span[contains(@class, "x1iyjqo2")]',
                        './/span[contains(@class, "xlyipyv")]'
                    ]
                    
                    for selector in name_selectors:
                        try:
                            elements = chat_content_div.find_elements(By.XPATH, selector)
                            for element in elements:
                                title = element.get_attribute('title')
                                if title and title.strip():
                                    name = title.strip()
                                    print(f"Nome encontrado: {name}")
                                    break
                            if name:
                                break
                        except Exception as e:
                            continue
                    
                    if not name:
                        name = "Contato sem nome"
                        print("Nome não encontrado")
                    
                    # Extrair última mensagem - tentar múltiplos seletores
                    last_message = None
                    message_selectors = [
                        './/span[@class="x78zum5 x1cy8zhl"]//span[@dir="ltr"]',
                        './/span[@class="x78zum5 x1cy8zhl"]//span[@dir="auto"]',
                        './/span[contains(@class, "x1cy8zhl")]//span',
                        './/span[@dir="ltr"]',
                        './/span[@dir="auto"]'
                    ]
                    
                    for selector in message_selectors:
                        try:
                            elements = chat_content_div.find_elements(By.XPATH, selector)
                            for element in elements:
                                text = element.text.strip()
                                if text and text != name:  # Evitar pegar o nome como mensagem
                                    last_message = text
                                    print(f"Mensagem encontrada: {last_message[:50]}...")
                                    break
                            if last_message:
                                break
                        except Exception as e:
                            continue
                    
                    if not last_message:
                        last_message = "Sem mensagem"
                        print("Mensagem não encontrada")
                    
                    # Extrair data/hora
                    date_time = None
                    date_selectors = [
                        './/div[contains(@class, "_ak8i")]',
                        './/div[@class="_ak8i"]',
                        './/div[contains(@class, "x1s688f")]',
                        './/div[contains(@class, "false")]'
                    ]
                    
                    for selector in date_selectors:
                        try:
                            elements = chat_content_div.find_elements(By.XPATH, selector)
                            for element in elements:
                                text = element.text.strip()
                                if text and len(text) < 20:  # Datas são geralmente curtas
                                    date_time = text
                                    print(f"Data encontrada: {date_time}")
                                    break
                            if date_time:
                                break
                        except Exception as e:
                            continue
                    
                    if not date_time:
                        date_time = "Data não disponível"
                        print("Data não encontrada")
                    
                    # Extrair foto do contato
                    photo_url = None
                    try:
                        img_elements = chat_content_div.find_elements(By.XPATH, './/img[@src]')
                        for img in img_elements:
                            src = img.get_attribute('src')
                            if src and 'whatsapp.net' in src:
                                photo_url = src
                                print(f"Foto encontrada: {photo_url[:50]}...")
                                break
                    except Exception as e:
                        print("Foto não encontrada")
                    
                    # Extrair número de mensagens não lidas - suporte para PT e EN
                    unread_count = "0"
                    unread_selectors = [
                        './/span[@aria-label*="mensagem não lida"]//span',  # PT
                        './/span[@aria-label*="unread message"]//span',  # EN
                        './/span[contains(@aria-label, "mensagem não lida")]//span',  # PT
                        './/span[contains(@aria-label, "unread message")]//span',  # EN
                        './/span[contains(@class, "x184q3qc")]',
                        './/span[contains(@class, "x1rg5ohu")]'
                    ]
                    
                    for selector in unread_selectors:
                        try:
                            elements = chat_content_div.find_elements(By.XPATH, selector)
                            for element in elements:
                                text = element.text.strip()
                                if text and text.isdigit():
                                    unread_count = text
                                    print(f"Mensagens não lidas: {unread_count}")
                                    break
                            if unread_count != "0":
                                break
                        except Exception as e:
                            continue
                    
                    # Extrair telefone se disponível
                    phone = None
                    if name and name.startswith('+55'):
                        phone = name
                    elif name and any(char.isdigit() for char in name):
                        phone = name
                    
                    result = {
                        'name': name,
                        'phone': phone,
                        'lastMessage': last_message,
                        'dateTime': date_time,
                        'photo': photo_url,
                        'unreadCount': unread_count
                    }

                    chatList.append(result)
                    print(f"Chat {i+1} processado com sucesso (método alternativo)")

                except Exception as e:
                    print(f"Erro ao processar chat {i} (método alternativo): {str(e)}")
                    continue

            print(f"Total de chats processados (método alternativo): {len(chatList)}")
            return chatList

        except Exception as e:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            time.sleep(1)
            print(f"Erro geral (método alternativo): {e}")
            return f"An error occurred: {e}"

    def sendToBucket(self, body, endPoint, method, timeout=12):
        try:
            if method == "POST":
                response = requests.post(endPoint, json=body, timeout=timeout)
            elif method == "GET":
                response = requests.get(endPoint, params=body, timeout=timeout)
            else:
                raise ValueError("Método inválido. Use 'POST' ou 'GET'.")

            response.raise_for_status()  # Verifica se houve algum erro na requisição

            # Retorna o conteúdo JSON da resposta, se houver
            return response.json() if response.headers.get('content-type') == 'application/json' else response.text
        except requests.exceptions.RequestException as e:
            print("Erro ao enviar a requisição:", e)
            return None