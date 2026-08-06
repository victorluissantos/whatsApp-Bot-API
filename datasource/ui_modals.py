"""
Fecha overlays do WhatsApp Web que bloqueiam a automação.

Exemplos: modal de novidades ("Novidades do WhatsApp Web") com botão Continuar.
Novos dismissers podem ser adicionados em dismiss_blocking_modals_once.
"""
import logging

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By


class Run:
	# CTA do modal de novidades / confirm-popup (i18n).
	_CONTINUE_LABELS = (
		"Continuar",
		"Continue",
	)

	# Fechar (canto superior) como fallback.
	_CLOSE_ARIA_LABELS = (
		"Fechar",
		"Close",
		"Cerrar",
	)

	def has_confirm_popup(self, navegador) -> bool:
		"""Retorna True se o confirm-popup estiver visível (checagem barata, só leitura)."""
		try:
			for popup in navegador.find_elements(
				By.CSS_SELECTOR, '[data-testid="confirm-popup"]'
			):
				try:
					if popup.is_displayed():
						return True
				except StaleElementReferenceException:
					continue
		except Exception:
			pass
		return False

	def dismiss_blocking_modals_once(self, navegador) -> bool:
		"""
		Tenta fechar modais conhecidos que bloqueiam a UI.
		Retorna True se algum clique foi disparado.
		"""
		if navegador is None:
			return False
		if self._dismiss_whats_new_confirm_popup(navegador):
			return True
		return False

	def _click_element(self, navegador, el) -> bool:
		try:
			el.click()
			return True
		except Exception:
			try:
				navegador.execute_script("arguments[0].click();", el)
				return True
			except Exception:
				return False

	def _dismiss_whats_new_confirm_popup(self, navegador) -> bool:
		"""
		Modal data-testid=confirm-popup (ex.: Novidades do WhatsApp Web).
		Prioriza o botão Continuar; se não achar, tenta Fechar.
		"""
		popups = []
		try:
			popups = navegador.find_elements(
				By.CSS_SELECTOR, '[data-testid="confirm-popup"]'
			)
		except Exception:
			popups = []

		for popup in popups:
			try:
				if not popup.is_displayed():
					continue
			except StaleElementReferenceException:
				continue

			for label in self._CONTINUE_LABELS:
				xpath = f".//button[.//span[normalize-space()='{label}']]"
				try:
					for btn in popup.find_elements(By.XPATH, xpath):
						try:
							if not btn.is_displayed():
								continue
						except StaleElementReferenceException:
							continue
						if self._click_element(navegador, btn):
							logging.info(
								"Modal confirm-popup fechado via Continuar (rótulo: %s)",
								label,
							)
							return True
				except StaleElementReferenceException:
					continue

			for aria in self._CLOSE_ARIA_LABELS:
				xpath = f'.//button[@aria-label="{aria}"]'
				try:
					for btn in popup.find_elements(By.XPATH, xpath):
						try:
							if not btn.is_displayed():
								continue
						except StaleElementReferenceException:
							continue
						if self._click_element(navegador, btn):
							logging.info(
								"Modal confirm-popup fechado via %s",
								aria,
							)
							return True
				except StaleElementReferenceException:
					continue

		# Fallback: Continuar visível fora do escopo confirm-popup (UI mudou o testid).
		for label in self._CONTINUE_LABELS:
			xpath = f"//button[.//span[normalize-space()='{label}']]"
			try:
				for btn in navegador.find_elements(By.XPATH, xpath):
					try:
						if not btn.is_displayed():
							continue
					except StaleElementReferenceException:
						continue
					if self._click_element(navegador, btn):
						logging.info(
							"Botão %s clicado (fallback sem confirm-popup)",
							label,
						)
						return True
			except StaleElementReferenceException:
				continue

		return False
