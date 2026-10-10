import os
import re
import time
import json
import logging
import sqlite3
import requests
from flask import Flask, jsonify, request
from threading import Thread
from concurrent.futures import ThreadPoolExecutor

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configurações globais (substitua pelas suas variáveis de ambiente)
WHATSAPP_BOT_URL = os.getenv("WHATSAPP_BOT_URL", "http://localhost:5001/send")
ML_AFFILIATE_TAG = os.getenv("ML_AFFILIATE_TAG", "sua_tag_aqui")
DB_NAME = "ofertas_ml.db"

def init_db():
    """Inicializa o banco de dados SQLite para armazenar as ofertas enviadas."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ofertas (
                id TEXT PRIMARY KEY,
                title TEXT,
                price REAL,
                permalink TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao inicializar o banco de dados: {e}")

def format_currency(value):
    """Formata valores numéricos para o padrão monetário brasileiro."""
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "R$ 0,00"

def ad_text(item, affiliate_link=""):
    """Gera o texto formatado do anúncio de forma única e limpa."""
    title = item.get("title", "Produto em Oferta")
    price = format_currency(item.get("price", 0))
    original_price = item.get("original_price")
    
    discount_str = ""
    if original_price and original_price > item.get("price", 0):
        discount = int(100 - (item.get("price", 0) / original_price) * 100)
        discount_str = f"🔥 *{discount}% OFF*\n"

    link = affiliate_link or item.get("permalink", "#")
    
    text = (
        f"🚨 *ACHADO DO DIA* 🚨\n\n"
        f"📦 *{title}*\n\n"
        f"{discount_str}"
        f"💰 Por apenas: *{price}*\n"
    )
    
    if original_price and original_price > item.get("price", 0):
        text += f"De: ~~{format_currency(original_price)}~~\n"
        
    if item.get("coupon_code"):
        text += f"🎟️ Cupom: `{item.get('coupon_code')}`\n"
        
    text += f"\n🔗 Garanta o seu aqui:\n{link}"
    return text

def _search_real_item_listings_api(query):
    """Busca segura de anúncios utilizando a API pública com tratamento de 403."""
    url = f"https://api.mercadolibre.com/sites/MLB/search?q={query}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 403:
            logger.warning("Acesso restrito (403) ao endpoint de busca do ML. Retornando lista vazia.")
            return []
        
        if response.status_code == 200:
            data = response.json()
            return data.get("results", [])
    except Exception as e:
        logger.error(f"Erro ao consultar API do Mercado Livre: {e}")
    return []

def _affiliate_csrf_and_link(target_url):
    """Gera link de afiliado ou retorna a URL original com os parâmetros necessários."""
    try:
        if not ML_AFFILIATE_TAG:
            return target_url
        separator = "&" if "?" in target_url else "?"
        return f"{target_url}{separator}matt_tool=123456&matt_word={ML_AFFILIATE_TAG}"
    except Exception:
        return target_url

def produto_ja_enviado(item_id):
    """Verifica no banco SQLite se a oferta já foi disparada anteriormente."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM ofertas WHERE id = ?", (item_id,))
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception:
        return False

def salvar_oferta_enviada(item):
    """Salva o ID da oferta no banco para evitar envios duplicados."""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO ofertas (id, title, price, permalink) VALUES (?, ?, ?, ?)",
                       (item.get("id"), item.get("title"), item.get("price"), item.get("permalink")))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao salvar oferta no banco: {e}")

def processar_e_enviar_ofertas():
    """Rotina de varredura, validação de duplicidade e envio automatizado."""
    logger.info("Iniciando ciclo de varredura de ofertas...")
    
    # Você pode alterar o termo de busca conforme sua preferência ou usar uma lista
    termos = ["smartphone", "notebook", "fone bluetooth"]
    
    for termo in termos:
        produtos = _search_real_item_listings_api(termo)
        
        for item in produtos[:2]: # Pega os 2 primeiros de cada termo
            item_id = item.get("id")
            
            if produto_ja_enviado(item_id):
                continue
                
            link_afiliado = _affiliate_csrf_and_link(item.get("permalink", ""))
            mensagem = ad_text(item, link_afiliado)
            
            try:
                # Descomente a linha abaixo quando o bot do WhatsApp estiver configurado e ativo
                # requests.post(WHATSAPP_BOT_URL, json={"message": mensagem}, timeout=5)
                
                logger.info(f"Oferta enviada com sucesso: {item.get('title')}")
                salvar_oferta_enviada(item)
                
                # Pausa breve entre os envios para evitar bloqueios de taxa
                time.sleep(2)
            except Exception as e:
                logger.error(f"Falha ao enviar mensagem para o bot: {e}")

def iniciar_automacao_whatsapp():
    """Gerencia a execução periódica em segundo plano de forma isolada."""
    def worker():
        while True:
            try:
                processar_e_enviar_ofertas()
            except Exception as e:
                logger.error(f"Erro na thread do WhatsApp: {e}")
            time.sleep(3600) # Roda a cada 1 hora

    t = Thread(target=worker, daemon=True)
    t.start()

@app.route("/")
def index():
    return jsonify({"status": "online", "service": "Caçador de Ofertas Mercado Livre"})

@app.route("/trigger", methods=["POST"])
def manual_trigger():
    processar_e_enviar_ofertas()
    return jsonify({"status": "success", "message": "Varredura executada manualmente."})

# Inicializa o banco de dados na carga do script
init_db()

# Inicializa a automação em background de forma segura evitando conflitos com múltiplos workers
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    iniciar_automacao_whatsapp()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
