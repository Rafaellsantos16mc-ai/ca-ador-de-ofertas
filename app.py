import os
import re
import json
import time
import math
import uuid
import sqlite3
import secrets
import hashlib
import base64
import threading
from urllib.parse import urlencode, quote

import requests
from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    jsonify,
    send_file,
)

# ============================================================
# CONFIG
# ============================================================

app = Flask(__name__)

APP_NAME = "Caçador de Ofertas"

DB_FILE = os.getenv("DB_FILE", "ofertas.db")

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback",
).strip()

SITE_ID = "MLB"

REQUEST_TIMEOUT = 10

# Quantidade de buscas.
MAX_QUERIES_PER_CATEGORY = 3
MAX_PRODUCTS_PER_QUERY = 10
MAX_ITEMS_PER_PRODUCT = 8

# Limites para não sobrecarregar a API.
MAX_OFFERS_PER_CATEGORY = 40
MAX_TOTAL_PRODUCTS = 100

session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (compatible; CacadorDeOfertas/1.0; "
            "+https://www.mercadolivre.com.br)"
        ),
        "Accept": "application/json",
    }
)

# ============================================================
# CATEGORIAS
# ============================================================

CATEGORIES = {
    "celulares": {
        "name": "📱 Celulares",
        "queries": [
            "celular smartphone",
            "smartphone 5g",
            "celular android",
        ],
    },
    "perfumes": {
        "name": "🌸 Perfumes",
        "queries": [
            "perfume feminino",
            "perfume masculino",
            "perfume importado",
        ],
    },
    "academia": {
        "name": "🏋️ Academia",
        "queries": [
            "tenis academia",
            "roupa academia",
            "suplemento academia",
        ],
    },
    "ferramentas": {
        "name": "🔧 Ferramentas",
        "queries": [
            "furadeira parafusadeira",
            "kit ferramentas",
            "ferramentas eletricas",
        ],
    },
    "eletronicos": {
        "name": "🎧 Eletrônicos",
        "queries": [
            "fone bluetooth",
            "caixa de som bluetooth",
            "smartwatch",
        ],
    },
    "casa": {
        "name": "🏠 Casa",
        "queries": [
            "organizador casa",
            "utilidades domesticas",
            "produto casa",
        ],
    },
    "automotivo": {
        "name": "🚗 Automotivo",
        "queries": [
            "acessorios automotivos",
            "produto automotivo",
            "acessorio carro",
        ],
    },
    "cozinha": {
        "name": "🍳 Cozinha",
        "queries": [
            "utensilios cozinha",
            "air fryer",
            "produto cozinha",
        ],
    },
    "moda": {
        "name": "👕 Moda",
        "queries": [
            "tenis masculino",
            "tenis feminino",
            "roupa masculina",
        ],
    },
}

# ============================================================
# FILTROS ACADEMIA
# ============================================================

ACADEMIA_BLOCK = [
    "halter",
    "halteres",
    "anilha",
    "anilhas",
    "barra olimpica",
    "barra reta",
    "barra musculacao",
    "banco supino",
    "estacao de musculacao",
    "torre de musculacao",
    "rack",
    "smith",
    "kettlebell",
    "peso academia",
    "kit peso",
    "estacao fitness",
]

# ============================================================
# ESTADO
# ============================================================

STATE = {
    "access_token": None,
    "refresh_token": None,
    "expires_at": 0,
    "user": None,
}

OAUTH_PENDING = {}

SCAN_STATE = {
    "running": False,
    "finished": False,
    "progress": 0,
    "message": "",
    "offers": [],
    "coupons": [],
    "started_at": None,
    "finished_at": None,
    "error": None,
}

STATE_LOCK = threading.Lock()

# ============================================================
# DATABASE
# ============================================================


def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            category TEXT,
            price REAL,
            seller_discount REAL,
            coupon_code TEXT,
            coupon_percent REAL,
            coupon_min REAL,
            coupon_max REAL,
            coupon_discount REAL,
            estimated_price REAL,
            shipping REAL,
            estimated_total REAL,
            free_shipping INTEGER,
            url TEXT,
            seller_id TEXT,
            affiliate_url TEXT
        )
        """
    )

    conn.commit()
    conn.close()


init_db()

# ============================================================
# HELPERS
# ============================================================


def money(value):
    try:
        value = float(value or 0)
    except Exception:
        value = 0

    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def safe_float(value, default=0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def normalize_text(text):
    if not text:
        return ""

    text = str(text).lower()

    replacements = {
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
        "ç": "c",
    }

    for a, b in replacements.items():
        text = text.replace(a, b)

    text = re.sub(r"\s+", " ", text).strip()

    return text


def ml_request(method, url, **kwargs):
    """
    Request helper.
    Tenta token atual.
    Se receber 401, tenta refresh.
    """

    token = STATE.get("access_token")

    headers = kwargs.pop("headers", {}) or {}
    headers = dict(headers)

    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = session.request(
            method,
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except Exception:
        return None

    if response.status_code == 401 and STATE.get("refresh_token"):
        if refresh_access_token():
            headers["Authorization"] = f"Bearer {STATE['access_token']}"

            try:
                response = session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    **kwargs,
                )
            except Exception:
                return None

    return response


# ============================================================
# OAUTH / PKCE
# ============================================================


def pkce_pair():
    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(verifier.encode()).digest()

    challenge = (
        base64.urlsafe_b64encode(digest)
        .decode()
        .rstrip("=")
    )

    return verifier, challenge


@app.route("/mercadolivre/conectar")
def mercadolivre_conectar():

    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado.", 500

    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()

    OAUTH_PENDING[state] = {
        "verifier": verifier,
        "created": time.time(),
    }

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    return redirect(
        ML_AUTH + "?" + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return (
            f"""
            <h2>Erro no Mercado Livre</h2>
            <p>{error}</p>
            <a href="/">Voltar</a>
            """,
            400,
        )

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return (
            "Código ou state ausente.",
            400,
        )

    pending = OAUTH_PENDING.pop(state, None)

    if not pending:
        return (
            "Sessão OAuth expirada ou inválida.",
            400,
        )

    data = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": pending["verifier"],
    }

    try:
        response = session.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        return f"Erro ao solicitar token: {e}", 500

    if response.status_code != 200:
        return (
            f"""
            <h2>Erro ao conectar Mercado Livre</h2>
            <pre>{response.text}</pre>
            <a href="/">Voltar</a>
            """,
            400,
        )

    token_data = response.json()

    STATE["access_token"] = token_data.get("access_token")
    STATE["refresh_token"] = token_data.get("refresh_token")

    expires = int(token_data.get("expires_in", 21600))

    STATE["expires_at"] = time.time() + expires - 120

    get_me()

    return redirect("/")


def refresh_access_token():
    refresh = STATE.get("refresh_token")

    if not refresh:
        return False

    data = {
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": refresh,
    }

    try:
        response = session.post(
            ML_TOKEN,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception:
        return False

    if response.status_code != 200:
        return False

    data = response.json()

    STATE["access_token"] = data.get(
        "access_token",
        STATE.get("access_token"),
    )

    STATE["refresh_token"] = data.get(
        "refresh_token",
        STATE.get("refresh_token"),
    )

    STATE["expires_at"] = (
        time.time()
        + int(data.get("expires_in", 21600))
        - 120
    )

    return True


def get_me():

    if not STATE.get("access_token"):
        return None

    response = ml_request(
        "GET",
        f"{ML_API}/users/me",
    )

    if response is None:
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
    except Exception:
        return None

    STATE["user"] = data

    return data


# ============================================================
# CUPONS
# ============================================================

COUPON_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/ofertas/cupons",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
]


def clean_html(text):
    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S,
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text,
    )

    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_coupon_text(text):
    """
    Tenta identificar códigos e condições em páginas públicas
    de promoções do Mercado Livre.

    Não considera o cupom como garantidamente elegível para
    qualquer produto.
    """

    text = clean_html(text)

    coupons = {}

    # Formatos comuns:
    # Cupom ABC123
    # CUPOM ABC123
    # Código ABC123
    patterns = [
        r"(?:cupom|codigo|código)\s+([A-Z0-9_-]{4,30})",
        r"\b([A-Z0-9][A-Z0-9_-]{5,24})\b",
    ]

    candidates = []

    for pattern in patterns:
        try:
            candidates.extend(
                re.findall(
                    pattern,
                    text,
                    flags=re.I,
                )
            )
        except Exception:
            pass

    stopwords = {
        "CUPOM",
        "CODIGO",
        "CÓDIGO",
        "MERCADO",
        "LIVRE",
        "DESCONTO",
        "PROMOCAO",
        "PROMOÇÃO",
        "TERMOS",
        "CONDICOES",
        "CONDIÇÕES",
        "VALIDO",
        "VÁLIDO",
        "FRETE",
        "GRATIS",
        "GRÁTIS",
        "PRODUTOS",
        "COMPRA",
        "CUPONS",
    }

    for code in candidates:

        code = str(code).upper().strip()

        if code in stopwords:
            continue

        if len(code) < 5 or len(code) > 25:
            continue

        # Evita palavras normais gigantes capturadas como código.
        if not re.search(r"[0-9]", code):
            continue

        coupons.setdefault(
            code,
            {
                "code": code,
                "percent": 0,
                "min_purchase": 0,
                "max_discount": 0,
                "source": "Mercado Livre",
                "raw": "",
            },
        )

    # Procura blocos ao redor dos códigos.
    for code, coupon in coupons.items():

        pos = text.upper().find(code)

        if pos < 0:
            continue

        block = text[
            max(0, pos - 350):
            min(len(text), pos + 800)
        ]

        coupon["raw"] = block

        # Percentual
        percent_match = re.search(
            r"(?:até\s*)?(\d{1,2})\s*%",
            block,
            flags=re.I,
        )

        if percent_match:
            coupon["percent"] = safe_float(
                percent_match.group(1)
            )

        # Valores em reais
        values = re.findall(
            r"R\$\s*([\d\.,]+)",
            block,
            flags=re.I,
        )

        parsed_values = []

        for value in values:
            value = value.replace(".", "").replace(",", ".")

            try:
                parsed_values.append(float(value))
            except Exception:
                pass

        # Mínimo
        minimum_match = re.search(
            r"(?:compra|compras|partir de|a partir)\s*(?:de)?\s*R\$\s*([\d\.,]+)",
            block,
            flags=re.I,
        )

        if minimum_match:
            value = (
                minimum_match.group(1)
                .replace(".", "")
                .replace(",", ".")
            )

            coupon["min_purchase"] = safe_float(value)

        # Máximo
        maximum_match = re.search(
            r"(?:máximo|maximo|desconto máximo|desconto maximo)"
            r".{0,80}?R\$\s*([\d\.,]+)",
            block,
            flags=re.I,
        )

        if maximum_match:
            value = (
                maximum_match.group(1)
                .replace(".", "")
                .replace(",", ".")
            )

            coupon["max_discount"] = safe_float(value)

        # Fallback:
        # se temos percentual e vários valores mas não achamos
        # explicitamente o máximo, usamos o maior valor.
        if (
            coupon["max_discount"] <= 0
            and coupon["percent"] > 0
            and parsed_values
        ):
            coupon["max_discount"] = max(parsed_values)

    # Remove códigos obviamente falsos.
    result = []

    for coupon in coupons.values():

        if coupon["percent"] <= 0:
            continue

        if coupon["percent"] > 100:
            continue

        result.append(coupon)

    # Remove duplicados.
    unique = {}

    for coupon in result:
        unique[coupon["code"]] = coupon

    return list(unique.values())


def fetch_coupons():

    all_coupons = {}

    for url in COUPON_URLS:

        try:
            response = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 "
                        "like Mac OS X) AppleWebKit/605.1.15"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )
        except Exception:
            continue

        if response.status_code != 200:
            continue

        parsed = parse_coupon_text(
            response.text
        )

        for coupon in parsed:

            code = coupon["code"]

            if code not in all_coupons:
                all_coupons[code] = coupon
            else:
                old = all_coupons[code]

                if coupon["percent"] > old["percent"]:
                    old["percent"] = coupon["percent"]

                if coupon["min_purchase"] > 0:
                    old["min_purchase"] = coupon["min_purchase"]

                if coupon["max_discount"] > 0:
                    old["max_discount"] = coupon["max_discount"]

    return list(all_coupons.values())


# ============================================================
# CUPOM CALCULATOR
# ============================================================


def calculate_coupon(price, coupon):

    price = safe_float(price)

    minimum = safe_float(
        coupon.get("min_purchase")
    )

    percent = safe_float(
        coupon.get("percent")
    )

    maximum = safe_float(
        coupon.get("max_discount")
    )

    if price <= 0:
        return {
            "eligible_estimate": False,
            "discount": 0,
            "estimated_price": price,
        }

    if minimum > 0 and price < minimum:
        return {
            "eligible_estimate": False,
            "discount": 0,
            "estimated_price": price,
        }

    if percent <= 0:
        return {
            "eligible_estimate": False,
            "discount": 0,
            "estimated_price": price,
        }

    discount = price * (percent / 100)

    if maximum > 0:
        discount = min(
            discount,
            maximum,
        )

    estimated = max(
        0,
        price - discount,
    )

    return {
        "eligible_estimate": True,
        "discount": round(discount, 2),
        "estimated_price": round(estimated, 2),
    }


# ============================================================
# PRODUCT SEARCH
# ============================================================


def product_search(query, limit=10):

    params = {
        "site_id": SITE_ID,
        "q": query,
        "limit": limit,
        "offset": 0,
    }

    response = ml_request(
        "GET",
        f"{ML_API}/products/search",
        params=params,
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = data.get("results", [])

    if not isinstance(results, list):
        return []

    return results


# ============================================================
# PRODUCT ITEMS
# ============================================================


def product_items(product_id, limit=8):

    url = (
        f"{ML_API}/products/"
        f"{quote(str(product_id), safe='')}/items"
    )

    params = {
        "limit": limit,
        "offset": 0,
    }

    response = ml_request(
        "GET",
        url,
        params=params,
    )

    if response is None:
        return []

    if response.status_code != 200:
        return []

    try:
        data = response.json()
    except Exception:
        return []

    results = data.get("results", [])

    if not isinstance(results, list):
        return []

    return results[:limit]


# ============================================================
# CATEGORIA
# ============================================================


def allowed_for_category(title, category_key):

    title_norm = normalize_text(title)

    if category_key == "academia":

        for blocked in ACADEMIA_BLOCK:
            if normalize_text(blocked) in title_norm:
                return False

    return True


# ============================================================
# CONVERTER PRODUTO EM OFERTA
# ============================================================


def build_offer(
    product,
    item,
    category_key,
    coupon,
):

    product_id = (
        product.get("id")
        or product.get("product_id")
        or ""
    )

    title = (
        product.get("name")
        or product.get("title")
        or item.get("title")
        or "Produto"
    )

    if not allowed_for_category(
        title,
        category_key,
    ):
        return None

    price = safe_float(
        item.get("price")
    )

    if price <= 0:
        price = safe_float(
            product.get("price")
        )

    if price <= 0:
        return None

    coupon_result = calculate_coupon(
        price,
        coupon,
    )

    if not coupon_result["eligible_estimate"]:
        return None

    shipping = item.get("shipping") or {}

    shipping_cost = safe_float(
        shipping.get("cost")
    )

    free_shipping = bool(
        shipping.get("free_shipping")
        or shipping.get("free_shipping_flag")
    )

    if free_shipping:
        shipping_cost = 0

    coupon_discount = coupon_result[
        "discount"
    ]

    estimated_price = coupon_result[
        "estimated_price"
    ]

    estimated_total = (
        estimated_price
        + shipping_cost
    )

    # Desconto normal do anúncio.
    original_price = safe_float(
        item.get("original_price")
    )

    seller_discount = 0

    if (
        original_price > price
        and original_price > 0
    ):
        seller_discount = (
            (original_price - price)
            / original_price
            * 100
        )

    seller_id = str(
        item.get("seller_id")
        or ""
    )

    item_id = str(
        item.get("item_id")
        or item.get("id")
        or ""
    )

    url = (
        item.get("permalink")
        or (
            f"https://www.mercadolivre.com.br/"
            f"{item_id}"
            if item_id
            else ""
        )
    )

    # Pontuação para ordenar:
    # primeiro economia real estimada,
    # depois preço final,
    # depois frete grátis.
    score = (
        coupon_discount * 100
        - estimated_total
        + (50 if free_shipping else 0)
    )

    return {
        "product_id": product_id,
        "item_id": item_id,
        "title": title,
        "category": CATEGORIES.get(
            category_key,
            {}
        ).get("name", category_key),
        "category_key": category_key,

        "price": round(price, 2),

        # Informação do vendedor, separada do cupom.
        "seller_discount": round(
            seller_discount,
            2,
        ),

        "coupon_code": coupon["code"],
        "coupon_percent": coupon["percent"],
        "coupon_min": coupon["min_purchase"],
        "coupon_max": coupon["max_discount"],

        "coupon_discount": round(
            coupon_discount,
            2,
        ),

        "estimated_price": round(
            estimated_price,
            2,
        ),

        "shipping": round(
            shipping_cost,
            2,
        ),

        "estimated_total": round(
            estimated_total,
            2,
        ),

        "free_shipping": free_shipping,

        "seller_id": seller_id,
        "url": url,

        # Ainda não é um link de afiliado.
        # O portal/Central do Mercado Livre deve gerar
        # o link oficial de afiliado.
        "affiliate_url": "",

        "score": round(
            score,
            2,
        ),

        "estimated": True,
    }


# ============================================================
# BUSCAR OFERTAS COM CUPONS
# ============================================================


def scan_category(
    category_key,
    coupons,
    progress_callback=None,
):

    category = CATEGORIES[category_key]

    offers = []

    products_seen = set()

    queries = category["queries"][
        :MAX_QUERIES_PER_CATEGORY
    ]

    for query_index, query in enumerate(queries):

        if progress_callback:
            progress_callback(
                f"{category['name']} — buscando {query}"
            )

        products = product_search(
            query,
            MAX_PRODUCTS_PER_QUERY,
        )

        for product in products:

            if len(products_seen) >= MAX_TOTAL_PRODUCTS:
                break

            product_id = (
                product.get("id")
                or product.get("product_id")
            )

            if not product_id:
                continue

            if product_id in products_seen:
                continue

            products_seen.add(product_id)

            title = (
                product.get("name")
                or product.get("title")
                or ""
            )

            if not allowed_for_category(
                title,
                category_key,
            ):
                continue

            items = product_items(
                product_id,
                MAX_ITEMS_PER_PRODUCT,
            )

            if not items:
                continue

            for item in items:

                for coupon in coupons:

                    offer = build_offer(
                        product,
                        item,
                        category_key,
                        coupon,
                    )

                    if not offer:
                        continue

                    offers.append(offer)

            # Limite por categoria.
            if len(offers) >= MAX_OFFERS_PER_CATEGORY:
                break

        if len(offers) >= MAX_OFFERS_PER_CATEGORY:
            break

    # ========================================================
    # Remove duplicações.
    # Um mesmo item pode aparecer em mais de uma busca.
    # ========================================================

    unique = {}

    for offer in offers:

        key = (
            offer["item_id"],
            offer["coupon_code"],
        )

        if key not in unique:
            unique[key] = offer
        else:
            old = unique[key]

            if (
                offer["estimated_total"]
                < old["estimated_total"]
            ):
                unique[key] = offer

    offers = list(unique.values())

    # ========================================================
    # ORDENAÇÃO
    # ========================================================

    offers.sort(
        key=lambda x: (
            -x["coupon_discount"],
            x["estimated_total"],
            not x["free_shipping"],
            x["price"],
        )
    )

    return offers[
        :MAX_OFFERS_PER_CATEGORY
    ]


# ============================================================
# SCAN COMPLETO
# ============================================================


def run_full_scan():

    global SCAN_STATE

    with STATE_LOCK:
        SCAN_STATE = {
            "running": True,
            "finished": False,
            "progress": 0,
            "message": "Buscando cupons...",
            "offers": [],
            "coupons": [],
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
        }

    try:

        # ====================================================
        # 1 — CUPONS PRIMEIRO
        # ====================================================

        coupons = fetch_coupons()

        with STATE_LOCK:
            SCAN_STATE["coupons"] = coupons
            SCAN_STATE["progress"] = 5
            SCAN_STATE["message"] = (
                f"{len(coupons)} cupons encontrados"
            )

        if not coupons:
            raise Exception(
                "Nenhum cupom foi encontrado nas páginas públicas."
            )

        # ====================================================
        # 2 — PRODUTOS
        # ====================================================

        category_keys = list(
            CATEGORIES.keys()
        )

        all_offers = []

        total_categories = len(
            category_keys
        )

        for index, category_key in enumerate(
            category_keys
        ):

            def progress_callback(message):
                progress = 5 + int(
                    (
                        index
                        / total_categories
                    )
                    * 90
                )

                with STATE_LOCK:
                    SCAN_STATE["progress"] = progress
                    SCAN_STATE["message"] = message

            offers = scan_category(
                category_key,
                coupons,
                progress_callback,
            )

            all_offers.extend(
                offers
            )

            progress = 5 + int(
                (
                    (index + 1)
                    / total_categories
                )
                * 90
            )

            with STATE_LOCK:
                SCAN_STATE["progress"] = progress
                SCAN_STATE["message"] = (
                    f"{CATEGORIES[category_key]['name']} concluída"
                )

        # ====================================================
        # 3 — ORDENAÇÃO GLOBAL
        # ====================================================

        all_offers.sort(
            key=lambda x: (
                -x["coupon_discount"],
                x["estimated_total"],
                not x["free_shipping"],
            )
        )

        # Limita quantidade total para interface.
        all_offers = all_offers[:250]

        # ====================================================
        # 4 — SALVAR
        # ====================================================

        save_offers(all_offers)

        with STATE_LOCK:
            SCAN_STATE["offers"] = all_offers
            SCAN_STATE["progress"] = 100
            SCAN_STATE["message"] = (
                f"Caça finalizada — "
                f"{len(all_offers)} ofertas com cupom"
            )
            SCAN_STATE["running"] = False
            SCAN_STATE["finished"] = True
            SCAN_STATE["finished_at"] = time.time()

    except Exception as e:

        with STATE_LOCK:
            SCAN_STATE["running"] = False
            SCAN_STATE["finished"] = True
            SCAN_STATE["error"] = str(e)
            SCAN_STATE["message"] = (
                f"Erro: {e}"
            )
            SCAN_STATE["finished_at"] = time.time()


# ============================================================
# SALVAR OFERTAS
# ============================================================


def save_offers(offers):

    conn = db()

    for offer in offers:

        try:
            conn.execute(
                """
                INSERT INTO ofertas (
                    product_id,
                    item_id,
                    title,
                    category,
                    price,
                    seller_discount,
                    coupon_code,
                    coupon_percent,
                    coupon_min,
                    coupon_max,
                    coupon_discount,
                    estimated_price,
                    shipping,
                    estimated_total,
                    free_shipping,
                    url,
                    seller_id,
                    affiliate_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    offer["product_id"],
                    offer["item_id"],
                    offer["title"],
                    offer["category"],
                    offer["price"],
                    offer["seller_discount"],
                    offer["coupon_code"],
                    offer["coupon_percent"],
                    offer["coupon_min"],
                    offer["coupon_max"],
                    offer["coupon_discount"],
                    offer["estimated_price"],
                    offer["shipping"],
                    offer["estimated_total"],
                    1 if offer["free_shipping"] else 0,
                    offer["url"],
                    offer["seller_id"],
                    offer["affiliate_url"],
                ),
            )
        except Exception:
            pass

    conn.commit()
    conn.close()


# ============================================================
# ROTAS
# ============================================================


@app.route("/")
def index():

    connected = bool(
        STATE.get("access_token")
    )

    user = STATE.get("user")

    return render_template_string(
        HTML,
        connected=connected,
        user=user,
        categories=CATEGORIES,
    )


@app.route("/health")
def health():

    return jsonify(
        {
            "status": "ok",
            "app": APP_NAME,
            "mercadolivre": bool(
                STATE.get("access_token")
            ),
        }
    )


@app.route("/api/status")
def api_status():

    with STATE_LOCK:
        state = dict(SCAN_STATE)

    return jsonify(
        {
            "running": state["running"],
            "finished": state["finished"],
            "progress": state["progress"],
            "message": state["message"],
            "offers": state["offers"],
            "coupons": state["coupons"],
            "error": state["error"],
        }
    )


@app.route("/api/coupons")
def api_coupons():

    coupons = fetch_coupons()

    return jsonify(
        {
            "count": len(coupons),
            "coupons": coupons,
        }
    )


@app.route("/api/cacar")
def api_cacar():

    with STATE_LOCK:
        if SCAN_STATE["running"]:
            return jsonify(
                {
                    "ok": False,
                    "message": "Uma caça já está em andamento.",
                }
            )

        SCAN_STATE["running"] = True

    thread = threading.Thread(
        target=run_full_scan,
        daemon=True,
    )

    thread.start()

    return jsonify(
        {
            "ok": True,
            "message": "Caça iniciada.",
        }
    )


@app.route("/api/search")
def api_search():

    query = request.args.get(
        "q",
        "",
    ).strip()

    if not query:
        return jsonify(
            {
                "offers": [],
                "error": "Informe uma busca.",
            }
        )

    coupons = fetch_coupons()

    products = product_search(
        query,
        MAX_PRODUCTS_PER_QUERY,
    )

    offers = []

    for product in products:

        product_id = (
            product.get("id")
            or product.get("product_id")
        )

        if not product_id:
            continue

        items = product_items(
            product_id,
            MAX_ITEMS_PER_PRODUCT,
        )

        for item in items:

            for coupon in coupons:

                offer = build_offer(
                    product,
                    item,
                    "busca",
                    coupon,
                )

                if offer:
                    offers.append(
                        offer
                    )

    # Remove duplicados.
    unique = {}

    for offer in offers:

        key = (
            offer["item_id"],
            offer["coupon_code"],
        )

        if key not in unique:
            unique[key] = offer

    offers = list(
        unique.values()
    )

    offers.sort(
        key=lambda x: (
            x["estimated_total"],
            -x["coupon_discount"],
        )
    )

    return jsonify(
        {
            "offers": offers[:100],
            "coupons": coupons,
        }
    )


@app.route("/api/salvar", methods=["POST"])
def api_salvar():

    data = request.get_json(
        silent=True
    ) or {}

    offer = data.get("offer")

    if not offer:
        return jsonify(
            {
                "ok": False,
                "error": "Oferta ausente.",
            }
        ), 400

    save_offers(
        [offer]
    )

    return jsonify(
        {
            "ok": True,
        }
    )


@app.route("/api/historico")
def api_historico():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM ofertas
        ORDER BY id DESC
        LIMIT 100
        """
    ).fetchall()

    conn.close()

    return jsonify(
        {
            "offers": [
                dict(row)
                for row in rows
            ]
        }
    )


# ============================================================
# HTML
# ============================================================

HTML = r'''
<!DOCTYPE html>
<html lang="pt-BR">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background:
        radial-gradient(
            circle at top,
            #172554 0,
            #080b14 42%,
            #05060a 100%
        );
    color: #f8fafc;
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;
}

.container {
    width: min(1180px, 94%);
    margin: auto;
    padding: 24px 0 60px;
}

.header {
    display: flex;
    justify-content: space-between;
    gap: 20px;
    align-items: center;
    flex-wrap: wrap;
}

.logo {
    font-size: 28px;
    font-weight: 900;
}

.subtitle {
    color: #94a3b8;
    margin-top: 6px;
}

.status {
    padding: 10px 14px;
    border-radius: 999px;
    background: rgba(15,23,42,.9);
    border: 1px solid #26334d;
    font-size: 14px;
}

.card {
    background:
        linear-gradient(
            145deg,
            rgba(15,23,42,.96),
            rgba(8,13,25,.96)
        );
    border: 1px solid #26334d;
    border-radius: 22px;
    padding: 20px;
    margin-top: 22px;
    box-shadow:
        0 20px 60px rgba(0,0,0,.28);
}

.hero {
    text-align: center;
    padding: 34px 20px;
}

.hero h1 {
    margin: 0;
    font-size: clamp(28px, 5vw, 48px);
}

.hero p {
    color: #94a3b8;
    max-width: 700px;
    margin: 12px auto 0;
    line-height: 1.6;
}

.btn {
    border: 0;
    border-radius: 14px;
    padding: 14px 20px;
    font-size: 16px;
    font-weight: 800;
    cursor: pointer;
    transition: .2s;
}

.btn:hover {
    transform: translateY(-1px);
}

.btn-primary {
    color: white;
    background:
        linear-gradient(
            135deg,
            #22c55e,
            #16a34a
        );
}

.btn-blue {
    color: white;
    background:
        linear-gradient(
            135deg,
            #2563eb,
            #4f46e5
        );
}

.btn-dark {
    color: white;
    background: #172033;
    border: 1px solid #334155;
}

.search {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}

.search input {
    flex: 1;
    min-width: 200px;
    padding: 14px 16px;
    border-radius: 14px;
    border: 1px solid #334155;
    background: #080d18;
    color: white;
    font-size: 16px;
    outline: none;
}

.progress {
    height: 12px;
    background: #0b1220;
    border-radius: 999px;
    overflow: hidden;
    margin-top: 14px;
    border: 1px solid #26334d;
}

.progress-bar {
    width: 0%;
    height: 100%;
    background:
        linear-gradient(
            90deg,
            #22c55e,
            #06b6d4,
            #3b82f6
        );
    transition: width .3s;
}

.coupons {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
}

.coupon {
    padding: 16px;
    border-radius: 16px;
    background: #0a1220;
    border: 1px solid #26334d;
}

.coupon-code {
    font-size: 19px;
    font-weight: 900;
    color: #facc15;
}

.coupon-info {
    color: #cbd5e1;
    font-size: 13px;
    line-height: 1.5;
    margin-top: 8px;
}

.results-header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
}

.results {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(280px, 1fr));
    gap: 16px;
    margin-top: 16px;
}

.offer {
    border-radius: 20px;
    overflow: hidden;
    background: #0b1220;
    border: 1px solid #26334d;
    padding: 18px;
    position: relative;
}

.badge {
    display: inline-block;
    background: #7c2d12;
    color: #fed7aa;
    border: 1px solid #c2410c;
    border-radius: 999px;
    padding: 6px 10px;
    font-size: 12px;
    font-weight: 900;
}

.badge-coupon {
    background: #14532d;
    color: #bbf7d0;
    border-color: #22c55e;
}

.title {
    font-size: 17px;
    font-weight: 800;
    line-height: 1.35;
    margin: 13px 0;
}

.price-old {
    color: #64748b;
    text-decoration: line-through;
    font-size: 14px;
}

.price-now {
    font-size: 27px;
    font-weight: 950;
    margin-top: 3px;
}

.coupon-box {
    margin-top: 15px;
    padding: 14px;
    border-radius: 16px;
    background:
        linear-gradient(
            135deg,
            rgba(34,197,94,.13),
            rgba(16,185,129,.05)
        );
    border: 1px solid rgba(34,197,94,.4);
}

.coupon-code-big {
    color: #86efac;
    font-weight: 950;
    font-size: 20px;
}

.estimated {
    margin-top: 8px;
    font-size: 23px;
    font-weight: 950;
}

.savings {
    color: #4ade80;
    font-weight: 800;
    margin-top: 4px;
}

.shipping {
    margin-top: 10px;
    color: #cbd5e1;
    font-size: 14px;
}

.warning {
    margin-top: 12px;
    padding: 10px;
    border-radius: 12px;
    background: #17130a;
    border: 1px solid #5b4611;
    color: #facc15;
    font-size: 12px;
    line-height: 1.45;
}

.actions {
    display: flex;
    gap: 8px;
    margin-top: 15px;
}

.actions a,
.actions button {
    flex: 1;
    text-align: center;
    text-decoration: none;
}

.small {
    color: #64748b;
    font-size: 12px;
}

.empty {
    text-align: center;
    color: #94a3b8;
    padding: 40px 10px;
}

.hidden {
    display: none !important;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
}

.stat {
    background: #0a1220;
    border: 1px solid #26334d;
    padding: 17px;
    border-radius: 16px;
}

.stat-number {
    font-size: 25px;
    font-weight: 950;
}

.stat-label {
    color: #94a3b8;
    font-size: 13px;
    margin-top: 4px;
}

.notice {
    color: #cbd5e1;
    line-height: 1.6;
    font-size: 14px;
}

.notice strong {
    color: #fff;
}

@media(max-width:600px) {

    .container {
        width: 92%;
        padding-top: 15px;
    }

    .card {
        padding: 15px;
        border-radius: 18px;
    }

    .hero {
        padding: 25px 12px;
    }

    .btn {
        width: 100%;
    }

    .search .btn {
        width: 100%;
    }

}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <div>
            <div class="logo">
                🏷️ Caçador de Ofertas
            </div>

            <div class="subtitle">
                Cupom primeiro • preço final estimado
            </div>
        </div>

        <div class="status">
            {% if connected %}
                🟢 Mercado Livre conectado
                {% if user %}
                    — {{ user.get("nickname", "") }}
                {% endif %}
            {% else %}
                🔴 Mercado Livre desconectado
            {% endif %}
        </div>

    </div>


    {% if not connected %}

    <div class="card hero">

        <h1>
            Encontre ofertas usando cupons
        </h1>

        <p>
            O sistema busca os cupons primeiro,
            encontra produtos que podem atingir
            as condições e calcula o preço
            estimado depois do cupom.
        </p>

        <br>

        <a
            href="/mercadolivre/conectar"
            class="btn btn-primary"
        >
            🔗 Conectar Mercado Livre
        </a>

    </div>

    {% else %}

    <div class="card hero">

        <h1>
            🔥 Caça de cupons
        </h1>

        <p>
            A prioridade agora é o desconto do
            <strong>cupom</strong>, e não o desconto
            normal do vendedor.
        </p>

        <br>

        <button
            class="btn btn-primary"
            onclick="cacar()"
            id="btnCacar"
        >
            🚀 CAÇAR OFERTAS COM CUPOM
        </button>

        <div
            id="progressArea"
            class="hidden"
            style="text-align:left;margin-top:22px;"
        >

            <div id="progressText">
                Preparando...
            </div>

            <div class="progress">
                <div
                    class="progress-bar"
                    id="progressBar"
                ></div>
            </div>

        </div>

    </div>


    <div class="card">

        <div class="results-header">

            <h2>
                🔎 Buscar produto
            </h2>

        </div>

        <div class="search">

            <input
                id="searchInput"
                placeholder="Ex.: smartwatch, perfume, fone..."
                onkeydown="
                    if(event.key === 'Enter') buscar();
                "
            >

            <button
                class="btn btn-blue"
                onclick="buscar()"
            >
                🔎 Buscar com cupom
            </button>

        </div>

    </div>


    <div class="card">

        <div class="results-header">

            <h2>
                🏷️ Cupons encontrados
            </h2>

            <button
                class="btn btn-dark"
                onclick="carregarCupons()"
            >
                🔄 Atualizar
            </button>

        </div>

        <div
            id="coupons"
            class="coupons"
            style="margin-top:16px;"
        >

            <div class="empty">
                Carregando cupons...
            </div>

        </div>

    </div>


    <div class="card">

        <div class="results-header">

            <h2>
                🔥 Ofertas
            </h2>

            <div
                id="resultCount"
                class="small"
            >
                Nenhuma busca realizada
            </div>

        </div>

        <div
            id="stats"
            class="stats"
            style="margin-top:15px;"
        ></div>

        <div
            id="results"
            class="results"
        >

            <div class="empty">
                Clique em
                <strong>CAÇAR OFERTAS COM CUPOM</strong>
                para começar.
            </div>

        </div>

    </div>


    <div class="card">

        <div class="notice">

            <strong>⚠️ Importante</strong>

            <br><br>

            O preço exibido como
            <strong>“estimado com cupom”</strong>
            é calculado usando as condições públicas
            do cupom.

            <br><br>

            A elegibilidade definitiva depende do
            produto, estoque, conta do comprador,
            limite do cupom e confirmação no checkout.

            <br><br>

            O desconto normal do anúncio é mostrado
            separadamente e não é tratado como cupom.

        </div>

    </div>

    {% endif %}

</div>


<script>

let scanTimer = null;


function money(value) {

    value = Number(value || 0);

    return value.toLocaleString(
        "pt-BR",
        {
            style: "currency",
            currency: "BRL"
        }
    );
}


function escapeHtml(text) {

    const div = document.createElement("div");

    div.textContent = text ?? "";

    return div.innerHTML;
}


async function carregarCupons() {

    const box =
        document.getElementById("coupons");

    box.innerHTML =
        '<div class="empty">🔎 Buscando cupons...</div>';

    try {

        const response =
            await fetch("/api/coupons");

        const data =
            await response.json();

        if (!data.coupons || !data.coupons.length) {

            box.innerHTML =
                '<div class="empty">Nenhum cupom encontrado agora.</div>';

            return;
        }

        box.innerHTML =
            data.coupons
                .map(coupon => {

                    return `
                        <div class="coupon">

                            <div class="coupon-code">
                                🏷️ ${escapeHtml(coupon.code)}
                            </div>

                            <div class="coupon-info">

                                ${coupon.percent}%
                                OFF

                                ${
                                    coupon.min_purchase
                                    ? " • mínimo " +
                                      money(coupon.min_purchase)
                                    : ""
                                }

                                ${
                                    coupon.max_discount
                                    ? " • máximo " +
                                      money(coupon.max_discount)
                                    : ""
                                }

                            </div>

                        </div>
                    `;

                })
                .join("");

    } catch (error) {

        box.innerHTML =
            '<div class="empty">Erro ao carregar cupons.</div>';
    }
}


async function cacar() {

    const button =
        document.getElementById("btnCacar");

    const progressArea =
        document.getElementById("progressArea");

    button.disabled = true;

    button.innerText =
        "⏳ CAÇANDO...";

    progressArea.classList.remove(
        "hidden"
    );

    try {

        const response =
            await fetch("/api/cacar");

        const data =
            await response.json();

        if (!data.ok) {

            alert(data.message);

            button.disabled = false;

            button.innerText =
                "🚀 CAÇAR OFERTAS COM CUPOM";

            return;
        }

        iniciarMonitor();

    } catch (error) {

        alert(
            "Erro ao iniciar a caça."
        );

        button.disabled = false;

        button.innerText =
            "🚀 CAÇAR OFERTAS COM CUPOM";
    }
}


function iniciarMonitor() {

    if (scanTimer) {
        clearInterval(scanTimer);
    }

    scanTimer =
        setInterval(
            verificarStatus,
            1200
        );

    verificarStatus();
}


async function verificarStatus() {

    try {

        const response =
            await fetch("/api/status");

        const data =
            await response.json();

        const progress =
            Math.max(
                0,
                Math.min(
                    100,
                    Number(data.progress || 0)
                )
            );

        document.getElementById(
            "progressBar"
        ).style.width =
            progress + "%";

        document.getElementById(
            "progressText"
        ).innerText =
            progress +
            "% — " +
            (
                data.message ||
                "Processando..."
            );

        if (data.offers) {

            renderOffers(
                data.offers
            );
        }

        if (data.finished) {

            clearInterval(
                scanTimer
            );

            scanTimer = null;

            const button =
                document.getElementById(
                    "btnCacar"
                );

            button.disabled = false;

            button.innerText =
                "🚀 CAÇAR OFERTAS COM CUPOM";

            if (data.error) {

                document.getElementById(
                    "progressText"
                ).innerText =
                    "❌ " + data.error;

            } else {

                document.getElementById(
                    "progressText"
                ).innerText =
                    "✅ " +
                    data.message;
            }

            carregarCupons();
        }

    } catch (error) {

        console.log(error);
    }
}


async function buscar() {

    const input =
        document.getElementById(
            "searchInput"
        );

    const query =
        input.value.trim();

    if (!query) {

        alert(
            "Digite um produto."
        );

        return;
    }

    const results =
        document.getElementById(
            "results"
        );

    results.innerHTML =
        '<div class="empty">🔎 Procurando produtos e calculando cupons...</div>';

    document.getElementById(
        "resultCount"
    ).innerText =
        "Pesquisando...";

    try {

        const response =
            await fetch(
                "/api/search?q=" +
                encodeURIComponent(query)
            );

        const data =
            await response.json();

        if (data.error) {

            results.innerHTML =
                `<div class="empty">
                    ${escapeHtml(data.error)}
                </div>`;

            return;
        }

        renderOffers(
            data.offers || []
        );

    } catch (error) {

        results.innerHTML =
            '<div class="empty">Erro na busca.</div>';
    }
}


function renderOffers(offers) {

    const results =
        document.getElementById(
            "results"
        );

    const count =
        document.getElementById(
            "resultCount"
        );

    const stats =
        document.getElementById(
            "stats"
        );

    if (!offers || !offers.length) {

        results.innerHTML =
            `
            <div class="empty">
                😕 Nenhuma oferta com cupom
                encontrada com os critérios atuais.
            </div>
            `;

        count.innerText =
            "0 ofertas";

        stats.innerHTML = "";

        return;
    }

    count.innerText =
        offers.length +
        " ofertas encontradas";

    const totalSavings =
        offers.reduce(
            (sum, offer) =>
                sum +
                Number(
                    offer.coupon_discount || 0
                ),
            0
        );

    const freeShipping =
        offers.filter(
            offer =>
                offer.free_shipping
        ).length;

    const lowest =
        Math.min(
            ...offers.map(
                offer =>
                    Number(
                        offer.estimated_total || 0
                    )
            )
        );

    stats.innerHTML = `

        <div class="stat">

            <div class="stat-number">
                ${offers.length}
            </div>

            <div class="stat-label">
                ofertas com cupom
            </div>

        </div>

        <div class="stat">

            <div class="stat-number">
                ${money(totalSavings)}
            </div>

            <div class="stat-label">
                soma dos descontos estimados
            </div>

        </div>

        <div class="stat">

            <div class="stat-number">
                ${money(lowest)}
            </div>

            <div class="stat-label">
                menor total estimado
            </div>

        </div>

        <div class="stat">

            <div class="stat-number">
                ${freeShipping}
            </div>

            <div class="stat-label">
                com frete grátis
            </div>

        </div>
    `;


    results.innerHTML =
        offers.map(
            (offer, index) => {

                const sellerDiscount =
                    Number(
                        offer.seller_discount || 0
                    );

                const couponPercent =
                    Number(
                        offer.coupon_percent || 0
                    );

                const couponDiscount =
                    Number(
                        offer.coupon_discount || 0
                    );

                const estimatedPrice =
                    Number(
                        offer.estimated_price || 0
                    );

                const shipping =
                    Number(
                        offer.shipping || 0
                    );

                const estimatedTotal =
                    Number(
                        offer.estimated_total || 0
                    );

                return `

                <div class="offer">

                    <div>

                        <span class="badge badge-coupon">
                            🏷️ CUPOM
                        </span>

                        ${
                            offer.free_shipping
                            ?
                            `
                            <span
                                class="badge"
                                style="
                                    margin-left:5px;
                                    background:#064e3b;
                                    border-color:#10b981;
                                    color:#a7f3d0;
                                "
                            >
                                🚚 FRETE GRÁTIS
                            </span>
                            `
                            :
                            ""
                        }

                    </div>


                    <div class="title">

                        ${escapeHtml(
                            offer.title
                        )}

                    </div>


                    <div class="price-old">

                        Preço no anúncio:
                        ${money(
                            offer.price
                        )}

                    </div>


                    <div class="price-now">

                        ${money(
                            offer.price
                        )}

                    </div>


                    ${
                        sellerDiscount > 0
                        ?
                        `
                        <div
                            class="small"
                            style="margin-top:4px;"
                        >
                            ↳ O anúncio já possui
                            ${sellerDiscount.toFixed(1)}%
                            de desconto do vendedor.
                        </div>
                        `
                        :
                        ""
                    }


                    <div class="coupon-box">

                        <div
                            class="coupon-code-big"
                        >
                            🏷️ CUPOM:
                            ${escapeHtml(
                                offer.coupon_code
                            )}
                        </div>


                        <div
                            style="
                                margin-top:5px;
                                color:#d1fae5;
                            "
                        >
                            ${couponPercent}%
                            OFF
                        </div>


                        <div class="savings">

                            💰 Desconto estimado:
                            ${money(
                                couponDiscount
                            )}

                        </div>


                        <div class="estimated">

                            🔥 Preço estimado:
                            ${money(
                                estimatedPrice
                            )}

                        </div>


                        <div
                            style="
                                color:#94a3b8;
                                font-size:12px;
                                margin-top:4px;
                            "
                        >
                            + frete:
                            ${
                                offer.free_shipping
                                ?
                                "GRÁTIS"
                                :
                                money(shipping)
                            }

                        </div>


                        <div
                            style="
                                color:#fff;
                                font-size:14px;
                                font-weight:800;
                                margin-top:8px;
                            "
                        >
                            💳 Total estimado:
                            ${money(
                                estimatedTotal
                            )}

                        </div>

                    </div>


                    <div class="warning">

                        ⚠️ Desconto estimado.
                        Confirme a elegibilidade do cupom
                        no checkout antes de divulgar
                        como preço final garantido.

                    </div>


                    <div class="actions">

                        ${
                            offer.url
                            ?
                            `
                            <a
                                href="${escapeHtml(
                                    offer.url
                                )}"
                                target="_blank"
                                rel="noopener"
                                class="btn btn-blue"
                            >
                                🛒 Ver produto
                            </a>
                            `
                            :
                            ""
                        }


                        <button
                            class="btn btn-dark"
                            onclick='salvarOferta(${JSON.stringify(
                                offer
                            )})'
                        >
                            💾 Salvar
                        </button>

                    </div>


                    <div
                        class="small"
                        style="margin-top:10px;"
                    >

                        ${escapeHtml(
                            offer.category || ""
                        )}

                        ${
                            offer.seller_id
                            ?
                            " • vendedor " +
                            escapeHtml(
                                offer.seller_id
                            )
                            :
                            ""
                        }

                    </div>

                </div>

                `;

            }
        ).join("");
}


async function salvarOferta(offer) {

    try {

        const response =
            await fetch(
                "/api/salvar",
                {
                    method: "POST",
                    headers: {
                        "Content-Type":
                            "application/json"
                    },
                    body:
                        JSON.stringify({
                            offer: offer
                        })
                }
            );

        const data =
            await response.json();

        if (data.ok) {

            alert(
                "Oferta salva!"
            );

        } else {

            alert(
                data.error ||
                "Não foi possível salvar."
            );
        }

    } catch (error) {

        alert(
            "Erro ao salvar oferta."
        );
    }
}


{% if connected %}

carregarCupons();

{% endif %}

</script>

</body>

</html>
'''


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )