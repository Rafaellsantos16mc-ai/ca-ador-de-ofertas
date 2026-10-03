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
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "cacador-ofertas-secret-key"
)

ML_CLIENT_ID = os.getenv(
    "ML_CLIENT_ID",
    ""
).strip()

ML_CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET",
    ""
).strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN = (
    "https://api.mercadolibre.com/oauth/token"
)

SITE_ID = "MLB"

DB_FILE = "ofertas.db"

MIN_PRODUCT_PRICE = 69.90

MAX_PRODUCTS = 60

PRODUCTS_PER_SEARCH = 20

MAX_CATEGORIES_PER_QUERY = 3

PRODUCTS_PER_CATEGORY = 8


# ============================================================
# CATEGORIAS DE ALTO GIRO
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
    ]
}


# ============================================================
# FONTES DE CUPOM
# ============================================================

COUPON_SOURCE_URLS = [

    "https://www.mercadolivre.com.br/l/descontaco-cupons",

    "https://www.mercadolivre.com.br/l/promocoes"

]


# ============================================================
# BANCO DE DADOS
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

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
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            description TEXT,
            discount_percent REAL,
            fixed_discount REAL DEFAULT 0,
            min_purchase REAL,
            max_discount REAL,
            source_url TEXT,
            conditions TEXT,
            active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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

    if isinstance(
        value,
        (str, int, float, bool)
    ):
        return value

    if isinstance(
        value,
        dict
    ):

        return {
            str(k): json_safe(v)
            for k, v in value.items()
        }

    if isinstance(
        value,
        list
    ):

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

    text = text.translate(
        trans
    )

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


def calc_discount(
    price,
    original
):

    try:

        price = float(price)
        original = float(original)

        if original > price > 0:

            return round(
                (
                    1 -
                    price / original
                ) * 100,
                2
            )

    except Exception:
        pass

    return 0


# ============================================================
# OAUTH PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(
        64
    )

    digest = hashlib.sha256(
        verifier.encode()
    ).digest()

    challenge = (
        base64.urlsafe_b64encode(
            digest
        )
        .decode()
        .rstrip("=")
    )

    return verifier, challenge


def get_tokens():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM oauth_tokens
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def save_tokens(
    data,
    user=None
):

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

        VALUES(
            1,
            ?,
            ?,
            ?,
            ?,
            ?
        )

        ON CONFLICT(id)
        DO UPDATE SET

            access_token =
                excluded.access_token,

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

        data.get(
            "access_token"
        ),

        data.get(
            "refresh_token"
        ),

        int(
            time.time()
        )
        +
        int(
            data.get(
                "expires_in",
                21600
            )
        ),

        (
            str(
                user.get("id")
            )
            if user and user.get("id")
            else old.get(
                "user_id"
            )
        ),

        (
            user.get(
                "nickname"
            )
            if user
            else old.get(
                "nickname"
            )
        )
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
                    data.get(
                        "user_id"
                    ),

                "nickname":
                    data.get(
                        "nickname"
                    )
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

    expires = (
        data.get(
            "expires_at"
        )
        or 0
    )

    if (
        token
        and
        time.time()
        <
        expires - 120
    ):

        return token

    return (
        refresh_token()
        or
        token
    )


# ============================================================
# LOGIN MERCADO LIVRE
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return jsonify({

            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = (
        generate_pkce()
    )

    state = secrets.token_urlsafe(
        32
    )

    session[
        "ml_state"
    ] = state

    session[
        "ml_code_verifier"
    ] = verifier

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
        ML_AUTH
        +
        "?"
        +
        urlencode(params)
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    if request.args.get(
        "error"
    ):

        return jsonify({

            "erro":
                request.args.get(
                    "error"
                ),

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

                ML_API
                +
                "/users/me",

                headers={

                    "Authorization":
                        "Bearer "
                        +
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


@app.route(
    "/mercadolivre/logout"
)
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
# API MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None
):

    token = access_token()

    if not token:

        return {}, 401, {}

    url = (
        path
        if path.startswith(
            "http"
        )
        else ML_API + path
    )

    try:

        response = requests.get(

            url,

            headers={

                "Authorization":
                    "Bearer "
                    +
                    token,

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
            dict(
                response.headers
            )
        )

    except Exception as e:

        return {
            "error":
                str(e)
        }, 500, {}


# ============================================================
# BUSCA DE PRODUTOS
# ============================================================

def search_products(
    query
):

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
                PRODUCTS_PER_SEARCH
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

    if not isinstance(
        results,
        list
    ):
        return []

    return results


def get_product(
    product_id
):

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


def get_product_items(
    product_id
):

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


# ============================================================
# MAIS VENDIDOS
# ============================================================

def discover_categories(
    query
):

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

        if not isinstance(
            item,
            dict
        ):
            continue

        category_id = (
            item.get(
                "category_id"
            )
            or
            item.get(
                "id"
            )
        )

        category_name = (
            item.get(
                "category_name"
            )
            or
            item.get(
                "name"
            )
            or
            category_id
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


def get_best_sellers(
    category_id
):

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
                )
        })

    return result


# ============================================================
# NORMALIZA ITEM
# ============================================================

def normalize_item(
    item
):

    if not isinstance(
        item,
        dict
    ):
        return None

    item_id = (
        item.get(
            "item_id"
        )
        or
        item.get(
            "id"
        )
    )

    if not item_id:
        return None

    price = safe_float(
        item.get(
            "price"
        )
    )

    original_price = safe_float(
        item.get(
            "original_price"
        )
    )

    shipping = (
        item.get(
            "shipping"
        )
        or
        {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    shipping_cost = safe_float(
        shipping.get(
            "cost"
        )
    )

    if free_shipping:
        shipping_cost = 0

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

    price = safe_float(
        buy_box.get(
            "price"
        )
    )

    if price is None:
        return None

    shipping = (
        buy_box.get(
            "shipping"
        )
        or
        {}
    )

    free_shipping = bool(
        shipping.get(
            "free_shipping"
        )
    )

    return {

        "item_id":
            buy_box.get(
                "item_id"
            )
            or
            buy_box.get(
                "id"
            ),

        "seller_id":
            buy_box.get(
                "seller_id"
            ),

        "price":
            price,

        "original_price":
            safe_float(
                buy_box.get(
                    "original_price"
                )
            ),

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
            or
            product_data.get(
                "permalink"
            ),

        "raw":
            buy_box
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    ranking_position,
    price,
    discount,
    free_shipping
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

        elif position <= 20:
            score += 45

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

        elif price <= 299.90:
            score += 17

        elif price <= 499.90:
            score += 12

        elif price <= 999.90:
            score += 8

    return round(
        score,
        2
    )


# ============================================================
# CUPONS
# ============================================================

class CouponParser(
    HTMLParser
):

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.parts = []

    def handle_data(
        self,
        data
    ):

        if not data:
            return

        data = re.sub(
            r"\s+",
            " ",
            data
        ).strip()

        if data:
            self.parts.append(
                data
            )


def html_to_text(
    raw_html
):

    parser = CouponParser()

    try:

        parser.feed(
            raw_html
        )

        text = "\n".join(
            parser.parts
        )

    except Exception:

        text = raw_html

        text = re.sub(
            r"<[^>]+>",
            "\n",
            text
        )

    text = html_lib.unescape(
        text
    )

    text = re.sub(
        r"\n+",
        "\n",
        text
    )

    return text.strip()


def extract_number(
    value
):

    if not value:
        return None

    match = re.search(
        r"(\d+(?:[.,]\d+)?)",
        str(value)
    )

    if not match:
        return None

    try:

        return float(
            match.group(
                1
            ).replace(
                ",",
                "."
            )
        )

    except Exception:

        return None


def parse_coupon_context(
    code,
    context,
    source_url
):

    percent = None

    fixed = None

    minimum = None

    maximum = None

    patterns_percent = [

        r"(\d+(?:[.,]\d+)?)\s*%\s*OFF",

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

    patterns_fixed = [

        r"R\$\s*(\d+(?:[.,]\d+)?)\s*OFF",

        r"R\$\s*(\d+(?:[.,]\d+)?)\s*de\s+desconto",

        r"desconto\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)"

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

    minimum_patterns = [

        r"compra\s+m[ií]nima\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"m[ií]nimo\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)",

        r"a\s+partir\s+de\s*R\$\s*(\d+(?:[.,]\d+)?)"

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
            context[:2000],

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
            context[:2000],

        "context":
            context
    }


def extract_coupon_blocks(
    text
):

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    positions = []

    pattern = re.compile(
        r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{5,29})\b",
        re.I
    )

    for index, line in enumerate(
        lines
    ):

        match = pattern.search(
            line
        )

        if not match:
            continue

        code = match.group(
            1
        ).upper()

        positions.append(
            (
                index,
                code
            )
        )

    result = []

    for pos, (
        index,
        code
    ) in enumerate(
        positions
    ):

        next_index = (

            positions[pos + 1][0]

            if pos + 1 < len(
                positions
            )

            else len(lines)
        )

        start = max(
            0,
            index - 8
        )

        end = min(
            next_index,
            index + 12
        )

        context = " ".join(
            lines[start:end]
        )

        context = re.sub(
            r"\s+",
            " ",
            context
        ).strip()

        result.append(
            (
                code,
                context
            )
        )

    return result


def sync_coupons():

    headers = {

        "User-Agent":
            "Mozilla/5.0",

        "Accept-Language":
            "pt-BR,pt;q=0.9",

        "Accept":
            "text/html,application/xhtml+xml"
    }

    coupons = {}

    for url in COUPON_SOURCE_URLS:

        try:

            response = requests.get(

                url,

                headers=headers,

                timeout=30
            )

            if response.status_code != 200:
                continue

            raw = response.text

            raw = re.sub(
                r"<script.*?</script>",
                " ",
                raw,
                flags=re.I | re.S
            )

            raw = re.sub(
                r"<style.*?</style>",
                " ",
                raw,
                flags=re.I | re.S
            )

            text = html_to_text(
                raw
            )

            blocks = extract_coupon_blocks(
                text
            )

            for code, context in blocks:

                coupon = parse_coupon_context(
                    code,
                    context,
                    url
                )

                if not (
                    coupon.get(
                        "discount_percent"
                    )
                    or
                    coupon.get(
                        "fixed_discount"
                    )
                ):
                    continue

                old = coupons.get(
                    code
                )

                if (
                    old is None
                    or
                    len(
                        coupon["context"]
                    )
                    >
                    len(
                        old["context"]
                    )
                ):

                    coupons[
                        code
                    ] = coupon

        except Exception as e:

            print(
                "[ERRO CUPONS]",
                url,
                repr(e)
            )

    conn = get_db()

    conn.execute(
        "UPDATE cupons SET active=0"
    )

    for coupon in coupons.values():

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
        "[CUPONS]",
        len(coupons),
        "cupons encontrados"
    )

    return {

        "ok":
            True,

        "cupons_encontrados":
            len(coupons)
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
# MATCH PRODUTO X CUPOM
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
    "uma",
    "um",
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
    "mercado",
    "livre"
}


def product_tokens(
    title
):

    tokens = []

    for token in norm(
        title
    ).split():

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


def coupon_match_score(
    title,
    coupon
):

    title_norm = norm(
        title
    )

    context_norm = norm(
        coupon.get(
            "context",
            ""
        )
    )

    if not title_norm or not context_norm:
        return 0

    if title_norm in context_norm:
        return 100

    tokens = product_tokens(
        title
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

    if len(matches) >= 2:
        score += 20

    return score


def coupon_discount(
    coupon,
    price
):

    price = float(
        price
    )

    minimum = safe_float(
        coupon.get(
            "min_purchase"
        )
    )

    if (
        minimum is not None
        and
        price < minimum
    ):
        return 0

    discounts = []

    percent = (
        safe_float(
            coupon.get(
                "discount_percent"
            )
        )
        or
        0
    )

    fixed = (
        safe_float(
            coupon.get(
                "fixed_discount"
            )
        )
        or
        0
    )

    maximum = safe_float(
        coupon.get(
            "max_discount"
        )
    )

    if percent > 0:

        value = (
            price *
            percent /
            100
        )

        if maximum is not None:

            value = min(
                value,
                maximum
            )

        discounts.append(
            value
        )

    if fixed > 0:

        value = fixed

        if maximum is not None:

            value = min(
                value,
                maximum
            )

        discounts.append(
            value
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


def find_best_coupon(
    title,
    price
):

    candidates = []

    for coupon in get_coupons():

        score = coupon_match_score(
            title,
            coupon
        )

        # Não joga cupom genérico
        # em produto aleatório.
        if score < 25:
            continue

        discount = coupon_discount(
            coupon,
            price
        )

        if discount <= 0:
            continue

        item = dict(
            coupon
        )

        item[
            "match_score"
        ] = score

        item[
            "desconto_estimado"
        ] = discount

        candidates.append(
            item
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (

            -x[
                "desconto_estimado"
            ],

            -x[
                "match_score"
            ]
        )
    )

    return candidates[0]


# ============================================================
# PROCESSAMENTO DO PRODUTO
# ============================================================

def process_product(
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
        data.get(
            "name"
        )
        or
        data.get(
            "title"
        )
        or
        product_id
    )

    image = None

    pictures = data.get(
        "pictures"
    ) or []

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
                or
                first.get(
                    "secure_url"
                )
            )

    # --------------------------------------------------------
    # Vendedores / itens
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

    # Fallback para Buy Box.
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

    valid = []

    for item in items:

        price = item.get(
            "price"
        )

        if price is None:
            continue

        if price < MIN_PRODUCT_PRICE:
            continue

        valid.append(
            item
        )

    if not valid:
        return None

    # Menor preço primeiro.
    # Em empate, frete grátis.
    valid.sort(
        key=lambda x: (

            x.get(
                "price"
            )
            or
            999999,

            0
            if x.get(
                "free_shipping"
            )
            else
            1
        )
    )

    item = valid[0]

    price = item.get(
        "price"
    )

    original_price = item.get(
        "original_price"
    )

    discount = calc_discount(
        price,
        original_price
    )

    shipping_cost = item.get(
        "shipping_cost"
    )

    if shipping_cost is None:
        shipping_cost = 0

    total_price = (
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

        item.get(
            "free_shipping"
        )
    )

    # --------------------------------------------------------
    # CUPOM
    # --------------------------------------------------------

    coupon = find_best_coupon(
        title,
        price
    )

    coupon_discount_value = 0

    coupon_final_price = None

    if coupon:

        coupon_discount_value = (
            coupon[
                "desconto_estimado"
            ]
        )

        coupon_final_price = round(

            max(
                0,
                total_price -
                coupon_discount_value
            ),

            2
        )

    # --------------------------------------------------------
    # Resultado
    # --------------------------------------------------------

    return {

        "product_id":
            product_id,

        "item_id":
            item.get(
                "item_id"
            ),

        "seller_id":
            item.get(
                "seller_id"
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
            (
                f"#{ranking_position} mais vendido"
                if ranking_position
                else
                "Em tendência"
            ),

        "price":
            price,

        "original_price":
            original_price,

        "discount":
            discount,

        "free_shipping":
            bool(
                item.get(
                    "free_shipping"
                )
            ),

        "shipping_cost":
            shipping_cost,

        "total_price":
            total_price,

        "permalink":
            item.get(
                "permalink"
            )
            or
            data.get(
                "permalink"
            )
            or
            (
                "https://www.mercadolivre.com.br/p/"
                +
                product_id
            ),

        "score":
            score,

        "cupom":
            coupon,

        "desconto_cupom":
            coupon_discount_value,

        "preco_com_cupom":
            coupon_final_price,

        "maior_desconto":
            coupon_discount_value,

        "affiliate_link":
            ""
    }


# ============================================================
# COLETA DE PRODUTOS
# ============================================================

def collect_candidates():

    categories = {}

    diagnostics = {}

    for category_name, queries in CATEGORIES.items():

        category_items = {}

        search_count = 0

        seller_count = 0

        for query in queries:

            # ------------------------------------------------
            # Primeiro tentamos os mais vendidos
            # ------------------------------------------------

            discovered = discover_categories(
                query
            )

            for discovered_category in discovered[
                :MAX_CATEGORIES_PER_QUERY
            ]:

                category_id = (
                    discovered_category[
                        "category_id"
                    ]
                )

                best_sellers = get_best_sellers(
                    category_id
                )

                for best in best_sellers:

                    product_id = best.get(
                        "id"
                    )

                    if not product_id:
                        continue

                    if product_id in category_items:
                        continue

                    category_items[
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
                            "mais_vendidos"
                    }

                    seller_count += 1

                    if len(
                        category_items
                    ) >= PRODUCTS_PER_CATEGORY:

                        break

                if len(
                    category_items
                ) >= PRODUCTS_PER_CATEGORY:

                    break

            # ------------------------------------------------
            # Complementa com busca de catálogo
            # ------------------------------------------------

            if len(
                category_items
            ) < PRODUCTS_PER_CATEGORY:

                results = search_products(
                    query
                )

                search_count += len(
                    results
                )

                for item in results:

                    product_id = item.get(
                        "id"
                    )

                    if not product_id:
                        continue

                    if product_id in category_items:
                        continue

                    category_items[
                        product_id
                    ] = {

                        "product_id":
                            product_id,

                        "category_name":
                            category_name,

                        "category_id":
                            item.get(
                                "category_id"
                            ),

                        "query":
                            query,

                        "ranking_position":
                            None,

                        "source":
                            "catalogo"
                    }

                    if len(
                        category_items
                    ) >= PRODUCTS_PER_CATEGORY:

                        break

            if len(
                category_items
            ) >= PRODUCTS_PER_CATEGORY:

                break

        categories[
            category_name
        ] = list(
            category_items.values()
        )

        diagnostics[
            category_name
        ] = {

            "candidatos":
                len(
                    category_items
                ),

            "busca_catalogo":
                search_count,

            "mais_vendidos":
                seller_count
        }

    return (
        categories,
        diagnostics
    )


# ============================================================
# CAÇADA PRINCIPAL
# ============================================================

def run_scan():

    started = time.time()

    print(
        ""
    )

    print(
        "=" * 60
    )

    print(
        "🔥 CAÇADOR DE OFERTAS"
    )

    print(
        "🔥 BUSCANDO PRODUTOS DE ALTO GIRO"
    )

    print(
        "=" * 60
    )

    # --------------------------------------------------------
    # PRODUTOS PRIMEIRO
    # --------------------------------------------------------

    candidates_by_category, diagnostics = (
        collect_candidates()
    )

    # --------------------------------------------------------
    # CUPONS DEPOIS
    # --------------------------------------------------------

    print(
        "[CUPONS] Atualizando..."
    )

    coupon_sync = sync_coupons()

    products = []

    global_seen = set()

    category_valid = {}

    for category_name, candidates in (
        candidates_by_category.items()
    ):

        valid = []

        for candidate in candidates:

            try:

                result = process_product(
                    candidate
                )

                if not result:
                    continue

                product_id = result[
                    "product_id"
                ]

                if product_id in global_seen:
                    continue

                global_seen.add(
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
            key=lambda x: (

                -x.get(
                    "score",
                    0
                ),

                -x.get(
                    "maior_desconto",
                    0
                ),

                x.get(
                    "price",
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

        products.extend(
            valid
        )

    # --------------------------------------------------------
    # RANKING GLOBAL
    # --------------------------------------------------------

    products.sort(
        key=lambda x: (

            -x.get(
                "score",
                0
            ),

            -x.get(
                "maior_desconto",
                0
            ),

            x.get(
                "preco_com_cupom"
            )
            if x.get(
                "preco_com_cupom"
            ) is not None
            else
            x.get(
                "price",
                999999
            )
        )
    )

    # --------------------------------------------------------
    # INTERCALA CATEGORIAS
    # --------------------------------------------------------

    by_category = {}

    for product in products:

        category = product[
            "category_name"
        ]

        by_category.setdefault(
            category,
            []
        ).append(
            product
        )

    final_products = []

    category_names = list(
        by_category.keys()
    )

    position = 0

    while len(
        final_products
    ) < MAX_PRODUCTS:

        added = False

        for category in category_names:

            items = by_category[
                category
            ]

            if position >= len(
                items
            ):
                continue

            final_products.append(
                items[position]
            )

            added = True

            if len(
                final_products
            ) >= MAX_PRODUCTS:

                break

        if not added:
            break

        position += 1

    # --------------------------------------------------------
    # ESTATÍSTICAS
    # --------------------------------------------------------

    prices = [

        p["price"]

        for p in final_products

        if p.get(
            "price"
        ) is not None
    ]

    categories_found = list({
        p["category_name"]
        for p in final_products
    })

    with_coupon = [
        p
        for p in final_products
        if p.get(
            "cupom"
        )
    ]

    with_discount = [
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

    elapsed = round(
        time.time()
        -
        started,
        2
    )

    for category in diagnostics:

        diagnostics[
            category
        ][
            "produtos_validos"
        ] = category_valid.get(
            category,
            0
        )

    stats = {

        "categorias":
            len(
                categories_found
            ),

        "com desconto":
            len(
                with_discount
            ),

        "com cupom":
            len(
                with_coupon
            ),

        "frete grátis":
            len(
                free_shipping
            ),

        "mais vendidos":
            sum(
                1
                for p in final_products
                if p.get(
                    "ranking_position"
                )
            ),

        "menor preço":
            brl(
                min(
                    prices,
                    default=0
                )
            ),

        "produtos":
            len(
                final_products
            ),

        "tempo":
            f"{elapsed}s"
    }

    print(
        "[RESULTADO]",
        len(final_products),
        "produtos"
    )

    print(
        "[CUPONS]",
        len(with_coupon)
    )

    print(
        "[TEMPO]",
        elapsed,
        "segundos"
    )

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

@app.route(
    "/api/cacar"
)
def api_cacar():

    if not access_token():

        return jsonify({

            "erro":
                "Conecte sua conta do Mercado Livre primeiro."

        }), 401

    try:

        result = run_scan()

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
# API BUSCA MANUAL
# ============================================================

@app.route(
    "/api/buscar"
)
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

    sync_coupons()

    candidates = search_products(
        query
    )

    results = []

    seen = set()

    for item in candidates:

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
                "busca"
        }

        product = process_product(
            candidate
        )

        if product:

            results.append(
                product
            )

    results.sort(
        key=lambda x: (

            -x.get(
                "score",
                0
            ),

            -x.get(
                "maior_desconto",
                0
            ),

            x.get(
                "price",
                999999
            )
        )
    )

    results = results[
        :MAX_PRODUCTS
    ]

    prices = [
        p["price"]
        for p in results
        if p.get(
            "price"
        ) is not None
    ]

    return jsonify(
        json_safe({

            "stats": {

                "categorias":
                    1,

                "com desconto":
                    sum(
                        1
                        for p in results
                        if p.get(
                            "discount",
                            0
                        ) > 0
                    ),

                "com cupom":
                    sum(
                        1
                        for p in results
                        if p.get(
                            "cupom"
                        )
                    ),

                "frete grátis":
                    sum(
                        1
                        for p in results
                        if p.get(
                            "free_shipping"
                        )
                    ),

                "mais vendidos":
                    0,

                "menor preço":
                    brl(
                        min(
                            prices,
                            default=0
                        )
                    ),

                "produtos":
                    len(
                        results
                    )
            },

            "produtos":
                results,

            "atualizado_em":
                time.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )
        })
    )


# ============================================================
# API CUPONS
# ============================================================

@app.route(
    "/api/cupons"
)
def api_cupons():

    sync = None

    if request.args.get(
        "atualizar"
    ) == "1":

        sync = sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                get_coupons()
            ),

        "fontes":
            COUPON_SOURCE_URLS,

        "sincronizacao":
            json_safe(
                sync
            )
    })


# ============================================================
# TEXTO DO ANÚNCIO
# ============================================================

def generate_ad_text(
    product,
    affiliate_link=""
):

    title = product.get(
        "title",
        "Produto"
    )

    price = safe_float(
        product.get(
            "price"
        )
    ) or 0

    original = safe_float(
        product.get(
            "original_price"
        )
    )

    discount = safe_float(
        product.get(
            "discount"
        )
    ) or 0

    coupon = product.get(
        "cupom"
    )

    coupon_discount_value = (
        safe_float(
            product.get(
                "desconto_cupom"
            )
        )
        or
        0
    )

    coupon_final = safe_float(
        product.get(
            "preco_com_cupom"
        )
    )

    free_shipping = bool(
        product.get(
            "free_shipping"
        )
    )

    if coupon:

        headline = (
            "🔥 OFERTA COM CUPOM!"
        )

    elif discount > 0:

        headline = (
            "🔥 OFERTA ENCONTRADA!"
        )

    elif product.get(
        "ranking_position"
    ):

        headline = (
            "🏆 MAIS VENDIDO!"
        )

    else:

        headline = (
            "🔥 ACHADO DO MELI!"
        )

    lines = [

        headline,

        "",

        title,

        ""
    ]

    if (
        original
        and
        original > price
    ):

        lines.append(
            f"De {brl(original)}"
        )

    lines.append(
        f"Por {brl(price)} 🔥"
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

            "🎟️ Cupom: "
            +
            coupon["code"]
        ]

        if coupon.get(
            "discount_percent"
        ):

            lines.append(
                "💥 Até "
                +
                str(
                    coupon[
                        "discount_percent"
                    ]
                )
                +
                "% OFF"
            )

        if coupon_discount_value > 0:

            lines.append(
                "💰 Economia: "
                +
                brl(
                    coupon_discount_value
                )
            )

        if coupon_final is not None:

            lines.append(
                "🔥 Com cupom: "
                +
                brl(
                    coupon_final
                )
            )

    lines += [

        "",

        "👉 Pegar promoção:",

        affiliate_link
        or
        product.get(
            "permalink",
            ""
        ),

        "",

        "Agora, no MELI! 🔥"
    ]

    return "\n".join(
        lines
    )


# ============================================================
# ÚNICA ROTA DE GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio",
    methods=[
        "GET",
        "POST"
    ]
)
def api_gerar_anuncio():

    if request.method == "POST":

        product = (
            request.get_json(
                silent=True
            )
            or
            {}
        )

        affiliate_link = (
            product.get(
                "affiliate_link"
            )
            or
            ""
        )

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
                )
                or
                0,

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
                )
                or
                0,

            "free_shipping":
                request.args.get(
                    "shipping_free"
                ) == "1"
        }

        affiliate_link = (
            request.args.get(
                "affiliate_link",
                ""
            )
            or
            ""
        ).strip()

        code = (
            request.args.get(
                "cupom",
                ""
            )
            or
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
                (
                    code,
                )
            ).fetchone()

            conn.close()

            if row:

                coupon = dict(
                    row
                )

                price = product[
                    "price"
                ]

                discount_value = (
                    coupon_discount(
                        coupon,
                        price
                    )
                )

                coupon[
                    "desconto_estimado"
                ] = discount_value

                product[
                    "cupom"
                ] = coupon

                product[
                    "desconto_cupom"
                ] = discount_value

                product[
                    "preco_com_cupom"
                ] = round(
                    max(
                        0,
                        price -
                        discount_value
                    ),
                    2
                )

    text = generate_ad_text(
        product,
        affiliate_link
    )

    return jsonify({

        "anuncio":
            text
    })


# ============================================================
# TESTE DE CUPOM
# ============================================================

@app.route(
    "/api/testar-cupom"
)
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
                "Informe title e price."

        }), 400

    sync_coupons()

    coupon = find_best_coupon(
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
                (
                    round(
                        price -
                        coupon[
                            "desconto_estimado"
                        ],
                        2
                    )
                    if coupon
                    else
                    price
                )
        })
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token = access_token()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(
                token
            ),

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

@app.route(
    "/mercadolivre/teste-produto"
)
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


@app.route(
    "/mercadolivre/teste-produto-itens"
)
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
# HEALTH
# ============================================================

@app.route(
    "/health"
)
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

        "produtos_minimo":
            MIN_PRODUCT_PRICE,

        "cupons":
            len(
                get_coupons()
            ),

        "gerador_anuncio":
            "ativo"
    })


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

.badge-coupon{
    background:#fff1b8;
    color:#806000;
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
    margin-top:5px;
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
}

.loading{
    padding:15px;
    background:#eef4ff;
    border-radius:10px;
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


async function cacar(){

    const status =
        document.getElementById(
            "status"
        );

    status.innerHTML =
        `
        <div class="loading">

            🔄 Buscando produtos
            de alto giro...

            <br><br>

            Depois verificando
            cupons compatíveis.

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
            "✅ Busca finalizada.";

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
        "🔎 Procurando...";

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
            "✅ Busca finalizada.";

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


    if(!products.length){

        results.innerHTML =
            `
            <p>
                Nenhum produto encontrado.
            </p>
            `;

    }else{

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

                    <hr>

                `
            )
            .join("");

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


    let badges = `

        <span class="badge">

            #${index + 1}

        </span>

    `;


    if(product.ranking_position){

        badges += `

            <span class="badge badge-green">

                🏆
                ${esc(
                    product.ranking_text
                )}

            </span>

        `;

    }


    if(product.cupom){

        badges += `

            <span class="badge badge-coupon">

                🎟️ CUPOM

            </span>

        `;

    }


    badges += `

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
            src="${esc(
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


        couponHtml = `

            <div class="coupon">

                <b>

                    🎟️ Cupom:
                    ${esc(
                        coupon.code
                    )}

                </b>


                ${
                    coupon.discount_percent
                    ?
                    `
                    <div>

                        🔥
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


                <div>

                    💵 Economia estimada:

                    <b>

                        ${brl(
                            product.desconto_cupom
                        )}

                    </b>

                </div>


                <div class="final">

                    💥 Preço estimado com cupom:

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


            <div class="score">

                ⭐ Pontuação:
                <b>
                    ${product.score}
                </b>

            </div>


            <br>


            <a
                href="${esc(
                    product.permalink
                )}"
                target="_blank"
            >

                🛒 Ver produto

            </a>


            <br>
            <br>


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

    const input =
        document.getElementById(
            "link_" + id
        );

    product.affiliate_link =
        input.value.trim();


    try{

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


        const data =
            await response.json();


        if(!response.ok){

            alert(
                data.erro ||
                "Erro ao gerar anúncio."
            );

            return;
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


    }catch(error){

        alert(
            "Erro ao gerar anúncio."
        );

    }
}


async function copiar(
    id
){

    const ad =
        document.getElementById(
            "ad_" + id
        );

    const text =
        ad.textContent.trim();


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
            "Não foi possível copiar automaticamente."
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
        mais vendidos, preços,
        cupons e frete.

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
                    {{ nickname }}
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

        O sistema busca primeiro
        produtos de alto giro e
        depois verifica cupons
        compatíveis.

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
        🏆 Produtos encontrados
    </h2>


    <p class="small">

        O ranking prioriza produtos
        que aparecem entre os mais
        vendidos, depois considera
        desconto, cupom e frete grátis.

    </p>


    <div id="results">

        <p>
            Faça uma busca para começar.
        </p>

    </div>

</div>


<div class="card">

    <h2>
        🧪 Cobertura da busca
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

        🎟️ Atualizar/testar cupons

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
# PÁGINA PRINCIPAL
# ============================================================

@app.route("/")
def index():

    tokens = get_tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            (
                tokens.get(
                    "nickname"
                )
                if tokens
                else None
            )
    )


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