from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException
import time


class Run:

    def __init__(self, browser=None, mongo=None, env=None):
        self.navegador = browser
        self.mongo = mongo
        self.env = env

    def _normalize_phone(self, phone):
        telefone_digits = "".join(filter(str.isdigit, str(phone or "")))
        if not telefone_digits:
            return None
        if not telefone_digits.startswith("55"):
            telefone_digits = "55" + telefone_digits
        return telefone_digits

    # WhatsApp Web atual (2026): sem message-in/out nem true_/false_ no data-id.
    # Sinais reais: tail-out/tail-in, aria-label="Você:", ícone de entrega.
    _EXTRACT_MESSAGES_JS = r"""
        const limit = arguments[0] || 20;
        const root = document.querySelector('#main') || document;

        let nodes = Array.from(root.querySelectorAll('[data-testid="msg-container"]'));
        if (!nodes.length) {
            nodes = Array.from(root.querySelectorAll('div.message-out, div.message-in'));
        }

        const filtered = [];
        for (const el of nodes) {
            if (filtered.some((n) => n.contains(el) || el.contains(n))) continue;
            filtered.push(el);
        }

        function detectOrigem(el) {
            // 1) Cauda da bolha (WhatsApp Web atual)
            if (el.querySelector('[data-icon="tail-out"], [data-testid="tail-out"]')) {
                return 'enviada';
            }
            if (el.querySelector('[data-icon="tail-in"], [data-testid="tail-in"]')) {
                return 'recebida';
            }

            // 2) aria-label do remetente (mensagem agrupada sem cauda)
            const labels = Array.from(el.querySelectorAll('[aria-label]'))
                .map((n) => ((n.getAttribute('aria-label') || '').trim().toLowerCase()));
            if (labels.some((l) => l === 'você:' || l === 'voce:' || l === 'you:')) {
                return 'enviada';
            }
            // Contato (telefone/nome) no aria-label => recebida
            if (labels.some((l) => l.endsWith(':'))) {
                return 'recebida';
            }

            // 3) Ícone/status de entrega (só enviadas)
            if (el.querySelector('[aria-label*="Entregue"], [aria-label*="Delivered"], [aria-label*="Lida"], [aria-label*="Read"], [aria-label*="Enviada"]')) {
                return 'enviada';
            }
            const titles = Array.from(el.querySelectorAll('svg title'))
                .map((t) => (t.textContent || '').toLowerCase());
            if (titles.some((t) => t.includes('wds-ic-read') || t.includes('msg-check') || t.includes('msg-dblcheck'))) {
                return 'enviada';
            }

            // 4) Fallbacks de versões antigas
            const bubble = el.closest('div.message-out, div.message-in') || el;
            const cls = (bubble.className && String(bubble.className)) || '';
            if (cls.includes('message-out')) return 'enviada';
            if (cls.includes('message-in')) return 'recebida';

            const idNode = el.closest('[data-id]') || el.querySelector('[data-id]');
            const dataId = ((idNode && idNode.getAttribute('data-id')) || '').toLowerCase();
            if (dataId.startsWith('true_')) return 'enviada';
            if (dataId.startsWith('false_')) return 'recebida';

            return 'recebida';
        }

        function extractText(el) {
            const textEl = el.querySelector(
                '[data-testid="selectable-text"], span.selectable-text.copyable-text, span.selectable-text, span[dir="ltr"]'
            );
            const message = textEl ? (textEl.innerText || '').trim() : '';
            if (message) return message;
            if (el.querySelector('[data-testid="audio-play"]')) return '[Áudio]';
            if (el.querySelector('img')) return '[Mídia]';
            return 'Mensagem não legível';
        }

        function extractData(el) {
            const copyable = el.querySelector('div.copyable-text');
            const pre = (copyable && copyable.getAttribute('data-pre-plain-text')) || '';
            if (pre) {
                const m = pre.match(/\[([^\]]+)\]/);
                return (m ? m[1] : pre).trim();
            }
            const meta = el.querySelector('[data-testid="msg-meta"]');
            const metaText = meta ? (meta.innerText || '').trim() : '';
            return metaText || 'Horário não disponível';
        }

        return filtered.slice(-limit).map((el) => ({
            message: extractText(el),
            data: extractData(el),
            origem: detectOrigem(el),
        }));
    """

    def getMessages(self, navegador, phone):
        """
        Abre a conversa de um contato pelo número (via URL do WhatsApp Web)
        e extrai as mensagens da conversa.
        """
        try:
            print(f"Iniciando busca de mensagens para o telefone: {phone}")

            telefone_digits = self._normalize_phone(phone)
            if not telefone_digits:
                return {
                    "success": False,
                    "error": "Telefone inválido",
                    "phone": phone,
                }

            # Mesmo fluxo resiliente do syncSendText: abre o chat direto por URL.
            link = f"https://web.whatsapp.com/send?phone={telefone_digits}"
            navegador.get(link)
            print(f"[DEPURACAO] URL aberta: {link}")

            WebDriverWait(navegador, 20).until(
                EC.presence_of_element_located((By.ID, "app"))
            )

            invalid_xpaths = [
                '//*[contains(text(), "número de telefone compartilhado pela URL é inválido")]',
                '//*[contains(text(), "Phone number shared via url is invalid")]',
                '//*[contains(text(), "phone number shared via url is invalid")]',
                '//*[contains(text(), "não está no WhatsApp")]',
                '//*[contains(text(), "isn\'t on WhatsApp")]',
            ]
            for invalid_xpath in invalid_xpaths:
                try:
                    WebDriverWait(navegador, 2).until(
                        EC.presence_of_element_located((By.XPATH, invalid_xpath))
                    )
                    return {
                        "success": False,
                        "error": "Contato/número inválido ou não está no WhatsApp",
                        "phone": phone,
                    }
                except TimeoutException:
                    continue

            conversation_loaded = False
            ready_selectors = [
                (By.CSS_SELECTOR, 'div[contenteditable="true"][data-testid="conversation-compose-box-input"]'),
                (By.CSS_SELECTOR, 'footer div[contenteditable="true"][role="textbox"]'),
                (By.CSS_SELECTOR, 'div[contenteditable="true"][aria-label="Digite uma mensagem"]'),
                (By.CSS_SELECTOR, 'div[contenteditable="true"][aria-label="Type a message"]'),
                (By.XPATH, '//div[@data-testid="conversation-panel-wrapper"]'),
                (By.XPATH, '//div[@data-testid="conversation-panel-messages"]'),
                (By.XPATH, '//div[@id="main"]'),
            ]
            for by, selector in ready_selectors:
                try:
                    WebDriverWait(navegador, 12).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    conversation_loaded = True
                    print(f"Conversa carregada com seletor: {selector}")
                    break
                except TimeoutException:
                    continue

            if not conversation_loaded:
                return {
                    "success": False,
                    "error": "Conversa não encontrada ou não foi possível carregar",
                    "phone": phone,
                }

            try:
                contact_name = navegador.find_element(
                    By.XPATH, '//span[@data-testid="conversation-title"]'
                )
                contact_name_text = contact_name.text
            except NoSuchElementException:
                contact_name_text = f"Contato {phone}"
                for title_selector in (
                    '#main header span[dir="auto"]',
                    '#main header span[title]',
                    'header span[data-testid="conversation-info-header-chat-title"]',
                ):
                    try:
                        el = navegador.find_element(By.CSS_SELECTOR, title_selector)
                        title = (el.get_attribute("title") or el.text or "").strip()
                        if title:
                            contact_name_text = title
                            break
                    except NoSuchElementException:
                        continue

            print(f"Nome do contato: {contact_name_text}")
            time.sleep(1.5)

            try:
                messages = navegador.execute_script(self._EXTRACT_MESSAGES_JS, 20) or []
            except Exception as js_err:
                print(f"Falha na extração JS de mensagens: {js_err}")
                messages = []

            if not isinstance(messages, list):
                messages = []

            cleaned = []
            for item in messages:
                if not isinstance(item, dict):
                    continue
                cleaned.append(
                    {
                        "message": str(item.get("message") or "Mensagem não legível"),
                        "data": str(item.get("data") or "Horário não disponível"),
                        "origem": (
                            "enviada"
                            if str(item.get("origem") or "").strip().lower() == "enviada"
                            else "recebida"
                        ),
                    }
                )

            print(f"Total de mensagens processadas: {len(cleaned)}")
            return {
                "success": True,
                "contact_name": contact_name_text,
                "phone": phone,
                "messages": cleaned,
                "total_messages": len(cleaned),
            }

        except Exception as e:
            print(f"Erro geral: {e}")
            try:
                webdriver.ActionChains(navegador).send_keys(Keys.ESCAPE).perform()
            except Exception:
                pass
            return {
                "success": False,
                "error": f"Erro ao abrir conversa: {e}",
                "phone": phone,
            }
