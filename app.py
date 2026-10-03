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

PORT = int(os.getenv("PORT", "8080"))

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback",
).strip()

ML_AUTH_URL = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ML_API = "https://api.mercadolibre.com"

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 20

# Quantidade de produtos processados por categoria/consulta.
MAX_PRODUCTS_PER_QUERY = 30

# Evita que a caça fique pesada demais.
MAX_TOTAL_PRODUCTS = 120

CATEGORIES = [
    "celular",
    "perfume",
    "academia",
    "ferramentas",
    "eletronicos",
    "casa",
    "automotivo",
    "cozinha",
    "moda",
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
            product_id TEXT,
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
            affiliate_link TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(product_id)
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# HELPERS
# ============================================================

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        value = str(value)
        value = value.replace("R$", "")
        value = value.replace(" ", "")
        value = value.replace(".", "")
        value = value.replace(",", ".")

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

    text = re.sub(r"[^a-z0-9]+", " ", text)

    return re.sub(r"\s+", " ", text).strip()


def unique(seq):
    seen = set()
    result = []

    for x in seq:
        if x and x not in seen:
            seen.add(x)
            result.append(x)

    return result


def ml_headers():
    if ML_TOKEN:
        return {
            "Authorization": f"Bearer {ML_TOKEN}",
            "Accept": "application/json",
            "User-Agent": "CacadorDeOfertas/1.0",
        }

    return {
        "Accept": "application/json",
        "User-Agent": "CacadorDeOfertas/1.0",
    }


# ============================================================
# PKCE
# ============================================================

def generate_pkce():
    verifier = secrets.token_urlsafe(64)

    digest = hashlib.sha256(verifier.encode()).digest()

    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")

    return verifier, challenge


# ============================================================
# MERCADO LIVRE - OAUTH
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID or not ML_REDIRECT_URI:
        return """
        <h2>OAuth não configurado</h2>
        <p>Configure ML_CLIENT_ID e ML_REDIRECT_URI no Railway.</p>
        """, 500

    state = secrets.token_urlsafe(32)

    verifier, challenge = generate_pkce()

    oauth_states[state] = {
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

    url = ML_AUTH_URL + "?" + urlencode(params)

    return redirect(url)


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    global ML_TOKEN
    global ML_REFRESH_TOKEN
    global ML_USER

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Erro no Mercado Livre</h2>
        <p>{error}</p>
        """

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return "Código OAuth ou state ausente.", 400

    data = oauth_states.pop(state, None)

    if not data:
        return "State inválido ou expirado.", 400

    payload = {
        "grant_type": "authorization_code",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI,
        "code_verifier": data["verifier"],
    }

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        token_data = response.json()

    except Exception as e:
        return f"Erro ao obter token: {e}", 500

    if response.status_code >= 400:
        return jsonify(token_data), response.status_code

    ML_TOKEN = token_data.get("access_token")
    ML_REFRESH_TOKEN = token_data.get("refresh_token")

    if not ML_TOKEN:
        return "Mercado Livre não retornou access_token.", 500

    try:
        me = requests.get(
            f"{ML_API}/users/me",
            headers=ml_headers(),
            timeout=REQUEST_TIMEOUT,
        )

        if me.ok:
            ML_USER = me.json()

    except Exception:
        ML_USER = None

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
        "grant_type": "refresh_token",
        "client_id": ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
        "refresh_token": ML_REFRESH_TOKEN,
    }

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data=payload,
            timeout=REQUEST_TIMEOUT,
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
# REQUEST ML
# ============================================================

def ml_get(path, params=None, retry=True):

    try:
        response = requests.get(
            ML_API + path,
            headers=ml_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code == 401 and retry:
            if refresh_token():
                return ml_get(path, params=params, retry=False)

        return response

    except Exception:
        return None


# ============================================================
# PRODUTOS
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


def product_search(query, limit=30):

    response = ml_get(
        "/products/search",
        params={
            "site_id": SITE_ID,
            "q": query,
            "limit": limit,
        },
    )

    if not response or not response.ok:
        return []

    try:
        data = response.json()

        return data.get("results", []) or []

    except Exception:
        return []


# ============================================================
# DOMÍNIO / CATEGORIA
# ============================================================

def discover_category(query):

    response = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        params={
            "q": query,
        },
    )

    if not response or not response.ok:
        return None

    try:
        data = response.json()

        if isinstance(data, list) and data:
            first = data[0]

            return (
                first.get("category_id")
                or first.get("id")
            )

        if isinstance(data, dict):

            result = data.get("results") or data.get("domains") or []

            if result:
                first = result[0]

                return (
                    first.get("category_id")
                    or first.get("id")
                )

    except Exception:
        pass

    return None


# ============================================================
# HIGHLIGHTS
# ============================================================

def get_highlights(category_id):

    if not category_id:
        return []

    response = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if not response or not response.ok:
        return []

    try:
        data = response.json()

        if isinstance(data, list):
            return data

        return data.get("content", []) or data.get("results", [])

    except Exception:
        return []


# ============================================================
# CUPONS
# ============================================================

COUPON_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]


def clean_html(text):
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_money(text):

    if not text:
        return None

    match = re.search(
        r"R\$\s*([\d\.\,]+)",
        text,
        flags=re.I,
    )

    if not match:
        return None

    return safe_float(match.group(1))


def parse_coupon_block(code, block, source):

    block_text = clean_html(block)

    normalized = normalize_text(block_text)

    percent = None
    fixed = None
    minimum = 0
    maximum = None

    # --------------------------------------------------------
    # PERCENTUAL
    # --------------------------------------------------------

    patterns = [
        r"(\d+(?:[\,\.]\d+)?)\s*%\s*(?:off|desconto)",
        r"(\d+(?:[\,\.]\d+)?)\s*%",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            block_text,
            flags=re.I,
        )

        if match:
            percent = safe_float(match.group(1))
            break

    # --------------------------------------------------------
    # VALOR FIXO
    # --------------------------------------------------------

    fixed_patterns = [
        r"R\$\s*([\d\.\,]+)\s*(?:off|de desconto)",
        r"cupom\s+R\$\s*([\d\.\,]+)",
    ]

    for pattern in fixed_patterns:

        match = re.search(
            pattern,
            block_text,
            flags=re.I,
        )

        if match:
            fixed = safe_float(match.group(1))
            break

    # --------------------------------------------------------
    # MÍNIMO
    # --------------------------------------------------------

    minimum_patterns = [
        r"(?:mínimo|minimo|compras?\s+a\s+partir\s+de)\s*R\$\s*([\d\.\,]+)",
        r"R\$\s*([\d\.\,]+)\s*(?:ou mais|ou superior)",
    ]

    for pattern in minimum_patterns:

        match = re.search(
            pattern,
            block_text,
            flags=re.I,
        )

        if match:
            minimum = safe_float(match.group(1))
            break

    # --------------------------------------------------------
    # MÁXIMO
    # --------------------------------------------------------

    maximum_patterns = [
        r"(?:máximo|maximo|limitado\s+a|até)\s*R\$\s*([\d\.\,]+)",
        r"R\$\s*([\d\.\,]+)\s*(?:de desconto|de economia)",
    ]

    for pattern in maximum_patterns:

        match = re.search(
            pattern,
            block_text,
            flags=re.I,
        )

        if match:

            value = safe_float(match.group(1))

            if maximum is None or value > maximum:
                maximum = value

    # --------------------------------------------------------
    # IDs DE PRODUTOS DENTRO DO BLOCO
    # --------------------------------------------------------

    product_ids = unique(
        re.findall(
            r"\bMLB\d{6,}\b",
            block,
            flags=re.I,
        )
    )

    product_ids = [x.upper() for x in product_ids]

    # --------------------------------------------------------
    # VALIDADE / PALAVRAS QUE INDICAM CUPOM
    # --------------------------------------------------------

    if percent is None and fixed is None:
        return None

    if percent is not None:
        coupon_type = "percent"
        coupon_value = percent
    else:
        coupon_type = "fixed"
        coupon_value = fixed

    return {
        "code": code.upper(),
        "type": coupon_type,
        "value": coupon_value,
        "min": minimum,
        "max": maximum,
        "product_ids": product_ids,
        "source": source,
        "raw": block_text,
        "normalized": normalized,
    }


def extract_coupons_from_page(html, source):

    coupons = []

    # --------------------------------------------------------
    # PRIMEIRO:
    # localiza blocos iniciados por "Cupom CODE"
    # --------------------------------------------------------

    matches = list(
        re.finditer(
            r"(?:Cupom|CUPOM)\s+([A-Z0-9]{4,20})",
            html,
            flags=re.I,
        )
    )

    for i, match in enumerate(matches):

        code = match.group(1)

        start = match.start()

        if i + 1 < len(matches):
            end = matches[i + 1].start()
        else:
            end = min(
                len(html),
                start + 30000,
            )

        block = html[start:end]

        parsed = parse_coupon_block(
            code,
            block,
            source,
        )

        if parsed:
            coupons.append(parsed)

    # --------------------------------------------------------
    # SEGUNDO:
    # procura códigos destacados em JSON/HTML
    # --------------------------------------------------------

    generic_codes = re.findall(
        r'"(?:coupon[_-]?code|code)"\s*:\s*"([A-Z0-9]{4,20})"',
        html,
        flags=re.I,
    )

    for code in generic_codes:

        code = code.upper()

        if any(x["code"] == code for x in coupons):
            continue

        pos = html.upper().find(code)

        if pos < 0:
            continue

        block = html[
            max(0, pos - 10000):
            min(len(html), pos + 20000)
        ]

        parsed = parse_coupon_block(
            code,
            block,
            source,
        )

        if parsed:
            coupons.append(parsed)

    # remove duplicados
    final = {}

    for coupon in coupons:

        key = (
            coupon["code"],
            tuple(sorted(coupon["product_ids"])),
        )

        final[key] = coupon

    return list(final.values())


def load_coupons():

    all_coupons = []

    for url in COUPON_URLS:

        try:

            response = requests.get(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 "
                        "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                        "AppleWebKit/605.1.15 "
                        "Mobile/15E148 Safari/604.1"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
                timeout=REQUEST_TIMEOUT,
            )

            if not response.ok:
                continue

            coupons = extract_coupons_from_page(
                response.text,
                url,
            )

            all_coupons.extend(coupons)

        except Exception:
            continue

    # --------------------------------------------------------
    # Deduplicação
    # --------------------------------------------------------

    final = {}

    for coupon in all_coupons:

        key = (
            coupon["code"],
            tuple(sorted(coupon["product_ids"])),
        )

        final[key] = coupon

    return list(final.values())


# ============================================================
# CUPOM CONFIRMADO PARA PRODUTO
# ============================================================

def coupons_for_product(product_id, product, coupons):

    product_id = str(product_id).upper()

    confirmed = []

    for coupon in coupons:

        ids = [
            str(x).upper()
            for x in coupon.get("product_ids", [])
        ]

        # ----------------------------------------------------
        # SÓ considera confirmado quando o MLB do produto
        # aparece dentro do bloco do cupom.
        # ----------------------------------------------------

        if product_id in ids:
            confirmed.append(coupon)
            continue

        # ----------------------------------------------------
        # Alguns produtos possuem children_ids.
        # ----------------------------------------------------

        children = product.get("children_ids") or []

        children = [
            str(x).upper()
            for x in children
        ]

        if any(child in ids for child in children):
            confirmed.append(coupon)

    return confirmed


# ============================================================
# CÁLCULO DO CUPOM
# ============================================================

def calculate_coupon(price, coupon):

    if price < coupon.get("min", 0):
        return None

    coupon_type = coupon.get("type")
    value = safe_float(coupon.get("value"))

    discount = 0

    if coupon_type == "percent":

        discount = price * (value / 100)

    elif coupon_type == "fixed":

        discount = value

    maximum = coupon.get("max")

    if maximum is not None and maximum > 0:
        discount = min(discount, maximum)

    discount = min(discount, price)

    final_price = price - discount

    if discount <= 0:
        return None

    return {
        "discount": round(discount, 2),
        "final_price": round(final_price, 2),
    }


def best_confirmed_coupon(price, product, product_id, coupons):

    candidates = coupons_for_product(
        product_id,
        product,
        coupons,
    )

    best = None

    for coupon in candidates:

        calculation = calculate_coupon(
            price,
            coupon,
        )

        if not calculation:
            continue

        result = dict(coupon)

        result.update(calculation)

        if best is None:
            best = result
            continue

        # maior economia real primeiro
        if result["discount"] > best["discount"]:
            best = result

        elif (
            result["discount"] == best["discount"]
            and result["final_price"] < best["final_price"]
        ):
            best = result

    return best


# ============================================================
# PRODUTO / SELLER
# ============================================================

def get_item_best_price(product_id):

    items = get_product_items(product_id)

    if not items:
        return None

    best = None

    for item in items:

        item_id = item.get("item_id")

        price = safe_float(
            item.get("price")
        )

        if price < MIN_PRODUCT_PRICE:
            continue

        shipping = item.get("shipping") or {}

        free_shipping = bool(
            shipping.get("free_shipping")
        )

        candidate = {
            "item_id": item_id,
            "seller_id": item.get("seller_id"),
            "price": price,
            "shipping_free": free_shipping,
            "link": (
                f"https://produto.mercadolivre.com.br/"
                f"{item_id}"
                if item_id
                else ""
            ),
            "item": item,
        }

        if best is None:
            best = candidate
            continue

        # Primeiro menor preço.
        # Em empate, frete grátis.
        if price < best["price"]:
            best = candidate

        elif (
            price == best["price"]
            and free_shipping
            and not best["shipping_free"]
        ):
            best = candidate

    return best


# ============================================================
# CAÇA DE UM PRODUTO
# ============================================================

def analyze_product(product_id, coupons):

    product = get_product(product_id)

    if not product:
        return None

    title = (
        product.get("name")
        or product.get("title")
        or "Produto Mercado Livre"
    )

    best_item = get_item_best_price(product_id)

    if not best_item:
        return None

    price = best_item["price"]

    if price < MIN_PRODUCT_PRICE:
        return None

    coupon = best_confirmed_coupon(
        price,
        product,
        product_id,
        coupons,
    )

    # --------------------------------------------------------
    # IMPORTANTE:
    # sem cupom confirmado NÃO entra na lista final.
    # --------------------------------------------------------

    if not coupon:
        return None

    return {
        "product_id": product_id,
        "product_title": title,
        "item_id": best_item["item_id"],
        "seller_id": best_item["seller_id"],
        "price": round(price, 2),
        "coupon_code": coupon["code"],
        "coupon_type": coupon["type"],
        "coupon_value": coupon["value"],
        "coupon_min": coupon["min"],
        "coupon_max": coupon["max"],
        "discount": coupon["discount"],
        "final_price": coupon["final_price"],
        "coupon_confirmed": 1,
        "coupon_source": coupon["source"],
        "shipping_free": 1 if best_item["shipping_free"] else 0,
        "link": best_item["link"],
    }


# ============================================================
# BUSCA DE PRODUTOS
# ============================================================

def collect_products():

    product_ids = []

    for query in CATEGORIES:

        try:

            results = product_search(
                query,
                limit=MAX_PRODUCTS_PER_QUERY,
            )

            for product in results:

                product_id = (
                    product.get("id")
                    or product.get("product_id")
                )

                if not product_id:
                    continue

                product_ids.append(product_id)

                if len(product_ids) >= MAX_TOTAL_PRODUCTS:
                    return unique(product_ids)

        except Exception:
            continue

    return unique(product_ids)


# ============================================================
# SALVAR OFERTA
# ============================================================

def save_offer(offer):

    conn = db()

    conn.execute(
        """
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
            affiliate_link,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(product_id)
        DO UPDATE SET
            product_title=excluded.product_title,
            item_id=excluded.item_id,
            seller_id=excluded.seller_id,
            price=excluded.price,
            coupon_code=excluded.coupon_code,
            coupon_type=excluded.coupon_type,
            coupon_value=excluded.coupon_value,
            coupon_min=excluded.coupon_min,
            coupon_max=excluded.coupon_max,
            discount=excluded.discount,
            final_price=excluded.final_price,
            coupon_confirmed=excluded.coupon_confirmed,
            coupon_source=excluded.coupon_source,
            shipping_free=excluded.shipping_free,
            link=excluded.link,
            affiliate_link=excluded.affiliate_link,
            created_at=excluded.created_at
        """,
        (
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
            "",
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# LIMPAR OFERTAS
# ============================================================

def clear_offers():

    conn = db()

    conn.execute("DELETE FROM offers")

    conn.commit()
    conn.close()


# ============================================================
# CAÇA PRINCIPAL
# ============================================================

def run_hunt(job_id):

    try:

        with LOCK:
            jobs[job_id] = {
                "status": "running",
                "progress": 0,
                "message": "Buscando cupons...",
                "found": 0,
            }

        coupons = load_coupons()

        with LOCK:
            jobs[job_id]["coupon_count"] = len(coupons)
            jobs[job_id]["message"] = (
                f"{len(coupons)} cupons encontrados. "
                "Buscando produtos..."
            )

        product_ids = collect_products()

        total = len(product_ids)

        with LOCK:
            jobs[job_id]["total"] = total
            jobs[job_id]["message"] = (
                f"{total} produtos encontrados."
            )

        clear_offers()

        found = 0

        for index, product_id in enumerate(product_ids, start=1):

            try:

                offer = analyze_product(
                    product_id,
                    coupons,
                )

                if offer:

                    save_offer(offer)

                    found += 1

            except Exception as e:

                print(
                    "[ERRO PRODUTO]",
                    product_id,
                    str(e),
                )

            progress = int(
                (index / max(total, 1)) * 100
            )

            with LOCK:
                jobs[job_id]["progress"] = progress
                jobs[job_id]["found"] = found
                jobs[job_id]["message"] = (
                    f"Analisando {index}/{total} "
                    f"• {found} oportunidades"
                )

        with LOCK:

            jobs[job_id]["status"] = "done"
            jobs[job_id]["progress"] = 100
            jobs[job_id]["found"] = found
            jobs[job_id]["message"] = (
                f"Caça finalizada: {found} "
                "produtos com cupom confirmado."
            )

    except Exception as e:

        print("[ERRO CAÇA]", str(e))

        with LOCK:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["message"] = str(e)


# ============================================================
# ROUTES
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre": bool(ML_TOKEN),
    })


@app.route("/api/status")
def api_status():

    return jsonify({
        "connected": bool(ML_TOKEN),
        "user": ML_USER,
    })


@app.route("/api/hunt", methods=["POST"])
def api_hunt():

    job_id = uuid.uuid4().hex

    with LOCK:

        jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Na fila...",
            "found": 0,
        }

    thread = threading.Thread(
        target=run_hunt,
        args=(job_id,),
        daemon=True,
    )

    thread.start()

    return jsonify({
        "ok": True,
        "job_id": job_id,
    })


@app.route("/api/job/<job_id>")
def api_job(job_id):

    with LOCK:
        job = jobs.get(job_id)

    if not job:
        return jsonify({
            "error": "job não encontrado"
        }), 404

    return jsonify(job)


@app.route("/api/offers")
def api_offers():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM offers
        WHERE coupon_confirmed = 1
        ORDER BY
            discount DESC,
            final_price ASC,
            shipping_free DESC
        """
    ).fetchall()

    conn.close()

    offers = []

    for row in rows:

        item = dict(row)

        if item["coupon_type"] == "percent":

            coupon_label = (
                f'{item["coupon_value"]:.0f}% OFF'
            )

        else:

            coupon_label = (
                f'R$ {item["coupon_value"]:.2f} OFF'
            )

        item["coupon_label"] = coupon_label

        offers.append(item)

    return jsonify(offers)


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route("/api/anuncio/<int:offer_id>", methods=["POST"])
def api_anuncio(offer_id):

    conn = db()

    row = conn.execute(
        "SELECT * FROM offers WHERE id = ?",
        (offer_id,),
    ).fetchone()

    conn.close()

    if not row:
        return jsonify({
            "error": "oferta não encontrada"
        }), 404

    offer = dict(row)

    title = offer["product_title"]

    if offer["coupon_type"] == "percent":
        coupon_text = (
            f'{offer["coupon_value"]:.0f}% OFF'
        )
    else:
        coupon_text = (
            f'R$ {offer["coupon_value"]:.2f} OFF'
        )

    text = (
        f"🔥 OFERTA ENCONTRADA!\n\n"
        f"{title}\n\n"
        f"💰 De R$ {offer['price']:.2f}\n"
        f"🎟️ Cupom: {offer['coupon_code']}\n"
        f"🏷️ {coupon_text}\n"
        f"💸 Por aproximadamente R$ {offer['final_price']:.2f}\n\n"
        f"⚠️ Cupom sujeito às regras do Mercado Livre "
        f"e confirmação no checkout.\n\n"
        f"🛒 Comprar:\n"
        f"{offer['link']}"
    )

    return jsonify({
        "ok": True,
        "text": text,
    })


@app.route("/api/affiliate/<int:offer_id>", methods=["POST"])
def api_affiliate(offer_id):

    data = request.get_json(silent=True) or {}

    affiliate_link = (
        data.get("affiliate_link")
        or ""
    ).strip()

    conn = db()

    conn.execute(
        """
        UPDATE offers
        SET affiliate_link = ?
        WHERE id = ?
        """,
        (
            affiliate_link,
            offer_id,
        ),
    )

    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "affiliate_link": affiliate_link,
    })


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
    background: #0b0f14;
    color: #fff;
    font-family: Arial, Helvetica, sans-serif;
}

.container {
    width: min(1150px, 94%);
    margin: 0 auto;
    padding: 25px 0 50px;
}

h1 {
    margin: 0;
    font-size: 28px;
}

.subtitle {
    color: #9ba5b1;
    margin-top: 7px;
}

.top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 15px;
    margin-bottom: 22px;
    flex-wrap: wrap;
}

.btn {
    border: 0;
    border-radius: 10px;
    padding: 13px 18px;
    cursor: pointer;
    font-weight: bold;
    font-size: 15px;
}

.btn-primary {
    background: #3483fa;
    color: #fff;
}

.btn-green {
    background: #00a650;
    color: #fff;
}

.btn-dark {
    background: #1b222c;
    color: #fff;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 12px;
    margin-bottom: 20px;
}

.stat {
    background: #121821;
    border: 1px solid #202936;
    border-radius: 14px;
    padding: 17px;
}

.stat-title {
    color: #9ba5b1;
    font-size: 13px;
}

.stat-value {
    margin-top: 6px;
    font-size: 25px;
    font-weight: bold;
}

.status {
    background: #111820;
    border: 1px solid #202936;
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 20px;
    color: #b9c3ce;
}

.progress-wrap {
    display: none;
    background: #121821;
    border: 1px solid #202936;
    border-radius: 12px;
    padding: 15px;
    margin-bottom: 20px;
}

.progress-bar {
    width: 100%;
    height: 9px;
    background: #222b36;
    border-radius: 20px;
    overflow: hidden;
    margin-top: 10px;
}

.progress {
    width: 0%;
    height: 100%;
    background: #3483fa;
    transition: width .3s;
}

.grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 15px;
}

.card {
    background: #121821;
    border: 1px solid #202936;
    border-radius: 15px;
    padding: 18px;
}

.card h3 {
    margin: 0 0 15px;
    font-size: 17px;
    line-height: 1.35;
}

.price {
    font-size: 15px;
    color: #c3ccd6;
}

.final {
    font-size: 25px;
    font-weight: bold;
    margin-top: 5px;
}

.coupon {
    margin-top: 14px;
    padding: 12px;
    border-radius: 10px;
    background: #10251b;
    border: 1px solid #174f32;
}

.coupon-code {
    font-weight: bold;
    font-size: 17px;
}

.discount {
    color: #43d17a;
    margin-top: 5px;
}

.actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 15px;
}

.actions a {
    text-decoration: none;
}

.small {
    color: #8793a0;
    font-size: 12px;
    margin-top: 12px;
    line-height: 1.4;
}

.empty {
    text-align: center;
    padding: 45px 15px;
    background: #121821;
    border: 1px solid #202936;
    border-radius: 15px;
    color: #9ba5b1;
}

input {
    width: 100%;
    background: #0b0f14;
    border: 1px solid #303b48;
    color: #fff;
    border-radius: 9px;
    padding: 10px;
    margin-top: 10px;
}

@media(max-width: 800px) {

    .stats {
        grid-template-columns: repeat(2, 1fr);
    }

    .grid {
        grid-template-columns: 1fr;
    }
}

</style>

</head>

<body>

<div class="container">

    <div class="top">

        <div>

            <h1>🛒 Caçador de Ofertas</h1>

            <div class="subtitle">
                Produtos com cupom confirmado
            </div>

        </div>

        <div>

            {% if connected %}

            <button
                class="btn btn-primary"
                onclick="hunt()"
            >
                🔎 CAÇAR OFERTAS
            </button>

            {% else %}

            <a
                class="btn btn-green"
                href="/mercadolivre/login"
            >
                🔗 Conectar Mercado Livre
            </a>

            {% endif %}

        </div>

    </div>


    <div class="status" id="status">

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
        class="progress-wrap"
        id="progressWrap"
    >

        <div id="progressText">
            Preparando...
        </div>

        <div class="progress-bar">

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
                id="statOffers"
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
                id="statCoupons"
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
                id="statValue"
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
                id="statFinal"
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
            Clique em <b>CAÇAR OFERTAS</b> para começar.
        </div>

    </div>

</div>


<script>

function money(value) {

    return Number(value || 0).toLocaleString(
        'pt-BR',
        {
            style: 'currency',
            currency: 'BRL'
        }
    );
}


async function loadOffers() {

    try {

        const response = await fetch(
            '/api/offers'
        );

        const offers = await response.json();

        renderOffers(offers);

    } catch(error) {

        console.error(error);

    }
}


function renderOffers(offers) {

    const container =
        document.getElementById('offers');

    container.innerHTML = '';

    let totalValue = 0;
    let totalFinal = 0;

    for(const offer of offers) {

        totalValue += Number(
            offer.price || 0
        );

        totalFinal += Number(
            offer.final_price || 0
        );

        const card =
            document.createElement('div');

        card.className = 'card';

        let couponText = '';

        if(
            offer.coupon_type === 'percent'
        ) {

            couponText =
                Number(
                    offer.coupon_value
                ).toFixed(0) + '% OFF';

        } else {

            couponText =
                money(
                    offer.coupon_value
                ) + ' OFF';

        }

        card.innerHTML = `

            <h3>
                ${escapeHtml(
                    offer.product_title
                )}
            </h3>

            <div class="price">
                Valor: <b>
                    ${money(offer.price)}
                </b>
            </div>

            <div class="coupon">

                <div class="coupon-code">
                    🎟️ ${escapeHtml(
                        offer.coupon_code
                    )}
                </div>

                <div class="discount">
                    ${couponText}
                    • economia de
                    ${money(offer.discount)}
                </div>

            </div>

            <div class="final">
                ${money(offer.final_price)}
            </div>

            <div class="actions">

                <a
                    class="btn btn-primary"
                    href="${offer.link}"
                    target="_blank"
                >
                    🛒 Ver produto
                </a>

                <button
                    class="btn btn-dark"
                    onclick="generateAd(${offer.id})"
                >
                    ✍️ Gerar anúncio
                </button>

            </div>

            <div class="small">
                Cupom identificado para este produto.
                A aplicação final depende das regras
                e da confirmação no checkout.
            </div>

        `;

        container.appendChild(card);
    }

    document.getElementById(
        'statOffers'
    ).innerText = offers.length;

    document.getElementById(
        'statCoupons'
    ).innerText = offers.length;

    document.getElementById(
        'statValue'
    ).innerText = money(totalValue);

    document.getElementById(
        'statFinal'
    ).innerText = money(totalFinal);

    if(!offers.length) {

        container.innerHTML = `
            <div class="empty">
                Nenhum produto com cupom
                confirmado foi encontrado
                nesta rodada.
            </div>
        `;
    }
}


function escapeHtml(value) {

    return String(value || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}


async function hunt() {

    const wrap =
        document.getElementById(
            'progressWrap'
        );

    const progress =
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

    wrap.style.display = 'block';

    progress.style.width = '0%';

    text.innerText =
        'Iniciando caça...';

    status.innerText =
        '🔎 Procurando produtos e verificando cupons...';

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

        if(!data.job_id) {

            throw new Error(
                'Não foi possível iniciar a caça.'
            );
        }

        await monitorJob(
            data.job_id
        );

    } catch(error) {

        text.innerText =
            error.message;

        status.innerText =
            '❌ Erro ao iniciar a caça.';

    }
}


async function monitorJob(jobId) {

    while(true) {

        const response =
            await fetch(
                '/api/job/' + jobId
            );

        const job =
            await response.json();

        const progress =
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

        progress.style.width =
            (job.progress || 0) + '%';

        text.innerText =
            job.message || '';

        if(job.status === 'done') {

            status.innerText =
                '🟢 Caça finalizada — ' +
                (job.found || 0) +
                ' oportunidades encontradas.';

            await loadOffers();

            break;
        }

        if(job.status === 'error') {

            status.innerText =
                '❌ ' +
                (job.message || 'Erro');

            break;
        }

        await new Promise(
            resolve =>
                setTimeout(
                    resolve,
                    1000
                )
        );
    }
}


async function generateAd(id) {

    try {

        const response =
            await fetch(
                '/api/anuncio/' + id,
                {
                    method: 'POST'
                }
            );

        const data =
            await response.json();

        if(!data.text) {
            alert(
                'Não foi possível gerar o anúncio.'
            );
            return;
        }

        const popup =
            window.open(
                '',
                '_blank',
                'width=500,height=600'
            );

        popup.document.write(`
            <html>
            <head>
                <title>Anúncio</title>
                <style>
                    body {
                        background:#0b0f14;
                        color:white;
                        font-family:Arial;
                        padding:20px;
                    }
                    textarea {
                        width:100%;
                        height:400px;
                        background:#121821;
                        color:white;
                        border:1px solid #303b48;
                        border-radius:10px;
                        padding:12px;
                    }
                </style>
            </head>
            <body>
                <h2>📢 Anúncio</h2>
                <textarea>${escapeHtml(
                    data.text
                )}</textarea>
            </body>
            </html>
        `);

        popup.document.close();

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
'''


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML,
        connected=bool(ML_TOKEN),
        user=ML_USER,
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("CAÇADOR DE OFERTAS")
    print("=" * 60)
    print("PORT:", PORT)
    print("ML CLIENT:", bool(ML_CLIENT_ID))
    print("REDIRECT:", ML_REDIRECT_URI)
    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
    )