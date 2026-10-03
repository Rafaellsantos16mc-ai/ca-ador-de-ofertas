import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
from urllib.parse import urlencode

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

COUPONS_URL = (
    "https://www.mercadolivre.com.br/l/promocoes"
)


# ============================================================
# CATÁLOGO
# ============================================================

CATALOG = {

    "📱 Celulares": [
        "smartphone",
        "iphone",
        "samsung galaxy",
        "motorola moto",
        "xiaomi redmi",
        "poco smartphone",
        "realme smartphone",
        "celular 5g",
        "celular samsung",
        "celular motorola"
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume nacional",
        "perfume eau de parfum",
        "perfume original",
        "perfume masculino importado",
        "perfume feminino importado"
    ],

    # ========================================================
    # ACADEMIA
    # SOMENTE ROUPAS, TÊNIS E SUPLEMENTOS
    # ========================================================

    "🏋️ Academia": [
        "roupa academia masculina",
        "roupa academia feminina",
        "camiseta academia",
        "short academia",
        "legging academia",
        "tenis academia",
        "tenis corrida",
        "tenis treino",
        "tenis esportivo",
        "whey protein",
        "creatina",
        "pre treino",
        "suplementos",
        "proteina whey"
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "maleta ferramentas",
        "serra",
        "chave de impacto",
        "furadeira parafusadeira",
        "ferramentas eletricas"
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "camera digital",
        "power bank",
        "fone sem fio",
        "monitor",
        "teclado mecanico"
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "air fryer",
        "ventilador",
        "ferro de passar",
        "aspirador vertical",
        "umidificador",
        "purificador de ar"
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "ferramentas automotivas",
        "tapete automotivo",
        "camera veicular",
        "inflador pneu",
        "politriz automotiva"
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sandwichera",
        "processador alimentos",
        "batedeira",
        "panela de pressão elétrica"
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "relogio masculino",
        "bolsa feminina",
        "oculos de sol",
        "camiseta masculina",
        "vestido feminino",
        "calca jeans"
    ]
}


# ============================================================
# PERFIS DE RELEVÂNCIA
# ============================================================

PROFILES = {

    "celular": {

        "strong": [
            "smartphone",
            "iphone",
            "galaxy",
            "samsung",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "realme",
            "celular"
        ],

        "bad": [
            "capa",
            "capinha",
            "pelicula",
            "suporte",
            "ventosa",
            "carregador",
            "cabo",
            "adaptador",
            "bateria",
            "case",
            "holder",
            "película",
            "suporte veicular"
        ]
    },

    "perfume": {

        "strong": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "parfum",
            "fragrance"
        ],

        "bad": [
            "frasco vazio",
            "decant",
            "amostra",
            "porta perfume",
            "refil vazio"
        ]
    },

    "academia": {

        "strong": [
            "roupa",
            "camiseta",
            "short",
            "legging",
            "tenis",
            "tênis",
            "corrida",
            "treino",
            "esportivo",
            "whey",
            "creatina",
            "pre treino",
            "pré treino",
            "suplemento",
            "suplementos",
            "proteina"
        ],

        "bad": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "barra musculacao",
            "banco musculacao",
            "banco de musculação",
            "caneleira",
            "elastico",
            "elástico",
            "corda naval",
            "kettlebell",
            "step",
            "estacao musculacao",
            "estação de musculação",
            "smith",
            "aparelho musculacao",
            "aparelho de musculação",
            "adesivo",
            "capa",
            "suporte",
            "peca de reposicao"
        ]
    },

    "ferramenta": {

        "strong": [
            "furadeira",
            "parafusadeira",
            "ferramenta",
            "esmerilhadeira",
            "serra",
            "impacto",
            "maleta",
            "kit ferramentas"
        ],

        "bad": [
            "broca avulsa",
            "peca",
            "carvao",
            "bateria avulsa",
            "capa"
        ]
    }
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
            min_purchase REAL,
            max_discount REAL,
            valid_until TEXT,
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

def json_safe(value):

    if value is None:
        return None

    if isinstance(
        value,
        (str, int, float, bool)
    ):
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

    return re.sub(
        r"\s+",
        " ",
        text
    ).strip()


def discount(price, original):

    try:

        p = float(price)
        o = float(original)

        if o > p > 0:

            return round(
                (1 - p / o) * 100,
                2
            )

    except Exception:
        pass

    return 0


def total(price, shipping):

    try:

        return round(
            float(price)
            +
            float(shipping or 0),
            2
        )

    except Exception:

        return None


def model_name(title):

    if not title:
        return "Produto"

    title = re.sub(
        r"\b(novo|original|oficial|promoção|promocao|frete grátis|frete gratis)\b",
        "",
        str(title),
        flags=re.I
    )

    return re.sub(
        r"\s+",
        " ",
        title
    ).strip()


def specs(title):

    if not title:
        return []

    title = str(title)

    result = []

    patterns = [

        r"\b\d+(?:GB|TB)\b",

        r"\b\d+\s*GB\s*(?:RAM|MEMORIA|DE MEMORIA)\b",

        r"\b(?:2G|3G|4G|5G)\b"

    ]

    for pattern in patterns:

        for value in re.findall(
            pattern,
            title,
            re.I
        ):

            value = re.sub(
                r"\s+",
                " ",
                value.upper()
            )

            if value not in result:
                result.append(value)

    for label, pattern in [
        (
            "Dual SIM",
            r"dual\s*sim"
        ),
        (
            "NFC",
            r"\bnfc\b"
        )
    ]:

        if re.search(
            pattern,
            title,
            re.I
        ):

            if label not in result:
                result.append(label)

    return result


# ============================================================
# DETECÇÃO DE PERFIL
# ============================================================

def profile_for(query):

    text = norm(query)

    if any(
        x in text
        for x in [
            "iphone",
            "samsung",
            "galaxy",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "smartphone",
            "celular"
        ]
    ):

        return "celular"

    if "perfume" in text:

        return "perfume"

    if any(
        x in text
        for x in [
            "academia",
            "roupa academia",
            "camiseta academia",
            "short academia",
            "legging academia",
            "tenis academia",
            "tenis corrida",
            "tenis treino",
            "tenis esportivo",
            "whey",
            "creatina",
            "pre treino",
            "suplemento",
            "proteina"
        ]
    ):

        return "academia"

    if any(
        x in text
        for x in [
            "furadeira",
            "parafusadeira",
            "ferramenta",
            "esmerilhadeira",
            "serra"
        ]
    ):

        return "ferramenta"

    return None


# ============================================================
# RELEVÂNCIA
# ============================================================

def relevance(title, query):

    text = norm(title)

    query_text = norm(query)

    profile = profile_for(
        query
    )

    score = 0

    profile_data = PROFILES.get(
        profile,
        {}
    )

    for word in profile_data.get(
        "strong",
        []
    ):

        if norm(word) in text:

            score += 35

    for word in query_text.split():

        if (
            len(word) >= 3
            and
            word in text
        ):

            score += 10

    for word in profile_data.get(
        "bad",
        []
    ):

        if norm(word) in text:

            score -= 100

    return score


def product_allowed(
    title,
    query
):

    score = relevance(
        title,
        query
    )

    profile = profile_for(
        query
    )

    if profile == "academia":

        text = norm(title)

        bad = PROFILES[
            "academia"
        ]["bad"]

        for word in bad:

            if norm(word) in text:

                return False

        strong = PROFILES[
            "academia"
        ]["strong"]

        if not any(
            norm(word) in text
            for word in strong
        ):

            return False

    return score >= 15


# ============================================================
# PKCE
# ============================================================

def pkce():

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


# ============================================================
# TOKENS
# ============================================================

def tokens():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM oauth_tokens
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(
    data,
    user=None
):

    old = tokens() or {}

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

        int(time.time())
        +
        int(
            data.get(
                "expires_in",
                21600
            )
        ),

        str(
            user.get("id")
        )
        if user and user.get("id")
        else old.get("user_id"),

        user.get("nickname")
        if user
        else old.get("nickname")
    ))

    conn.commit()

    conn.close()


def refresh():

    token_data = tokens()

    if (
        not token_data
        or
        not token_data.get(
            "refresh_token"
        )
    ):

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
                    token_data[
                        "refresh_token"
                    ]
            },

            timeout=30
        )

        if response.status_code != 200:

            return None

        data = response.json()

        save_tokens(
            data,
            {
                "id":
                    token_data.get(
                        "user_id"
                    ),

                "nickname":
                    token_data.get(
                        "nickname"
                    )
            }
        )

        return data.get(
            "access_token"
        )

    except Exception:

        return None


def access_token():

    data = tokens()

    if not data:

        return None

    if (
        data.get("access_token")
        and
        time.time()
        <
        (
            data.get(
                "expires_at"
            )
            or 0
        )
        - 120
    ):

        return data[
            "access_token"
        ]

    return (
        refresh()
        or
        data.get(
            "access_token"
        )
    )


# ============================================================
# REQUEST MERCADO LIVRE
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
                    "application/json"
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

    except requests.RequestException as error:

        return {
            "error":
                str(error)
        }, 500, {}


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def ml_login():

    if not ML_CLIENT_ID:

        return jsonify({

            "erro":
                "ML_CLIENT_ID não configurado."
        }), 500

    verifier, challenge = pkce()

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
def ml_callback():

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

    if (
        not code
        or
        state != session.get(
            "ml_state"
        )
    ):

        return jsonify({

            "erro":
                "Código ou state inválido."
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

        data = response.json()

        user = None

        if data.get(
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
                        data[
                            "access_token"
                        ]
                },

                timeout=30
            )

            if me.status_code == 200:

                user = me.json()

        save_tokens(
            data,
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

    except Exception as error:

        return jsonify({
            "erro":
                str(error)
        }), 500


@app.route(
    "/mercadolivre/logout"
)
def ml_logout():

    conn = get_db()

    conn.execute(
        """
        DELETE FROM oauth_tokens
        WHERE id=1
        """
    )

    conn.commit()

    conn.close()

    session.clear()

    return redirect("/")


# ============================================================
# CATEGORIAS
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

    if (
        status != 200
        or
        not isinstance(
            data,
            list
        )
    ):

        return []

    result = []

    for item in data:

        category_id = (
            item.get(
                "category_id"
            )
            or
            item.get("id")
        )

        category_name = (
            item.get(
                "category_name"
            )
            or
            item.get("name")
            or
            category_id
        )

        if category_id:

            result.append({

                "category_id":
                    category_id,

                "category_name":
                    category_name
            })

    return result


# ============================================================
# HIGHLIGHTS
# ============================================================

def highlights(
    category_id
):

    data, status, _ = ml_get(

        f"/highlights/{SITE_ID}/category/{category_id}"
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

        return data.get(
            "content",
            data.get(
                "results",
                []
            )
        )

    return []


# ============================================================
# PRODUTO
# ============================================================

def product(
    product_id
):

    data, status, _ = ml_get(

        f"/products/{product_id}"
    )

    if (
        status == 200
        and
        isinstance(
            data,
            dict
        )
    ):

        return data

    return None


# ============================================================
# ITENS / VENDEDORES
# ============================================================

def product_items(
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

        return data.get(
            "results",
            []
        )

    return []


def normalize_item(
    item
):

    if (
        not isinstance(
            item,
            dict
        )
        or
        not item.get(
            "item_id"
        )
    ):

        return None

    shipping = (
        item.get(
            "shipping"
        )
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
        else
        shipping.get(
            "cost"
        )
    )

    return {

        "item_id":
            item.get(
                "item_id"
            ),

        "seller_id":
            item.get(
                "seller_id"
            ),

        "price":
            item.get(
                "price"
            ),

        "original_price":
            item.get(
                "original_price"
            ),

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
            )
    }


# ============================================================
# CUPONS
# ============================================================

def number(value):

    if value is None:

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


def sync_coupons():

    try:

        response = requests.get(

            COUPONS_URL,

            headers={
                "User-Agent":
                    "Mozilla/5.0"
            },

            timeout=30
        )

        if response.status_code != 200:

            return {
                "ok": False,
                "status":
                    response.status_code
            }

        text = html_lib.unescape(
            response.text
        )

        text = re.sub(
            r"<script.*?</script>|<style.*?</style>|<[^>]+>",
            " ",
            text,
            flags=re.I | re.S
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        codes = list(
            dict.fromkeys(

                x.upper()

                for x in re.findall(

                    r"(?:Cupom\s+)([A-Z0-9]{5,20})",

                    text,

                    re.I
                )
            )
        )

        conn = get_db()

        found = 0

        for code in codes:

            pos = text.lower().find(
                code.lower()
            )

            excerpt = text[
                max(
                    0,
                    pos - 100
                ):
                min(
                    len(text),
                    pos + 900
                )
            ]

            pct_match = re.search(

                r"até\s+(\d+(?:[.,]\d+)?)\s*%",

                excerpt,

                re.I
            )

            pct = (

                number(
                    pct_match.group(1)
                )

                if pct_match

                else None
            )

            min_match = re.search(

                r"(?:a partir de|partir de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",

                excerpt,

                re.I
            )

            minimum = (

                number(
                    min_match.group(1)
                )

                if min_match

                else None
            )

            max_match = re.search(

                r"(?:máximo de|maximo de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",

                excerpt,

                re.I
            )

            maximum = (

                number(
                    max_match.group(1)
                )

                if max_match

                else None
            )

            if (
                pct is None
                and
                maximum is None
            ):

                continue

            conn.execute("""

                INSERT INTO cupons(

                    code,
                    description,
                    discount_percent,
                    min_purchase,
                    max_discount,
                    source_url,
                    conditions,
                    active,
                    updated_at

                )

                VALUES(

                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    1,
                    CURRENT_TIMESTAMP

                )

                ON CONFLICT(code)

                DO UPDATE SET

                    description =
                        excluded.description,

                    discount_percent =
                        excluded.discount_percent,

                    min_purchase =
                        excluded.min_purchase,

                    max_discount =
                        excluded.max_discount,

                    conditions =
                        excluded.conditions,

                    active =
                        1,

                    updated_at =
                        CURRENT_TIMESTAMP

            """, (

                code,
                excerpt,
                pct,
                minimum,
                maximum,
                COUPONS_URL,
                excerpt
            ))

            found += 1

        conn.commit()

        conn.close()

        return {

            "ok":
                True,

            "cupons_encontrados":
                found
        }

    except Exception as error:

        return {

            "ok":
                False,

            "erro":
                str(error)
        }


def coupons():

    conn = get_db()

    rows = conn.execute("""

        SELECT *

        FROM cupons

        WHERE active=1

        ORDER BY
            discount_percent DESC,
            max_discount DESC

    """).fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


def coupon_discount(
    coupon,
    price
):

    try:

        price = float(
            price
        )

        minimum = coupon.get(
            "min_purchase"
        )

        if (
            minimum
            and
            price < float(
                minimum
            )
        ):

            return 0

        percent = float(
            coupon.get(
                "discount_percent"
            )
            or 0
        )

        discount_value = (
            price
            *
            percent
            /
            100
        )

        maximum = coupon.get(
            "max_discount"
        )

        if maximum:

            discount_value = min(
                discount_value,
                float(maximum)
            )

        return round(
            max(
                0,
                discount_value
            ),
            2
        )

    except Exception:

        return 0


def best_coupon(
    price
):

    choices = []

    for coupon in coupons():

        discount_value = coupon_discount(
            coupon,
            price
        )

        if discount_value > 0:

            item = dict(
                coupon
            )

            item[
                "desconto_estimado"
            ] = discount_value

            choices.append(
                item
            )

    if not choices:

        return None

    return max(

        choices,

        key=lambda x:
            x[
                "desconto_estimado"
            ]
    )


# ============================================================
# SCAN DE UMA QUERY
# ============================================================

def scan_queries(
    queries,
    min_discount=0,
    category_name=None
):

    products = {}

    queries = [
        str(q).strip()
        for q in queries
        if str(q).strip()
    ]

    # --------------------------------------------------------
    # DESCOBRIR PRODUTOS
    # --------------------------------------------------------

    for query in queries:

        categories = discover_categories(
            query
        )

        for category in categories[:4]:

            category_id = category[
                "category_id"
            ]

            category_label = (
                category_name
                or
                category[
                    "category_name"
                ]
            )

            items = highlights(
                category_id
            )

            for highlight in items:

                if highlight.get(
                    "type"
                ) != "PRODUCT":

                    continue

                product_id = (
                    highlight.get(
                        "id"
                    )
                    or
                    highlight.get(
                        "product_id"
                    )
                )

                if not product_id:

                    continue

                products.setdefault(

                    product_id,

                    {

                        "category_id":
                            category_id,

                        "category_name":
                            category_label,

                        "query":
                            query
                    }
                )

    offers = []

    seen_items = set()

    # --------------------------------------------------------
    # PRODUTOS / VENDEDORES
    # --------------------------------------------------------

    for product_id, base in products.items():

        product_data = product(
            product_id
        )

        if not product_data:

            continue

        title = (
            product_data.get(
                "name"
            )
            or
            product_data.get(
                "title"
            )
            or
            product_id
        )

        # FILTRO DE RELEVÂNCIA
        if not product_allowed(
            title,
            base["query"]
        ):

            continue

        score = relevance(
            title,
            base["query"]
        )

        pictures = (
            product_data.get(
                "pictures"
            )
            or []
        )

        image = None

        if pictures:

            first_picture = pictures[0]

            if isinstance(
                first_picture,
                dict
            ):

                image = first_picture.get(
                    "url"
                )

        raw_items = product_items(
            product_id
        )

        # ----------------------------------------------------
        # EVITA EXPLODIR A QUANTIDADE DE VENDEDORES
        # ----------------------------------------------------

        raw_items = raw_items[:20]

        for raw_item in raw_items:

            item = normalize_item(
                raw_item
            )

            if not item:

                continue

            item_id = item[
                "item_id"
            ]

            if item_id in seen_items:

                continue

            seen_items.add(
                item_id
            )

            try:

                price = float(
                    item[
                        "price"
                    ]
                )

            except Exception:

                continue

            original_price = item.get(
                "original_price"
            )

            try:

                if original_price is not None:

                    original_price = float(
                        original_price
                    )

            except Exception:

                original_price = None

            discount_value = discount(
                price,
                original_price
            )

            if (
                discount_value
                <
                float(
                    min_discount
                    or 0
                )
            ):

                continue

            shipping_cost = item.get(
                "shipping_cost"
            )

            total_price = (

                total(
                    price,
                    shipping_cost
                )

                if
                shipping_cost is not None

                else
                price
            )

            coupon = best_coupon(
                price
            )

            coupon_discount_value = (

                coupon[
                    "desconto_estimado"
                ]

                if coupon

                else 0
            )

            estimated_final_price = round(

                max(
                    0,
                    total_price
                    -
                    coupon_discount_value
                ),

                2
            )

            offers.append({

                "product_id":
                    product_id,

                "item_id":
                    item_id,

                "title":
                    title,

                "modelo_nome":
                    model_name(
                        title
                    ),

                "especificacoes":
                    specs(
                        title
                    ),

                "image":
                    image,

                "permalink":
                    item.get(
                        "permalink"
                    )
                    or
                    product_data.get(
                        "permalink"
                    )
                    or
                    f"https://www.mercadolivre.com.br/p/{product_id}",

                "price":
                    price,

                "original_price":
                    original_price,

                "discount":
                    discount_value,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "condition":
                    item.get(
                        "condition"
                    ),

                "free_shipping":
                    item.get(
                        "free_shipping"
                    ),

                "shipping_cost":
                    shipping_cost,

                "shipping_known":
                    shipping_cost is not None,

                "total_price":
                    total_price,

                "relevance_score":
                    score,

                "cupom":
                    coupon,

                "preco_com_cupom":
                    estimated_final_price
                    if coupon
                    else None,

                "affiliate_link":
                    "",

                "extra_earnings":
                    0,

                "category_id":
                    base.get(
                        "category_id"
                    ),

                "category_name":
                    base.get(
                        "category_name"
                    )
            })

    # --------------------------------------------------------
    # AGRUPAR VENDEDORES PELO PRODUTO
    # --------------------------------------------------------

    groups = {}

    for offer in offers:

        product_id = offer[
            "product_id"
        ]

        if product_id not in groups:

            groups[
                product_id
            ] = {

                "product_id":
                    product_id,

                "title":
                    offer[
                        "title"
                    ],

                "modelo_nome":
                    offer[
                        "modelo_nome"
                    ],

                "especificacoes":
                    offer[
                        "especificacoes"
                    ],

                "image":
                    offer[
                        "image"
                    ],

                "category_name":
                    offer.get(
                        "category_name",
                        ""
                    ),

                "ofertas":
                    []
            }

        groups[
            product_id
        ][
            "ofertas"
        ].append(
            offer
        )

    models = []

    for group in groups.values():

        group[
            "ofertas"
        ].sort(

            key=lambda x:

                (

                    x.get(
                        "preco_com_cupom"
                    )

                    if
                    x.get(
                        "preco_com_cupom"
                    )
                    is not None

                    else
                    x.get(
                        "total_price"
                    )
                    or
                    999999999

                )
        )

        if group[
            "ofertas"
        ]:

            best = group[
                "ofertas"
            ][0]

            for offer in group[
                "ofertas"
            ]:

                offer[
                    "menor_preco_modelo"
                ] = (
                    offer
                    is best
                )

        models.append(
            group
        )

    # --------------------------------------------------------
    # RESULTADO
    # --------------------------------------------------------

    return {

        "stats": {

            "produtos_catalogo":
                len(products),

            "modelos":
                len(models),

            "vendedores":
                len({
                    str(
                        x.get(
                            "seller_id"
                        )
                    )

                    for x in offers

                    if x.get(
                        "seller_id"
                    )
                }),

            "anuncios":
                len(offers),

            "com_desconto":
                sum(
                    1
                    for x in offers
                    if x.get(
                        "discount",
                        0
                    ) > 0
                ),

            "com_cupom":
                sum(
                    1
                    for x in offers
                    if x.get(
                        "cupom"
                    )
                ),

            "frete_gratis":
                sum(
                    1
                    for x in offers
                    if x.get(
                        "free_shipping"
                    )
                )
        },

        "modelos":
            models,

        "ofertas":
            offers
    }


# ============================================================
# NOVO CAÇADOR AUTOMÁTICO
# ============================================================

def auto_scan(
    category=None,
    min_discount=0
):

    # ========================================================
    # UMA CATEGORIA
    # ========================================================

    if category:

        queries = CATALOG.get(
            category,
            []
        )

        return scan_queries(

            queries,

            min_discount,

            category
        )

    # ========================================================
    # TODAS AS CATEGORIAS
    #
    # Agora não fica limitado aos primeiros 18 termos.
    # Ele percorre TODAS as categorias.
    # ========================================================

    all_models = {}

    all_offers = []

    global_seen_items = set()

    category_stats = {}

    for category_name, queries in CATALOG.items():

        try:

            result = scan_queries(

                queries,

                min_discount,

                category_name
            )

        except Exception as error:

            category_stats[
                category_name
            ] = {

                "erro":
                    str(error),

                "modelos":
                    0,

                "ofertas":
                    0
            }

            continue

        category_stats[
            category_name
        ] = {

            "modelos":
                result[
                    "stats"
                ][
                    "modelos"
                ],

            "ofertas":
                result[
                    "stats"
                ][
                    "anuncios"
                ],

            "descontos":
                result[
                    "stats"
                ][
                    "com_desconto"
                ],

            "cupons":
                result[
                    "stats"
                ][
                    "com_cupom"
                ]
        }

        # ----------------------------------------------------
        # JUNTAR MODELOS
        # ----------------------------------------------------

        for model in result.get(
            "modelos",
            []
        ):

            product_id = model.get(
                "product_id"
            )

            if not product_id:

                continue

            if product_id not in all_models:

                all_models[
                    product_id
                ] = {

                    "product_id":
                        product_id,

                    "title":
                        model.get(
                            "title"
                        ),

                    "modelo_nome":
                        model.get(
                            "modelo_nome"
                        ),

                    "especificacoes":
                        model.get(
                            "especificacoes",
                            []
                        ),

                    "image":
                        model.get(
                            "image"
                        ),

                    "category_name":
                        category_name,

                    "ofertas":
                        []
                }

            # ------------------------------------------------
            # JUNTAR OFERTAS
            # ------------------------------------------------

            for offer in model.get(
                "ofertas",
                []
            ):

                item_id = offer.get(
                    "item_id"
                )

                if (
                    not item_id
                    or
                    item_id in global_seen_items
                ):

                    continue

                global_seen_items.add(
                    item_id
                )

                offer[
                    "category_name"
                ] = category_name

                all_models[
                    product_id
                ][
                    "ofertas"
                ].append(
                    offer
                )

                all_offers.append(
                    offer
                )

    # ========================================================
    # ORDENAR CADA MODELO PELO MENOR PREÇO
    # ========================================================

    for model in all_models.values():

        model[
            "ofertas"
        ].sort(

            key=lambda x:

                (

                    x.get(
                        "preco_com_cupom"
                    )

                    if
                    x.get(
                        "preco_com_cupom"
                    )
                    is not None

                    else
                    x.get(
                        "total_price"
                    )
                    or
                    999999999

                )
        )

        if model[
            "ofertas"
        ]:

            for index, offer in enumerate(
                model[
                    "ofertas"
                ]
            ):

                offer[
                    "menor_preco_modelo"
                ] = (
                    index == 0
                )

    # ========================================================
    # DEDUPLICAÇÃO INTELIGENTE
    #
    # Evita que exatamente o mesmo título fique aparecendo
    # várias vezes em categorias diferentes.
    # ========================================================

    models = list(
        all_models.values()
    )

    unique_models = []

    seen_signatures = set()

    for model in models:

        title = norm(
            model.get(
                "modelo_nome"
            )
        )

        product_id = model.get(
            "product_id"
        )

        # Usa produto como principal identificação.
        # O título serve como proteção extra.
        signature = (
            product_id,
            title
        )

        if signature in seen_signatures:

            continue

        seen_signatures.add(
            signature
        )

        unique_models.append(
            model
        )

    # ========================================================
    # RANKING GLOBAL
    #
    # Prioriza:
    # 1. Desconto
    # 2. Cupom
    # 3. Frete grátis
    # 4. Menor preço
    # ========================================================

    def model_score(model):

        offers = model.get(
            "ofertas",
            []
        )

        if not offers:

            return -999999

        best = offers[0]

        discount_value = float(
            best.get(
                "discount",
                0
            )
            or 0
        )

        coupon_bonus = 15 if best.get(
            "cupom"
        ) else 0

        shipping_bonus = 10 if best.get(
            "free_shipping"
        ) else 0

        relevance_value = float(
            best.get(
                "relevance_score",
                0
            )
            or 0
        )

        price = float(
            best.get(
                "preco_com_cupom"
            )
            if
            best.get(
                "preco_com_cupom"
            )
            is not None

            else
            best.get(
                "total_price"
            )
            or
            999999
        )

        price_bonus = max(
            0,
            30 -
            min(
                price / 100,
                30
            )
        )

        return (
            discount_value * 5
            +
            coupon_bonus
            +
            shipping_bonus
            +
            relevance_value
            +
            price_bonus
        )

    unique_models.sort(
        key=model_score,
        reverse=True
    )

    # ========================================================
    # LIMITA RESULTADO FINAL
    # ========================================================

    unique_models = unique_models[
        :80
    ]

    final_offers = []

    for model in unique_models:

        final_offers.extend(
            model.get(
                "ofertas",
                []
            )
        )

    # ========================================================
    # ESTATÍSTICAS
    # ========================================================

    sellers = {

        str(
            offer.get(
                "seller_id"
            )
        )

        for offer in final_offers

        if offer.get(
            "seller_id"
        )
    }

    stats = {

        "categorias":
            len(CATALOG),

        "categorias_processadas":
            len(category_stats),

        "produtos_catalogo":
            len(all_models),

        "modelos":
            len(unique_models),

        "vendedores":
            len(sellers),

        "anuncios":
            len(final_offers),

        "com_desconto":
            sum(

                1

                for offer in final_offers

                if offer.get(
                    "discount",
                    0
                ) > 0
            ),

        "com_cupom":
            sum(

                1

                for offer in final_offers

                if offer.get(
                    "cupom"
                )
            ),

        "frete_gratis":
            sum(

                1

                for offer in final_offers

                if offer.get(
                    "free_shipping"
                )
            )
    }

    return {

        "stats":
            stats,

        "categorias":
            category_stats,

        "modelos":
            unique_models,

        "ofertas":
            final_offers
    }


# ============================================================
# GERADOR DE ANÚNCIO
# ============================================================

def ad_text(
    offer,
    affiliate=""
):

    lines = [

        "🔥 OFERTA ENCONTRADA!",

        "",

        f"🛍️ {offer.get('title', 'Produto')}"
    ]

    if offer.get(
        "original_price"
    ):

        lines.append(

            "💸 De: "
            +
            brl(
                offer[
                    "original_price"
                ]
            )
        )

    lines.append(

        "🔥 Por: "
        +
        brl(
            offer.get(
                "price",
                0
            )
        )
    )

    if offer.get(
        "discount",
        0
    ) > 0:

        lines.append(

            f"🏷️ {offer['discount']}% OFF"
        )

    if offer.get(
        "free_shipping"
    ):

        lines.append(
            "🚚 Frete grátis"
        )

    if offer.get(
        "cupom"
    ):

        coupon = offer[
            "cupom"
        ]

        lines.extend([

            "",

            "🎟️ CUPOM: "
            +
            str(
                coupon.get(
                    "code"
                )
            )
        ])

        if coupon.get(
            "discount_percent"
        ):

            lines.append(

                "🔥 Até "
                +
                str(
                    coupon[
                        "discount_percent"
                    ]
                )
                +
                "% OFF"
            )

        if coupon.get(
            "max_discount"
        ):

            lines.append(

                "💰 Até "
                +
                brl(
                    coupon[
                        "max_discount"
                    ]
                )
                +
                " OFF"
            )

        if coupon.get(
            "min_purchase"
        ):

            lines.append(

                "🛒 Compra mínima: "
                +
                brl(
                    coupon[
                        "min_purchase"
                    ]
                )
            )

        lines.extend([

            "",

            "💥 PREÇO ESTIMADO COM CUPOM: "
            +
            brl(
                offer[
                    "preco_com_cupom"
                ]
            )
        ])

    lines.extend([

        "",

        "⚠️ Confira as condições e confirme o cupom no checkout.",

        "",

        "🛒 PEGAR OFERTA:",

        affiliate
        or
        "Cole aqui seu link de afiliado."
    ])

    return "\n".join(
        lines
    )


# ============================================================
# API BUSCAR
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

    sync_coupons()

    result = scan_queries(

        [query],

        request.args.get(
            "desconto",
            0
        )
    )

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# API CAÇAR
# ============================================================

@app.route(
    "/api/cacar"
)
def api_cacar():

    sync_coupons()

    category = request.args.get(
        "categoria",
        ""
    ).strip()

    result = auto_scan(

        category
        if category
        else None,

        request.args.get(
            "desconto",
            0
        )
    )

    return jsonify(
        json_safe(
            result
        )
    )


# ============================================================
# API CUPONS
# ============================================================

@app.route(
    "/api/cupons"
)
def api_cupons():

    if request.args.get(
        "atualizar"
    ) == "1":

        sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                coupons()
            ),

        "fonte":
            COUPONS_URL
    })


# ============================================================
# API ANÚNCIO
# ============================================================

@app.route(
    "/api/gerar-anuncio"
)
def api_anuncio():

    offer = {

        "title":
            request.args.get(
                "title",
                "Produto"
            ),

        "price":
            request.args.get(
                "price",
                0
            ),

        "original_price":
            request.args.get(
                "original_price"
            ),

        "discount":
            float(
                request.args.get(
                    "discount",
                    0
                )
                or 0
            ),

        "free_shipping":
            request.args.get(
                "shipping_free"
            ) == "1",

        "cupom":
            None,

        "preco_com_cupom":
            None
    }

    coupon_code = request.args.get(
        "cupom",
        ""
    ).strip().upper()

    if coupon_code:

        conn = get_db()

        row = conn.execute(

            """
            SELECT *
            FROM cupons
            WHERE code=?
            AND active=1
            """,

            (
                coupon_code,
            )
        ).fetchone()

        conn.close()

        if row:

            coupon = dict(
                row
            )

            coupon[
                "desconto_estimado"
            ] = coupon_discount(

                coupon,

                float(
                    offer[
                        "price"
                    ]
                )
            )

            offer[
                "cupom"
            ] = coupon

            offer[
                "preco_com_cupom"
            ] = max(

                0,

                float(
                    offer[
                        "price"
                    ]
                )
                -
                coupon[
                    "desconto_estimado"
                ]
            )

    return jsonify({

        "anuncio":
            ad_text(

                offer,

                request.args.get(
                    "affiliate_link",
                    ""
                ).strip()
            )
    })


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


# ============================================================
# TESTE ITENS
# ============================================================

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
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    token_data = tokens()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(
                access_token()
            )
    }

    token = access_token()

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

    if token_data:

        result[
            "token_local"
        ] = {

            "user_id":
                token_data.get(
                    "user_id"
                ),

            "nickname":
                token_data.get(
                    "nickname"
                ),

            "expires_at":
                token_data.get(
                    "expires_at"
                )
        }

    return jsonify(
        json_safe(
            result
        )
    )


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
    box-sizing:border-box
}

body{
    margin:0;
    background:#f4f5f7;
    font-family:Arial,sans-serif;
    color:#222
}

.container{
    max-width:1100px;
    margin:auto;
    padding:18px
}

.card{
    background:#fff;
    border-radius:16px;
    padding:18px;
    margin-bottom:18px;
    box-shadow:0 5px 20px #0000000c
}

h1{
    margin-top:0
}

button,
input{
    width:100%;
    padding:13px;
    border-radius:10px;
    border:1px solid #ddd;
    font-size:15px
}

button{
    border:0;
    background:#3483fa;
    color:#fff;
    cursor:pointer;
    margin-top:7px;
    font-weight:bold
}

button:hover{
    opacity:.92
}

.login{
    background:#ffe600;
    color:#222
}

.all{
    background:#00a650;
    font-size:17px;
    padding:16px
}

.grid{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );
    gap:9px
}

.cat{
    background:#fff;
    border:1px solid #ddd;
    color:#222;
    text-align:left;
    min-height:58px
}

.stats{
    display:grid;
    grid-template-columns:
        repeat(
            auto-fit,
            minmax(120px,1fr)
        );
    gap:9px
}

.stat{
    background:#f3f4f6;
    padding:13px;
    border-radius:11px
}

.stat b{
    display:block;
    font-size:23px;
    margin-top:4px
}

.category-stat{
    background:#f8f8f8;
    border:1px solid #eee;
    padding:9px;
    border-radius:9px;
    margin-top:7px;
    font-size:12px
}

.modelo{
    border:2px solid #eee;
    border-radius:15px;
    padding:14px;
    margin:13px 0
}

.mh{
    display:flex;
    gap:12px;
    align-items:center
}

.mh img{
    width:90px;
    height:90px;
    object-fit:contain;
    background:#fafafa;
    border-radius:10px
}

.title{
    font-size:18px;
    font-weight:bold
}

.tag{
    display:inline-block;
    background:#eef4ff;
    color:#3483fa;
    border-radius:7px;
    padding:5px 8px;
    font-size:11px;
    margin:3px
}

.seller{
    background:#fafafa;
    border:1px solid #eee;
    border-radius:12px;
    padding:12px;
    margin-top:10px
}

.price{
    font-size:22px;
    font-weight:bold
}

.green{
    color:#00a650;
    font-weight:bold
}

.old{
    text-decoration:line-through;
    color:#777
}

.coupon{
    background:#fff8d6;
    border:1px dashed #d7ad00;
    border-radius:10px;
    padding:10px;
    margin-top:9px
}

.final{
    background:#eaf8ef;
    color:#008a3e;
    font-weight:bold;
    padding:9px;
    border-radius:8px;
    margin-top:7px
}

.ad{
    display:none;
    white-space:pre-wrap;
    background:#f7f7f7;
    padding:10px;
    border-radius:9px;
    margin-top:8px;
    font-size:13px
}

.small{
    font-size:12px;
    color:#666
}

.status{
    background:#eef8f0;
    padding:10px;
    border-radius:9px
}

.loading{
    padding:20px;
    text-align:center;
    font-weight:bold
}

</style>


<script>

async function cacar(categoria){

    const status =
        document.getElementById(
            'status'
        );

    status.textContent =
        '🔄 Caçando ofertas...';

    document.getElementById(
        'results'
    ).innerHTML =
        '<div class="loading">🔎 Procurando produtos, vendedores, descontos, cupons e frete...</div>';

    try{

        let url =
            '/api/cacar';

        if(categoria){

            url +=
                '?categoria='
                +
                encodeURIComponent(
                    categoria
                );
        }

        const response =
            await fetch(
                url
            );

        const data =
            await response.json();

        if(data.erro){

            throw new Error(
                data.erro
            );
        }

        render(
            data
        );

        status.textContent =
            categoria
            ?
            '✅ Categoria atualizada.'
            :
            '✅ Todas as categorias foram verificadas.';

    }catch(error){

        status.textContent =
            '❌ Erro: '
            +
            error.message;

        document.getElementById(
            'results'
        ).innerHTML =
            '<p>Não foi possível concluir a busca.</p>';
    }
}


async function buscar(){

    const input =
        document.getElementById(
            'q'
        );

    const q =
        input.value.trim();

    if(!q)
        return;

    document.getElementById(
        'status'
    ).textContent =
        '🔄 Procurando...';

    document.getElementById(
        'results'
    ).innerHTML =
        '<div class="loading">🔎 Procurando...</div>';

    try{

        const response =
            await fetch(
                '/api/buscar?q='
                +
                encodeURIComponent(
                    q
                )
            );

        const data =
            await response.json();

        if(data.erro){

            throw new Error(
                data.erro
            );
        }

        render(
            data
        );

        document.getElementById(
            'status'
        ).textContent =
            '✅ Busca concluída.';

    }catch(error){

        document.getElementById(
            'status'
        ).textContent =
            '❌ '
            +
            error.message;
    }
}


function render(data){

    const stats =
        data.stats || {};

    document.getElementById(
        'stats'
    ).innerHTML = `

        <div class="stat">
            Categorias
            <b>${stats.categorias || 0}</b>
        </div>

        <div class="stat">
            Modelos
            <b>${stats.modelos || 0}</b>
        </div>

        <div class="stat">
            Vendedores
            <b>${stats.vendedores || 0}</b>
        </div>

        <div class="stat">
            Anúncios
            <b>${stats.anuncios || 0}</b>
        </div>

        <div class="stat">
            Descontos
            <b>${stats.com_desconto || 0}</b>
        </div>

        <div class="stat">
            Cupons
            <b>${stats.com_cupom || 0}</b>
        </div>

        <div class="stat">
            Frete grátis
            <b>${stats.frete_gratis || 0}</b>
        </div>

    `;


    let categoryHTML = '';

    if(data.categorias){

        categoryHTML =
            '<h3>📊 Resultado por categoria</h3>';

        for(
            const [name, info]
            of Object.entries(
                data.categorias
            )
        ){

            categoryHTML += `

                <div class="category-stat">

                    <b>
                        ${esc(name)}
                    </b>

                    ${
                        info.erro
                        ?
                        '❌ '+esc(info.erro)
                        :
                        `
                        ${info.modelos || 0}
                        modelos ·
                        ${info.ofertas || 0}
                        anúncios ·
                        ${info.descontos || 0}
                        descontos ·
                        ${info.cupons || 0}
                        cupons
                        `
                    }

                </div>

            `;
        }
    }


    const models =
        data.modelos || [];


    const results =
        models.map(

            (model,index) => {

                return `

                <div class="modelo">

                    <div class="mh">

                        ${
                            model.image
                            ?
                            `
                            <img
                                src="${model.image}"
                            >
                            `
                            :
                            ''
                        }

                        <div>

                            <span
                                class="tag"
                                style="
                                    background:#00a650;
                                    color:white
                                "
                            >
                                🏆 OPORTUNIDADE #${index + 1}
                            </span>

                            <span class="tag">
                                ${esc(
                                    model.category_name || ''
                                )}
                            </span>

                            <div class="title">
                                ${esc(
                                    model.modelo_nome
                                )}
                            </div>

                            ${
                                (
                                    model.especificacoes
                                    ||
                                    []
                                )
                                .map(
                                    spec =>
                                        `
                                        <span class="tag">
                                            ${esc(spec)}
                                        </span>
                                        `
                                )
                                .join('')
                            }

                            <div class="small">

                                ${
                                    model.ofertas.length
                                }
                                vendedor(es)

                            </div>

                        </div>

                    </div>

                    ${
                        (
                            model.ofertas
                            ||
                            []
                        )
                        .map(
                            (offer,offerIndex) =>
                                seller(
                                    offer,
                                    index,
                                    offerIndex
                                )
                        )
                        .join('')
                    }

                </div>

                `;
            }
        ).join('');


    document.getElementById(
        'results'
    ).innerHTML =

        categoryHTML
        +
        (
            results
            ||
            '<p>Nenhuma oportunidade encontrada.</p>'
        );
}


function seller(
    offer,
    modelIndex,
    offerIndex
){

    const id =
        'a'
        +
        modelIndex
        +
        '_'
        +
        offerIndex;

    const coupon =
        offer.cupom;


    return `

    <div class="seller">

        ${
            offer.menor_preco_modelo
            ?
            `
            <span
                class="tag"
                style="
                    background:#00a650;
                    color:white
                "
            >
                💰 MENOR PREÇO TOTAL
            </span>
            `
            :
            ''
        }


        ${
            offer.discount > 0
            ?
            `
            <span
                class="tag"
                style="
                    background:#ffebee;
                    color:#d32f2f
                "
            >
                🔥 ${offer.discount}% OFF
            </span>
            `
            :
            ''
        }


        <div class="price">

            ${brl(
                offer.price
            )}

        </div>


        ${
            offer.original_price
            ?
            `
            <div class="old">

                De:
                ${brl(
                    offer.original_price
                )}

            </div>
            `
            :
            ''
        }


        ${
            offer.free_shipping
            ?
            `
            <div class="green">
                🚚 Frete grátis
            </div>
            `
            :
            ''
        }


        ${
            offer.shipping_known
            ?
            `
            <div class="green">

                💰 Total:
                ${brl(
                    offer.total_price
                )}

            </div>
            `
            :
            ''
        }


        ${
            coupon
            ?
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
                    ''
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
                    ''
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
                    ''
                }

                ${
                    offer.preco_com_cupom
                    ?
                    `
                    <div class="final">

                        💥 Estimado com cupom:

                        ${brl(
                            offer.preco_com_cupom
                        )}

                    </div>
                    `
                    :
                    ''
                }

                <div class="small">

                    ⚠️ Estimativa.
                    Confirme a elegibilidade no checkout.

                </div>

            </div>
            `
            :
            ''
        }


        <div class="small">

            👤 Vendedor:
            ${offer.seller_id || 'N/A'}

        </div>


        <div class="small">

            📦 Condição:
            ${offer.condition || 'N/A'}

        </div>


        <br>


        <a
            href="${offer.permalink}"
            target="_blank"
        >
            🛒 Ver produto
        </a>


        <input
            id="link_${id}"
            placeholder="Cole seu link de afiliado aqui"
        >


        <button
            onclick='gerarAnuncio(
                "${id}",
                ${JSON.stringify(
                    offer
                )}
            )'
        >
            📢 GERAR ANÚNCIO
        </button>


        <button
            id="copy_${id}"
            style="
                display:none;
                background:#ff8a00
            "
            onclick="copyAd('${id}')"
        >
            📋 COPIAR ANÚNCIO
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
    offer
){

    const affiliate =
        document.getElementById(
            'link_'+id
        ).value;


    const params =
        new URLSearchParams({

            title:
                offer.title,

            price:
                offer.price,

            discount:
                offer.discount,

            shipping_free:
                offer.free_shipping
                ?
                '1'
                :
                '0',

            cupom:
                offer.cupom
                ?
                offer.cupom.code
                :
                '',

            affiliate_link:
                affiliate
        });


    if(
        offer.original_price
    ){

        params.set(
            'original_price',
            offer.original_price
        );
    }


    const response =
        await fetch(
            '/api/gerar-anuncio?'
            +
            params.toString()
        );


    const data =
        await response.json();


    const box =
        document.getElementById(
            'ad_'+id
        );


    box.style.display =
        'block';


    box.textContent =
        data.anuncio;


    document.getElementById(
        'copy_'+id
    ).style.display =
        'block';
}


function copyAd(id){

    const text =
        document.getElementById(
            'ad_'+id
        ).textContent;


    navigator.clipboard.writeText(
        text
    );


    alert(
        '📋 Anúncio copiado!'
    );
}


function brl(value){

    return (

        'R$ '

        +

        Number(
            value || 0
        ).toLocaleString(

            'pt-BR',

            {
                minimumFractionDigits:2,
                maximumFractionDigits:2
            }
        )
    );
}


function esc(value){

    return String(
        value || ''
    ).replace(

        /[&<>"']/g,

        character => (

            {
                '&':
                    '&amp;',

                '<':
                    '&lt;',

                '>':
                    '&gt;',

                '"':
                    '&quot;',

                "'":
                    '&#039;'
            }[
                character
            ]
        )
    );
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
        Encontre produtos, compare vendedores,
        descontos, cupons e frete.
    </p>


    {% if conectado %}

        <div class="status">

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
        🚀 Caçador automático
    </h2>


    <p class="small">

        O sistema percorre as categorias,
        procura produtos, compara vendedores
        e organiza as melhores oportunidades.

    </p>


    <button
        class="all"
        onclick="cacar('')"
    >

        🔥 CAÇAR TODAS AS CATEGORIAS

    </button>


    <div
        class="grid"
        style="margin-top:10px"
    >

        {% for category in categorias %}

            <button
                class="cat"
                onclick="cacar({{category|tojson}})"
            >

                {{category}}

            </button>

        {% endfor %}

    </div>


    <p
        id="status"
        class="small"
    >

        Escolha uma categoria
        ou clique em CAÇAR TODAS.

    </p>

</div>


<div class="card">

    <h2>
        🔎 Busca manual
    </h2>


    <input
        id="q"
        placeholder="Ex: celular, perfume, whey, furadeira..."
        onkeydown="
            if(event.key==='Enter')
                buscar()
        "
    >


    <button
        onclick="buscar()"
    >

        🔎 PROCURAR

    </button>

</div>


<div class="card">

    <h2>
        📊 Resumo da caça
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


    <div id="results">

        <p>

            Faça uma busca
            para começar.

        </p>

    </div>

</div>


<div class="card">

    <a
        href="/api/cupons?atualizar=1"
        target="_blank"
    >

        🎟️ Atualizar cupons

    </a>


    <br>
    <br>


    <a
        href="/mercadolivre/diagnostico"
        target="_blank"
    >

        🧪 Diagnóstico Mercado Livre

    </a>


    <br>
    <br>


    <a
        href="/health"
        target="_blank"
    >

        ❤️ Health

    </a>

</div>


</div>

</body>

</html>

"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    token_data = tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            token_data.get(
                "nickname"
            )
            if token_data
            else None,

        categorias=
            list(
                CATALOG.keys()
            )
    )


# ============================================================
# BUSCA PAGE
# ============================================================

@app.route(
    "/buscar"
)
def buscar_page():

    query = request.args.get(
        "q",
        ""
    ).strip()

    if not query:

        return redirect("/")

    sync_coupons()

    result = scan_queries(
        [query],
        request.args.get(
            "desconto",
            0
        )
    )

    token_data = tokens()

    return render_template_string(

        HTML,

        conectado=
            bool(
                access_token()
            ),

        nickname=
            token_data.get(
                "nickname"
            )
            if token_data
            else None,

        categorias=
            list(
                CATALOG.keys()
            ),

        resultado=
            result
    )


# ============================================================
# CUPONS PAGE
# ============================================================

@app.route(
    "/cupons"
)
def coupons_page():

    sync_coupons()

    return jsonify({

        "cupons":
            json_safe(
                coupons()
            ),

        "fonte":
            COUPONS_URL
    })


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
                CATALOG
            ),

        "produtos":
            "products/{product_id}/items",

        "caca_todas":
            "ativo",

        "comparacao_vendedores":
            "ativo",

        "cupons":
            "ativo",

        "gerador_anuncios":
            "ativo"
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