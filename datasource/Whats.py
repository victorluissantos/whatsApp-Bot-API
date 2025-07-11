from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from decouple import Config, RepositoryEnv
from datetime import datetime
from PIL import Image
from io import BytesIO
from urllib.parse import unquote
from random import randint
from random import seed
from random import random

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
import threading

class Run:

	def __init__(self, browser=None, mongo=None, env=None):
		self.navegador = browser
		self.mongo = mongo
		self.env = env

	def getProfile(self, navegador=None):
		instancy = {}
		menu = False
		WebDriverWait(navegador, 10).until(
			EC.presence_of_element_located((By.ID, 'side'))
		)
		webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
		
		try:
			# Simular Control + Alt + P
			webdriver.ActionChains(navegador).key_down(Keys.CONTROL).key_down(Keys.ALT).send_keys('p').key_up(Keys.ALT).key_up(Keys.CONTROL).perform()
			# navegador.find_element(By.XPATH, '//*[@id="app"]/div/div/div[3]/header/div[1]/div/img').click()
			menu = True
		except:
			try:
				navegador.find_element(By.XPATH, '//*[@id="app"]/div/div/div[4]/header/div[1]/div/img').click()
				menu = True
			except:
				menu = False
	
		if menu:    
			time.sleep(3)
			##########################################
			## Tenta pegar o nome do usuario logado ##
			##########################################
			try:
				name = navegador.find_element(By.XPATH, '//*[@id="app"]/div/div[3]/div[2]/div[1]/span/div/span/div/div/div[2]/div[1]/div[1]/div[2]/div/div')
				instancy['profileName'] = name.get_attribute("innerText")
			except NoSuchElementException:
				instancy['profileName'] = 'Web Whatsapp'


			##########################################
			## END 
			##########################################

			##########################################
			## Tenta pegar a foto do usuario logado ##
			##########################################
			try:
				# Common
				imgsrc = navegador.find_element(By.XPATH, '//*[@id="app"]/div/div[3]/div[2]/div[1]/span/div/span/div/div/div[1]/div[2]/div/div/button/div/div/div/img')
			except NoSuchElementException:
				# Default if none of the XPaths work
				instancy['profilePhote'] = 'https://i0.wp.com/ugtechmag.com/wp-content/uploads/2018/08/whatsapp-promo.png'

			# If one of the XPaths succeeded, download the image and set the path
			if 'imgsrc' in locals():
				try:
					urllib.request.urlretrieve(imgsrc.get_attribute("src"), "static/tmp/photo.png")
					instancy['profilePhote'] = "static/tmp/photo.png"
				except Exception as e:
					# Handle the exception if image download fails
					print(f"Error downloading image: {str(e)}")
			##########################################
			## END 
			##########################################

			webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
			return instancy

	def isLogado(self, navegador=None):
	    try:
	        selectors = [
	            (By.ID, 'side'),
	            (By.XPATH, '//div[@data-testid="chat-list"]'),
	            (By.XPATH, '//div[@data-testid="conversation-panel-wrapper"]'),
	            (By.XPATH, '//div[@data-testid="default-user"]'),
	            (By.XPATH, '//div[@data-testid="chat-list-search"]')
	        ]
	        for selector in selectors:
	            try:
	                WebDriverWait(navegador, 3).until(
	                    EC.presence_of_element_located(selector)
	                )
	                return True
	            except:
	                continue
	        # Se nenhum seletor funcionou, verifica se existe QR code (indicando que não está logado)
	        try:
	            qr_selectors = [
	                'canvas[aria-label="Scan this QR code to link a device!"]',
	                'canvas[width="228"][height="228"]',
	                '//canvas[@aria-label="Scan this QR code to link a device!"]'
	            ]
	            for qr_selector in qr_selectors:
	                try:
	                    if qr_selector.startswith('//'):
	                        navegador.find_element(By.XPATH, qr_selector)
	                    else:
	                        navegador.find_element(By.CSS_SELECTOR, qr_selector)
	                    return False  # QR code encontrado = não logado
	                except:
	                    continue
	            return False
	        except:
	            return False
	    except Exception as e:
	        return False

	def getScreenShot(self, navegador=None):
		file_name = 'static/tmp/shot.png'
		navegador.save_screenshot(file_name)
		return file_name

	def getQrCode(self, navegador=None):
		# Tenta encontrar o canvas do QR code
		qr_selectors = [
			'canvas[aria-label="Scan this QR code to link a device!"]',
			'canvas[width="228"][height="228"]',
			'//canvas[@aria-label="Scan this QR code to link a device!"]',
			'//canvas[@width="228" and @height="228"]'
		]
		canvas = None
		for selector in qr_selectors:
			try:
				if selector.startswith('//'):
					canvas = navegador.find_element(By.XPATH, selector)
				else:
					canvas = navegador.find_element(By.CSS_SELECTOR, selector)
				if canvas:
					break
			except:
				continue

		# Se não encontrou QR code, pode estar logado ou carregando
		if not canvas:
			raise Exception("QR code não encontrado - pode estar logado ou carregando")

		# Tenta clicar no botão de refresh do QR code (opcional)
		try:
			navegador.find_element(By.XPATH, '//*[@id="app"]/div/div/div[3]/div[1]/div/div/div[2]/div/span/button').click()
		except:
			pass

		time.sleep(1)

		# Extrai o QR code diretamente do canvas como base64 completo
		qr_base64_full = navegador.execute_script("""
			var canvas = arguments[0];
			return canvas.toDataURL('image/png');
		""", canvas)

		return qr_base64_full  # Exemplo: 'data:image/png;base64,...'

	def syncSendText(self, telefone, message):
		for thread in threading.enumerate():
			if thread is not threading.current_thread():  # Não encerra a própria thread
				os._exit(0)  # Força o encerramento de outros processos
				
		try:
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			time.sleep(0.5)
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			time.sleep(0.5)

			# Clica no botão 'Nova conversa' usando seletor CSS
			btn_nova_conversa = self.navegador.find_element(By.CSS_SELECTOR, 'button[title="Nova conversa"][aria-label="Nova conversa"]')
			btn_nova_conversa.click()
			time.sleep(0.5)

			# Campo de busca de contato/conversa
			campo_busca = self.navegador.find_element(By.CSS_SELECTOR, 'div[contenteditable="true"][aria-label="Pesquisar nome ou número"]')
			campo_busca.clear()
			time.sleep(0.2)
			telefone = '+55'+telefone
			campo_busca.send_keys(telefone)
			time.sleep(1)
			campo_busca.send_keys(Keys.RETURN)
			time.sleep(1)

			# Campo de mensagem
			inputField = self.getInputMessageEl()
			if not inputField:
				return 'Campo de mensagem não encontrado'
			inputField.clear()
			time.sleep(0.5)
			inputField.send_keys(message)
			time.sleep(0.5)
			inputField.send_keys(Keys.RETURN)

		except NoSuchElementException:
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			return 'Invalido!'

		# volta a home page
		webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()

		return 'Enviado'


	def getInputMessageEl(self):
		try:
			# Novo seletor robusto baseado no HTML inspecionado
			inputField = WebDriverWait(self.navegador, 12).until(
				EC.presence_of_element_located(
					(By.CSS_SELECTOR, 'div[contenteditable="true"][aria-label="Digite uma mensagem"]')
				)
			)
			return inputField
		except NoSuchElementException:
			return None

	def syncSendTextUrl(self, telefone, message):

		try:
			
			link = f"https://web.whatsapp.com/send?phone={telefone}&text={message}"
			self.navegador.get(link)

			try:
				WebDriverWait(self.navegador, 30).until(
					EC.presence_of_element_located((By.ID, 'side'))
				)
			except Exception as e:
				with open("erros_log.txt", "a", encoding="utf-8") as f:
					f.write(f"Erro ao processar o syncSendTextUrl[233]: {str(e)}\n")
				time.sleep(30)

			try:
				element = WebDriverWait(self.navegador, 10).until(
					EC.element_to_be_clickable((By.XPATH, '//div[@aria-label="Enviar"]'))
				)
				element.click()
			except Exception as e:
				with open("erros_log.txt", "a", encoding="utf-8") as f:
					f.write(f"Erro ao processar o syncSendTextUrl[241]: {str(e)}\n")

				for i in [20, 21, 22, 23, 24, 25, 26]:
					try:
						button = self.navegador.execute_script(f"return document.getElementsByTagName('button')[{i}];")
						# if button:
						self.navegador.execute_script(f"document.getElementsByTagName('button')[{i}].click();")
						print(f"Botão {i} clicado com sucesso.")
							# break  # Sai do loop se um clique for bem-sucedido
					except Exception as e:
						print(f"Erro ao tentar clicar no botão {i}, ignorando...")
				time.sleep(4)
			seed(4)

			# volta a home page
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			status = 'Enviado'

		except Exception as e:
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			with open("erros_log.txt", "a", encoding="utf-8") as f:
				f.write(f"Erro ao processar o syncSendTextUrl[261]: {str(e)}\n")
			status = 'Pendente'

		message = unquote(message)
		# Persistência no MongoDB
		self.mongo.collection.insert_one({
			"telefone": telefone,
			"mensagem": message,
			"data_hora": datetime.utcnow(),  # Hora em UTC
			"status": status
		})

		return status

	def unconnect(self, navegador):
		try:
			WebDriverWait(navegador, 10).until(
				EC.presence_of_element_located((By.ID, 'side'))
			)

			element = navegador.find_element_by_xpath('//div[@title="Mais opções"]')
			navegador.execute_script("arguments[0].click();", element)

			time.sleep(1.5)
			
			element = navegador.find_element_by_xpath('//div[@aria-label="Desconectar"]')
			navegador.execute_script("arguments[0].click();", element)

			element = WebDriverWait(navegador, 10).until(
				EC.presence_of_element_located((By.XPATH, '//*[@id="app"]/div/span[2]/div/div/div/div/div/div/div[3]/div/button[2]'))
			)

			navegador.execute_script("arguments[0].click();", element)
			return True
		except Exception as e:
			return f"Please, check if your instancie are connect!"

	def checkDailyLimit(self, navegador, limit = 100, delay = 15000):
		'''
			Verifica o limite diario estabelecido pelo operador, para envio de mensagens
		'''
		pass

	def resetPage(self, navegador=None):
	    if navegador is None:
	        raise Exception("Navegador não informado")

	    actions = ActionChains(navegador)
	    actions.key_down(Keys.CONTROL).key_down(Keys.SHIFT).send_keys('r').key_up(Keys.SHIFT).key_up(Keys.CONTROL).perform()
	    time.sleep(5)  # Aguarda recarregar
	    return True


# if __name__ == "__main__":
#     env=Config(RepositoryEnv('.env'))
#     db = database.Database('queues', env.get('FLASK_NAME'))
#     w = Run('modelo', db)
#     w.run()