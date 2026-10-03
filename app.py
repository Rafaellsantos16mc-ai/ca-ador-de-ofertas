import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
from urllib.parse import urlencode
from html.parser import HTMLParser

import requests
from flask import Flask, request, redirect, session, jsonify, render_template_string


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-cacador-ofertas"
)

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

MIN_PRODUCT_PRICE = 69.90

MAX_PRODUCTS = 60

TOP_PRODUCTS_PER_CATEGORY = 8

MAX_DISCOVERED_CATEGORIES = 3

COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]


# ============================================================
# CATEGORIAS
# ============================================================

CATEGORIES = {

    "📱 Celulares": [
        "celular",
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth"
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "ventilador",
        "ferro de passar"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "chave de impacto"
    ],

    "🏋️ Academia": [
        "tenis corrida",
        "roupa academia",
        "whey protein",
        "creatina",
        "legging"
    ],

    "🌸 Beleza": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "secador de cabelo",
        "chapinha"
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "tapete automotivo"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "bolsa feminina",
        "oculos de sol"
    ],
}


# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            permalink TEXT,
            price REAL,
            original_price REAL,
            discount REAL,
            seller_id TEXT,
            image TEXT,
            category_id TEXT,
            category_name TEXT,
            condition TEXT,
            listing_type_id TEXT,
            free_shipping INTEGER DEFAULT 0,
            shipping_cost REAL,
            total_price REAL,
            relevance_score REAL DEFAULT 0,
            affiliate_link TEXT,
            extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            description TEXT,
            discount_percent REAL,
            fixed_discount REAL DEFAULT 0,
            min_purchase REAL,
            max_discount REAL,
            valid_until TEXT,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute(
            "ALTER TABLE cupons ADD COLUMN fixed_discount REAL DEFAULT 0"
        )
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILIDADES
# ============================================================

def brl(value):

    try:
        return (
            f"R$ {float(value):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )
    except Exception:
        return "R$ 0,00"


def safe_float(value):

    try:
        return float(value)
    except Exception:
        return None


def json_safe(value):

    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(value, list):
        return [
            json_safe(v)
            for v in value
        ]

    return str(value)


def norm(text):

    if not text:
        return ""

    text = str(text).lower()

    trans = str.maketrans(
        "áàãâäéèêëíìîïóòõôöúùûüç",
        "aaaaaeeeeiiiiooooouuuuc"
    )

    text = text.translate(trans)

    text = re.sub(
        r"[^a-z0-9\s]+",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def calc_discount(price, original):

    try:

        price = float(price)
        original = float(original)

        if original > price > 0:

            return round(
                (1 - price / original) * 100,
                2
            )

    except Exception:
        pass

    return 0


# ============================================================
# OAUTH
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


def get_tokens():

    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):

    old = get_tokens() or {}

    conn = get_db()

    conn.execute("""
        INSERT INTO oauth_tokens(
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname
        )
        VALUES(1,?,?,?,?,?)

        ON CONFLICT(id) DO UPDATE SET

            access_token = excluded.access_token,

            refresh_token =
                COALESCE(
                    excluded.refresh_token,
                    oauth_tokens.refresh_token
                ),

            expires_at =
                excluded.expires_at,

            user_id =
                COALESCE(
                    excluded.user_id,
                    oauth_tokens.user_id
                ),

            nickname =
                COALESCE(
                    excluded.nickname,
                    oauth_tokens.nickname
                )
    """, (
        data.get("access_token"),

        data.get("refresh_token"),

        int(time.time()) +
        int(data.get("expires_in", 21600)),

        str(user.get("id"))
        if user and user.get("id")
        else old.get("user_id"),

        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    conn.commit()
    conn.close()


def refresh_token():

    data = get_tokens()

    if not data:
        return None

    refresh = data.get(
        "refresh_token"
    )

    if not refresh:
        return None

    try:

        response = requests.post(
            ML_TOKEN,
            data={
                "grant_type":
                    "refresh_token",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "refresh_token":
                    refresh
            },
            timeout=30
        )

        if response.status_code != 200:

            print(
                "[REFRESH TOKEN]",
                response.status_code,
                response.text[:500]
            )

            return None

        new_data = response.json()

        save_tokens(
            new_data,
            {
                "id":
                    data.get("user_id"),

                "nickname":
                    data.get("nickname")
            }
        )

        return new_data.get(
            "access_token"
        )

    except Exception as e:

        print(
            "[ERRO REFRESH]",
            repr(e)
        )

        return None


def access_token():

    data = get_tokens()

    if not data:
        return None

    token = data.get(
        "access_token"
    )

    expires = data.get(
        "expires_at"
    ) or 0

    if token and time.time() < expires - 120:
        return token

    return refresh_token() or token


# ============================================================
# API MERCADO LIVRE
# ============================================================

def ml_get(path, params=None):

    token = access_token()

    if not token:
        return {}, 401, {}

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    try:

        response = requests.get(

            url,

            headers={
                "Authorization":
                    f"Bearer {token}",

                "Accept":
                    "application/json",

                "User-Agent":
                    "CacadorDeOfertas/2.0"
            },

            params=params,

            timeout=30
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "message":
                    response.text
            }

        return (
            data,
            response.status_code,
            dict(response.headers)
        )

    except requests.RequestException as e:

        return {
            "error":
                str(e)
        }, 500, {}


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return jsonify({
            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = generate_pkce()

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {

        "response_type":
            "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state":
            state,

        "code_challenge":
            challenge,

        "code_challenge_method":
            "S256"
    }

    return redirect(
        ML_AUTH + "?" +
        urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    if request.args.get("error"):

        return jsonify({

            "erro":
                request.args.get("error"),

            "descricao":
                request.args.get(
                    "error_description"
                )

        }), 400

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    if not code:

        return jsonify({
            "erro":
                "Código não recebido."
        }), 400

    if state != session.get(
        "ml_state"
    ):

        return jsonify({
            "erro":
                "State inválido."
        }), 400

    try:

        response = requests.post(

            ML_TOKEN,

            data={

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
                    session.get(
                        "ml_code_verifier"
                    )
            },

            timeout=30
        )

        if response.status_code != 200:

            return jsonify({

                "erro":
                    "Falha ao obter token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text

            }), response.status_code

        token_data = response.json()

        user = None

        if token_data.get(
            "access_token"
        ):

            me = requests.get(

                ML_API +
                "/users/me",

                headers={
                    "Authorization":
                        "Bearer " +
                        token_data[
                            "access_token"
                        ]
                },

                timeout=30
            )

            if me.status_code == 200:

                user = me.json()

        save_tokens(
            token_data,
            user
        )

        session.pop(
            "ml_state",
            None
        )

        session.pop(
            "ml_code_verifier",
            None
        )

        return redirect(
            "/?conectado=1"
        )

    except Exception as e:

        return jsonify({
            "erro":
                str(e)
        }), 500


@app.route("/mercadolivre/logout")
def mercadolivre_logout():

    conn = get_db()

    conn.execute(
        "DELETE FROM oauth_tokens WHERE id=1"
    )

    conn.commit()
    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# PRODUTOS
# ============================================================

def discover_categories(query):

    data, status, _ = ml_get(
        f"/sites/{SITE_ID}/domain_discovery/search",
        {
            "q":
                query
        }
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        list
    ):
        return []

    result = []

    seen = set()

    for item in data:

        category_id = (
            item.get("category_id")
            or item.get("id")
        )

        category_name = (
            item.get("category_name")
            or item.get("name")
            or category_id
        )

        if not category_id:
            continue

        if category_id in seen:
            continue

        seen.add(
            category_id
        )

        result.append({

            "category_id":
                category_id,

            "category_name":
                category_name
        })

    return result


def get_best_sellers(category_id):

    data, status, _ = ml_get(
        f"/highlights/{SITE_ID}/category/{category_id}"
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        dict
    ):
        return []

    content = data.get(
        "content",
        []
    )

    if not isinstance(
        content,
        list
    ):
        return []

    result = []

    for item in content:

        if not isinstance(
            item,
            dict
        ):
            continue

        product_id = item.get(
            "id"
        )

        if not product_id:
            continue

        result.append({

            "id":
                product_id,

            "position":
                item.get(
                    "position"
                ),

            "type":
                item.get(
                    "type"
                ),

            "category_id":
                category_id
        })

    return result


def get_product(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    if status != 200:
        return None

    if not isinstance(
        data,
        dict
    ):
        return None

    return data


def get_product_items(product_id):

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    if status != 200:
        return []

    if isinstance(
        data,
        list
    ):
        return data

    if isinstance(
        data,
        dict
    ):

        results = data.get(
            "results",
            []
        )

        if isinstance(
            results,
            list
        ):
            return results

    return []


def normalize_item(item):

    if not isinstance(
        item,
        dict
    ):
        return None

    item_id = (
        item.get("item_id")
        or item.get("id")
    )

    if not item_id:
        return None

    shipping = (
        item.get("shipping")
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    shipping_cost = (
        0
        if free_shipping
        else safe_float(
            shipping.get("cost")
        )
    )

    price = safe_float(
        item.get("price")
    )

    original_price = safe_float(
        item.get("original_price")
    )

    return {

        "item_id":
            item_id,

        "seller_id":
            item.get(
                "seller_id"
            ),

        "price":
            price,

        "original_price":
            original_price,

        "condition":
            item.get(
                "condition"
            ),

        "listing_type_id":
            item.get(
                "listing_type_id"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            shipping_cost,

        "permalink":
            item.get(
                "permalink"
            ),

        "user_product_id":
            item.get(
                "user_product_id"
            ),

        "raw":
            item
    }


def normalize_buy_box(
    buy_box,
    product_data
):

    if isinstance(
        buy_box,
        list
    ):

        buy_box = (
            buy_box[0]
            if buy_box
            else None
        )

    if not isinstance(
        buy_box,
        dict
    ):
        return None

    item_id = (
        buy_box.get(
            "item_id"
        )
        or buy_box.get(
            "id"
        )
    )

    price = safe_float(
        buy_box.get(
            "price"
        )
    )

    original_price = safe_float(
        buy_box.get(
            "original_price"
        )
    )

    shipping = (
        buy_box.get(
            "shipping"
        )
        or {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    if not item_id and price is None:
        return None

    return {

        "item_id":
            item_id,

        "seller_id":
            buy_box.get(
                "seller_id"
            ),

        "price":
            price,

        "original_price":
            original_price,

        "condition":
            buy_box.get(
                "condition"
            ),

        "listing_type_id":
            buy_box.get(
                "listing_type_id"
            ),

        "free_shipping":
            free_shipping,

        "shipping_cost":
            0
            if free_shipping
            else safe_float(
                shipping.get(
                    "cost"
                )
            ),

        "permalink":
            buy_box.get(
                "permalink"
            )
            or product_data.get(
                "permalink"
            ),

        "user_product_id":
            buy_box.get(
                "user_product_id"
            ),

        "raw":
            buy_box
    }


def search_products(query):

    data, status, _ = ml_get(
        "/products/search",
        {
            "site_id":
                SITE_ID,

            "status":
                "active",

            "q":
                query,

            "limit":
                10
        }
    )

    if status != 200:
        return []

    if not isinstance(
        data,
        dict
    ):
        return []

    results = data.get(
        "results",
        []
    )

    return (
        results
        if isinstance(
            results,
            list
        )
        else []
    )


# ============================================================
# SCORE DOS PRODUTOS
# ============================================================

def calculate_score(
    ranking_position,
    price,
    discount,
    free_shipping,
    trend_bonus=0
):

    score = 0

    if ranking_position:

        position = int(
            ranking_position
        )

        if position == 1:
            score += 100

        elif position <= 3:
            score += 90

        elif position <= 5:
            score += 80

        elif position <= 10:
            score += 65

        elif position <= 15:
            score += 50

        elif position <= 20:
            score += 40

    score += trend_bonus

    if discount > 0:

        score += min(
            discount * 0.8,
            25
        )

    if free_shipping:
        score += 10

    if price:

        if 69.90 <= price <= 149.90:
            score += 20

        elif 150 <= price <= 299.90:
            score += 17

        elif 300 <= price <= 499.90:
            score += 12

        elif 500 <= price <= 999.90:
            score += 8

        elif price > 1500:
            score -= 5

    return round(
        score,
        2
    )


# ============================================================
# HTML PARSER PARA CUPONS
# ============================================================

class CouponHTMLParser(HTMLParser):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.parts = []

    def handle_data(self, data):

        if data and data.strip():

            text = re.sub(
                r"\s+",
                " ",
                data
            ).strip()

            if text:
                self.parts.append(
                    text
                )


def html_to_text(raw_html):

    parser = CouponHTMLParser()

    try:
        parser.feed(
            raw_html
        )

        text = "\n".join(
            parser.parts
        )

    except Exception:

        text = html_lib.unescape(
            raw_html
        )

        text = re.sub(
            r"<[^>]+>",
            "\n",
            text
        )

    text = html_lib.unescape(
        text
    )

    text = text.replace(
        "\r",
        "\n"
    )

    text = re.sub(
        r"\n+",
        "\n",
        text
    )

    return text.strip()


# ============================================================
# CUPONS
# ============================================================

def coupon_code_valid(code):

    if not code:
        return False

    code = code.upper().strip()

    if not re.fullmatch(
        r"[A-Z0-9][A-Z0-9_-]{5,29}",
        code
    ):
        return False

    if not re.search(
        r"[A-Z]",
        code
    ):
        return False

    if not re.search(
        r"\d",
        code
    ):
        return False

    return True


def extract_number(text):

    if not text:
        return None

    match = re.search(
        r"(\d+(?:[.,]\d+)?)",
        str(text)
    )

    if not match:
        return None

    try:

        return float(
            match.group(1)
            .replace(",", ".")
        )

    except Exception:

        return None


def parse_coupon_values(
    code,
    context,
    source_url
):

    percent = None

    fixed = None

    minimum = None

    maximum = None

    # --------------------------------------------------------
    # Percentual
    # --------------------------------------------------------

    patterns_percent = [

        r"até\s+(\d+(?:[.,]\d+)?)\s*%",

        r"(\d+(?:[.,]\d+)?)\s*%\s*off",

        r"(\d+(?:[.,]\d+)?)\s*%\s*de\s+desconto",

        r"desconto\s+de\s+(\d+(?:[.,]\d+)?)\s*%"
    ]

    for pattern in patterns_percent:

        match = re.search(
            pattern,
            context,
            re.I
        )

        if match:

            percent = extract_number(
                match.group(1)
            )

            break

    # --------------------------------------------------------
    # Fixo
    # --------------------------------------------------------

    patterns_fixed = [

        r"R\$\s*(\d+(?:[.,]\d+)?)\s*off",

        r"R\$\s*(\d+(?:[.,]\d+)?)\s*de\s+desconto",

        r"desconto\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"ganhe\s*R\$\s*(\d+(?:[.,]\d+)?)"
    ]

    for pattern in patterns_fixed:

        match = re.search(
            pattern,
            context,
            re.I
        )

        if match:

            fixed = extract_number(
                match.group(1)
            )

            break

    # --------------------------------------------------------
    # Compra mínima
    # --------------------------------------------------------

    minimum_patterns = [

        r"compra\s+m[ií]nima\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"m[ií]nimo\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"a\s+partir\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"partir\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)"
    ]

    for pattern in minimum_patterns:

        match = re.search(
            pattern,
            context,
            re.I
        )

        if match:

            minimum = extract_number(
                match.group(1)
            )

            break

    # --------------------------------------------------------
    # Máximo
    # --------------------------------------------------------

    maximum_patterns = [

        r"desconto\s+m[aá]ximo\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"m[aá]ximo\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"limitado\s+a\s*R\$\s*(\d+(?:[.,]\d+)?)"
    ]

    for pattern in maximum_patterns:

        match = re.search(
            pattern,
            context,
            re.I
        )

        if match:

            maximum = extract_number(
                match.group(1)
            )

            break

    return {

        "code":
            code,

        "description":
            context[:2500],

        "discount_percent":
            percent,

        "fixed_discount":
            fixed or 0,

        "min_purchase":
            minimum,

        "max_discount":
            maximum,

        "source_url":
            source_url,

        "conditions":
            context[:2500],

        "context":
            context
    }


def build_coupon_contexts(text):

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    contexts = []

    coupon_positions = []

    pattern = re.compile(
        r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{5,29})\b",
        re.I
    )

    for index, line in enumerate(lines):

        match = pattern.search(
            line
        )

        if not match:
            continue

        code = match.group(
            1
        ).upper()

        if not coupon_code_valid(
            code
        ):
            continue

        coupon_positions.append(
            (
                index,
                code
            )
        )

    for pos, (index, code) in enumerate(
        coupon_positions
    ):

        # Nunca atravessa o próximo cupom.
        next_index = (
            coupon_positions[pos + 1][0]
            if pos + 1 < len(coupon_positions)
            else len(lines)
        )

        start = max(
            0,
            index - 12
        )

        end = min(
            next_index,
            index + 12
        )

        block_lines = lines[
            start:end
        ]

        context = " ".join(
            block_lines
        )

        # Limpa repetições exageradas.
        context = re.sub(
            r"\s+",
            " ",
            context
        ).strip()

        contexts.append(
            (
                code,
                context
            )
        )

    return contexts


def sync_coupons():

    headers = {

        "User-Agent":
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1",

        "Accept-Language":
            "pt-BR,pt;q=0.9",

        "Accept":
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    parsed = {}

    errors = []

    for source_url in COUPON_SOURCE_URLS:

        try:

            response = requests.get(

                source_url,

                headers=headers,

                timeout=30
            )

            if response.status_code != 200:

                errors.append(
                    f"{source_url}: HTTP {response.status_code}"
                )

                continue

            raw_html = response.text

            # Remove conteúdo que polui a leitura.
            cleaned = re.sub(
                r"<script.*?</script>",
                " ",
                raw_html,
                flags=re.I | re.S
            )

            cleaned = re.sub(
                r"<style.*?</style>",
                " ",
                cleaned,
                flags=re.I | re.S
            )

            text = html_to_text(
                cleaned
            )

            contexts = build_coupon_contexts(
                text
            )

            for code, context in contexts:

                coupon = parse_coupon_values(
                    code,
                    context,
                    source_url
                )

                if (
                    not coupon.get(
                        "discount_percent"
                    )
                    and not coupon.get(
                        "fixed_discount"
                    )
                ):
                    continue

                old = parsed.get(
                    code
                )

                if (
                    old is None
                    or len(
                        coupon.get(
                            "context",
                            ""
                        )
                    )
                    >
                    len(
                        old.get(
                            "context",
                            ""
                        )
                    )
                ):

                    parsed[
                        code
                    ] = coupon

        except Exception as e:

            errors.append(
                f"{source_url}: {e}"
            )

    coupons_list = list(
        parsed.values()
    )

    conn = get_db()

    conn.execute(
        "UPDATE cupons SET active=0"
    )

    for coupon in coupons_list:

        conn.execute("""
            INSERT INTO cupons(
                code,
                description,
                discount_percent,
                fixed_discount,
                min_purchase,
                max_discount,
                source_url,
                conditions,
                active,
                updated_at
            )

            VALUES(
                ?,?,?,?,?,?,?,?,1,
                CURRENT_TIMESTAMP
            )

            ON CONFLICT(code)
            DO UPDATE SET

                description =
                    excluded.description,

                discount_percent =
                    excluded.discount_percent,

                fixed_discount =
                    excluded.fixed_discount,

                min_purchase =
                    excluded.min_purchase,

                max_discount =
                    excluded.max_discount,

                source_url =
                    excluded.source_url,

                conditions =
                    excluded.conditions,

                active = 1,

                updated_at =
                    CURRENT_TIMESTAMP
        """, (

            coupon["code"],

            coupon["description"],

            coupon["discount_percent"],

            coupon["fixed_discount"],

            coupon["min_purchase"],

            coupon["max_discount"],

            coupon["source_url"],

            coupon["conditions"]
        ))

    conn.commit()
    conn.close()

    print(
        "[CUPONS] Encontrados:",
        len(coupons_list)
    )

    for coupon in coupons_list:

        print(
            "[CUPOM]",
            coupon["code"],
            "|",
            coupon.get(
                "discount_percent"
            ),
            "%",
            "| R$",
            coupon.get(
                "fixed_discount"
            ),
            "| mínimo",
            coupon.get(
                "min_purchase"
            ),
            "| máximo",
            coupon.get(
                "max_discount"
            )
        )

    return {

        "ok":
            True,

        "cupons_encontrados":
            len(coupons_list),

        "fontes":
            len(COUPON_SOURCE_URLS),

        "erros":
            errors
    }


def get_coupons():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY updated_at DESC
    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# PALAVRAS PARA MATCH DO PRODUTO
# ============================================================

STOP_WORDS = {
    "para",
    "com",
    "sem",
    "de",
    "da",
    "do",
    "das",
    "dos",
    "e",
    "ou",
    "em",
    "por",
    "um",
    "uma",
    "novo",
    "nova",
    "original",
    "oficial",
    "preto",
    "preta",
    "branco",
    "branca",
    "azul",
    "verde",
    "amarelo",
    "amarela",
    "frete",
    "gratis",
    "grátis",
    "mlb",
    "mercado",
    "livre"
}


def product_tokens(title):

    text = norm(
        title
    )

    tokens = []

    for token in text.split():

        if len(token) < 4:
            continue

        if token in STOP_WORDS:
            continue

        if token.isdigit():
            continue

        if token not in tokens:
            tokens.append(
                token
            )

    return tokens


def coupon_context_score(
    product_title,
    coupon
):

    title_norm = norm(
        product_title
    )

    context_norm = norm(
        coupon.get(
            "context",
            ""
        )
    )

    if not title_norm or not context_norm:
        return 0

    # Match direto do título inteiro.
    if title_norm in context_norm:
        return 100

    tokens = product_tokens(
        product_title
    )

    if not tokens:
        return 0

    context_tokens = set(
        context_norm.split()
    )

    matches = [
        token
        for token in tokens
        if token in context_tokens
    ]

    if not matches:
        return 0

    score = 0

    for token in matches:

        # Palavras muito específicas pesam mais.
        if len(token) >= 8:
            score += 12

        elif len(token) >= 6:
            score += 8

        else:
            score += 4

    ratio = (
        len(matches)
        /
        max(
            len(tokens),
            1
        )
    )

    if ratio >= 0.60:
        score += 25

    elif ratio >= 0.40:
        score += 15

    elif ratio >= 0.25:
        score += 8

    # Para evitar falsos positivos:
    # precisa de pelo menos 2 tokens ou 1 token muito específico.
    if len(matches) >= 2:
        score += 20

    elif len(matches) == 1 and len(matches[0]) >= 8:
        score += 10

    return score


# ============================================================
# CÁLCULO DO CUPOM
# ============================================================

def calculate_coupon_discount(
    coupon,
    price
):

    try:

        price = float(
            price
        )

    except Exception:

        return 0

    minimum = safe_float(
        coupon.get(
            "min_purchase"
        )
    )

    if (
        minimum is not None
        and price < minimum
    ):
        return 0

    discounts = []

    percent = safe_float(
        coupon.get(
            "discount_percent"
        )
    ) or 0

    fixed = safe_float(
        coupon.get(
            "fixed_discount"
        )
    ) or 0

    maximum = safe_float(
        coupon.get(
            "max_discount"
        )
    )

    if percent > 0:

        discount = (
            price *
            percent /
            100
        )

        if maximum is not None:
            discount = min(
                discount,
                maximum
            )

        discounts.append(
            discount
        )

    if fixed > 0:

        discount = fixed

        if maximum is not None:
            discount = min(
                discount,
                maximum
            )

        discounts.append(
            discount
        )

    if not discounts:
        return 0

    return round(
        min(
            max(
                discounts
            ),
            price
        ),
        2
    )


def find_best_product_coupon(
    product_title,
    price
):

    all_coupons = get_coupons()

    candidates = []

    for coupon in all_coupons:

        match_score = coupon_context_score(
            product_title,
            coupon
        )

        # Regra importante:
        # não basta o cupom existir.
        # ele precisa ter associação textual
        # suficientemente forte com o produto.
        if match_score < 25:
            continue

        discount_value = calculate_coupon_discount(
            coupon,
            price
        )

        if discount_value <= 0:
            continue

        item = dict(
            coupon
        )

        item[
            "match_score"
        ] = match_score

        item[
            "desconto_estimado"
        ] = discount_value

        item[
            "percentual_efetivo"
        ] = round(
            (
                discount_value /
                float(price)
            ) * 100,
            2
        )

        candidates.append(
            item
        )

    if not candidates:
        return None

    # Maior economia real em R$
    candidates.sort(
        key=lambda item: (

            -item[
                "desconto_estimado"
            ],

            -item[
                "match_score"
            ],

            -item[
                "percentual_efetivo"
            ]
        )
    )

    return candidates[0]


# ============================================================
# PIX / À VISTA
# ============================================================

def detect_cash_discount(
    raw_item,
    price
):

    if not isinstance(
        raw_item,
        dict
    ):
        return 0, None

    try:
        price = float(
            price
        )
    except Exception:
        return 0, None

    possibilities = []

    # Campos diretos.
    for key in [

        "pix_discount",
        "cash_discount",
        "discount_pix",
        "payment_discount"

    ]:

        value = raw_item.get(
            key
        )

        if isinstance(
            value,
            (int, float)
        ):

            if 0 < float(value) < price:

                possibilities.append(
                    float(value)
                )

    # Preço Pix.
    for key in [

        "pix_price",
        "cash_price",
        "price_pix",
        "price_cash"

    ]:

        value = raw_item.get(
            key
        )

        if isinstance(
            value,
            (int, float)
        ):

            value = float(
                value
            )

            if 0 < value < price:

                possibilities.append(
                    price - value
                )

    discount = round(
        max(
            possibilities,
            default=0
        ),
        2
    )

    if discount <= 0:
        return 0, None

    return (
        discount,
        "Pix"
    )


# ============================================================
# CANDIDATOS
# ============================================================

def collect_ranked_candidates():

    candidates = {}

    diagnostics = {}

    for category_name, queries in CATEGORIES.items():

        category_candidates = {}

        discovered_count = 0

        ranking_count = 0

        # ----------------------------------------------------
        # MAIS VENDIDOS
        # ----------------------------------------------------

        for query in queries:

            discovered = discover_categories(
                query
            )

            for discovered_category in discovered[
                :MAX_DISCOVERED_CATEGORIES
            ]:

                discovered_count += 1

                category_id = discovered_category[
                    "category_id"
                ]

                best_sellers = get_best_sellers(
                    category_id
                )

                for best in best_sellers:

                    product_id = best.get(
                        "id"
                    )

                    if not product_id:
                        continue

                    if product_id in category_candidates:
                        continue

                    category_candidates[
                        product_id
                    ] = {

                        "product_id":
                            product_id,

                        "category_name":
                            category_name,

                        "category_id":
                            category_id,

                        "query":
                            query,

                        "ranking_position":
                            best.get(
                                "position"
                            ),

                        "source":
                            "mais_vendidos",

                        "trend_bonus":
                            0
                    }

                    ranking_count += 1

                    if len(
                        category_candidates
                    ) >= TOP_PRODUCTS_PER_CATEGORY:

                        break

                if len(
                    category_candidates
                ) >= TOP_PRODUCTS_PER_CATEGORY:

                    break

            if len(
                category_candidates
            ) >= TOP_PRODUCTS_PER_CATEGORY:

                break

        candidates[
            category_name
        ] = list(
            category_candidates.values()
        )

        diagnostics[
            category_name
        ] = {

            "categorias_consultadas":
                discovered_count,

            "mais_vendidos":
                ranking_count,

            "candidatos":
                len(
                    category_candidates
                )
        }

    return (
        candidates,
        diagnostics
    )


# ============================================================
# PROCESSA PRODUTO
# ============================================================

def process_candidate(
    candidate
):

    product_id = candidate[
        "product_id"
    ]

    data = get_product(
        product_id
    )

    if not data:
        return None

    title = (
        data.get("name")
        or data.get("title")
        or product_id
    )

    pictures = (
        data.get("pictures")
        or []
    )

    image = None

    if pictures:

        first = pictures[0]

        if isinstance(
            first,
            dict
        ):

            image = (
                first.get(
                    "url"
                )
                or first.get(
                    "secure_url"
                )
            )

    # --------------------------------------------------------
    # Ofertas
    # --------------------------------------------------------

    raw_items = get_product_items(
        product_id
    )

    items = []

    for raw in raw_items:

        item = normalize_item(
            raw
        )

        if item:
            items.append(
                item
            )

    # Fallback Buy Box.
    if not items:

        buy_box = normalize_buy_box(
            data.get(
                "buy_box_winner"
            ),
            data
        )

        if buy_box:

            items.append(
                buy_box
            )

    if not items:
        return None

    valid_items = []

    for item in items:

        price = item.get(
            "price"
        )

        if price is None:
            continue

        if price < MIN_PRODUCT_PRICE:
            continue

        valid_items.append(
            item
        )

    if not valid_items:
        return None

    valid_items.sort(
        key=lambda item: (

            item.get(
                "price"
            )
            if item.get(
                "price"
            ) is not None
            else 999999,

            0
            if item.get(
                "free_shipping"
            )
            else 1
        )
    )

    best_item = valid_items[0]

    price = best_item[
        "price"
    ]

    original_price = best_item.get(
        "original_price"
    )

    discount = calc_discount(
        price,
        original_price
    )

    shipping_cost = best_item.get(
        "shipping_cost"
    )

    total_price = (

        price

        if shipping_cost is None

        else
        price +
        shipping_cost
    )

    ranking_position = candidate.get(
        "ranking_position"
    )

    score = calculate_score(

        ranking_position,

        price,

        discount,

        best_item.get(
            "free_shipping"
        ),

        candidate.get(
            "trend_bonus",
            0
        )
    )

    # --------------------------------------------------------
    # CUPOM
    # --------------------------------------------------------

    coupon = find_best_product_coupon(
        title,
        price
    )

    coupon_discount = 0

    coupon_final = None

    if coupon:

        coupon_discount = coupon[
            "desconto_estimado"
        ]

        coupon_final = round(
            max(
                0,
                total_price -
                coupon_discount
            ),
            2
        )

    # --------------------------------------------------------
    # PIX
    # --------------------------------------------------------

    raw_item = best_item.get(
        "raw",
        {}
    )

    cash_discount, cash_label = (
        detect_cash_discount(
            raw_item,
            price
        )
    )

    cash_final = None

    if cash_discount > 0:

        cash_final = round(
            max(
                0,
                total_price -
                cash_discount
            ),
            2
        )

    # --------------------------------------------------------
    # Melhor alternativa
    # --------------------------------------------------------

    best_final = total_price

    best_mode = None

    best_saving = 0

    if coupon_final is not None:

        if coupon_discount > best_saving:

            best_saving = (
                coupon_discount
            )

            best_final = (
                coupon_final
            )

            best_mode = (
                "cupom"
            )

    if cash_final is not None:

        if cash_discount > best_saving:

            best_saving = (
                cash_discount
            )

            best_final = (
                cash_final
            )

            best_mode = (
                "pix"
            )

    if ranking_position:

        ranking_text = (
            f"#{ranking_position} "
            "mais vendido"
        )

    else:

        ranking_text = (
            "Em tendência"
        )

    return {

        "product_id":
            product_id,

        "item_id":
            best_item.get(
                "item_id"
            ),

        "title":
            title,

        "image":
            image,

        "category_name":
            candidate[
                "category_name"
            ],

        "category_id":
            candidate.get(
                "category_id"
            ),

        "query":
            candidate.get(
                "query"
            ),

        "ranking_position":
            ranking_position,

        "ranking_text":
            ranking_text,

        "source":
            candidate.get(
                "source"
            ),

        "price":
            price,

        "original_price":
            original_price,

        "discount":
            discount,

        "seller_id":
            best_item.get(
                "seller_id"
            ),

        "free_shipping":
            bool(
                best_item.get(
                    "free_shipping"
                )
            ),

        "shipping_cost":
            shipping_cost,

        "total_price":
            total_price,

        "condition":
            best_item.get(
                "condition"
            ),

        "permalink":
            best_item.get(
                "permalink"
            )
            or data.get(
                "permalink"
            )
            or (
                "https://www.mercadolivre.com.br/p/"
                + product_id
            ),

        "score":
            score,

        "cupom":
            coupon,

        "desconto_cupom":
            coupon_discount,

        "preco_com_cupom":
            coupon_final,

        "cash_discount":
            cash_discount,

        "cash_label":
            cash_label,

        "cash_final":
            cash_final,

        "melhor_forma":
            best_mode,

        "maior_desconto":
            best_saving,

        "preco_final_melhor":
            best_final,

        "affiliate_link":
            ""
    }


# ============================================================
# BUSCA INTELIGENTE
# ============================================================

def run_smart_scan():

    started = time.time()

    print("")
    print("=" * 60)
    print("🔥 CAÇADOR DE OFERTAS")
    print("🔥 PRODUTOS + CUPONS ESPECÍFICOS")
    print("=" * 60)

    # --------------------------------------------------------
    # Primeiro produtos
    # --------------------------------------------------------

    candidates_by_category, diagnostics = (
        collect_ranked_candidates()
    )

    # --------------------------------------------------------
    # Depois cupons
    # --------------------------------------------------------

    print(
        "[CUPONS] Atualizando fontes públicas..."
    )

    coupon_sync = sync_coupons()

    all_products = []

    seen_products = set()

    category_valid = {}

    for category_name, candidates in (
        candidates_by_category.items()
    ):

        valid = []

        for candidate in candidates:

            try:

                result = process_candidate(
                    candidate
                )

                if not result:
                    continue

                product_id = result[
                    "product_id"
                ]

                if product_id in seen_products:
                    continue

                seen_products.add(
                    product_id
                )

                valid.append(
                    result
                )

            except Exception as e:

                print(
                    "[ERRO PRODUTO]",
                    candidate.get(
                        "product_id"
                    ),
                    repr(e)
                )

        valid.sort(
            key=lambda item: (

                -item.get(
                    "score",
                    0
                ),

                -item.get(
                    "maior_desconto",
                    0
                ),

                item.get(
                    "preco_final_melhor",
                    999999
                )
            )
        )

        valid = valid[:10]

        category_valid[
            category_name
        ] = len(
            valid
        )

        all_products.extend(
            valid
        )

    # --------------------------------------------------------
    # Ranking
    # --------------------------------------------------------

    all_products.sort(
        key=lambda item: (

            -item.get(
                "maior_desconto",
                0
            ),

            -item.get(
                "score",
                0
            ),

            item.get(
                "preco_final_melhor",
                999999
            ),

            0
            if item.get(
                "free_shipping"
            )
            else 1
        )
    )

    # --------------------------------------------------------
    # Distribuição entre categorias
    # --------------------------------------------------------

    category_lists = {}

    for product in all_products:

        category = product[
            "category_name"
        ]

        category_lists.setdefault(
            category,
            []
        ).append(
            product
        )

    final_products = []

    category_names = list(
        category_lists.keys()
    )

    index = 0

    while len(
        final_products
    ) < MAX_PRODUCTS:

        added = False

        for category in category_names:

            products = category_lists[
                category
            ]

            if index >= len(
                products
            ):
                continue

            final_products.append(
                products[index]
            )

            added = True

            if len(
                final_products
            ) >= MAX_PRODUCTS:

                break

        if not added:
            break

        index += 1

    # --------------------------------------------------------
    # Estatísticas
    # --------------------------------------------------------

    prices = [
        p["price"]
        for p in final_products
        if p.get("price") is not None
    ]

    products_with_coupon = [
        p
        for p in final_products
        if p.get("cupom")
    ]

    products_with_discount = [
        p
        for p in final_products
        if p.get(
            "discount",
            0
        ) > 0
    ]

    free_shipping = [
        p
        for p in final_products
        if p.get(
            "free_shipping"
        )
    ]

    categories_found = list({
        p["category_name"]
        for p in final_products
    })

    elapsed = round(
        time.time() - started,
        2
    )

    stats = {

        "produtos":
            len(
                final_products
            ),

        "categorias":
            len(
                categories_found
            ),

        "mais vendidos":
            sum(
                1
                for p in final_products
                if p.get(
                    "ranking_position"
                )
            ),

        "com cupom":
            len(
                products_with_coupon
            ),

        "com desconto":
            len(
                products_with_discount
            ),

        "frete grátis":
            len(
                free_shipping
            ),

        "menor preço":
            brl(
                min(
                    prices,
                    default=0
                )
            ),

        "tempo":
            f"{elapsed}s"
    }

    for category in diagnostics:

        diagnostics[
            category
        ][
            "produtos_validos"
        ] = category_valid.get(
            category,
            0
        )

    print(
        "[RESULTADO]",
        len(final_products),
        "produtos"
    )

    print(
        "[COM CUPOM]",
        len(products_with_coupon)
    )

    print(
        "[TEMPO]",
        elapsed,
        "segundos"
    )

    print("=" * 60)

    return {

        "stats":
            stats,

        "produtos":
            final_products,

        "diagnostico":
            diagnostics,

        "coupon_sync":
            coupon_sync,

        "atualizado_em":
            time.strftime(
                "%d/%m/%Y %H:%M:%S"
            )
    }


# ============================================================
# API CAÇAR
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    if not access_token():

        return jsonify({

            "erro":
                "Conecte sua conta do Mercado Livre primeiro."

        }), 401

    try:

        result = run_smart_scan()

        return jsonify(
            json_safe(
                result
            )
        )

    except Exception as e:

        print(
            "[ERRO CAÇA]",
            repr(e)
        )

        return jsonify({

            "erro":
                "Erro durante a busca.",

            "detalhes":
                str(e)

        }), 500


# ============================================================
# BUSCA MANUAL
# ============================================================

@app.route("/api/buscar")
def api_buscar():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return jsonify({
            "erro":
                "Informe uma busca."
        }), 400

    if not access_token():

        return jsonify({
            "erro":
                "Conecte sua conta do Mercado Livre primeiro."
        }), 401

    # Atualiza cupons antes da busca manual.
    sync_coupons()

    products = search_products(
        query
    )

    result = []

    seen = set()

    for item in products:

        product_id = item.get(
            "id"
        )

        if not product_id:
            continue

        if product_id in seen:
            continue

        seen.add(
            product_id
        )

        candidate = {

            "product_id":
                product_id,

            "category_name":
                "🔎 Busca manual",

            "category_id":
                item.get(
                    "category_id"
                ),

            "query":
                query,

            "ranking_position":
                None,

            "source":
                "busca",

            "trend_bonus":
                0
        }

        product_result = process_candidate(
            candidate
        )

        if product_result:

            result.append(
                product_result
            )

    result.sort(
        key=lambda item: (

            -item.get(
                "maior_desconto",
                0
            ),

            -item.get(
                "score",
                0
            ),

            item.get(
                "preco_final_melhor",
                999999
            )
        )
    )

    result = result[:MAX_PRODUCTS]

    return jsonify(
        json_safe({

            "stats": {

                "produtos":
                    len(result),

                "categorias":
                    1,

                "com cupom":
                    sum(
                        1
                        for p in result
                        if p.get(
                            "cupom"
                        )
                    ),

                "com desconto":
                    sum(
                        1
                        for p in result
                        if p.get(
                            "discount",
                            0
                        ) > 0
                    ),

                "frete grátis":
                    sum(
                        1
                        for p in result
                        if p.get(
                            "free_shipping"
                        )
                    ),

                "menor preço":
                    brl(
                        min(
                            [
                                p["price"]
                                for p in result
                                if p.get(
                                    "price"
                                ) is not None
                            ],
                            default=0
                        )
                    )
            },

            "produtos":
                result,

            "atualizado_em":
                time.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
        })
    )


# ============================================================
# CUPONS
# ============================================================

@app.route("/api/cupons")
def api_cupons():

    atualizar = request.args.get(
        "atualizar"
    ) == "1"

    sincronizacao = None

    if atualizar:

        sincronizacao = sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                get_coupons()
            ),

        "fontes":
            COUPON_SOURCE_URLS,

        "sincronizacao":
            json_safe(
                sincronizacao
            )
    })


# ============================================================
# TESTE DE CUPOM PARA PRODUTO
# ============================================================

@app.route("/api/testar-cupom")
def testar_cupom():

    title = request.args.get(
        "title",
        ""
    ).strip()

    price = safe_float(
        request.args.get(
            "price"
        )
    )

    if not title or price is None:

        return jsonify({

            "erro":
                "Informe title e price.",

            "exemplo":
                "/api/testar-cupom?title=Perfume%20Club%20De%20Nuit%20Intense&price=177"
        }), 400

    sync_coupons()

    coupon = find_best_product_coupon(
        title,
        price
    )

    return jsonify(
        json_safe({

            "produto":
                title,

            "preco":
                price,

            "cupom":
                coupon,

            "preco_final":
                round(
                    price -
                    coupon[
                        "desconto_estimado"
                    ],
                    2
                )
                if coupon
                else price
        })
    )


# ============================================================
# GERADOR DE ANÚNCIO
# ============================================================

def generate_ad(product):

    title = product.get(
        "title",
        "Produto"
    )

    price = safe_float(
        product.get(
            "price"
        )
    ) or 0

    original_price = safe_float(
        product.get(
            "original_price"
        )
    )

    discount = safe_float(
        product.get(
            "discount"
        )
    ) or 0

    free_shipping = bool(
        product.get(
            "free_shipping"
        )
    )

    coupon = product.get(
        "cupom"
    )

    coupon_discount = safe_float(
        product.get(
            "desconto_cupom"
        )
    ) or 0

    coupon_final = safe_float(
        product.get(
            "preco_com_cupom"
        )
    )

    cash_discount = safe_float(
        product.get(
            "cash_discount"
        )
    ) or 0

    cash_final = safe_float(
        product.get(
            "cash_final"
        )
    )

    affiliate_link = (
        product.get(
            "affiliate_link"
        )
        or ""
    ).strip()

    # --------------------------------------------------------
    # TÍTULO CHAMATIVO
    # --------------------------------------------------------

    if coupon:

        headline = (
            "🔥 ACHADO DO MELI!"
        )

    elif discount > 0:

        headline = (
            "🔥 OFERTA ENCONTRADA!"
        )

    elif product.get(
        "ranking_position"
    ):

        headline = (
            "🏆 MAIS VENDIDO DO MELI!"
        )

    else:

        headline = (
            "🔥 OFERTA NO MERCADO LIVRE!"
        )

    lines = [

        headline,

        "",

        title,

        ""
    ]

    if original_price and original_price > price:

        lines.append(
            f"De {brl(original_price)}"
        )

    lines.append(
        f"Por {brl(price)}"
    )

    if cash_final is not None and cash_discount > 0:

        lines.append(
            f"🔥 {brl(cash_final)} no Pix"
        )

    elif coupon_final is not None and coupon_discount > 0:

        lines.append(
            f"🔥 {brl(coupon_final)} com cupom"
        )

    if discount > 0:

        lines.append(
            f"🏷️ {discount}% OFF"
        )

    if free_shipping:

        lines.append(
            "🚚 Frete grátis"
        )

    if coupon:

        lines += [

            "",

            f"🎟️ Cupom: {coupon['code']}"
        ]

        if coupon.get(
            "discount_percent"
        ):

            lines.append(
                f"💥 Até {coupon['discount_percent']}% OFF"
            )

        if coupon.get(
            "fixed_discount"
        ):

            lines.append(
                f"💰 {brl(coupon['fixed_discount'])} OFF"
            )

        if coupon_discount > 0:

            lines.append(
                f"💵 Economia estimada: {brl(coupon_discount)}"
            )

    lines += [

        "",

        "👉 Pegar promoção:",

        affiliate_link
        or "COLE SEU LINK DE AFILIADO AQUI",

        "",

        "Agora, no MELI! 🔥"
    ]

    return "\n".join(
        lines
    )


@app.route("/api/gerar-anuncio")
def api_gerar_anuncio():

    product = {

        "title":
            request.args.get(
                "title",
                "Produto"
            ),

        "price":
            safe_float(
                request.args.get(
                    "price"
                )
            ) or 0,

        "original_price":
            safe_float(
                request.args.get(
                    "original_price"
                )
            ),

        "discount":
            safe_float(
                request.args.get(
                    "discount"
                )
            ) or 0,

        "free_shipping":
            request.args.get(
                "shipping_free"
            ) == "1",

        "affiliate_link":
            request.args.get(
                "affiliate_link",
                ""
            ).strip()
    }

    code = request.args.get(
        "cupom",
        ""
    ).strip().upper()

    if code:

        conn = get_db()

        row = conn.execute(
            """
            SELECT *
            FROM cupons
            WHERE code=?
            AND active=1
            """,
            (code,)
        ).fetchone()

        conn.close()

        if row:

            coupon = dict(
                row
            )

            price = product[
                "price"
            ]

            coupon[
                "desconto_estimado"
            ] = calculate_coupon_discount(
                coupon,
                price
            )

            product[
                "cupom"
            ] = coupon

            product[
                "desconto_cupom"
            ] = coupon[
                "desconto_estimado"
            ]

            product[
                "preco_com_cupom"
            ] = round(
                price -
                coupon[
                    "desconto_estimado"
                ],
                2
            )

    return jsonify({

        "anuncio":
            generate_ad(
                product
            )
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/diagnostico")
def diagnostico():

    token = access_token()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(token),

        "categorias":
            len(
                CATEGORIES
            ),

        "cupons":
            len(
                get_coupons()
            )
    }

    if token:

        me, status, _ = ml_get(
            "/users/me"
        )

        result[
            "users_me"
        ] = {

            "status_http":
                status,

            "resposta":
                me
        }

    local = get_tokens()

    if local:

        result[
            "token_local"
        ] = {

            "user_id":
                local.get(
                    "user_id"
                ),

            "nickname":
                local.get(
                    "nickname"
                ),

            "expires_at":
                local.get(
                    "expires_at"
                )
        }

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# TESTE PRODUTO
# ============================================================

@app.route("/mercadolivre/teste-produto")
def teste_produto():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{product_id}"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


@app.route("/mercadolivre/teste-produto-itens")
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status, _ = ml_get(
        f"/products/{product_id}/items"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data
    }), status


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!doctype html>

<html lang="pt-BR">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>
Caçador de Ofertas
</title>

<style>

*{
    box-sizing:border-box;
}

body{
    margin:0;
    background:#f4f5f7;
    font-family:Arial,sans-serif;
    color:#222;
}

.container{
    max-width:1100px;
    margin:auto;
    padding:16px;
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:16px;
    box-shadow:0 5px 20px #0000000d;
}

button,
input{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px;
}

button{
    border:0;
    background:#3483fa;
    color:white;
    cursor:pointer;
    margin-top:7px;
    font-weight:bold;
}

.login{
    background:#ffe600;
    color:#222;
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(auto-fit,minmax(130px,1fr));
    gap:8px;
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px;
}

.stat b{
    display:block;
    font-size:21px;
    margin-top:5px;
}

.product{
    border:2px solid #eee;
    border-radius:15px;
    padding:13px;
    margin-top:12px;
}

.product-head{
    display:flex;
    gap:12px;
    align-items:center;
}

.product-head img{
    width:90px;
    height:90px;
    object-fit:contain;
    border-radius:10px;
    background:#fafafa;
}

.title{
    font-size:17px;
    font-weight:bold;
}

.badge{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    border-radius:7px;
    padding:5px 8px;
    font-size:11px;
    margin:3px 3px 3px 0;
}

.badge-green{
    background:#00a650;
    color:white;
}

.badge-orange{
    background:#fff1dc;
    color:#b86b00;
}

.badge-coupon{
    background:#fff1b8;
    color:#8a6800;
}

.price{
    font-size:23px;
    font-weight:bold;
    margin-top:10px;
}

.old{
    color:#777;
    text-decoration:line-through;
}

.green{
    color:#008a3e;
    font-weight:bold;
    margin-top:4px;
}

.coupon{
    background:#fff8d6;
    border:1px dashed #d5ad00;
    border-radius:11px;
    padding:12px;
    margin-top:10px;
}

.final{
    background:#eaf8ef;
    color:#008a3e;
    padding:9px;
    border-radius:8px;
    font-weight:bold;
    margin-top:7px;
}

.score{
    background:#f5f5f5;
    border-radius:8px;
    padding:7px;
    margin-top:8px;
    font-size:12px;
}

.small{
    font-size:12px;
    color:#666;
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f7f7f7;
    padding:10px;
    border-radius:9px;
    margin-top:8px;
    font-size:13px;
}

.diagnostico{
    font-size:12px;
    background:#fafafa;
    padding:10px;
    border-radius:10px;
    margin-top:8px;
}

.loading{
    padding:15px;
    background:#eef4ff;
    border-radius:10px;
    margin-top:10px;
}

</style>


<script>

function brl(value){

    return (
        "R$ " +
        Number(
            value || 0
        ).toLocaleString(
            "pt-BR",
            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        )
    );
}


function esc(value){

    return String(
        value || ""
    ).replace(
        /[&<>"']/g,
        function(char){

            return {

                "&":"&amp;",
                "<":"&lt;",
                ">":"&gt;",
                '"':"&quot;",
                "'":"&#039;"

            }[char];

        }
    );
}


function attr(value){

    return String(
        value || ""
    ).replace(
        /"/g,
        "&quot;"
    );
}


async function cacar(){

    const status =
        document.getElementById(
            "status"
        );

    status.innerHTML =
        `
        <div class="loading">

            🔄 Buscando os produtos
            que mais vendem...

            <br><br>

            Depois vamos verificar
            os cupons compatíveis.

        </div>
        `;

    try{

        const response =
            await fetch(
                "/api/cacar"
            );

        const data =
            await response.json();

        if(!response.ok){

            status.innerHTML =
                "❌ " +
                esc(
                    data.erro ||
                    "Erro."
                );

            return;
        }

        render(
            data
        );

        status.innerHTML =
            "✅ Atualizado em " +
            esc(
                data.atualizado_em
            );

    }catch(error){

        console.error(
            error
        );

        status.innerHTML =
            "❌ Erro de conexão.";

    }
}


async function buscar(){

    const q =
        document.getElementById(
            "q"
        ).value.trim();

    if(!q){
        return;
    }

    const status =
        document.getElementById(
            "status"
        );

    status.textContent =
        "🔎 Procurando produto...";

    try{

        const response =
            await fetch(
                "/api/buscar?q=" +
                encodeURIComponent(q)
            );

        const data =
            await response.json();

        if(!response.ok){

            status.textContent =
                "❌ " +
                (
                    data.erro ||
                    "Erro"
                );

            return;
        }

        render(
            data
        );

        status.textContent =
            "✅ Busca concluída.";

    }catch(error){

        status.textContent =
            "❌ Erro.";

    }
}


function render(data){

    const stats =
        document.getElementById(
            "stats"
        );

    stats.innerHTML =
        Object.entries(
            data.stats || {}
        )
        .map(
            ([key,value]) => `

                <div class="stat">

                    ${esc(key)}

                    <b>
                        ${esc(value)}
                    </b>

                </div>

            `
        )
        .join("");


    const results =
        document.getElementById(
            "results"
        );

    const products =
        data.produtos || [];


    results.innerHTML =
        products
        .map(
            (
                product,
                index
            ) =>
                renderProduct(
                    product,
                    index
                )
        )
        .join("");


    if(!products.length){

        results.innerHTML =
            `
            <p>
                Nenhum produto encontrado.
            </p>
            `;
    }


    const diag =
        document.getElementById(
            "diagnostico"
        );

    if(data.diagnostico){

        diag.innerHTML =
            Object.entries(
                data.diagnostico
            )
            .map(
                (
                    [category,value]
                ) => `

                    <div>

                        <b>
                            ${esc(category)}
                        </b>

                        <br>

                        Candidatos:
                        ${value.candidatos || 0}

                        · Mais vendidos:
                        ${value.mais_vendidos || 0}

                        · Válidos:
                        ${value.produtos_validos || 0}

                    </div>

                `
            )
            .join("<hr>");

    }else{

        diag.innerHTML =
            "Busca manual.";

    }
}


function renderProduct(
    product,
    index
){

    const id =
        "p" +
        index;


    let badges = "";


    badges +=
        `
        <span class="badge">

            #${index + 1}

        </span>
        `;


    if(
        product.ranking_position
    ){

        badges +=
            `
            <span class="badge badge-green">

                🏆
                ${esc(
                    product.ranking_text
                )}

            </span>
            `;

    }else if(
        product.source ===
        "tendencia"
    ){

        badges +=
            `
            <span class="badge badge-orange">

                📈 Em tendência

            </span>
            `;

    }


    if(product.cupom){

        badges +=
            `
            <span class="badge badge-coupon">

                🎟️ CUPOM

            </span>
            `;

    }


    badges +=
        `
        <span class="badge">

            ${esc(
                product.category_name
            )}

        </span>
        `;


    const image =
        product.image
        ?
        `
        <img
            src="${attr(
                product.image
            )}"
        >
        `
        :
        "";


    let couponHtml = "";


    if(product.cupom){

        const coupon =
            product.cupom;


        couponHtml =
            `
            <div class="coupon">

                <b>
                    🎟️ CUPOM:
                    ${esc(
                        coupon.code
                    )}
                </b>


                ${
                    coupon.discount_percent
                    ?
                    `
                    <div>

                        🔥 Até
                        ${coupon.discount_percent}%
                        OFF

                    </div>
                    `
                    :
                    ""
                }


                ${
                    coupon.fixed_discount
                    ?
                    `
                    <div>

                        💰
                        ${brl(
                            coupon.fixed_discount
                        )}
                        OFF

                    </div>
                    `
                    :
                    ""
                }


                ${
                    coupon.min_purchase
                    ?
                    `
                    <div class="small">

                        Compra mínima:
                        ${brl(
                            coupon.min_purchase
                        )}

                    </div>
                    `
                    :
                    ""
                }


                ${
                    coupon.max_discount
                    ?
                    `
                    <div class="small">

                        Desconto máximo:
                        ${brl(
                            coupon.max_discount
                        )}

                    </div>
                    `
                    :
                    ""
                }


                <div>

                    💵 Economia:
                    <b>
                        ${brl(
                            product.desconto_cupom
                        )}
                    </b>

                </div>


                <div class="final">

                    💥 Estimado com cupom:
                    ${brl(
                        product.preco_com_cupom
                    )}

                </div>


                <div class="small">

                    ⚠️ Confirme o cupom
                    no checkout.

                </div>

            </div>
            `;

    }


    let pixHtml = "";


    if(
        product.cash_discount > 0
        &&
        product.cash_final
    ){

        pixHtml =
            `
            <div
                class="coupon"
                style="
                background:#eefaf2;
                border-color:#70c48d;
                "
            >

                <b>
                    💳 ${esc(
                        product.cash_label ||
                        "Pix"
                    )}
                </b>

                <div>

                    Economia:
                    ${brl(
                        product.cash_discount
                    )}

                </div>

                <div class="final">

                    💥 Final:
                    ${brl(
                        product.cash_final
                    )}

                </div>

                <div class="small">

                    ⚠️ Não somado
                    ao cupom.

                </div>

            </div>
            `;

    }


    return `

    <div class="product">


        <div class="product-head">

            ${image}

            <div>

                ${badges}

                <div class="title">

                    ${esc(
                        product.title
                    )}

                </div>

            </div>

        </div>


        <div class="price">

            ${brl(
                product.price
            )}

        </div>


        ${
            product.original_price
            ?
            `
            <div class="old">

                De:
                ${brl(
                    product.original_price
                )}

            </div>
            `
            :
            ""
        }


        ${
            product.discount > 0
            ?
            `
            <div class="green">

                🔥
                ${product.discount}%
                OFF

            </div>
            `
            :
            ""
        }


        ${
            product.free_shipping
            ?
            `
            <div class="green">

                🚚 Frete grátis

            </div>
            `
            :
            ""
        }


        ${couponHtml}

        ${pixHtml}


        <div class="score">

            ⭐ Pontuação:
            <b>
                ${product.score}
            </b>

            ${
                product.ranking_position
                ?
                `
                · Ranking:
                #${product.ranking_position}
                `
                :
                ""
            }

        </div>


        <div class="small">

            👤 Vendedor:
            ${esc(
                product.seller_id ||
                "N/A"
            )}

        </div>


        <br>


        <a
            href="${attr(
                product.permalink
            )}"
            target="_blank"
        >

            🛒 Ver produto

        </a>


        <input
            id="link_${id}"
            placeholder="Cole seu link de afiliado"
        >


        <button
            onclick='gerarAnuncio(
                "${id}",
                ${JSON.stringify(
                    product
                )}
            )'
        >

            📢 Gerar anúncio

        </button>


        <button
            id="copy_${id}"
            style="
            display:none;
            background:#ff8a00;
            "
            onclick="copiar('${id}')"
        >

            📋 Copiar oferta

        </button>


        <div
            id="ad_${id}"
            class="ad"
        ></div>


    </div>

    `;
}


async function gerarAnuncio(
    id,
    product
){

    const link =
        document.getElementById(
            "link_" + id
        ).value;


    product.affiliate_link =
        link;


    const response =
        await fetch(
            "/api/gerar-anuncio",
            {

                method:
                    "POST",

                headers:{
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify(
                        product
                    )
            }
        );


    let data;


    if(response.ok){

        data =
            await response.json();

    }else{

        const params =
            new URLSearchParams({

                title:
                    product.title,

                price:
                    product.price,

                original_price:
                    product.original_price || "",

                discount:
                    product.discount || 0,

                shipping_free:
                    product.free_shipping
                    ? "1"
                    : "0",

                cupom:
                    product.cupom
                    ?
                    product.cupom.code
                    :
                    "",

                affiliate_link:
                    link
            });


        const fallback =
            await fetch(
                "/api/gerar-anuncio?" +
                params.toString()
            );

        data =
            await fallback.json();
    }


    const ad =
        document.getElementById(
            "ad_" + id
        );


    ad.style.display =
        "block";

    ad.textContent =
        data.anuncio;


    document.getElementById(
        "copy_" + id
    ).style.display =
        "block";
}


async function copiar(id){

    const element =
        document.getElementById(
            "ad_" + id
        );

    const text =
        element.textContent.trim();


    if(!text){
        return;
    }


    try{

        await navigator.clipboard.writeText(
            text
        );

        const button =
            document.getElementById(
                "copy_" + id
            );

        const old =
            button.textContent;

        button.textContent =
            "✅ Copiado!";

        setTimeout(
            function(){

                button.textContent =
                    old;

            },
            1500
        );

    }catch(error){

        alert(
            "Selecione o texto e copie."
        );
    }
}

</script>

</head>


<body>


<div class="container">


<div class="card">

    <h1>
        🛒 Caçador de Ofertas
    </h1>

    <p>

        Produtos de alto giro,
        preços, frete e cupons
        compatíveis.

    </p>


    {% if conectado %}

        <div
            style="
            background:#eaf8ef;
            padding:10px;
            border-radius:9px;
            "
        >

            🟢 Mercado Livre conectado

            {% if nickname %}

                <br>

                <b>
                    {{nickname}}
                </b>

            {% endif %}

        </div>


        <a
            href="/mercadolivre/logout"
        >

            <button>
                Desconectar
            </button>

        </a>

    {% else %}

        <a
            href="/mercadolivre/login"
        >

            <button class="login">

                🔗 Conectar Mercado Livre

            </button>

        </a>

    {% endif %}

</div>


<div class="card">

    <h2>
        🔥 Caçador automático
    </h2>


    <button
        onclick="cacar()"
    >

        🚀 BUSCAR MELHORES OFERTAS

    </button>


    <p
        id="status"
        class="small"
    >

        Primeiro busca produtos
        de alto giro e depois
        verifica os cupons.

    </p>

</div>


<div class="card">

    <h2>
        🔎 Busca manual
    </h2>


    <input
        id="q"
        placeholder="Ex: perfume, air fryer, celular..."
    >


    <button
        onclick="buscar()"
    >

        Procurar

    </button>

</div>


<div class="card">

    <h2>
        📊 Resultado
    </h2>


    <div
        id="stats"
        class="stats"
    ></div>

</div>


<div class="card">

    <h2>
        🏆 Melhores oportunidades
    </h2>


    <p class="small">

        O sistema prioriza produtos
        de alto giro e, quando encontra
        um cupom relacionado ao produto,
        calcula a economia real em R$.

    </p>


    <div id="results">

        <p>
            Faça uma busca para começar.
        </p>

    </div>

</div>


<div class="card">

    <h2>
        🧪 Cobertura
    </h2>


    <div
        id="diagnostico"
        class="diagnostico"
    >

        Aguardando busca...

    </div>

</div>


<div class="card">

    <a
        href="/api/cupons?atualizar=1"
        target="_blank"
    >

        🎟️ Testar atualização dos cupons

    </a>


    <br>
    <br>


    <a
        href="/mercadolivre/diagnostico"
        target="_blank"
    >

        🧪 Diagnóstico Mercado Livre

    </a>

</div>


</div>


</body>

</html>
"""


# ============================================================
# POST GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio",
    methods=["GET", "POST"]
)
def api_gerar_anuncio():

    if request.method == "POST":

        data = request.get_json(
            silent=True
        ) or {}

        product = data

    else:

        product = {

            "title":
                request.args.get(
                    "title",
                    "Produto"
                ),

            "price":
                safe_float(
                    request.args.get(
                        "price"
                    )
                ) or 0,

            "original_price":
                safe_float(
                    request.args.get(
                        "original_price"
                    )
                ),

            "discount":
                safe_float(
                    request.args.get(
                        "discount"
                    )
                ) or 0,

            "free_shipping":
                request.args.get(
                    "shipping_free"
                ) == "1",

            "affiliate_link":
                request.args.get(
                    "affiliate_link",
                    ""
                ).strip()
        }

        code = request.args.get(
            "cupom",
            ""
        ).strip().upper()

        if code:

            conn = get_db()

            row = conn.execute(
                """
                SELECT *
                FROM cupons
                WHERE code=?
                AND active=1
                """,
                (code,)
            ).fetchone()

            conn.close()

            if row:

                coupon = dict(
                    row
                )

                price = product[
                    "price"
                ]

                coupon[
                    "desconto_estimado"
                ] = calculate_coupon_discount(
                    coupon,
                    price
                )

                product[
                    "cupom"
                ] = coupon

                product[
                    "desconto_cupom"
                ] = coupon[
                    "desconto_estimado"
                ]

                product[
                    "preco_com_cupom"
                ] = round(
                    price -
                    coupon[
                        "desconto_estimado"
                    ],
                    2
                )

    return jsonify({

        "anuncio":
            generate_ad(
                product
            )
    })


# ============================================================
# INDEX
# ============================================================

@app.route("/")
def index():

    token = get_tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            token.get(
                "nickname"
            )
            if token
            else None
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

        "mercado_livre_conectado":
            bool(
                access_token()
            ),

        "categorias":
            len(
                CATEGORIES
            ),

        "minimo":
            MIN_PRODUCT_PRICE,

        "estrategia":
            "mais vendidos + cupons especificos",

        "cupons":
            len(
                get_coupons()
            )
    })


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def error_404(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path

    }), 404


@app.errorhandler(500)
def error_500(error):

    return jsonify({

        "erro":
            "Erro interno no servidor.",

        "detalhes":
            str(error)

    }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),

        debug=False
    )