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
		print("[SYNC_SENDTEXT_V2] Iniciando fluxo por URL")

		telefone = ''.join(filter(str.isdigit, str(telefone)))
		if not telefone.startswith("55"):
			telefone = "55" + telefone
		telefone = "+" + telefone
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

			# Fluxo resiliente: abre direto o chat por URL para evitar quebra de seletor
			link = f"https://web.whatsapp.com/send?phone={telefone.replace('+', '')}"
			self.navegador.get(link)
			print(f"[DEPURACAO] URL aberta: {link}")

			WebDriverWait(self.navegador, 20).until(
				EC.presence_of_element_located((By.ID, "app"))
			)

			# Detecta número inválido (pt/en) de forma explícita
			invalid_xpaths = [
				'//*[contains(text(), "número de telefone compartilhado pela URL é inválido")]',
				'//*[contains(text(), "Phone number shared via url is invalid")]',
				'//*[contains(text(), "phone number shared via url is invalid")]',
				'//*[contains(text(), "não está no WhatsApp")]',
				'//*[contains(text(), "isn\'t on WhatsApp")]'
			]
			for invalid_xpath in invalid_xpaths:
				try:
					WebDriverWait(self.navegador, 2).until(
						EC.presence_of_element_located((By.XPATH, invalid_xpath))
					)
					print("[DEPURACAO] Número marcado como inválido pelo WhatsApp Web")
					try:
						self.navegador.save_screenshot("static/tmp/send_invalid.png")
					except Exception:
						pass
					status = "Inválido!"
					break
				except TimeoutException:
					continue

			if status != "Enviado":
				return status

			# Aguarda campo de mensagem com seletores atuais + fallback
			inputField = None
			seletores_input = [
				'div[contenteditable="true"][data-testid="conversation-compose-box-input"]',
				'footer div[contenteditable="true"][role="textbox"]',
				'div[contenteditable="true"][aria-label="Digite uma mensagem"]',
				'div[contenteditable="true"][aria-label="Type a message"]'
			]
			for seletor in seletores_input:
				try:
					inputField = WebDriverWait(self.navegador, 12).until(
						EC.presence_of_element_located((By.CSS_SELECTOR, seletor))
					)
					print(f"[DEPURACAO] Campo de mensagem encontrado: {seletor}")
					break
				except TimeoutException:
					continue

			if not inputField:
				print("❌ Campo de mensagem não encontrado!")
				try:
					self.navegador.save_screenshot("static/tmp/send_input_not_found.png")
				except Exception:
					pass
				return "Campo de mensagem não encontrado"

			inputField.click()
			time.sleep(0.2)
			inputField.send_keys(Keys.CONTROL, "a")
			inputField.send_keys(Keys.BACKSPACE)
			print(f"[DEPURACAO] Iniciando digitação da mensagem: {message}")

			for i, parte in enumerate(message.split('\n')):
				if i > 0:
					inputField.send_keys(Keys.SHIFT, Keys.ENTER)
				inputField.send_keys(parte)

			time.sleep(0.3)
			inputField.send_keys(Keys.RETURN)
			print(f"[DEPURACAO] Mensagem enviada")
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
			try:
				self.navegador.save_screenshot("static/tmp/send_exception.png")
			except Exception:
				pass
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