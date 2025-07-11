from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import TimeoutException
from decouple import Config, RepositoryEnv
from datetime import datetime
# Removido import do Flask - não necessário para FastAPI
from PIL import Image
from io import BytesIO

import urllib.request
import urllib.parse
import requests
import urllib
import PIL, sys
import json
import time
import shutil
import sys
import os
import re


class Run:

    def webhook(self, navegador, bucket=None, method="POST"):

        while True:
            chatList = [{'msg':'Nenhuma mensagem não lida'}]

            try:
                # Verifica se há elementos não lidos na lista de chat
                try:
                    navegador.find_element_by_xpath('//span[@aria-label="Não lidas"] | //span[contains(@aria-label, "mensagem não lida")]')

                    chatList = self.getUnreadChats(navegador)

                    # self.sendToBucket(chatList, bucket, method)
                    if any(chat.get('total') and int(chat['total']) >= 1 for chat in chatList):
                        self.sendToBucket(chatList, bucket, method)
                    
                    time.sleep(5)
                    
                    # else:
                    #     self.sendToBucket([{'nao':'total de 1 ou mais nao localizado'}], bucket, method)
                    #     time.sleep(5)

                except NoSuchElementException:
                    # self.sendToBucket([{'excpetion':'line 53'}], bucket, method)
                    pass  # Se não encontrar elementos, continua sem chamar sendToBucket

            except TimeoutException:
                # self.sendToBucket([{'excpetion':'line 57'}], bucket, method)
                webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
                time.sleep(10)


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
                # chatList.append({'excpetion':str(e)})
                time.sleep(1)
                # print("[DC-gU45] - Please, unread list in your instancie are connect!")
                # return chatList

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

    def getConversation(self, navegador, telefone):
        if not telefone.startswith("+55"):
            telefone = "+55" + telefone

        webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.5)

        try:
            ActionChains(navegador).key_down(Keys.CONTROL).key_down(Keys.ALT).send_keys("n").perform()
        except (NoSuchElementException, TimeoutException):
            try:
                element = navegador.find_element(By.XPATH, '//button[@title="Nova conversa"]')
            except (NoSuchElementException, TimeoutException):
                element = navegador.find_element(By.XPATH, '//div[@title="Nova conversa"]')
            
            navegador.execute_script("arguments[0].click();", element)

        time.sleep(0.3)

        try:
            element = WebDriverWait(navegador, 2).until(
                EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/div/div[2]/div[2]/div[1]/span/div/span/div/div[1]/div[2]/div[2]/div/div[1]/p'))
            )
        except (NoSuchElementException, TimeoutException):
            try:
                element = WebDriverWait(navegador, 2).until(
                    EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/div/div/div[3]/div/div[2]/div[1]/span/div/span/div/div[1]/div[2]/div/div/div[1]/p'))
                )
            except (NoSuchElementException, TimeoutException):
                element = WebDriverWait(navegador, 2).until(
                    EC.presence_of_element_located((By.XPATH, '//*[@id="app"]/div/div[3]/div[2]/div[1]/span/div/span/div/div[1]/div[2]/div[2]/div/div/p'))
                )

        element.clear()
        time.sleep(0.2)

        for char in telefone:
            element.send_keys(char)
            time.sleep(0.1)

        time.sleep(1)
        element.send_keys(Keys.RETURN)

        try:
            # Aguarde até carregar a conversa se houver
            main = WebDriverWait(navegador, 20).until(
                EC.presence_of_element_located((By.ID, 'main'))
            )

            messages = main.find_elements(By.CSS_SELECTOR, '[data-pre-plain-text]')

            mensagens = []
            is_sender = None

            for message in reversed(messages):
                data_id = message.get_attribute('data-id')

                if data_id:
                    is_sender = data_id == "true_"

                message_info = self.extract_message_info(message)
                mensagens.append(message_info)

            hasChat = bool(messages)
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            
            return {
                'messages': mensagens,
                'hasChat': hasChat,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': True,
                'error': None
            }

        except NoSuchElementException:
            webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()

            return {
                'messages': [],
                'hasChat': False,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'status': False,
                'error': True
            }

    def extract_message_info(self, element):
        msg = element.text.strip()
        timestamp = element.get_attribute('data-pre-plain-text')
        if timestamp:
            try:
                origin = timestamp.split('] ')[1].split(': ')[0]
                # Extrair a parte numérica da string de timestamp
                timestamp_str = timestamp.split(']')[0].replace('[', '').strip()
                timestamp = datetime.strptime(timestamp_str, '%H:%M, %d/%m/%Y').strftime('%d/%m/%Y %H:%M')
            except Exception as e:
                print(f"Erro ao converter timestamp: {e}")
                origin = timestamp = None

        return {"msg": msg, "datetime": timestamp, "origin":origin}


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