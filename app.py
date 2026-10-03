import os
import re
import json
import time
import uuid
import sqlite3
import secrets
import hashlib
import base64
import threading
from urllib.parse import urlencode, quote

import requests
from flask import Flask, request, redirect, render_template_string, jsonify


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

PORT = int(os.getenv("PORT", "8080"))

DB_FILE = "ofertas.db"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 15

MAX_PRODUCTS = 45

CATEGORIES = [
    "celular",
    "perfume",
    "academia",
    "ferramentas",
    "eletronicos",
    "casa",
    "automotivo",
    "cozinha",
    "moda"
]


# ============================================================
# ESTADO
# ============================================================

ML_TOKEN = None
ML_REFRESH_TOKEN = None
ML_USER = None

oauth_states = {}

jobs = {}

LOCK = threading.Lock()


# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            product_id TEXT UNIQUE,
            product_title TEXT,

            item_id TEXT,
            seller_id TEXT,

            price REAL,

            coupon_code TEXT,
            coupon_type TEXT,
            coupon_value REAL,

            coupon_min REAL,
            coupon_max REAL,

            discount REAL,
            final_price REAL,

            coupon_confirmed INTEGER DEFAULT 0,

            coupon_source TEXT,

            shipping_free INTEGER DEFAULT 0,

            link TEXT,

            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0):

    try:

        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        text = str(value)

        text = (
            text
            .replace("R$", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        return float(text)

    except Exception:

        return default


def normalize(text):

    if not text:
        return ""

    text = str(text).lower()

    accents = {
        "á": "a",
        "à": "a",
        "ã": "a",
        "â": "a",
        "ä": "a",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "í": "i",
        "ì": "i",
        "î": "i",
        "ï": "i",
        "ó": "o",
        "ò": "o",
        "õ": "o",
        "ô": "o",
        "ö": "o",
        "ú": "u",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c"
    }

    for a, b in accents.items():
        text = text.replace(a, b)

    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def ml_headers():

    headers = {
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/2.0"
    }

    if ML_TOKEN:
        headers["Authorization"] = f"Bearer {ML_TOKEN}"

    return headers


def unique(values):

    seen = set()
    result = []

    for value in values:

        if value and value not in seen:
            seen.add(value)
            result.append(value)

    return result


# ============================================================
# PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = base64.urlsafe_b64encode(
        digest
    ).decode().rstrip("=")

    return verifier, challenge


# ============================================================
# OAUTH
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not ML_CLIENT_ID:

        return """
        <h2>ML_CLIENT_ID não configurado.</h2>
        """, 500

    state = secrets.token_urlsafe(32)

    verifier, challenge = generate_pkce()

    oauth_states[state] = {
        "verifier": verifier,
        "created": time.time()
    }

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256"
    }

    url = ML_AUTH_URL + "?" + urlencode(params)

    return redirect(url)


@app.route("/mercadolivre/callback")
def ml_callback():

    global ML_TOKEN
    global ML_REFRESH_TOKEN
    global ML_USER

    error = request.args.get("error")

    if error:
        return f"<h2>Erro Mercado Livre: {error}</h2>"

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return "OAuth inválido.", 400

    saved = oauth_states.pop(state, None)

    if not saved:
        return "State inválido ou expirado.", 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": saved["verifier"]
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

    except Exception as e:

        return f"Erro OAuth: {e}", 500

    if not response.ok:

        return jsonify(data), response.status_code

    ML_TOKEN = data.get("access_token")
    ML_REFRESH_TOKEN = data.get("refresh_token")

    if not ML_TOKEN:

        return "Token não recebido.", 500

    try:

        me = requests.get(
            f"{ML_API}/users/me",
            headers=ml_headers(),
            timeout=REQUEST_TIMEOUT
        )

        if me.ok:
            ML_USER = me.json()

    except Exception:
        pass

    return redirect("/")


# ============================================================
# REFRESH
# ============================================================

def refresh_token():

    global ML_TOKEN
    global ML_REFRESH_TOKEN

    if not ML_REFRESH_TOKEN:
        return False

    payload = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": ML_REFRESH_TOKEN
    }

    try:

        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

        if response.ok and data.get("access_token"):

            ML_TOKEN = data["access_token"]

            if data.get("refresh_token"):
                ML_REFRESH_TOKEN = data["refresh_token"]

            return True

    except Exception:
        pass

    return False


# ============================================================
# API ML
# ============================================================

def ml_get(path, params=None, retry=True):

    try:

        response = requests.get(
            ML_API + path,
            headers=ml_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 401 and retry:

            if refresh_token():

                return ml_get(
                    path,
                    params=params,
                    retry=False
                )

        return response

    except Exception:

        return None


# ============================================================
# PRODUCT SEARCH
# ============================================================

def search_products(query, limit=10):

    response = ml_get(
        "/products/search",
        {
            "site_id": SITE_ID,
            "q": query,
            "limit": limit
        }
    )

    if not response or not response.ok:
        return []

    try:

        data = response.json()

        return data.get("results", []) or []

    except Exception:

        return []


# ============================================================
# PRODUCT DETAIL
# ============================================================

def get_product(product_id):

    response = ml_get(
        f"/products/{product_id}"
    )

    if not response or not response.ok:
        return None

    try:
        return response.json()

    except Exception:
        return None


# ============================================================
# ITENS DO PRODUTO
# ============================================================

def get_product_items(product_id):

    response = ml_get(
        f"/products/{product_id}/items"
    )

    if not response or not response.ok:
        return []

    try:

        data = response.json()

        return data.get("results", []) or []

    except Exception:

        return []


# ============================================================
# MELHOR ITEM DO PRODUTO
# ============================================================

def best_item(product_id):

    items = get_product_items(product_id)

    candidates = []

    for item in items:

        price = safe_float(
            item.get("price")
        )

        if price < MIN_PRODUCT_PRICE:
            continue

        item_id = item.get("item_id")

        if not item_id:
            continue

        shipping = item.get("shipping") or {}

        free_shipping = bool(
            shipping.get("free_shipping")
        )

        candidates.append({
            "item_id": item_id,
            "seller_id": item.get("seller_id"),
            "price": price,
            "free_shipping": free_shipping
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["price"],
            not x["free_shipping"]
        )
    )

    return candidates[0]


# ============================================================
# PÁGINA PÚBLICA DO PRODUTO
# ============================================================

def get_public_product_text(product):

    urls = []

    permalink = product.get("permalink")

    if permalink:
        urls.append(permalink)

    product_id = product.get("id")

    if product_id:
        urls.append(
            f"https://www.mercadolivre.com.br/p/{product_id}"
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 "
            "like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/18.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9"
    }

    for url in unique(urls):

        try:

            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            if not response.ok:
                continue

            text = response.text

            text = re.sub(
                r"\s+",
                " ",
                text
            )

            return text

        except Exception:
            continue

    return ""


# ============================================================
# DETECTAR CUPOM NA PÁGINA DO PRODUTO
# ============================================================

def extract_product_coupon(product_text):

    if not product_text:
        return None

    # --------------------------------------------------------
    # Procuramos os formatos mais comuns.
    # --------------------------------------------------------

    patterns = [

        # 15% OFF com cupom
        (
            r"(\d{1,2}(?:[,.]\d+)?)\s*%\s*"
            r"(?:OFF|DE DESCONTO).*?"
            r"(?:CUPOM|CUPOM\s+DE)"
        ),

        # Cupom 15% OFF
        (
            r"(?:CUPOM).*?"
            r"(\d{1,2}(?:[,.]\d+)?)\s*%\s*OFF"
        ),

        # R$ 20 OFF com cupom
        (
            r"R\$\s*([\d\.,]+)\s*"
            r"(?:OFF|DE DESCONTO).*?"
            r"(?:CUPOM)"
        ),

        # Cupom R$20 OFF
        (
            r"(?:CUPOM).*?"
            r"R\$\s*([\d\.,]+)\s*OFF"
        )
    ]

    text = product_text

    for index, pattern in enumerate(patterns):

        match = re.search(
            pattern,
            text,
            flags=re.I
        )

        if not match:
            continue

        value = safe_float(
            match.group(1)
        )

        if value <= 0:
            continue

        if index in (0, 1):

            return {
                "type": "percent",
                "value": value,
                "confirmed": True,
                "source": "pagina_produto"
            }

        return {
            "type": "fixed",
            "value": value,
            "confirmed": True,
            "source": "pagina_produto"
        }

    # --------------------------------------------------------
    # Código de cupom próximo da indicação.
    # --------------------------------------------------------

    coupon_code = re.search(
        r"(?:cupom|código)\s*[:\-]?\s*"
        r"([A-Z0-9]{4,20})",
        text,
        flags=re.I
    )

    if coupon_code:

        window_start = max(
            0,
            coupon_code.start() - 500
        )

        window_end = min(
            len(text),
            coupon_code.end() + 1000
        )

        window = text[
            window_start:window_end
        ]

        percent = re.search(
            r"(\d{1,2}(?:[,.]\d+)?)\s*%",
            window
        )

        if percent:

            return {
                "type": "percent",
                "value": safe_float(
                    percent.group(1)
                ),
                "code": coupon_code.group(1).upper(),
                "confirmed": True,
                "source": "pagina_produto"
            }

    return None


# ============================================================
# CUPONS GERAIS
# ============================================================

GENERAL_COUPONS = [

    {
        "code": "1FRUIT",
        "type": "percent",
        "value": 10,
        "min": 79,
        "max": 50
    },

    {
        "code": "S5PRUNK",
        "type": "percent",
        "value": 15,
        "min": 119,
        "max": 70
    },

    {
        "code": "EC0LACOLA",
        "type": "percent",
        "value": 12,
        "min": 99,
        "max": 60
    },

    {
        "code": "CR3VI1S",
        "type": "percent",
        "value": 12,
        "min": 129,
        "max": 50
    },

    {
        "code": "N4GAS4K1",
        "type": "percent",
        "value": 10,
        "min": 159,
        "max": 50
    },

    {
        "code": "W33ENY1",
        "type": "percent",
        "value": 10,
        "min": 99,
        "max": 60
    },

    {
        "code": "P4NOR4M1C",
        "type": "percent",
        "value": 12,
        "min": 129,
        "max": 60
    }
]


# ============================================================
# CALCULAR CUPOM
# ============================================================

def calculate_coupon(price, coupon):

    minimum = safe_float(
        coupon.get("min", 0)
    )

    if price < minimum:
        return None

    if coupon["type"] == "percent":

        discount = price * (
            safe_float(coupon["value"]) / 100
        )

    else:

        discount = safe_float(
            coupon["value"]
        )

    maximum = coupon.get("max")

    if maximum is not None:

        maximum = safe_float(maximum)

        if maximum > 0:
            discount = min(
                discount,
                maximum
            )

    discount = min(
        discount,
        price
    )

    if discount <= 0:
        return None

    return {
        "discount": round(
            discount,
            2
        ),
        "final_price": round(
            price - discount,
            2
        )
    }


# ============================================================
# CUPOM LOCALIZADO
# ============================================================

def product_specific_coupon(product, price):

    html = get_public_product_text(
        product
    )

    detected = extract_product_coupon(
        html
    )

    if not detected:
        return None

    result = calculate_coupon(
        price,
        detected
    )

    if not result:
        return None

    detected.update(result)

    detected["code"] = (
        detected.get("code")
        or "CUPOM"
    )

    detected["min"] = 0
    detected["max"] = None

    return detected


# ============================================================
# ESCOLHER CUPOM GERAL
# ============================================================

def estimated_coupon(price, product):

    candidates = []

    title = normalize(
        product.get("name")
        or product.get("title")
        or ""
    )

    # --------------------------------------------------------
    # Não usa sempre o mesmo cupom.
    #
    # Distribuímos a avaliação dos cupons pelo produto para
    # evitar que uma campanha genérica domine todos os cards.
    #
    # Isso NÃO significa que o cupom esteja garantido.
    # --------------------------------------------------------

    product_id = str(
        product.get("id") or ""
    )

    seed = int(
        hashlib.md5(
            product_id.encode()
        ).hexdigest()[:8],
        16
    )

    ordered = GENERAL_COUPONS[:]

    shift = seed % len(ordered)

    ordered = (
        ordered[shift:]
        + ordered[:shift]
    )

    for coupon in ordered:

        result = calculate_coupon(
            price,
            coupon
        )

        if not result:
            continue

        item = dict(coupon)

        item.update(result)

        item["confirmed"] = False

        item["source"] = (
            "cupom_geral_estimado"
        )

        candidates.append(item)

    if not candidates:
        return None

    # Maior economia real.
    candidates.sort(
        key=lambda x: (
            x["discount"],
            -x["final_price"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# ANALISAR PRODUTO
# ============================================================

def analyze_product(product_id):

    product = get_product(
        product_id
    )

    if not product:
        return None

    item = best_item(
        product_id
    )

    if not item:
        return None

    price = item["price"]

    # --------------------------------------------------------
    # PRIMEIRO: cupom encontrado na página do produto.
    # --------------------------------------------------------

    coupon = product_specific_coupon(
        product,
        price
    )

    # --------------------------------------------------------
    # SEGUNDO: se não encontrou, usa cupom geral estimado.
    # --------------------------------------------------------

    if not coupon:

        coupon = estimated_coupon(
            price,
            product
        )

    if not coupon:
        return None

    title = (
        product.get("name")
        or product.get("title")
        or "Produto Mercado Livre"
    )

    coupon_type = coupon["type"]

    coupon_value = safe_float(
        coupon["value"]
    )

    code = coupon.get(
        "code",
        "CUPOM"
    )

    if coupon_type == "percent":

        coupon_label = (
            f"{coupon_value:.0f}% OFF"
        )

    else:

        coupon_label = (
            f"R$ {coupon_value:.2f} OFF"
        )

    link = (
        f"https://produto.mercadolivre.com.br/"
        f"{item['item_id']}"
    )

    return {

        "product_id": product_id,

        "product_title": title,

        "item_id": item["item_id"],

        "seller_id": item["seller_id"],

        "price": round(
            price,
            2
        ),

        "coupon_code": code,

        "coupon_type": coupon_type,

        "coupon_value": coupon_value,

        "coupon_min": safe_float(
            coupon.get("min", 0)
        ),

        "coupon_max": (
            safe_float(coupon["max"])
            if coupon.get("max") is not None
            else None
        ),

        "discount": coupon["discount"],

        "final_price": coupon["final_price"],

        "coupon_confirmed": (
            1
            if coupon.get("confirmed")
            else 0
        ),

        "coupon_source": coupon.get(
            "source",
            ""
        ),

        "coupon_label": coupon_label,

        "shipping_free": (
            1
            if item["free_shipping"]
            else 0
        ),

        "link": link
    }


# ============================================================
# COLETAR PRODUTOS
# ============================================================

def collect_products():

    result = []

    seen = set()

    for query in CATEGORIES:

        if len(result) >= MAX_PRODUCTS:
            break

        products = search_products(
            query,
            limit=6
        )

        for product in products:

            product_id = (
                product.get("id")
                or product.get("product_id")
            )

            if not product_id:
                continue

            product_id = str(
                product_id
            )

            if product_id in seen:
                continue

            seen.add(
                product_id
            )

            result.append(
                product_id
            )

            if len(result) >= MAX_PRODUCTS:
                break

    return result


# ============================================================
# SALVAR
# ============================================================

def save_offer(offer):

    conn = db()

    conn.execute("""
        INSERT INTO offers (
            product_id,
            product_title,
            item_id,
            seller_id,
            price,
            coupon_code,
            coupon_type,
            coupon_value,
            coupon_min,
            coupon_max,
            discount,
            final_price,
            coupon_confirmed,
            coupon_source,
            shipping_free,
            link,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(product_id)
        DO UPDATE SET

            product_title = excluded.product_title,

            item_id = excluded.item_id,

            seller_id = excluded.seller_id,

            price = excluded.price,

            coupon_code = excluded.coupon_code,

            coupon_type = excluded.coupon_type,

            coupon_value = excluded.coupon_value,

            coupon_min = excluded.coupon_min,

            coupon_max = excluded.coupon_max,

            discount = excluded.discount,

            final_price = excluded.final_price,

            coupon_confirmed = excluded.coupon_confirmed,

            coupon_source = excluded.coupon_source,

            shipping_free = excluded.shipping_free,

            link = excluded.link,

            created_at = excluded.created_at
    """, (

        offer["product_id"],
        offer["product_title"],
        offer["item_id"],
        offer["seller_id"],
        offer["price"],
        offer["coupon_code"],
        offer["coupon_type"],
        offer["coupon_value"],
        offer["coupon_min"],
        offer["coupon_max"],
        offer["discount"],
        offer["final_price"],
        offer["coupon_confirmed"],
        offer["coupon_source"],
        offer["shipping_free"],
        offer["link"],
        time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    ))

    conn.commit()
    conn.close()


# ============================================================
# LIMPAR
# ============================================================

def clear_offers():

    conn = db()

    conn.execute(
        "DELETE FROM offers"
    )

    conn.commit()
    conn.close()


# ============================================================
# CAÇA
# ============================================================

def run_hunt(job_id):

    try:

        with LOCK:

            jobs[job_id] = {
                "status": "running",
                "progress": 0,
                "message": "Buscando produtos...",
                "found": 0
            }

        product_ids = collect_products()

        total = len(
            product_ids
        )

        clear_offers()

        found = 0

        with LOCK:

            jobs[job_id]["total"] = total

            jobs[job_id]["message"] = (
                f"{total} produtos encontrados."
            )

        for index, product_id in enumerate(
            product_ids,
            start=1
        ):

            try:

                offer = analyze_product(
                    product_id
                )

                if offer:

                    save_offer(
                        offer
                    )

                    found += 1

            except Exception as e:

                print(
                    "[ERRO PRODUTO]",
                    product_id,
                    e
                )

            progress = int(
                index /
                max(total, 1)
                * 100
            )

            with LOCK:

                jobs[job_id]["progress"] = progress

                jobs[job_id]["found"] = found

                jobs[job_id]["message"] = (
                    f"Analisando "
                    f"{index}/{total} "
                    f"• {found} oportunidades"
                )

        with LOCK:

            jobs[job_id]["status"] = "done"

            jobs[job_id]["progress"] = 100

            jobs[job_id]["found"] = found

            jobs[job_id]["message"] = (
                f"Caça finalizada — "
                f"{found} oportunidades encontradas."
            )

    except Exception as e:

        print(
            "[ERRO CAÇA]",
            e
        )

        with LOCK:

            jobs[job_id]["status"] = "error"

            jobs[job_id]["message"] = str(e)


# ============================================================
# API
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre": bool(ML_TOKEN)
    })


@app.route("/api/hunt", methods=["POST"])
def start_hunt():

    job_id = uuid.uuid4().hex

    with LOCK:

        jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Iniciando...",
            "found": 0
        }

    thread = threading.Thread(
        target=run_hunt,
        args=(job_id,),
        daemon=True
    )

    thread.start()

    return jsonify({
        "ok": True,
        "job_id": job_id
    })


@app.route("/api/job/<job_id>")
def job_status(job_id):

    with LOCK:

        job = jobs.get(
            job_id
        )

    if not job:

        return jsonify({
            "error": "job não encontrado"
        }), 404

    return jsonify(job)


@app.route("/api/offers")
def offers():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM offers
        ORDER BY
            discount DESC,
            final_price ASC
    """).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/anuncio/<int:offer_id>",
    methods=["POST"]
)
def generate_ad(offer_id):

    conn = db()

    row = conn.execute(
        "SELECT * FROM offers WHERE id = ?",
        (offer_id,)
    ).fetchone()

    conn.close()

    if not row:

        return jsonify({
            "error": "oferta não encontrada"
        }), 404

    offer = dict(row)

    if offer["coupon_type"] == "percent":

        coupon_text = (
            f'{offer["coupon_value"]:.0f}% OFF'
        )

    else:

        coupon_text = (
            f'R$ {offer["coupon_value"]:.2f} OFF'
        )

    estimated = (
        ""
        if offer["coupon_confirmed"]
        else " aproximadamente"
    )

    text = (
        f"🔥 OFERTA ENCONTRADA!\n\n"

        f"{offer['product_title']}\n\n"

        f"💰 Valor: "
        f"R$ {offer['price']:.2f}\n"

        f"🎟️ Cupom: "
        f"{offer['coupon_code']}\n"

        f"🏷️ {coupon_text}\n"

        f"💸 Por{estimated} "
        f"R$ {offer['final_price']:.2f}\n\n"

        f"⚠️ Confira a aplicação do cupom "
        f"no checkout.\n\n"

        f"🛒 Comprar:\n"
        f"{offer['link']}"
    )

    return jsonify({
        "ok": True,
        "text": text
    })


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!DOCTYPE html>

<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1.0"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background: #0b0f14;

    color: white;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {

    width: min(
        1150px,
        94%
    );

    margin: auto;

    padding:
        25px
        0
        50px;
}

.header {

    display: flex;

    justify-content:
        space-between;

    align-items: center;

    gap: 15px;

    flex-wrap: wrap;

    margin-bottom: 20px;
}

h1 {

    margin: 0;

    font-size: 29px;
}

.subtitle {

    color: #9ba5b1;

    margin-top: 8px;

    font-size: 17px;
}

button {

    border: 0;

    cursor: pointer;

    border-radius: 12px;

    font-weight: bold;

    color: white;
}

.hunt {

    background: #3483fa;

    padding:
        15px
        22px;

    font-size: 16px;
}

.status {

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 14px;

    padding: 16px;

    margin-bottom: 18px;

    color: #c7d0da;
}

.progress-box {

    display: none;

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 14px;

    padding: 17px;

    margin-bottom: 18px;
}

.progress-bg {

    height: 9px;

    background: #252e39;

    border-radius: 20px;

    margin-top: 12px;

    overflow: hidden;
}

.progress {

    width: 0%;

    height: 100%;

    background: #3483fa;

    transition:
        width .25s;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(4, 1fr);

    gap: 12px;

    margin-bottom: 18px;
}

.stat {

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 14px;

    padding: 18px;
}

.stat-title {

    color: #9ba5b1;

    font-size: 14px;
}

.stat-value {

    font-size: 27px;

    font-weight: bold;

    margin-top: 7px;
}

.grid {

    display: grid;

    grid-template-columns:
        repeat(2, 1fr);

    gap: 15px;
}

.card {

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 15px;

    padding: 18px;
}

.card h3 {

    margin: 0 0 15px;

    line-height: 1.4;

    font-size: 17px;
}

.value {

    color: #bfc8d2;
}

.final {

    font-size: 26px;

    font-weight: bold;

    margin-top: 6px;
}

.coupon {

    margin-top: 15px;

    padding: 13px;

    border-radius: 11px;

    background: #10251b;

    border:
        1px solid
        #174f32;
}

.coupon.estimated {

    background: #211b0d;

    border-color: #594613;
}

.coupon-code {

    font-size: 18px;

    font-weight: bold;
}

.saving {

    color: #43d17a;

    margin-top: 6px;
}

.estimated-text {

    color: #e5c45c;

    font-size: 12px;

    margin-top: 7px;
}

.actions {

    display: flex;

    gap: 8px;

    margin-top: 15px;

    flex-wrap: wrap;
}

.actions a,
.actions button {

    text-decoration: none;

    padding:
        11px
        14px;

    border-radius: 9px;

    font-size: 14px;
}

.product-link {

    background: #3483fa;

    color: white;
}

.ad-button {

    background: #242d38;

    color: white;
}

.empty {

    grid-column:
        1 / -1;

    text-align: center;

    padding: 55px 20px;

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 15px;

    color: #9ba5b1;

    font-size: 18px;
}

@media(max-width:800px) {

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }

    .grid {

        grid-template-columns: 1fr;
    }
}

</style>

</head>

<body>

<div class="container">

<div class="header">

<div>

<h1>
🛒 Caçador de Ofertas
</h1>

<div class="subtitle">
Produtos com cupom
</div>

</div>

{% if connected %}

<button
    class="hunt"
    onclick="hunt()"
>
🔎 CAÇAR OFERTAS
</button>

{% else %}

<a
    href="/mercadolivre/login"
    style="
        background:#00a650;
        color:white;
        padding:15px 20px;
        border-radius:12px;
        text-decoration:none;
        font-weight:bold;
    "
>
🔗 Conectar Mercado Livre
</a>

{% endif %}

</div>


<div
    class="status"
    id="status"
>

{% if connected %}

🟢 Mercado Livre conectado

{% if user %}
— {{ user.nickname or user.id }}
{% endif %}

{% else %}

🔴 Mercado Livre não conectado

{% endif %}

</div>


<div
    class="progress-box"
    id="progressBox"
>

<div id="progressText">
Preparando...
</div>

<div class="progress-bg">

<div
    class="progress"
    id="progress"
></div>

</div>

</div>


<div class="stats">

<div class="stat">

<div class="stat-title">
Ofertas
</div>

<div
    class="stat-value"
    id="offersCount"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Cupom aplicável
</div>

<div
    class="stat-value"
    id="couponCount"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Valor
</div>

<div
    class="stat-value"
    id="totalValue"
>
R$ 0,00
</div>

</div>


<div class="stat">

<div class="stat-title">
Valor com o desconto
</div>

<div
    class="stat-value"
    id="totalFinal"
>
R$ 0,00
</div>

</div>

</div>


<div
    class="grid"
    id="offers"
>

<div class="empty">
Clique em <b>CAÇAR OFERTAS</b>
para começar.
</div>

</div>

</div>


<script>

function money(value) {

    return Number(
        value || 0
    ).toLocaleString(
        'pt-BR',
        {
            style: 'currency',
            currency: 'BRL'
        }
    );
}


function escapeHtml(value) {

    return String(
        value || ''
    )
    .replace(
        /&/g,
        '&amp;'
    )
    .replace(
        /</g,
        '&lt;'
    )
    .replace(
        />/g,
        '&gt;'
    )
    .replace(
        /"/g,
        '&quot;'
    )
    .replace(
        /'/g,
        '&#039;'
    );
}


async function loadOffers() {

    try {

        const response =
            await fetch(
                '/api/offers'
            );

        const offers =
            await response.json();

        renderOffers(
            offers
        );

    } catch(error) {

        console.error(error);

    }
}


function renderOffers(
    offers
) {

    const container =
        document.getElementById(
            'offers'
        );

    container.innerHTML = '';

    let totalValue = 0;

    let totalFinal = 0;

    for(
        const offer of offers
    ) {

        totalValue += Number(
            offer.price || 0
        );

        totalFinal += Number(
            offer.final_price || 0
        );

        const card =
            document.createElement(
                'div'
            );

        card.className = 'card';

        const estimated =
            !Number(
                offer.coupon_confirmed
            );

        let couponText;

        if(
            offer.coupon_type ===
            'percent'
        ) {

            couponText =
                Number(
                    offer.coupon_value
                ).toFixed(0)
                + '% OFF';

        } else {

            couponText =
                money(
                    offer.coupon_value
                )
                + ' OFF';
        }

        card.innerHTML = `

            <h3>
                ${escapeHtml(
                    offer.product_title
                )}
            </h3>

            <div class="value">
                Valor:
                <b>
                    ${money(
                        offer.price
                    )}
                </b>
            </div>

            <div
                class="coupon
                ${estimated
                    ? 'estimated'
                    : ''}"
            >

                <div
                    class="coupon-code"
                >
                    🎟️
                    ${escapeHtml(
                        offer.coupon_code
                    )}
                </div>

                <div
                    class="saving"
                >
                    ${couponText}
                    • economia de
                    ${money(
                        offer.discount
                    )}
                </div>

                ${
                    estimated
                    ?
                    `
                    <div
                        class="estimated-text"
                    >
                        ⚠️ Cupom estimado.
                        Confirme no checkout.
                    </div>
                    `
                    :
                    `
                    <div
                        class="estimated-text"
                        style="color:#43d17a"
                    >
                        ✓ Cupom identificado
                        para esta oferta.
                    </div>
                    `
                }

            </div>

            <div class="final">
                ${money(
                    offer.final_price
                )}
            </div>

            <div class="actions">

                <a
                    class="product-link"
                    href="${offer.link}"
                    target="_blank"
                >
                    🛒 Ver produto
                </a>

                <button
                    class="ad-button"
                    onclick="
                        generateAd(
                            ${offer.id}
                        )
                    "
                >
                    ✍️ Gerar anúncio
                </button>

            </div>

        `;

        container.appendChild(
            card
        );
    }

    document.getElementById(
        'offersCount'
    ).innerText =
        offers.length;

    document.getElementById(
        'couponCount'
    ).innerText =
        offers.length;

    document.getElementById(
        'totalValue'
    ).innerText =
        money(
            totalValue
        );

    document.getElementById(
        'totalFinal'
    ).innerText =
        money(
            totalFinal
        );

    if(
        offers.length === 0
    ) {

        container.innerHTML = `
            <div class="empty">
                Nenhuma oportunidade
                encontrada nesta rodada.
            </div>
        `;
    }
}


async function hunt() {

    const box =
        document.getElementById(
            'progressBox'
        );

    const bar =
        document.getElementById(
            'progress'
        );

    const text =
        document.getElementById(
            'progressText'
        );

    const status =
        document.getElementById(
            'status'
        );

    box.style.display =
        'block';

    bar.style.width =
        '0%';

    text.innerText =
        'Buscando produtos...';

    status.innerText =
        '🔎 Caçando ofertas...';

    try {

        const response =
            await fetch(
                '/api/hunt',
                {
                    method: 'POST'
                }
            );

        const data =
            await response.json();

        if(
            !data.job_id
        ) {

            throw new Error(
                'Não foi possível iniciar.'
            );
        }

        await monitor(
            data.job_id
        );

    } catch(error) {

        status.innerText =
            '❌ ' +
            error.message;
    }
}


async function monitor(
    jobId
) {

    while(true) {

        const response =
            await fetch(
                '/api/job/' +
                jobId
            );

        const job =
            await response.json();

        document.getElementById(
            'progress'
        ).style.width =
            (
                job.progress || 0
            ) + '%';

        document.getElementById(
            'progressText'
        ).innerText =
            job.message || '';

        if(
            job.status ===
            'done'
        ) {

            document.getElementById(
                'status'
            ).innerText =
                '🟢 ' +
                job.message;

            await loadOffers();

            break;
        }

        if(
            job.status ===
            'error'
        ) {

            document.getElementById(
                'status'
            ).innerText =
                '❌ ' +
                job.message;

            break;
        }

        await new Promise(
            resolve =>
                setTimeout(
                    resolve,
                    700
                )
        );
    }
}


async function generateAd(
    id
) {

    try {

        const response =
            await fetch(
                '/api/anuncio/' +
                id,
                {
                    method: 'POST'
                }
            );

        const data =
            await response.json();

        if(
            !data.text
        ) {

            alert(
                'Não foi possível gerar.'
            );

            return;
        }

        const win =
            window.open(
                '',
                '_blank'
            );

        win.document.write(`
            <html>

            <body
                style="
                    background:#0b0f14;
                    color:white;
                    font-family:Arial;
                    padding:20px;
                "
            >

            <h2>
                📢 Anúncio
            </h2>

            <textarea
                style="
                    width:100%;
                    height:400px;
                    background:#121821;
                    color:white;
                    border:1px solid #303b48;
                    border-radius:10px;
                    padding:12px;
                    font-size:15px;
                "
            >${escapeHtml(
                data.text
            )}</textarea>

            </body>
            </html>
        `);

        win.document.close();

    } catch(error) {

        alert(
            'Erro ao gerar anúncio.'
        );
    }
}


loadOffers();

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML,
        connected=bool(ML_TOKEN),
        user=ML_USER
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("=" * 60)

    print(
        "CAÇADOR DE OFERTAS v2"
    )

    print(
        "PORT:",
        PORT
    )

    print(
        "ML CLIENT:",
        bool(ML_CLIENT_ID)
    )

    print(
        "REDIRECT:",
        ML_REDIRECT_URI
    )

    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False
    )