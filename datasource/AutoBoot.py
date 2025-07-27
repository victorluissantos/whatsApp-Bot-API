import time
import pymongo
from datetime import datetime
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium import webdriver
from decouple import Config, RepositoryEnv
import pyperclip

class WhatsAppBot:
	
	def __init__(self, navegador, mongo):
		
		self.navegador = navegador
		self.mongo = mongo


	def syncSendText(self, telefone, message, unic_sent=False):

		if not telefone.startswith("+55"):
			telefone = "+55" + telefone
		status = "Enviado"

		print(f"🔍 Iniciando envio para: {telefone}")

		# Verificar se a mensagem já foi enviada para este número (agora opcional)
		if unic_sent:
			mensagem_existente = self.mongo.collection.find_one({
				"telefone": telefone,
				"mensagem": message,
				"status": "Enviado"
			})

			if mensagem_existente:
				print(f"📌 Mensagem já enviada para {telefone}, ignorando envio.")
				return "Já enviada"

		try:
			import time
			start = time.time()
			print(f"[TEMPO] Início do envio: {start}")

			print("🔄 Pressionando ESC duas vezes...")
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			time.sleep(0.2)  # Reduzido de 0.5s
			webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			time.sleep(0.2)  # Reduzido de 0.5s
			print(f"[TEMPO] Após ESC: {time.time() - start:.2f}s")

			print("🔄 Usando Ctrl+N para nova conversa...")
			actions = ActionChains(self.navegador)
			actions.key_down(Keys.CONTROL).key_down(Keys.ALT).send_keys('n').key_up(Keys.ALT).key_up(Keys.CONTROL).perform()
			time.sleep(1)  # Reduzido de 2s
			print(f"[TEMPO] Após Ctrl+Alt+N: {time.time() - start:.2f}s")

			print("🔍 Procurando campo de busca...")
			# Campo de busca de contato/conversa - seletores otimizados
			campo_busca = None
			seletores_busca = [
				'div[contenteditable="true"][data-tab="3"]',  # Mais comum primeiro
				'div[contenteditable="true"][aria-label="Pesquisar nome ou número"]',
				'div[contenteditable="true"][aria-label="Search or start new chat"]',
				'div[contenteditable="true"][data-tab="1"]',
				'div[contenteditable="true"][role="textbox"]',
				'div[contenteditable="true"]'
			]
			for i, seletor in enumerate(seletores_busca):
				try:
					print(f"🔍 Tentando seletor {i+1}: {seletor}")
					campo_busca = WebDriverWait(self.navegador, 2).until(  # Reduzido de 3s
						EC.presence_of_element_located((By.CSS_SELECTOR, seletor))
					)
					print(f"✅ Campo de busca encontrado com seletor {i+1}!")
					break
				except (NoSuchElementException, TimeoutException) as e:
					print(f"❌ Seletor {i+1} falhou: {e}")
					continue
			if not campo_busca:
				print("❌ Nenhum seletor funcionou para o campo de busca!")
				raise Exception("Campo de busca não encontrado com nenhum seletor")
			print(f"[TEMPO] Após encontrar campo de busca: {time.time() - start:.2f}s")

			campo_busca.clear()
			time.sleep(0.1)  # Reduzido de 0.2s
			# Verificar se o telefone já tem +55, se não tiver, adicionar
			if not telefone.startswith('+55'):
				telefone = '+55'+telefone
			campo_busca.send_keys(telefone)
			print(f"[DEPURACAO] Telefone digitado: {telefone}")
			time.sleep(1)  # Reduzido de 2s
			
			# Verificar se o contato foi encontrado antes de pressionar Enter
			try:
				# Aguardar até 3 segundos para o resultado da busca aparecer
				resultado_busca = WebDriverWait(self.navegador, 3).until(  # Reduzido de 5s
					EC.presence_of_element_located((By.CSS_SELECTOR, 'div[data-testid="cell-phone"], div[role="option"]'))
				)
				print(f"[DEPURACAO] Contato encontrado: {resultado_busca.text}")
				time.sleep(0.5)  # Reduzido de 1s
			except:
				print(f"[DEPURACAO] Contato não encontrado, tentando mesmo assim...")
			
			campo_busca.send_keys(Keys.RETURN)
			print(f"[DEPURACAO] Enter pressionado para abrir conversa")
			time.sleep(1)  # Reduzido de 2s

			# 3. Preencher e enviar mensagem
			inputField = None
			seletores_input = [
				'div[contenteditable="true"][aria-label="Digite uma mensagem"]',
				'div[contenteditable="true"][data-tab="10"]',
				'div[contenteditable="true"][role="textbox"]',
				'div[contenteditable="true"]'
			]
			for i, seletor in enumerate(seletores_input):
				try:
					inputField = WebDriverWait(self.navegador, 3).until(  # Reduzido de 5s
						EC.presence_of_element_located((By.CSS_SELECTOR, seletor))
					)
					break
				except (NoSuchElementException, TimeoutException):
					continue
			if not inputField:
				print("❌ Campo de mensagem não encontrado!")
				return "Campo de mensagem não encontrado"
			inputField.click()
			inputField.clear()
			time.sleep(0.1)  # Reduzido de 0.2s
			print(f"[DEPURACAO] Iniciando digitação da mensagem: {message}")
			# Enviar mensagem com suporte a quebras de linha
			for i, parte in enumerate(message.split('\n')):
				if i > 0:
					inputField.send_keys(Keys.SHIFT, Keys.ENTER)  # Quebra de linha
				inputField.send_keys(parte)
			print(f"[DEPURACAO] Mensagem digitada completamente (com quebras de linha)")
			time.sleep(1)
			inputField.send_keys(Keys.RETURN)
			print(f"[DEPURACAO] Enter enviado")
			print(f"[TEMPO] Após enviar mensagem: {time.time() - start:.2f}s")
			# Captura screenshot após o envio
			# try:
				# screenshot_path = 'static/tmp/after_send.png'
				# self.navegador.save_screenshot(screenshot_path)
				# print(f"[DEPURACAO] Screenshot salvo em {screenshot_path}")
			# except Exception as e:
				# print(f"[ERRO] Falha ao capturar screenshot: {e}")
			# print(f"[TEMPO] Tempo total do método: {time.time() - start:.2f}s")

		except Exception as e:
			print(f"❌ Erro ao enviar mensagem: {e}")
			print(f"❌ Tipo do erro: {type(e).__name__}")
			import traceback
			print(f"❌ Traceback completo:")
			traceback.print_exc()
			status = "Inválido!"

		# Volta para a home
		webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()

		# Persistência no MongoDB
		self.mongo.collection.insert_one({
			"telefone": telefone,
			"mensagem": message,
			"data_hora": datetime.utcnow(),  # Hora em UTC
			"status": status
		})

		return status

	'''
	Localiza o inut onde deve ser digitado mensagem
	'''
	def getInputMessageEl(self):
		seletores = [
			'div[contenteditable="true"][aria-label="Digite uma mensagem"]',
			'div[contenteditable="true"][data-tab="6"]',
			'div[contenteditable="true"][data-tab="10"]',
			'div[contenteditable="true"][role="textbox"]',
			'div[contenteditable="true"]',
		]
		for i, seletor in enumerate(seletores):
			try:
				print(f"🔍 Tentando seletor de mensagem {i+1}: {seletor}")
				inputField = WebDriverWait(self.navegador, 3).until(
					EC.presence_of_element_located((By.CSS_SELECTOR, seletor))
				)
				print(f"✅ Campo de mensagem encontrado com seletor {i+1}!")
				return inputField
			except (NoSuchElementException, TimeoutException) as e:
				print(f"❌ Seletor de mensagem {i+1} falhou: {e}")
				continue
		print("❌ Nenhum seletor funcionou para o campo de mensagem!")
		return None