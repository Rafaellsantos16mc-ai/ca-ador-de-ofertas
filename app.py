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
from urllib.parse import urlencode

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

# Quantidade máxima de anúncios que serão analisados.
MAX_PRODUCTS = 45

# Quantidade de anúncios por busca.
SEARCH_LIMIT = 6

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
            original_price REAL,

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

    # --------------------------------------------------------
    # Compatibilidade com banco antigo.
    # --------------------------------------------------------

    existing = []

    try:

        rows = conn.execute(
            "PRAGMA table_info(offers)"
        ).fetchall()

        existing = [
            row["name"]
            for row in rows
        ]

    except Exception:
        pass

    if "original_price" not in existing:

        try:

            conn.execute(
                "ALTER TABLE offers ADD COLUMN original_price REAL"
            )

        except Exception:
            pass

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

        text = str(value).strip()

        text = (
            text
            .replace("R$", "")
            .replace(" ", "")
        )

        # Brasil:
        # 1.299,90
        if "," in text:

            text = (
                text
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

    text = re.sub(
        r"[^a-z0-9]+",
        " ",
        text
    )

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def ml_headers():

    headers = {
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/3.0"
    }

    if ML_TOKEN:

        headers["Authorization"] = (
            f"Bearer {ML_TOKEN}"
        )

    return headers


def unique(values):

    seen = set()

    result = []

    for value in values:

        if value and value not in seen:

            seen.add(value)

            result.append(value)

    return result


def set_job(job_id, **values):

    with LOCK:

        if job_id in jobs:

            jobs[job_id].update(values)


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

    url = (
        ML_AUTH_URL
        + "?"
        + urlencode(params)
    )

    return redirect(url)


@app.route("/mercadolivre/callback")
def ml_callback():

    global ML_TOKEN
    global ML_REFRESH_TOKEN
    global ML_USER

    error = request.args.get(
        "error"
    )

    if error:

        return (
            f"<h2>Erro Mercado Livre: "
            f"{error}</h2>"
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if not code or not state:

        return (
            "OAuth inválido.",
            400
        )

    saved = oauth_states.pop(
        state,
        None
    )

    if not saved:

        return (
            "State inválido ou expirado.",
            400
        )

    payload = {

        "grant_type":
            "authorization_code",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "code":
            code,

        "redirect_uri":
            ML_REDIRECT_URI,

        "code_verifier":
            saved["verifier"]
    }

    try:

        response = requests.post(

            ML_TOKEN_URL,

            data=payload,

            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

    except Exception as e:

        return (
            f"Erro OAuth: {e}",
            500
        )

    if not response.ok:

        return jsonify(
            data
        ), response.status_code

    ML_TOKEN = data.get(
        "access_token"
    )

    ML_REFRESH_TOKEN = data.get(
        "refresh_token"
    )

    if not ML_TOKEN:

        return (
            "Token não recebido.",
            500
        )

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
# REFRESH TOKEN
# ============================================================

def refresh_token():

    global ML_TOKEN
    global ML_REFRESH_TOKEN

    if not ML_REFRESH_TOKEN:

        return False

    payload = {

        "grant_type":
            "refresh_token",

        "client_id":
            ML_CLIENT_ID,

        "client_secret":
            ML_CLIENT_SECRET,

        "refresh_token":
            ML_REFRESH_TOKEN
    }

    try:

        response = requests.post(

            ML_TOKEN_URL,

            data=payload,

            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

        if (
            response.ok
            and data.get("access_token")
        ):

            ML_TOKEN = data[
                "access_token"
            ]

            if data.get(
                "refresh_token"
            ):

                ML_REFRESH_TOKEN = data[
                    "refresh_token"
                ]

            return True

    except Exception:

        pass

    return False


# ============================================================
# API GET MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None,
    retry=True,
    headers=None
):

    try:

        request_headers = (
            headers
            or ml_headers()
        )

        response = requests.get(

            ML_API + path,

            headers=request_headers,

            params=params,

            timeout=REQUEST_TIMEOUT
        )

        if (
            response.status_code == 401
            and retry
        ):

            if refresh_token():

                return ml_get(
                    path,
                    params=params,
                    retry=False,
                    headers=None
                )

        return response

    except Exception as e:

        print(
            "[ML GET ERROR]",
            path,
            e
        )

        return None


# ============================================================
# BUSCAR ANÚNCIOS REAIS
# ============================================================

def search_items(
    query,
    limit=6
):

    response = ml_get(

        f"/sites/{SITE_ID}/search",

        {
            "q": query,
            "limit": limit,
            "status": "active"
        }
    )

    if not response:

        return []

    if not response.ok:

        print(
            "[BUSCA ML]",
            query,
            response.status_code,
            response.text[:300]
        )

        return []

    try:

        data = response.json()

        return data.get(
            "results",
            []
        ) or []

    except Exception as e:

        print(
            "[JSON BUSCA]",
            e
        )

        return []


# ============================================================
# DETALHES DE UM ITEM
# ============================================================

def get_item(item_id):

    response = ml_get(
        f"/items/{item_id}"
    )

    if not response:

        return None

    if not response.ok:

        return None

    try:

        return response.json()

    except Exception:

        return None


# ============================================================
# BUSCAR TEXTO DA PÁGINA
# ============================================================

def get_public_product_text(
    item
):

    urls = []

    permalink = item.get(
        "permalink"
    )

    if permalink:

        urls.append(
            permalink
        )

    item_id = item.get(
        "id"
    )

    if item_id:

        urls.append(
            f"https://www.mercadolivre.com.br/"
            f"p/{item_id}"
        )

    headers = {

        "User-Agent": (
            "Mozilla/5.0 (iPhone; "
            "CPU iPhone OS 18_0 "
            "like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) "
            "Version/18.0 "
            "Mobile/15E148 "
            "Safari/604.1"
        ),

        "Accept-Language":
            "pt-BR,pt;q=0.9",

        "Accept":
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8"
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

            # ------------------------------------------------
            # Mantemos o HTML porque muitas informações de
            # cupom aparecem dentro de scripts/JSON.
            # ------------------------------------------------

            return text

        except Exception:

            continue

    return ""


# ============================================================
# LIMPAR HTML
# ============================================================

def clean_page_text(html):

    if not html:

        return ""

    text = re.sub(
        r"<script[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style[^>]*>.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = (
        text
        .replace("&nbsp;", " ")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# CALCULAR DESCONTO
# ============================================================

def calculate_discount(
    price,
    coupon_type,
    value,
    minimum=0,
    maximum=None
):

    price = safe_float(
        price
    )

    value = safe_float(
        value
    )

    minimum = safe_float(
        minimum
    )

    if price <= 0:

        return None

    if value <= 0:

        return None

    if price < minimum:

        return None

    if coupon_type == "percent":

        discount = (
            price
            * value
            / 100
        )

    else:

        discount = value

    if maximum is not None:

        maximum = safe_float(
            maximum
        )

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

        "discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                price - discount,
                2
            )
    }


# ============================================================
# EXTRAIR CUPOM DA PÁGINA
# ============================================================

def extract_product_coupon(
    html
):

    if not html:

        return None

    page = clean_page_text(
        html
    )

    # --------------------------------------------------------
    # Criamos várias versões do texto.
    # --------------------------------------------------------

    text = re.sub(
        r"\s+",
        " ",
        page
    )

    lower = text.lower()

    # --------------------------------------------------------
    # 1. PERCENTUAL + CUPOM
    # --------------------------------------------------------

    patterns_percent = [

        r"(\d{1,2}(?:[,.]\d+)?)\s*%"
        r"\s*(?:off|de\s+desconto)"
        r".{0,180}"
        r"cupom",

        r"cupom"
        r".{0,180}"
        r"(\d{1,2}(?:[,.]\d+)?)\s*%"
        r"\s*(?:off|de\s+desconto)",

        r"(\d{1,2}(?:[,.]\d+)?)\s*%"
        r".{0,100}"
        r"(?:use|usar|aplique)"
        r".{0,100}"
        r"cupom"
    ]

    for pattern in patterns_percent:

        match = re.search(
            pattern,
            lower,
            flags=re.I
        )

        if not match:

            continue

        value = safe_float(
            match.group(1)
        )

        if (
            value > 0
            and value <= 100
        ):

            code = extract_coupon_code(
                text,
                match.start(),
                match.end()
            )

            return {

                "type":
                    "percent",

                "value":
                    value,

                "code":
                    code or "CUPOM",

                "confirmed":
                    True,

                "source":
                    "pagina_produto"
            }

    # --------------------------------------------------------
    # 2. VALOR FIXO + CUPOM
    # --------------------------------------------------------

    patterns_fixed = [

        r"R\$\s*([\d\.,]+)"
        r"\s*(?:off|de\s+desconto)"
        r".{0,180}"
        r"cupom",

        r"cupom"
        r".{0,180}"
        r"R\$\s*([\d\.,]+)"
        r"\s*(?:off|de\s+desconto)",

        r"(?:economize|desconto)"
        r".{0,100}"
        r"R\$\s*([\d\.,]+)"
        r".{0,100}"
        r"cupom"
    ]

    for pattern in patterns_fixed:

        match = re.search(
            pattern,
            lower,
            flags=re.I
        )

        if not match:

            continue

        value = safe_float(
            match.group(1)
        )

        if value <= 0:

            continue

        code = extract_coupon_code(
            text,
            match.start(),
            match.end()
        )

        return {

            "type":
                "fixed",

            "value":
                value,

            "code":
                code or "CUPOM",

            "confirmed":
                True,

            "source":
                "pagina_produto"
        }

    # --------------------------------------------------------
    # 3. CUPOM + CÓDIGO + DESCONTO NA MESMA REGIÃO
    # --------------------------------------------------------

    for match in re.finditer(
        r"cupom",
        lower,
        flags=re.I
    ):

        start = max(
            0,
            match.start() - 300
        )

        end = min(
            len(text),
            match.end() + 800
        )

        window = text[
            start:end
        ]

        # Percentual
        percent = re.search(
            r"(\d{1,2}(?:[,.]\d+)?)\s*%",
            window
        )

        if percent:

            value = safe_float(
                percent.group(1)
            )

            if (
                value > 0
                and value <= 100
            ):

                code = extract_coupon_code(
                    window,
                    0,
                    len(window)
                )

                return {

                    "type":
                        "percent",

                    "value":
                        value,

                    "code":
                        code or "CUPOM",

                    "confirmed":
                        True,

                    "source":
                        "pagina_produto"
                }

        # Valor fixo
        fixed = re.search(
            r"R\$\s*([\d\.,]+)"
            r"\s*(?:off|desconto)?",
            window,
            flags=re.I
        )

        if fixed:

            value = safe_float(
                fixed.group(1)
            )

            if value > 0:

                code = extract_coupon_code(
                    window,
                    0,
                    len(window)
                )

                return {

                    "type":
                        "fixed",

                    "value":
                        value,

                    "code":
                        code or "CUPOM",

                    "confirmed":
                        True,

                    "source":
                        "pagina_produto"
                }

    return None


# ============================================================
# TENTAR ENCONTRAR CÓDIGO
# ============================================================

def extract_coupon_code(
    text,
    start=0,
    end=None
):

    if not text:

        return None

    if end is None:

        end = len(text)

    left = max(
        0,
        start - 500
    )

    right = min(
        len(text),
        end + 800
    )

    window = text[
        left:right
    ]

    patterns = [

        r"(?:cupom|código)"
        r"\s*[:\-]?\s*"
        r"([A-Z0-9][A-Z0-9_-]{3,24})",

        r"(?:use|usar)"
        r"\s+(?:o\s+)?"
        r"(?:cupom|código)"
        r"\s*[:\-]?\s*"
        r"([A-Z0-9][A-Z0-9_-]{3,24})"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            window,
            flags=re.I
        )

        if not match:

            continue

        code = (
            match.group(1)
            .strip()
            .upper()
        )

        # Evita pegar palavras comuns como código.
        invalid = {
            "DESCONTO",
            "CUPOM",
            "OFF",
            "MERCADO",
            "LIVRE",
            "PROMOCAO",
            "PROMOÇÃO",
            "PARA",
            "TODOS"
        }

        if code in invalid:

            continue

        return code

    return None


# ============================================================
# DETECTAR PROMOÇÃO/CUPOM EM JSON EMBUTIDO
# ============================================================

def extract_coupon_from_json_text(
    html
):

    if not html:

        return None

    lower = html.lower()

    # --------------------------------------------------------
    # Primeiro verifica se existe alguma referência a cupom.
    # --------------------------------------------------------

    if (
        "cupom" not in lower
        and "coupon" not in lower
    ):

        return None

    # --------------------------------------------------------
    # Procuramos regiões ao redor de "cupom".
    # --------------------------------------------------------

    positions = []

    for term in (
        "cupom",
        "coupon"
    ):

        start = 0

        while True:

            pos = lower.find(
                term,
                start
            )

            if pos < 0:

                break

            positions.append(
                pos
            )

            start = pos + len(term)

            if len(positions) >= 30:

                break

    for pos in positions:

        left = max(
            0,
            pos - 1000
        )

        right = min(
            len(html),
            pos + 2500
        )

        window = html[
            left:right
        ]

        # Removemos escapes JSON comuns.
        window = (
            window
            .replace("\\u0025", "%")
            .replace("\\u0024", "$")
            .replace("\\/", "/")
            .replace('\\"', '"')
        )

        result = extract_product_coupon(
            window
        )

        if result:

            return result

    return None


# ============================================================
# ANALISAR CUPOM
# ============================================================

def detect_coupon(
    item,
    price
):

    html = get_public_product_text(
        item
    )

    if not html:

        return None

    # --------------------------------------------------------
    # Primeiro JSON/scripts.
    # --------------------------------------------------------

    detected = extract_coupon_from_json_text(
        html
    )

    if detected:

        return detected

    # --------------------------------------------------------
    # Depois texto visível.
    # --------------------------------------------------------

    detected = extract_product_coupon(
        html
    )

    if detected:

        return detected

    return None


# ============================================================
# ANALISAR ANÚNCIO
# ============================================================

def analyze_item(
    item
):

    if not item:

        return {
            "offer": None,
            "reason":
                "item vazio"
        }

    item_id = str(
        item.get("id")
        or ""
    )

    if not item_id:

        return {
            "offer": None,
            "reason":
                "item sem ID"
        }

    title = (
        item.get("title")
        or "Produto Mercado Livre"
    )

    price = safe_float(
        item.get("price")
    )

    original_price = safe_float(
        item.get("original_price")
    )

    if price <= 0:

        return {
            "offer": None,
            "reason":
                "preço inválido"
        }

    if price < MIN_PRODUCT_PRICE:

        return {
            "offer": None,
            "reason":
                "abaixo do preço mínimo"
        }

    # --------------------------------------------------------
    # Detectar cupom REAL.
    # --------------------------------------------------------

    coupon = detect_coupon(
        item,
        price
    )

    if not coupon:

        return {
            "offer": None,
            "reason":
                "nenhum cupom identificado"
        }

    calculation = calculate_discount(

        price,

        coupon["type"],

        coupon["value"],

        0,

        None
    )

    if not calculation:

        return {
            "offer": None,
            "reason":
                "cupom sem desconto válido"
        }

    seller = item.get(
        "seller"
    ) or {}

    seller_id = (
        seller.get("id")
        or item.get("seller_id")
    )

    shipping = (
        item.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    permalink = item.get(
        "permalink"
    )

    if not permalink:

        permalink = (
            f"https://produto."
            f"mercadolivre.com.br/"
            f"{item_id}"
        )

    code = (
        coupon.get("code")
        or "CUPOM"
    )

    coupon_value = safe_float(
        coupon.get("value")
    )

    if coupon["type"] == "percent":

        coupon_label = (
            f"{coupon_value:.0f}% OFF"
        )

    else:

        coupon_label = (
            f"R$ {coupon_value:.2f} OFF"
        )

    offer = {

        "product_id":
            item_id,

        "product_title":
            title,

        "item_id":
            item_id,

        "seller_id":
            seller_id,

        "price":
            round(price, 2),

        "original_price":
            (
                round(
                    original_price,
                    2
                )
                if original_price > 0
                else None
            ),

        "coupon_code":
            code,

        "coupon_type":
            coupon["type"],

        "coupon_value":
            coupon_value,

        "coupon_min":
            0,

        "coupon_max":
            None,

        "discount":
            calculation["discount"],

        "final_price":
            calculation["final_price"],

        "coupon_confirmed":
            1,

        "coupon_source":
            coupon.get(
                "source",
                "pagina_produto"
            ),

        "coupon_label":
            coupon_label,

        "shipping_free":
            1 if free_shipping else 0,

        "link":
            permalink
    }

    return {

        "offer":
            offer,

        "reason":
            "cupom identificado"
    }


# ============================================================
# COLETAR ANÚNCIOS
# ============================================================

def collect_products():

    result = []

    seen = set()

    stats = {

        "queries":
            0,

        "search_results":
            0,

        "unique_items":
            0
    }

    for query in CATEGORIES:

        if len(result) >= MAX_PRODUCTS:

            break

        stats[
            "queries"
        ] += 1

        products = search_items(
            query,
            limit=SEARCH_LIMIT
        )

        stats[
            "search_results"
        ] += len(products)

        for item in products:

            item_id = str(
                item.get("id")
                or ""
            )

            if not item_id:

                continue

            if item_id in seen:

                continue

            seen.add(
                item_id
            )

            result.append(
                item
            )

            if len(result) >= MAX_PRODUCTS:

                break

    stats[
        "unique_items"
    ] = len(result)

    return result, stats


# ============================================================
# SALVAR OFERTA
# ============================================================

def save_offer(
    offer
):

    conn = db()

    conn.execute("""
        INSERT INTO offers (
            product_id,
            product_title,
            item_id,
            seller_id,
            price,
            original_price,
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
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?
        )

        ON CONFLICT(product_id)
        DO UPDATE SET

            product_title =
                excluded.product_title,

            item_id =
                excluded.item_id,

            seller_id =
                excluded.seller_id,

            price =
                excluded.price,

            original_price =
                excluded.original_price,

            coupon_code =
                excluded.coupon_code,

            coupon_type =
                excluded.coupon_type,

            coupon_value =
                excluded.coupon_value,

            coupon_min =
                excluded.coupon_min,

            coupon_max =
                excluded.coupon_max,

            discount =
                excluded.discount,

            final_price =
                excluded.final_price,

            coupon_confirmed =
                excluded.coupon_confirmed,

            coupon_source =
                excluded.coupon_source,

            shipping_free =
                excluded.shipping_free,

            link =
                excluded.link,

            created_at =
                excluded.created_at
    """, (

        offer[
            "product_id"
        ],

        offer[
            "product_title"
        ],

        offer[
            "item_id"
        ],

        offer[
            "seller_id"
        ],

        offer[
            "price"
        ],

        offer.get(
            "original_price"
        ),

        offer[
            "coupon_code"
        ],

        offer[
            "coupon_type"
        ],

        offer[
            "coupon_value"
        ],

        offer[
            "coupon_min"
        ],

        offer[
            "coupon_max"
        ],

        offer[
            "discount"
        ],

        offer[
            "final_price"
        ],

        offer[
            "coupon_confirmed"
        ],

        offer[
            "coupon_source"
        ],

        offer[
            "shipping_free"
        ],

        offer[
            "link"
        ],

        time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    ))

    conn.commit()

    conn.close()


# ============================================================
# LIMPAR OFERTAS
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

def run_hunt(
    job_id
):

    try:

        set_job(

            job_id,

            status="running",

            progress=2,

            message=(
                "Buscando anúncios "
                "reais no Mercado Livre..."
            ),

            found=0,

            analyzed=0,

            products_found=0,

            coupon_found=0
        )

        items, search_stats = (
            collect_products()
        )

        total = len(items)

        clear_offers()

        set_job(

            job_id,

            total=total,

            products_found=total,

            message=(
                f"{total} anúncios encontrados. "
                f"Iniciando análise de cupons..."
            )
        )

        if total == 0:

            set_job(

                job_id,

                status="done",

                progress=100,

                found=0,

                analyzed=0,

                coupon_found=0,

                message=(
                    "Nenhum anúncio foi "
                    "retornado pela busca."
                ),

                diagnostic={
                    "queries":
                        search_stats[
                            "queries"
                        ],

                    "search_results":
                        search_stats[
                            "search_results"
                        ],

                    "unique_items":
                        0,

                    "reason":
                        "A busca do Mercado Livre "
                        "não retornou anúncios."
                }
            )

            return

        found = 0

        analyzed = 0

        reasons = {}

        for index, item in enumerate(
            items,
            start=1
        ):

            try:

                result = analyze_item(
                    item
                )

                analyzed += 1

                reason = result.get(
                    "reason",
                    "sem motivo"
                )

                reasons[reason] = (
                    reasons.get(
                        reason,
                        0
                    )
                    + 1
                )

                offer = result.get(
                    "offer"
                )

                if offer:

                    save_offer(
                        offer
                    )

                    found += 1

            except Exception as e:

                print(
                    "[ERRO ITEM]",
                    item.get("id"),
                    e
                )

                reason = (
                    "erro na análise"
                )

                reasons[reason] = (
                    reasons.get(
                        reason,
                        0
                    )
                    + 1
                )

            progress = int(
                index
                /
                max(total, 1)
                * 100
            )

            set_job(

                job_id,

                progress=progress,

                found=found,

                analyzed=analyzed,

                coupon_found=found,

                message=(
                    f"Analisando "
                    f"{index}/{total} "
                    f"• {found} "
                    f"oportunidades"
                )
            )

        # ----------------------------------------------------
        # Diagnóstico final.
        # ----------------------------------------------------

        diagnostic = {

            "queries":
                search_stats[
                    "queries"
                ],

            "search_results":
                search_stats[
                    "search_results"
                ],

            "unique_items":
                total,

            "analyzed":
                analyzed,

            "opportunities":
                found,

            "reasons":
                reasons
        }

        if found > 0:

            final_message = (
                f"Caça finalizada — "
                f"{found} oportunidades "
                f"encontradas."
            )

        else:

            final_message = (
                "Caça finalizada — "
                "0 oportunidades. "
                "Nenhum cupom real foi "
                "identificado nos anúncios "
                "analisados."
            )

        set_job(

            job_id,

            status="done",

            progress=100,

            found=found,

            analyzed=analyzed,

            coupon_found=found,

            message=final_message,

            diagnostic=diagnostic
        )

    except Exception as e:

        print(
            "[ERRO CAÇA]",
            repr(e)
        )

        set_job(

            job_id,

            status="error",

            message=str(e),

            error=str(e)
        )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    return jsonify({

        "status":
            "ok",

        "app":
            "Cacador de Ofertas",

        "mercadolivre":
            bool(ML_TOKEN),

        "version":
            "3.0"
    })


# ============================================================
# INICIAR CAÇA
# ============================================================

@app.route(
    "/api/hunt",
    methods=["POST"]
)
def start_hunt():

    if not ML_TOKEN:

        return jsonify({

            "ok":
                False,

            "error":
                "Mercado Livre não conectado."
        }), 401

    job_id = uuid.uuid4().hex

    with LOCK:

        jobs[job_id] = {

            "status":
                "queued",

            "progress":
                0,

            "message":
                "Iniciando...",

            "found":
                0,

            "analyzed":
                0,

            "products_found":
                0,

            "coupon_found":
                0
        }

    thread = threading.Thread(

        target=run_hunt,

        args=(job_id,),

        daemon=True
    )

    thread.start()

    return jsonify({

        "ok":
            True,

        "job_id":
            job_id
    })


# ============================================================
# STATUS DO JOB
# ============================================================

@app.route(
    "/api/job/<job_id>"
)
def job_status(
    job_id
):

    with LOCK:

        job = jobs.get(
            job_id
        )

    if not job:

        return jsonify({

            "error":
                "job não encontrado"
        }), 404

    return jsonify(
        job
    )


# ============================================================
# OFERTAS
# ============================================================

@app.route(
    "/api/offers"
)
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
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/api/diagnostic"
)
def diagnostic():

    conn = db()

    total = conn.execute(
        "SELECT COUNT(*) AS total FROM offers"
    ).fetchone()["total"]

    confirmed = conn.execute("""
        SELECT COUNT(*) AS total
        FROM offers
        WHERE coupon_confirmed = 1
    """).fetchone()["total"]

    conn.close()

    return jsonify({

        "offers":
            total,

        "confirmed_coupons":
            confirmed,

        "token":
            bool(ML_TOKEN),

        "categories":
            len(CATEGORIES),

        "max_products":
            MAX_PRODUCTS
    })


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/anuncio/<int:offer_id>",
    methods=["POST"]
)
def generate_ad(
    offer_id
):

    conn = db()

    row = conn.execute(

        "SELECT * FROM offers "
        "WHERE id = ?",

        (offer_id,)

    ).fetchone()

    conn.close()

    if not row:

        return jsonify({

            "error":
                "oferta não encontrada"
        }), 404

    offer = dict(row)

    if (
        offer["coupon_type"]
        == "percent"
    ):

        coupon_text = (
            f'{offer["coupon_value"]:.0f}% OFF'
        )

    else:

        coupon_text = (
            f'R$ '
            f'{offer["coupon_value"]:.2f}'
            f' OFF'
        )

    text = (

        "🔥 OFERTA ENCONTRADA!\n\n"

        f"{offer['product_title']}\n\n"

        f"💰 Valor: "
        f"R$ {offer['price']:.2f}\n"

        f"🎟️ Cupom: "
        f"{offer['coupon_code']}\n"

        f"🏷️ {coupon_text}\n"

        f"💸 Por apenas "
        f"R$ {offer['final_price']:.2f}\n\n"

        f"💰 Economia de "
        f"R$ {offer['discount']:.2f}\n\n"

        "⚠️ Confira a aplicação do cupom "
        "no checkout.\n\n"

        "🛒 Comprar:\n"

        f"{offer['link']}"
    )

    return jsonify({

        "ok":
            True,

        "text":
            text
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

.hunt:disabled {

    opacity: .55;

    cursor:
        not-allowed;
}

.status {

    background: #121821;

    border:
        1px solid
        #202936;

    border-radius: 14px;

    padding: 16px;

    margin-bottom: 12px;

    color: #c7d0da;

    line-height: 1.5;
}

.diagnostic {

    display: none;

    background: #151d27;

    border:
        1px solid
        #293545;

    border-radius: 14px;

    padding: 15px;

    margin-bottom: 18px;

    color: #bfc8d2;

    font-size: 14px;

    line-height: 1.6;
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

    margin:
        0
        0
        15px;

    line-height: 1.4;

    font-size: 17px;
}

.value {

    color: #bfc8d2;
}

.original {

    color: #858f9b;

    text-decoration:
        line-through;

    font-size: 14px;

    margin-top: 5px;
}

.final {

    font-size: 26px;

    font-weight: bold;

    margin-top: 14px;
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

.coupon-code {

    font-size: 18px;

    font-weight: bold;
}

.saving {

    color: #43d17a;

    margin-top: 6px;
}

.confirmed {

    color: #43d17a;

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

    padding:
        55px
        20px;

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
    id="huntButton"
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
    class="diagnostic"
    id="diagnostic"
></div>


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
Cupom identificado
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

Clique em
<b>CAÇAR OFERTAS</b>
para começar.

</div>

</div>

</div>


<script>

let hunting = false;


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

        console.error(
            error
        );

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

    for (
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

        card.className =
            'card';

        let couponText;

        if (
            offer.coupon_type
            === 'percent'
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

        let original = '';

        if (
            Number(
                offer.original_price
            ) > Number(
                offer.price
            )
        ) {

            original = `
                <div class="original">
                    Antes:
                    ${money(
                        offer.original_price
                    )}
                </div>
            `;
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

            ${original}

            <div
                class="coupon"
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

                <div
                    class="confirmed"
                >
                    ✓ Cupom identificado
                    para esta oferta
                </div>

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
                    rel="noopener"
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


    if (
        offers.length === 0
    ) {

        container.innerHTML = `

            <div class="empty">

                Nenhuma oportunidade
                encontrada nesta rodada.

                <br><br>

                <small>
                    O sistema agora só mostra
                    anúncios quando identifica
                    uma indicação de cupom real.
                </small>

            </div>

        `;
    }
}


function showDiagnostic(
    diagnostic
) {

    const box =
        document.getElementById(
            'diagnostic'
        );

    if (
        !diagnostic
    ) {

        box.style.display =
            'none';

        return;
    }

    const reasons =
        diagnostic.reasons
        || {};

    let reasonsHtml = '';

    for (
        const key in reasons
    ) {

        reasonsHtml += `
            <div>
                • ${escapeHtml(
                    key
                )}:
                <b>
                    ${reasons[key]}
                </b>
            </div>
        `;
    }

    box.innerHTML = `

        <b>🔎 Diagnóstico da caça</b>

        <div>
            Consultas:
            ${diagnostic.queries || 0}
        </div>

        <div>
            Anúncios retornados:
            ${diagnostic.search_results || 0}
        </div>

        <div>
            Anúncios únicos:
            ${diagnostic.unique_items || 0}
        </div>

        <div>
            Analisados:
            ${diagnostic.analyzed || 0}
        </div>

        <div>
            Oportunidades:
            ${diagnostic.opportunities || 0}
        </div>

        <br>

        <b>Motivos:</b>

        ${reasonsHtml}

    `;

    box.style.display =
        'block';
}


async function hunt() {

    if (hunting) {

        return;
    }

    hunting = true;

    const button =
        document.getElementById(
            'huntButton'
        );

    if (button) {

        button.disabled =
            true;

        button.innerText =
            '⏳ CAÇANDO...';
    }


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

    const diagnostic =
        document.getElementById(
            'diagnostic'
        );


    diagnostic.style.display =
        'none';


    box.style.display =
        'block';


    bar.style.width =
        '0%';


    text.innerText =
        'Buscando anúncios reais...';


    status.innerText =
        '🔎 Caçando ofertas...';


    try {

        const response =
            await fetch(
                '/api/hunt',
                {
                    method:
                        'POST'
                }
            );


        const data =
            await response.json();


        if (
            !data.job_id
        ) {

            throw new Error(
                data.error
                ||
                'Não foi possível iniciar.'
            );
        }


        await monitor(
            data.job_id
        );


    } catch(error) {

        status.innerText =
            '❌ '
            + error.message;

    } finally {

        hunting = false;

        if (button) {

            button.disabled =
                false;

            button.innerText =
                '🔎 CAÇAR OFERTAS';
        }
    }
}


async function monitor(
    jobId
) {

    while(true) {

        const response =
            await fetch(
                '/api/job/'
                + jobId
            );


        const job =
            await response.json();


        document.getElementById(
            'progress'
        ).style.width =
            (
                job.progress || 0
            )
            + '%';


        document.getElementById(
            'progressText'
        ).innerText =
            job.message || '';


        if (
            job.status
            === 'done'
        ) {

            document.getElementById(
                'status'
            ).innerText =
                '🟢 '
                + job.message;


            showDiagnostic(
                job.diagnostic
            );


            await loadOffers();


            break;
        }


        if (
            job.status
            === 'error'
        ) {

            document.getElementById(
                'status'
            ).innerText =
                '❌ '
                + job.message;


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
                '/api/anuncio/'
                + id,
                {
                    method:
                        'POST'
                }
            );


        const data =
            await response.json();


        if (
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

        connected=
            bool(ML_TOKEN),

        user=
            ML_USER
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("=" * 60)

    print(
        "CAÇADOR DE OFERTAS v3"
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
        "TOKEN:",
        bool(ML_TOKEN)
    )

    print(
        "REDIRECT:",
        ML_REDIRECT_URI
    )

    print(
        "MAX PRODUCTS:",
        MAX_PRODUCTS
    )

    print("=" * 60)

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False
    )