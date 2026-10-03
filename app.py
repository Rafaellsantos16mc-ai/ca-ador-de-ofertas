import os
import re
import json
import time
import uuid
import base64
import hashlib
import secrets
import sqlite3
import threading
from urllib.parse import urlencode, quote

import requests
from flask import Flask, request, jsonify, redirect, render_template_string

# ============================================================
# CONFIG
# ============================================================

app = Flask(__name__)

APP_NAME = "Cacador de Ofertas"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

DATABASE = "ofertas.db"

REQUEST_TIMEOUT = 10

# REGRA PRINCIPAL
MIN_PRODUCT_PRICE = 69.90

# Quantidade de produtos pesquisados
MAX_PRODUCTS_PER_QUERY = 10

# Quantidade de vendedores/itens por produto
MAX_ITEMS_PER_PRODUCT = 10

# Quantidade máxima de ofertas finais
MAX_FINAL_OFFERS = 80

# ============================================================
# CATEGORIAS
# ============================================================

CATEGORIES = {
    "📱 Celulares": [
        "celular smartphone",
        "iphone",
        "samsung galaxy",
        "xiaomi redmi",
        "motorola",
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "kit perfume",
        "perfume original",
    ],

    "🏋️ Academia": [
        "tenis academia",
        "tenis corrida",
        "camiseta academia",
        "roupa academia masculina",
        "roupa academia feminina",
        "whey protein",
        "creatina",
        "suplemento",
    ],

    "🔧 Ferramentas": [
        "parafusadeira",
        "furadeira",
        "kit ferramentas",
        "chave de impacto",
        "ferramenta eletrica",
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "smartwatch",
        "caixa de som bluetooth",
        "tablet",
        "monitor",
        "teclado mecanico",
        "mouse gamer",
    ],

    "🏠 Casa": [
        "aspirador",
        "air fryer",
        "liquidificador",
        "cafeteira",
        "organizador",
        "utensilios cozinha",
        "ventilador",
    ],

    "🚗 Automotivo": [
        "central multimidia",
        "camera de ré",
        "tapete automotivo",
        "acessorios carro",
        "lampada automotiva",
        "carregador veicular",
    ],

    "🍳 Cozinha": [
        "air fryer",
        "jogo de panelas",
        "panela",
        "liquidificador",
        "cafeteira",
        "sandwicheira",
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "camiseta masculina",
        "calca masculina",
        "vestido feminino",
        "mochila",
    ],
}

# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/140 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
})

# ============================================================
# ESTADO OAUTH
# ============================================================

OAUTH_STATE = {}
OAUTH_LOCK = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            product_id TEXT,
            title TEXT,
            category TEXT,
            seller_id TEXT,
            seller_nickname TEXT,
            product_price REAL,
            seller_discount REAL,
            coupon_code TEXT,
            coupon_percent REAL,
            coupon_discount REAL,
            cash_discount REAL,
            total_discount REAL,
            final_price REAL,
            shipping REAL,
            free_shipping INTEGER,
            affiliate_url TEXT,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def brl(value):
    try:
        return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"


def num(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()

        text = text.replace("R$", "").replace("%", "").strip()

        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")

        return float(text)
    except Exception:
        return default


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def now():
    return int(time.time())


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


# ============================================================
# OAUTH
# ============================================================

def create_pkce():
    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(48)
    ).decode().rstrip("=")

    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")

    return verifier, challenge


def ml_token():
    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    if not row:
        return None

    access = row["access_token"]
    expires_at = row["expires_at"] or 0

    # Ainda válido
    if access and expires_at > now() + 120:
        return access

    refresh = row["refresh_token"]

    if not refresh:
        return access

    try:
        response = SESSION.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = safe_json(response)

        if response.status_code != 200:
            return access

        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token", refresh)
        expires = int(data.get("expires_in", 21600))

        conn = db()

        conn.execute("""
            UPDATE oauth_tokens
            SET access_token=?,
                refresh_token=?,
                expires_at=?,
                updated_at=?
            WHERE id=1
        """, (
            new_access,
            new_refresh,
            now() + expires,
            now(),
        ))

        conn.commit()
        conn.close()

        return new_access

    except Exception:
        return access


def ml_headers():
    token = ml_token()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    return headers


# ============================================================
# OAUTH ROTAS
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado", 500

    verifier, challenge = create_pkce()
    state = secrets.token_urlsafe(32)

    with OAUTH_LOCK:
        OAUTH_STATE[state] = {
            "verifier": verifier,
            "created_at": now(),
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
def ml_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Erro no Mercado Livre</h2>
        <p>{error}</p>
        <p><a href="/">Voltar</a></p>
        """

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return "Código/state ausente", 400

    with OAUTH_LOCK:
        data_state = OAUTH_STATE.pop(state, None)

    if not data_state:
        return "State inválido ou expirado", 400

    verifier = data_state["verifier"]

    try:
        response = SESSION.post(
            ML_TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = safe_json(response)

        if response.status_code != 200:
            return f"""
            <h2>Erro ao conectar</h2>
            <pre>{json.dumps(data, ensure_ascii=False, indent=2)}</pre>
            <a href="/">Voltar</a>
            """, 400

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = int(data.get("expires_in", 21600))

        user_id = ""
        nickname = ""

        try:
            me = SESSION.get(
                ML_API + "/users/me",
                headers={
                    "Authorization": f"Bearer {access_token}"
                },
                timeout=REQUEST_TIMEOUT,
            )

            me_data = safe_json(me)

            user_id = str(me_data.get("id", ""))
            nickname = me_data.get("nickname", "")

        except Exception:
            pass

        conn = db()

        conn.execute("""
            INSERT INTO oauth_tokens (
                id,
                access_token,
                refresh_token,
                expires_at,
                user_id,
                nickname,
                updated_at
            )
            VALUES (1,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                access_token=excluded.access_token,
                refresh_token=excluded.refresh_token,
                expires_at=excluded.expires_at,
                user_id=excluded.user_id,
                nickname=excluded.nickname,
                updated_at=excluded.updated_at
        """, (
            access_token,
            refresh_token,
            now() + expires_in,
            user_id,
            nickname,
            now(),
        ))

        conn.commit()
        conn.close()

        return redirect("/")

    except Exception as e:
        return f"Erro OAuth: {e}", 500


# ============================================================
# MERCADO LIVRE API
# ============================================================

def ml_get(path, params=None, timeout=REQUEST_TIMEOUT):

    try:
        return SESSION.get(
            ML_API + path,
            headers=ml_headers(),
            params=params or {},
            timeout=timeout,
        )
    except Exception:
        return None


def product_search(query, limit=10):

    response = ml_get(
        "/products/search",
        {
            "site_id": SITE_ID,
            "q": query,
            "status": "active",
            "limit": min(limit, 50),
        }
    )

    if not response:
        return []

    if response.status_code != 200:
        return []

    data = safe_json(response)

    results = data.get("results", [])

    if isinstance(results, list):
        return results

    return []


def product_detail(product_id):

    response = ml_get(
        f"/products/{quote(str(product_id), safe='')}"
    )

    if not response or response.status_code != 200:
        return {}

    return safe_json(response)


def product_items(product_id):

    response = ml_get(
        f"/products/{quote(str(product_id), safe='')}/items",
        {
            "limit": MAX_ITEMS_PER_PRODUCT,
        }
    )

    if not response or response.status_code != 200:
        return []

    data = safe_json(response)

    results = data.get("results", [])

    if isinstance(results, list):
        return results

    return []


# ============================================================
# CUPONS
# ============================================================

COUPON_PAGES = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/ofertas/cupons",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
]


def coupon_pages():

    pages = []

    for url in COUPON_PAGES:

        try:
            response = SESSION.get(
                url,
                timeout=12,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
                        "like Mac OS X) AppleWebKit/605.1.15 "
                        "Version/17 Mobile/15E148 Safari/604.1"
                    ),
                    "Accept-Language": "pt-BR,pt;q=0.9",
                },
            )

            if response.status_code == 200:
                pages.append(response.text)

        except Exception:
            continue

    return pages


def normalize_coupon_code(code):

    code = clean_text(code).upper()

    code = re.sub(
        r"[^A-Z0-9_-]",
        "",
        code
    )

    return code


def parse_money_near(text):

    values = []

    patterns = [
        r"R\$\s*([\d\.,]+)",
        r"até\s*R\$\s*([\d\.,]+)",
        r"máx(?:imo)?\.?\s*R\$\s*([\d\.,]+)",
        r"máximo\s+de\s*R\$\s*([\d\.,]+)",
    ]

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            text,
            flags=re.I
        ):
            values.append(
                num(match.group(1))
            )

    return values


def parse_percentages(text):

    values = []

    for match in re.finditer(
        r"(\d+(?:[.,]\d+)?)\s*%",
        text
    ):
        value = num(match.group(1))

        if 1 <= value <= 100:
            values.append(value)

    return values


def parse_coupon_text(text):

    text = clean_text(text)

    if not text:
        return []

    coupons = []

    # Padrões de códigos encontrados normalmente nas páginas.
    code_patterns = [
        r"(?:cupom|código)\s*[:\-]?\s*([A-Z0-9][A-Z0-9_-]{4,})",
        r"\b([A-Z0-9]{5,}(?:[_-][A-Z0-9]+)*)\b",
    ]

    candidates = set()

    for pattern in code_patterns:

        for match in re.finditer(
            pattern,
            text,
            flags=re.I
        ):

            code = normalize_coupon_code(
                match.group(1)
            )

            if len(code) < 5:
                continue

            # Evita palavras comuns
            ignored = {
                "MERCADOLIVRE",
                "MERCADOLIBRE",
                "DESCONTO",
                "PROMOCAO",
                "OFERTAS",
                "CUPONS",
                "CUPOM",
                "PIX",
                "FRETEGRATIS",
            }

            if code in ignored:
                continue

            if not re.search(r"\d", code):
                continue

            candidates.add(code)

    # Divide o texto em trechos para associar
    # informações próximas ao código.
    for code in candidates:

        positions = [
            m.start()
            for m in re.finditer(
                re.escape(code),
                text,
                flags=re.I
            )
        ]

        for position in positions:

            start = max(0, position - 500)
            end = min(
                len(text),
                position + 900
            )

            nearby = text[start:end]

            percentages = parse_percentages(
                nearby
            )

            money_values = parse_money_near(
                nearby
            )

            # Mínimo de compra
            minimum = 0.0

            minimum_patterns = [
                r"(?:mínimo|min\.?|a partir de|compras? de)\s*R\$\s*([\d\.,]+)",
                r"R\$\s*([\d\.,]+)\s*(?:ou mais|em compras?)",
            ]

            for pattern in minimum_patterns:

                match = re.search(
                    pattern,
                    nearby,
                    flags=re.I
                )

                if match:
                    minimum = num(
                        match.group(1)
                    )
                    break

            # Teto do desconto
            maximum = 0.0

            max_patterns = [
                r"(?:máximo|máx\.?|limite|limitado)\s*(?:de)?\s*R\$\s*([\d\.,]+)",
                r"até\s*R\$\s*([\d\.,]+)\s*(?:de desconto)?",
            ]

            for pattern in max_patterns:

                match = re.search(
                    pattern,
                    nearby,
                    flags=re.I
                )

                if match:
                    maximum = num(
                        match.group(1)
                    )
                    break

            percent = (
                max(percentages)
                if percentages
                else 0.0
            )

            # Caso haja valor fixo explícito.
            fixed = 0.0

            fixed_patterns = [
                r"R\$\s*([\d\.,]+)\s*OFF",
                r"R\$\s*([\d\.,]+)\s*de\s*desconto",
                r"ganhe\s*R\$\s*([\d\.,]+)",
            ]

            for pattern in fixed_patterns:

                match = re.search(
                    pattern,
                    nearby,
                    flags=re.I
                )

                if match:
                    fixed = max(
                        fixed,
                        num(match.group(1))
                    )

            # Se encontramos valores mas nenhum padrão
            # claro de desconto fixo, não assume automaticamente
            # que todo R$ próximo seja desconto.
            if percent <= 0 and fixed <= 0:
                continue

            coupons.append({
                "code": code,
                "percent": percent,
                "fixed": fixed,
                "minimum": minimum,
                "maximum": maximum,
                "source_text": nearby,
            })

            break

    # Remove duplicados
    unique = {}

    for coupon in coupons:

        key = (
            coupon["code"],
            round(coupon["percent"], 2),
            round(coupon["fixed"], 2),
            round(coupon["minimum"], 2),
            round(coupon["maximum"], 2),
        )

        unique[key] = coupon

    return list(unique.values())


def get_coupons():

    coupons = []

    pages = coupon_pages()

    for html in pages:

        # Remove scripts gigantes quando possível.
        text = re.sub(
            r"<script.*?</script>",
            " ",
            html,
            flags=re.I | re.S
        )

        text = re.sub(
            r"<style.*?</style>",
            " ",
            text,
            flags=re.I | re.S
        )

        text = re.sub(
            r"<[^>]+>",
            " ",
            text
        )

        text = clean_text(text)

        coupons.extend(
            parse_coupon_text(text)
        )

    # Dedup
    unique = {}

    for coupon in coupons:

        code = coupon["code"]

        if not code:
            continue

        current = unique.get(code)

        if not current:
            unique[code] = coupon
            continue

        # Mantém a configuração que contém
        # mais informação.
        score_current = sum([
            1 if current["percent"] else 0,
            1 if current["fixed"] else 0,
            1 if current["minimum"] else 0,
            1 if current["maximum"] else 0,
        ])

        score_new = sum([
            1 if coupon["percent"] else 0,
            1 if coupon["fixed"] else 0,
            1 if coupon["minimum"] else 0,
            1 if coupon["maximum"] else 0,
        ])

        if score_new > score_current:
            unique[code] = coupon

    return list(unique.values())


# ============================================================
# DESCONTO DO CUPOM
# ============================================================

def calculate_coupon_discount(price, coupon):

    price = num(price)

    if price < MIN_PRODUCT_PRICE:
        return 0.0

    minimum = num(
        coupon.get("minimum")
    )

    if minimum > 0 and price < minimum:
        return 0.0

    percent = num(
        coupon.get("percent")
    )

    fixed = num(
        coupon.get("fixed")
    )

    maximum = num(
        coupon.get("maximum")
    )

    discount_percent = 0.0

    if percent > 0:
        discount_percent = price * (
            percent / 100
        )

        if maximum > 0:
            discount_percent = min(
                discount_percent,
                maximum
            )

    discount_fixed = fixed

    # Se o cupom é fixo, não pode ultrapassar
    # o valor do produto.
    if discount_fixed > 0:
        discount_fixed = min(
            discount_fixed,
            price
        )

    # Para um mesmo cupom, se houver
    # percentual e valor fixo identificados,
    # usa o benefício maior somente quando a
    # página realmente apresenta ambos.
    discount = max(
        discount_percent,
        discount_fixed
    )

    return round(
        min(discount, price),
        2
    )


# ============================================================
# DESCONTOS À VISTA / PIX
# ============================================================

def detect_cash_discount(item, product_detail_data=None):

    """
    O endpoint /products/{id}/items não garante que
    descontos de Pix apareçam sempre.

    Esta função somente utiliza informações explícitas
    que eventualmente venham na resposta da API.

    Não inventa desconto.
    Não presume que todo Pix tenha desconto.
    """

    item = item or {}
    product_detail_data = product_detail_data or {}

    candidates = []

    def recursive_scan(obj, path=""):

        if isinstance(obj, dict):

            for key, value in obj.items():

                key_low = str(key).lower()

                path_new = (
                    f"{path}.{key_low}"
                    if path
                    else key_low
                )

                if isinstance(value, (int, float)):

                    if any(term in key_low for term in [
                        "cash",
                        "pix",
                        "discount",
                        "desconto",
                        "cash_price",
                        "pix_price",
                    ]):
                        candidates.append(
                            (
                                path_new,
                                float(value)
                            )
                        )

                elif isinstance(value, str):

                    if any(term in key_low for term in [
                        "cash",
                        "pix",
                        "discount",
                        "desconto",
                    ]):

                        value_num = num(value, -1)

                        if value_num >= 0:
                            candidates.append(
                                (
                                    path_new,
                                    value_num
                                )
                            )

                elif isinstance(value, (dict, list)):
                    recursive_scan(
                        value,
                        path_new
                    )

        elif isinstance(obj, list):

            for index, value in enumerate(obj):
                recursive_scan(
                    value,
                    f"{path}[{index}]"
                )

    recursive_scan(item)
    recursive_scan(product_detail_data)

    # Nesta etapa não assumimos que um número
    # encontrado seja automaticamente um desconto.
    # Somente consideramos chaves explicitamente
    # relacionadas a desconto.
    discounts = []

    for path, value in candidates:

        if "discount" in path or "desconto" in path:

            if 0 < value < 100000:
                discounts.append(value)

    if not discounts:
        return 0.0

    return round(
        max(discounts),
        2
    )


# ============================================================
# AVALIAÇÃO DO PRODUTO
# ============================================================

def shipping_info(item):

    shipping = item.get("shipping") or {}

    cost = num(
        shipping.get("cost")
    )

    free = bool(
        shipping.get("free_shipping")
    )

    return cost, free


def seller_discount(item):

    price = num(
        item.get("price")
    )

    original = num(
        item.get("original_price")
    )

    if (
        original > price
        and price > 0
    ):
        return round(
            original - price,
            2
        )

    return 0.0


def evaluate_coupon(
    item,
    product,
    coupon,
    category,
    detail=None,
):

    price = num(
        item.get("price")
    )

    if price < MIN_PRODUCT_PRICE:
        return None

    coupon_discount = calculate_coupon_discount(
        price,
        coupon
    )

    if coupon_discount <= 0:
        return None

    shipping, free_shipping = shipping_info(
        item
    )

    cash_discount = detect_cash_discount(
        item,
        detail
    )

    # Importante:
    # não somamos automaticamente cupom + Pix.
    #
    # O desconto à vista somente é somado quando
    # a própria fonte fornece explicitamente uma
    # combinação/benefício compatível.
    #
    # Sem confirmação de cumulatividade, comparamos
    # as alternativas.
    coupon_price = max(
        0,
        price - coupon_discount
    )

    cash_price = max(
        0,
        price - cash_discount
    ) if cash_discount > 0 else price

    if cash_discount > coupon_discount:
        chosen_cash = True
        chosen_discount = cash_discount
        final_before_shipping = cash_price
    else:
        chosen_cash = False
        chosen_discount = coupon_discount
        final_before_shipping = coupon_price

    final_total = (
        final_before_shipping + shipping
    )

    effective_percent = (
        chosen_discount / price * 100
        if price > 0
        else 0
    )

    return {
        "item_id": item.get("item_id"),
        "product_id": product.get("id"),

        "title": (
            product.get("name")
            or product.get("title")
            or "Produto"
        ),

        "category": category,

        "seller_id": item.get("seller_id"),

        "product_price": round(price, 2),

        "seller_discount": seller_discount(item),

        "coupon_code": coupon.get("code", ""),

        "coupon_percent": num(
            coupon.get("percent")
        ),

        "coupon_fixed": num(
            coupon.get("fixed")
        ),

        "coupon_discount": round(
            coupon_discount,
            2
        ),

        "cash_discount": round(
            cash_discount,
            2
        ),

        "chosen_cash": chosen_cash,

        "total_discount": round(
            chosen_discount,
            2
        ),

        "effective_percent": round(
            effective_percent,
            2
        ),

        "final_price": round(
            final_before_shipping,
            2
        ),

        "shipping": round(
            shipping,
            2
        ),

        "final_total": round(
            final_total,
            2
        ),

        "free_shipping": free_shipping,

        "coupon_minimum": num(
            coupon.get("minimum")
        ),

        "coupon_maximum": num(
            coupon.get("maximum")
        ),

        "coupon_text": coupon.get(
            "source_text",
            ""
        ),

        "thumbnail": (
            item.get("thumbnail")
            or product.get("thumbnail")
            or ""
        ),

        "permalink": (
            item.get("permalink")
            or product.get("permalink")
            or (
                f"https://www.mercadolivre.com.br/"
                f"{item.get('item_id', '')}"
            )
        ),
    }


# ============================================================
# COMPARAÇÃO DE CUPONS
# ============================================================

def better_offer(a, b):

    if not a:
        return b

    if not b:
        return a

    # REGRA PRINCIPAL:
    # maior economia REAL em reais.
    if (
        b["total_discount"]
        >
        a["total_discount"]
    ):
        return b

    if (
        b["total_discount"]
        <
        a["total_discount"]
    ):
        return a

    # Empate:
    # maior percentual efetivo.
    if (
        b["effective_percent"]
        >
        a["effective_percent"]
    ):
        return b

    if (
        b["effective_percent"]
        <
        a["effective_percent"]
    ):
        return a

    # Depois, menor preço final.
    if (
        b["final_total"]
        <
        a["final_total"]
    ):
        return b

    if (
        b["final_total"]
        >
        a["final_total"]
    ):
        return a

    # Depois, frete grátis.
    if (
        b["free_shipping"]
        and not a["free_shipping"]
    ):
        return b

    return a


def best_offer_for_item(
    item,
    product,
    coupons,
    category,
    detail=None,
):

    best = None

    for coupon in coupons:

        offer = evaluate_coupon(
            item,
            product,
            coupon,
            category,
            detail,
        )

        if not offer:
            continue

        best = better_offer(
            best,
            offer
        )

    # Se não existe cupom aplicável,
    # não entra na lista principal.
    return best


# ============================================================
# DEDUPLICAÇÃO
# ============================================================

def deduplicate_offers(offers):

    unique = {}

    for offer in offers:

        item_id = str(
            offer.get("item_id") or ""
        )

        if not item_id:
            key = (
                offer.get("product_id"),
                offer.get("seller_id"),
                offer.get("title"),
            )
        else:
            key = item_id

        current = unique.get(key)

        if current is None:
            unique[key] = offer
        else:
            unique[key] = better_offer(
                current,
                offer
            )

    return list(unique.values())


# ============================================================
# RANKING FINAL
# ============================================================

def sort_offers(offers):

    return sorted(
        offers,
        key=lambda x: (
            -num(x.get("total_discount")),
            -num(x.get("effective_percent")),
            num(x.get("final_total")),
            -int(bool(x.get("free_shipping"))),
            num(x.get("product_price")),
        )
    )


# ============================================================
# SCAN COMPLETO
# ============================================================

def run_full_scan():

    started = time.time()

    coupons = get_coupons()

    if not coupons:
        return {
            "ok": False,
            "message": (
                "Nenhum cupom público foi encontrado "
                "nas páginas consultadas."
            ),
            "offers": [],
            "coupons": [],
        }

    all_offers = []

    seen_products = set()

    for category, queries in CATEGORIES.items():

        for query in queries:

            if time.time() - started > 150:
                break

            products = product_search(
                query,
                MAX_PRODUCTS_PER_QUERY
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

                if product_id in seen_products:
                    continue

                seen_products.add(
                    product_id
                )

                detail = product_detail(
                    product_id
                )

                items = product_items(
                    product_id
                )

                if not items:
                    continue

                for item in items:

                    price = num(
                        item.get("price")
                    )

                    if price < MIN_PRODUCT_PRICE:
                        continue

                    best = best_offer_for_item(
                        item,
                        product,
                        coupons,
                        category,
                        detail,
                    )

                    if best:
                        all_offers.append(
                            best
                        )

    all_offers = deduplicate_offers(
        all_offers
    )

    all_offers = sort_offers(
        all_offers
    )

    all_offers = all_offers[
        :MAX_FINAL_OFFERS
    ]

    save_offers(
        all_offers
    )

    return {
        "ok": True,
        "message": "Caça finalizada",
        "offers": all_offers,
        "coupons": coupons,
        "elapsed": round(
            time.time() - started,
            1
        ),
    }


# ============================================================
# SALVAR OFERTAS
# ============================================================

def save_offers(offers):

    conn = db()

    conn.execute(
        "DELETE FROM ofertas"
    )

    for offer in offers:

        conn.execute("""
            INSERT INTO ofertas (
                item_id,
                product_id,
                title,
                category,
                seller_id,
                seller_nickname,
                product_price,
                seller_discount,
                coupon_code,
                coupon_percent,
                coupon_discount,
                cash_discount,
                total_discount,
                final_price,
                shipping,
                free_shipping,
                affiliate_url,
                created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            offer.get("item_id"),
            offer.get("product_id"),
            offer.get("title"),
            offer.get("category"),
            str(offer.get("seller_id") or ""),
            "",
            offer.get("product_price"),
            offer.get("seller_discount"),
            offer.get("coupon_code"),
            offer.get("coupon_percent"),
            offer.get("coupon_discount"),
            offer.get("cash_discount"),
            offer.get("total_discount"),
            offer.get("final_price"),
            offer.get("shipping"),
            int(bool(
                offer.get("free_shipping")
            )),
            offer.get("permalink"),
            now(),
        ))

    conn.commit()
    conn.close()


# ============================================================
# STATUS
# ============================================================

@app.route("/health")
def health():

    connected = bool(
        ml_token()
    )

    return jsonify({
        "status": "ok",
        "app": "Cacador de Ofertas",
        "mercadolivre": connected,
        "minimum_product_price": MIN_PRODUCT_PRICE,
    })


@app.route("/api/status")
def api_status():

    token = ml_token()

    connected = bool(token)

    user = {}

    conn = db()

    row = conn.execute(
        "SELECT user_id,nickname FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    if row:
        user = {
            "id": row["user_id"],
            "nickname": row["nickname"],
        }

    return jsonify({
        "connected": connected,
        "user": user,
    })


# ============================================================
# API CUPONS
# ============================================================

@app.route("/api/coupons")
def api_coupons():

    coupons = get_coupons()

    return jsonify({
        "ok": True,
        "count": len(coupons),
        "coupons": coupons,
    })


# ============================================================
# API CAÇAR
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    result = run_full_scan()

    return jsonify(result)


# ============================================================
# BUSCA MANUAL
# ============================================================

@app.route("/api/search")
def api_search():

    query = clean_text(
        request.args.get(
            "q",
            ""
        )
    )

    if not query:
        return jsonify({
            "ok": False,
            "message": "Informe uma busca.",
            "offers": [],
        }), 400

    coupons = get_coupons()

    products = product_search(
        query,
        20
    )

    offers = []

    for product in products:

        product_id = (
            product.get("id")
            or product.get("product_id")
        )

        if not product_id:
            continue

        detail = product_detail(
            product_id
        )

        items = product_items(
            product_id
        )

        for item in items:

            price = num(
                item.get("price")
            )

            if price < MIN_PRODUCT_PRICE:
                continue

            best = best_offer_for_item(
                item,
                product,
                coupons,
                "🔎 Busca manual",
                detail,
            )

            if best:
                offers.append(
                    best
                )

    offers = deduplicate_offers(
        offers
    )

    offers = sort_offers(
        offers
    )

    return jsonify({
        "ok": True,
        "query": query,
        "count": len(offers),
        "offers": offers[:MAX_FINAL_OFFERS],
        "coupons": coupons,
    })


# ============================================================
# HISTÓRICO
# ============================================================

@app.route("/api/historico")
def historico():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY total_discount DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    offers = []

    for row in rows:

        offers.append({
            "item_id": row["item_id"],
            "product_id": row["product_id"],
            "title": row["title"],
            "category": row["category"],
            "product_price": row["product_price"],
            "seller_discount": row["seller_discount"],
            "coupon_code": row["coupon_code"],
            "coupon_percent": row["coupon_percent"],
            "coupon_discount": row["coupon_discount"],
            "cash_discount": row["cash_discount"],
            "total_discount": row["total_discount"],
            "final_price": row["final_price"],
            "shipping": row["shipping"],
            "free_shipping": bool(
                row["free_shipping"]
            ),
            "permalink": row["affiliate_url"],
        })

    return jsonify({
        "ok": True,
        "offers": offers,
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
    content="width=device-width,initial-scale=1"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f5f6f8;
    color: #17202a;
    font-family: Arial, Helvetica, sans-serif;
}

header {
    background: #ffe600;
    padding: 18px 16px;
    border-bottom: 1px solid #e0c900;
}

.header-inner {
    max-width: 1150px;
    margin: auto;
}

h1 {
    margin: 0;
    font-size: 27px;
}

.subtitle {
    margin-top: 6px;
    font-size: 14px;
    color: #4a4a4a;
}

.container {
    max-width: 1150px;
    margin: auto;
    padding: 18px 14px 60px;
}

.topbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 15px;
}

button,
input {
    border: 0;
    border-radius: 10px;
    padding: 13px 15px;
    font-size: 15px;
}

button {
    cursor: pointer;
    font-weight: 700;
}

.primary {
    background: #3483fa;
    color: white;
}

.secondary {
    background: #fff;
    border: 1px solid #ddd;
}

.search {
    display: flex;
    flex: 1;
    min-width: 250px;
}

.search input {
    width: 100%;
    border: 1px solid #ddd;
}

.status {
    background: white;
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 15px;
    border: 1px solid #e5e5e5;
}

.warning {
    background: #fff8db;
    border: 1px solid #f0d66a;
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 15px;
    font-size: 14px;
}

.summary {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}

.stat {
    background: white;
    padding: 14px;
    border-radius: 12px;
    border: 1px solid #e5e5e5;
}

.stat b {
    display: block;
    font-size: 21px;
    margin-top: 5px;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
}

.card {
    background: white;
    border-radius: 14px;
    border: 1px solid #e2e2e2;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.card-body {
    padding: 15px;
}

.card h3 {
    font-size: 16px;
    line-height: 1.35;
    margin: 0 0 12px;
}

.category {
    font-size: 12px;
    color: #777;
    margin-bottom: 8px;
}

.old {
    color: #777;
    text-decoration: line-through;
    font-size: 13px;
}

.price {
    font-size: 24px;
    font-weight: 800;
    margin-top: 4px;
}

.coupon {
    background: #e8f7ed;
    border: 1px solid #b9e6c6;
    border-radius: 10px;
    padding: 11px;
    margin-top: 12px;
}

.coupon-code {
    font-size: 18px;
    font-weight: 900;
    color: #16803c;
}

.discount {
    font-size: 15px;
    font-weight: 700;
    margin-top: 5px;
}

.final {
    margin-top: 12px;
    background: #fff0f0;
    border-radius: 10px;
    padding: 11px;
}

.final-price {
    font-size: 23px;
    font-weight: 900;
    color: #d70000;
}

.cash {
    margin-top: 8px;
    color: #147a37;
    font-weight: 700;
}

.shipping {
    margin-top: 9px;
    font-size: 14px;
}

.actions {
    display: flex;
    gap: 8px;
    margin-top: 13px;
}

.actions a {
    flex: 1;
    text-align: center;
    text-decoration: none;
    padding: 11px;
    border-radius: 9px;
    background: #3483fa;
    color: white;
    font-weight: 700;
}

.empty {
    background: white;
    padding: 30px;
    border-radius: 14px;
    text-align: center;
}

.loader {
    display: none;
    padding: 20px;
    text-align: center;
}

@media(max-width:700px) {

    .summary {
        grid-template-columns:
            repeat(2, 1fr);
    }

    h1 {
        font-size: 23px;
    }

}

</style>

</head>

<body>

<header>

<div class="header-inner">

<h1>🔥 Caçador de Ofertas</h1>

<div class="subtitle">
Cupons primeiro • produtos a partir de R$ 69,90
</div>

</div>

</header>

<div class="container">

<div class="topbar">

<button
    class="primary"
    onclick="cacar()"
>
🏹 CAÇAR OFERTAS
</button>

<button
    class="secondary"
    onclick="carregarCupons()"
>
🏷️ VER CUPONS
</button>

<div class="search">

<input
    id="search"
    placeholder="Pesquisar produto..."
>

<button
    class="primary"
    onclick="buscar()"
>
🔎 Buscar
</button>

</div>

</div>

<div
    id="status"
    class="status"
>
Carregando...
</div>

<div class="warning">

<b>⚠️ Importante:</b>
o desconto do cupom e o preço final são
estimativas. A aplicação real do cupom deve ser
confirmada no checkout do Mercado Livre.

<br><br>

O sistema prioriza o <b>maior desconto real em
R$ para o cliente</b>, independentemente de o
benefício ser percentual ou valor fixo.

</div>

<div
    id="summary"
    class="summary"
></div>

<div
    id="loader"
    class="loader"
>
🔥 Procurando produtos e comparando cupons...
</div>

<div
    id="results"
    class="grid"
></div>

</div>


<script>

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

    return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


async function atualizarStatus() {

    try {

        const response =
            await fetch("/api/status");

        const data =
            await response.json();

        const status =
            document.getElementById("status");

        if (data.connected) {

            const nick =
                data.user?.nickname || "";

            status.innerHTML =
                "🟢 <b>Mercado Livre conectado</b>" +
                (nick
                    ? " — " + escapeHtml(nick)
                    : "");

        } else {

            status.innerHTML =
                "🔴 Mercado Livre não conectado — " +
                "<a href='/mercadolivre/login'>" +
                "Conectar Mercado Livre" +
                "</a>";
        }

    } catch (e) {

        document.getElementById(
            "status"
        ).innerHTML =
            "⚠️ Não foi possível consultar o status.";
    }
}


function renderSummary(offers) {

    const summary =
        document.getElementById(
            "summary"
        );

    if (!offers.length) {

        summary.innerHTML = "";

        return;
    }

    const biggest =
        Math.max(
            ...offers.map(
                x => Number(
                    x.total_discount || 0
                )
            )
        );

    const lowest =
        Math.min(
            ...offers.map(
                x => Number(
                    x.final_total || x.final_price || 0
                )
            )
        );

    const avg =
        offers.reduce(
            (sum, x) =>
                sum + Number(
                    x.effective_percent || 0
                ),
            0
        ) / offers.length;

    summary.innerHTML = `

        <div class="stat">
            Ofertas
            <b>${offers.length}</b>
        </div>

        <div class="stat">
            Maior desconto
            <b>${money(biggest)}</b>
        </div>

        <div class="stat">
            Menor preço final
            <b>${money(lowest)}</b>
        </div>

        <div class="stat">
            Economia média
            <b>${avg.toFixed(1)}%</b>
        </div>

    `;
}


function renderOffers(offers) {

    const results =
        document.getElementById(
            "results"
        );

    if (!offers.length) {

        results.innerHTML = `
            <div class="empty">
                Nenhuma oferta com cupom
                aplicável foi encontrada.
            </div>
        `;

        return;
    }

    results.innerHTML =
        offers.map(
            offer => {

                const shipping =
                    Number(
                        offer.shipping || 0
                    );

                const coupon =
                    escapeHtml(
                        offer.coupon_code
                    );

                const chosenCash =
                    Boolean(
                        offer.chosen_cash
                    );

                return `

                <div class="card">

                    <div class="card-body">

                        <div class="category">
                            ${escapeHtml(
                                offer.category
                            )}
                        </div>

                        <h3>
                            ${escapeHtml(
                                offer.title
                            )}
                        </h3>

                        <div class="old">
                            Produto:
                            ${money(
                                offer.product_price
                            )}
                        </div>

                        <div class="coupon">

                            <div>
                                🏷️ CUPOM
                            </div>

                            <div class="coupon-code">
                                ${coupon}
                            </div>

                            <div class="discount">
                                💰 Desconto real:
                                <b>
                                ${money(
                                    offer.total_discount
                                )}
                                </b>
                            </div>

                            <div>
                                📊 Economia:
                                <b>
                                ${Number(
                                    offer.effective_percent || 0
                                ).toFixed(2)}%
                                </b>
                            </div>

                        </div>

                        ${
                            chosenCash
                            ? `
                            <div class="cash">
                                💳 Melhor condição:
                                pagamento à vista
                            </div>
                            `
                            : ""
                        }

                        <div class="final">

                            <div>
                                🔥 PREÇO FINAL ESTIMADO
                            </div>

                            <div class="final-price">
                                ${money(
                                    offer.final_total ||
                                    offer.final_price
                                )}
                            </div>

                        </div>

                        <div class="shipping">

                            ${
                                offer.free_shipping
                                ? "🚚 Frete grátis"
                                : (
                                    "🚚 Frete: " +
                                    money(shipping)
                                )
                            }

                        </div>

                        ${
                            offer.seller_discount > 0
                            ? `
                            <div
                                style="
                                margin-top:8px;
                                font-size:12px;
                                color:#777;
                                "
                            >
                                ℹ️ O vendedor já possui
                                ${money(
                                    offer.seller_discount
                                )}
                                de desconto no preço.
                                Isso é separado do cupom.
                            </div>
                            `
                            : ""
                        }

                        <div class="actions">

                            <a
                                href="${escapeHtml(
                                    offer.permalink
                                )}"
                                target="_blank"
                                rel="noopener"
                            >
                                🛒 Ver produto
                            </a>

                        </div>

                    </div>

                </div>

                `;
            }
        ).join("");
}


async function cacar() {

    const loader =
        document.getElementById(
            "loader"
        );

    const results =
        document.getElementById(
            "results"
        );

    loader.style.display =
        "block";

    results.innerHTML = "";

    try {

        const response =
            await fetch(
                "/api/cacar"
            );

        const data =
            await response.json();

        if (!data.ok) {

            results.innerHTML = `
                <div class="empty">
                    ❌ ${escapeHtml(
                        data.message
                    )}
                </div>
            `;

            return;
        }

        renderSummary(
            data.offers || []
        );

        renderOffers(
            data.offers || []
        );

    } catch (e) {

        results.innerHTML = `
            <div class="empty">
                ❌ Erro ao realizar a caça.
            </div>
        `;

    } finally {

        loader.style.display =
            "none";
    }
}


async function buscar() {

    const query =
        document.getElementById(
            "search"
        ).value.trim();

    if (!query) {
        return;
    }

    const loader =
        document.getElementById(
            "loader"
        );

    loader.style.display =
        "block";

    try {

        const response =
            await fetch(
                "/api/search?q=" +
                encodeURIComponent(query)
            );

        const data =
            await response.json();

        renderSummary(
            data.offers || []
        );

        renderOffers(
            data.offers || []
        );

    } catch (e) {

        document.getElementById(
            "results"
        ).innerHTML = `
            <div class="empty">
                ❌ Erro na busca.
            </div>
        `;

    } finally {

        loader.style.display =
            "none";
    }
}


async function carregarCupons() {

    const loader =
        document.getElementById(
            "loader"
        );

    const results =
        document.getElementById(
            "results"
        );

    loader.style.display =
        "block";

    try {

        const response =
            await fetch(
                "/api/coupons"
            );

        const data =
            await response.json();

        const coupons =
            data.coupons || [];

        if (!coupons.length) {

            results.innerHTML = `
                <div class="empty">
                    Nenhum cupom público encontrado.
                </div>
            `;

            return;
        }

        results.innerHTML =
            coupons.map(
                coupon => `

                <div class="card">

                    <div class="card-body">

                        <div class="coupon">

                            <div>
                                🏷️ CUPOM
                            </div>

                            <div class="coupon-code">
                                ${escapeHtml(
                                    coupon.code
                                )}
                            </div>

                            ${
                                coupon.percent
                                ? `
                                <div class="discount">
                                    ${coupon.percent}% OFF
                                </div>
                                `
                                : ""
                            }

                            ${
                                coupon.fixed
                                ? `
                                <div class="discount">
                                    ${money(
                                        coupon.fixed
                                    )}
                                    OFF
                                </div>
                                `
                                : ""
                            }

                            ${
                                coupon.minimum
                                ? `
                                <div>
                                    Compra mínima:
                                    ${money(
                                        coupon.minimum
                                    )}
                                </div>
                                `
                                : ""
                            }

                            ${
                                coupon.maximum
                                ? `
                                <div>
                                    Limite:
                                    ${money(
                                        coupon.maximum
                                    )}
                                </div>
                                `
                                : ""
                            }

                        </div>

                    </div>

                </div>

                `
            ).join("");

    } catch (e) {

        results.innerHTML = `
            <div class="empty">
                ❌ Erro ao buscar cupons.
            </div>
        `;

    } finally {

        loader.style.display =
            "none";
    }
}


atualizarStatus();

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
        HTML
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "8080"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )