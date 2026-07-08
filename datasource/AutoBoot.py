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


	def syncSendText(
		self,
		telefone,
		message,
		unic_sent=False,
		unRead=False,
		skip_open=False,
		return_home=True,
	):
		"""
		Envia texto no WhatsApp Web.
		skip_open=True: chat já está aberto (ex.: após getMessages leave_open).
		return_home=False: mantém o chat aberto para outro envio na sequência.
		"""
		print("[SYNC_SENDTEXT_V2] Iniciando fluxo por URL" if not skip_open else "[SYNC_SENDTEXT_V2] Envio no chat já aberto")

		telefone = ''.join(filter(str.isdigit, str(telefone)))
		if not telefone.startswith("55"):
			telefone = "55" + telefone
		telefone = "+" + telefone
		status = "Enviado"

		print(f"🔍 Iniciando envio para: {telefone}")

		# Verificar se a mensagem já foi enviada para este número (agora opcional).
		# Status deleted é ignorado (soft-delete = como se não existisse).
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
				from datasource import async_send_queue as async_queue

				if async_queue.has_active_queue_message(self.mongo, telefone, message):
					print(f"📌 Mensagem já na fila ativa para {telefone}, ignorando envio.")
					return "Já enviada"
			except Exception as queue_err:
				print(f"[DEPURACAO] Checagem na fila falhou ({queue_err}); seguindo só com histórico legado")

		try:
			import time
			start = time.time()
			print(f"[TEMPO] Início do envio: {start}")

			if not skip_open:
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

			# send_keys no contenteditable costuma falhar com emoji (planos Unicode > BMP).
			# Colar via clipboard é o fluxo mais estável no WhatsApp Web + Selenium.
			if not message.isascii():
				try:
					pyperclip.copy(message)
					time.sleep(0.05)
					inputField.send_keys(Keys.CONTROL, "v")
				except Exception as clip_err:
					print(f"[DEPURACAO] Clipboard falhou ({clip_err}), tentando send_keys")
					for i, parte in enumerate(message.split("\n")):
						if i > 0:
							inputField.send_keys(Keys.SHIFT, Keys.ENTER)
						inputField.send_keys(parte)
			else:
				for i, parte in enumerate(message.split("\n")):
					if i > 0:
						inputField.send_keys(Keys.SHIFT, Keys.ENTER)
					inputField.send_keys(parte)

			time.sleep(0.3)
			inputField.send_keys(Keys.RETURN)
			print(f"[DEPURACAO] Mensagem enviada")
			print(f"[TEMPO] Após enviar mensagem: {time.time() - start:.2f}s")

			if unRead:
				time.sleep(0.2)
				try:
					ActionChains(self.navegador).key_down(Keys.CONTROL).key_down(Keys.ALT).key_down(
						Keys.SHIFT
					).send_keys("u").key_up(Keys.SHIFT).key_up(Keys.ALT).key_up(Keys.CONTROL).perform()
					print("[DEPURACAO] Chat marcado como não lido (Ctrl+Alt+Shift+U)")
				except Exception as unread_err:
					print(f"[DEPURACAO] Falha ao marcar como não lido: {unread_err}")

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
			# Não confundir falha de automação com número inválido (UI WhatsApp).
			err_preview = str(e).replace("\n", " ")[:180]
			status = f"Erro ao enviar: {err_preview}"

		if return_home:
			# Volta para a home / lista lateral (poller de triggers depende do #pane-side)
			try:
				webdriver.ActionChains(self.navegador).send_keys(Keys.ESCAPE).perform()
			except Exception:
				pass
			try:
				self.navegador.get("https://web.whatsapp.com/")
				WebDriverWait(self.navegador, 15).until(
					EC.presence_of_element_located((By.ID, "app"))
				)
			except Exception as home_err:
				print(f"[DEPURACAO] Falha ao voltar para home após envio: {home_err}")

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