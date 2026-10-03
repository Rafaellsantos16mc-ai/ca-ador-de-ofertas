import os
import sqlite3
import secrets
import hashlib
import base64
import time
import re
import html as html_lib
import threading
import uuid

from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    session,
    jsonify,
    render_template_string,
)


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

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 30

MAX_PRODUCTS = 60


# ============================================================
# FONTES PÚBLICAS DE CUPOM
# ============================================================

COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/l/promocoes",
]


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
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "perfume nacional",
        "perfume eau de parfum",
    ],

    "🏋️ Academia": [
        "roupa academia masculina",
        "roupa academia feminina",
        "camiseta academia",
        "short academia",
        "legging academia",
        "tenis academia",
        "tenis corrida",
        "tenis treino",
        "whey protein",
        "creatina",
        "pre treino",
        "suplementos",
    ],

    "🔧 Ferramentas": [
        "furadeira",
        "parafusadeira",
        "esmerilhadeira",
        "kit ferramentas",
        "maleta ferramentas",
        "serra",
        "chave de impacto",
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "headset",
        "smartwatch",
        "tablet",
        "caixa de som bluetooth",
        "camera digital",
        "power bank",
    ],

    "🏠 Casa": [
        "aspirador de pó",
        "liquidificador",
        "cafeteira",
        "air fryer",
        "ventilador",
        "ferro de passar",
    ],

    "🍳 Cozinha": [
        "air fryer",
        "panela elétrica",
        "jogo de panelas",
        "cafeteira",
        "liquidificador",
        "sandwichera",
    ],

    "🚗 Automotivo": [
        "compressor automotivo",
        "aspirador automotivo",
        "suporte celular carro",
        "carregador automotivo",
        "ferramentas automotivas",
        "tapete automotivo",
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "mochila",
        "relogio masculino",
        "bolsa feminina",
        "oculos de sol",
    ],
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
        ],
    },

    "perfume": {
        "strong": [
            "perfume",
            "eau de parfum",
            "eau de toilette",
            "parfum",
        ],
        "bad": [
            "frasco vazio",
            "decant",
            "amostra",
            "porta perfume",
            "refil vazio",
        ],
    },

    "academia": {
        "strong": [
            "roupa",
            "camiseta",
            "short",
            "legging",
            "tenis",
            "corrida",
            "treino",
            "whey",
            "creatina",
            "pre treino",
            "suplemento",
        ],
        "bad": [
            "halter",
            "halteres",
            "anilha",
            "barra",
            "banco musculacao",
            "caneleira",
            "elastico",
            "adesivo",
            "capa",
            "suporte",
            "peca de reposicao",
        ],
    },

    "ferramenta": {
        "strong": [
            "furadeira",
            "parafusadeira",
            "esmerilhadeira",
            "ferramenta",
            "serra",
            "impacto",
        ],
        "bad": [
            "broca avulsa",
            "peca",
            "carvao",
            "bateria avulsa",
            "capa",
        ],
    },
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

            coupon_code TEXT,
            coupon_type TEXT,
            coupon_value REAL,
            coupon_discount REAL,
            coupon_final_price REAL,

            coupon_confirmed INTEGER DEFAULT 0,
            coupon_source TEXT,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            code TEXT UNIQUE,
            description TEXT,

            discount_percent REAL DEFAULT 0,
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


def safe_float(value, default=0):

    try:

        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        value = (
            str(value)
            .replace("R$", "")
            .replace(" ", "")
        )

        if "," in value:

            value = (
                value
                .replace(".", "")
                .replace(",", ".")
            )

        return float(value)

    except Exception:

        return default


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


def unique(values):

    output = []

    seen = set()

    for value in values:

        if value and value not in seen:

            seen.add(value)

            output.append(value)

    return output


def discount_percent(price, original):

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


def total_price(price, shipping):

    try:

        return round(
            float(price) + float(shipping or 0),
            2
        )

    except Exception:

        return float(price or 0)


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


# ============================================================
# RELEVÂNCIA
# ============================================================

def profile_for(query):

    q = norm(query)

    if any(
        x in q
        for x in [
            "iphone",
            "samsung",
            "galaxy",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "smartphone",
            "celular",
        ]
    ):

        return "celular"

    if "perfume" in q:
        return "perfume"

    if any(
        x in q
        for x in [
            "academia",
            "roupa academia",
            "camiseta academia",
            "short academia",
            "legging academia",
            "tenis academia",
            "tenis corrida",
            "tenis treino",
            "whey",
            "creatina",
            "pre treino",
            "suplemento",
        ]
    ):

        return "academia"

    if any(
        x in q
        for x in [
            "furadeira",
            "parafusadeira",
            "ferramenta",
            "esmerilhadeira",
            "serra",
        ]
    ):

        return "ferramenta"

    return None


def relevance(title, query):

    title_n = norm(title)

    query_n = norm(query)

    profile = profile_for(query)

    score = 0

    strong = (
        PROFILES
        .get(profile, {})
        .get("strong", [])
    )

    bad = (
        PROFILES
        .get(profile, {})
        .get("bad", [])
    )

    for word in strong:

        if word in title_n:

            score += 35

    for word in query_n.split():

        if len(word) >= 3 and word in title_n:

            score += 10

    for word in bad:

        if word in title_n:

            score -= 90

    return score


# ============================================================
# OAUTH PKCE
# ============================================================

def generate_pkce():

    verifier = secrets.token_urlsafe(64)

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


def tokens():

    conn = get_db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def save_tokens(data, user=None):

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
        VALUES(1,?,?,?,?,?)
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

        data.get("access_token"),

        data.get("refresh_token"),

        int(time.time())
        + int(
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
        else old.get("nickname"),

    ))

    conn.commit()

    conn.close()


def refresh_token():

    data = tokens()

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
                    refresh,
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:
            return None

        result = response.json()

        if not result.get(
            "access_token"
        ):
            return None

        save_tokens(
            result,
            {
                "id":
                    data.get("user_id"),

                "nickname":
                    data.get("nickname"),
            }
        )

        return result.get(
            "access_token"
        )

    except Exception:

        return None


def access_token():

    data = tokens()

    if not data:
        return None

    expires = int(
        data.get("expires_at") or 0
    )

    token = data.get(
        "access_token"
    )

    if (
        token
        and time.time()
        < expires - 120
    ):

        return token

    return (
        refresh_token()
        or token
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    verifier, challenge = (
        generate_pkce()
    )

    state = secrets.token_urlsafe(32)

    session["ml_state"] = state

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
            "S256",
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return jsonify({
            "erro": error,
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
        or state != session.get(
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
                    ),
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            return jsonify({
                "erro":
                    "Falha ao obter token.",

                "status":
                    response.status_code,

                "resposta":
                    response.text,
            }), response.status_code

        data = response.json()

        user = None

        if data.get(
            "access_token"
        ):

            me = requests.get(

                ML_API
                + "/users/me",

                headers={
                    "Authorization":
                        "Bearer "
                        + data[
                            "access_token"
                        ]
                },

                timeout=REQUEST_TIMEOUT
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

    except Exception as e:

        return jsonify({
            "erro": str(e)
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
# API MERCADO LIVRE
# ============================================================

def ml_get(path, params=None):

    token = access_token()

    if not token:

        return {}, 401

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    headers = {
        "Authorization":
            "Bearer " + token,

        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/5.0",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if (
            response.status_code == 401
        ):

            token = refresh_token()

            if token:

                headers[
                    "Authorization"
                ] = "Bearer " + token

                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT
                )

        try:

            data = response.json()

        except Exception:

            data = {
                "message":
                    response.text[:1000]
            }

        return data, response.status_code

    except Exception as e:

        return {
            "error": str(e)
        }, 500


# ============================================================
# CATEGORIAS ML
# ============================================================

def discover_categories(query):

    data, status = ml_get(

        f"/sites/{SITE_ID}/domain_discovery/search",

        {
            "q": query
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

        if category_id:

            result.append({
                "category_id":
                    category_id,

                "category_name":
                    category_name,
            })

    return result


def get_highlights(category_id):

    data, status = ml_get(
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

        return (
            data.get("content")
            or data.get("results")
            or []
        )

    return []


def get_product(product_id):

    data, status = ml_get(
        f"/products/{product_id}"
    )

    if (
        status == 200
        and isinstance(data, dict)
    ):

        return data

    return None


def get_product_items(product_id):

    data, status = ml_get(
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

        return (
            data.get("results")
            or []
        )

    return []


# ============================================================
# PÁGINA PÚBLICA DE CUPONS
# ============================================================

def clean_public_html(raw):

    text = html_lib.unescape(
        raw or ""
    )

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=re.I | re.S
    )

    text = re.sub(
        r"<noscript.*?</noscript>",
        " ",
        text,
        flags=re.I | re.S
    )

    # Preserva separação de blocos.
    text = re.sub(
        r"</(?:div|p|li|article|section|h1|h2|h3|h4|h5|br)>",
        "\n",
        text,
        flags=re.I
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text
    )

    text = re.sub(
        r"\n\s+",
        "\n",
        text
    )

    return text.strip()


def fetch_coupon_pages():

    headers = {

        "User-Agent":
            "Mozilla/5.0 "
            "(iPhone; CPU iPhone OS 18_0 like Mac OS X) "
            "AppleWebKit/605.1.15 "
            "Version/18.0 Mobile/15E148 Safari/604.1",

        "Accept-Language":
            "pt-BR,pt;q=0.9",

        "Accept":
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,*/*;q=0.8",
    }

    pages = []

    for url in COUPON_SOURCE_URLS:

        try:

            response = requests.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:

                pages.append({
                    "url": response.url,
                    "html": response.text,
                })

        except Exception as e:

            print(
                "[CUPOM PAGE ERROR]",
                url,
                e
            )

    return pages


# ============================================================
# PARSER DE CUPONS
# ============================================================

def parse_money(text):

    if not text:
        return None

    match = re.search(
        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2}"
        r"|[0-9]+,[0-9]{2}"
        r"|[0-9]+(?:\.[0-9]{2})?)",
        text,
        re.I
    )

    if not match:
        return None

    value = match.group(1)

    try:

        if "," in value:

            value = (
                value
                .replace(".", "")
                .replace(",", ".")
            )

        return float(value)

    except Exception:

        return None


def coupon_from_text(text):

    if not text:
        return None

    # --------------------------------------------------------
    # CUPOM FIXO
    # --------------------------------------------------------

    patterns_fixed = [

        r"\bCupom\s+R\$\s*([\d\.,]+)\s*OFF\b",

        r"\bR\$\s*([\d\.,]+)\s*OFF\s+com\s+Cupom\b",

        r"\bCupom\s+de\s+R\$\s*([\d\.,]+)\b",

    ]

    for pattern in patterns_fixed:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = parse_money(
                "R$ "
                + match.group(1)
            )

            if value:

                return {
                    "type":
                        "fixed",

                    "value":
                        value,

                    "label":
                        f"Cupom {brl(value)} OFF",
                }

    # --------------------------------------------------------
    # CUPOM PERCENTUAL
    # --------------------------------------------------------

    patterns_percent = [

        r"\bCupom\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF\b",

        r"\b(\d+(?:[.,]\d+)?)\s*%\s*OFF\s+com\s+Cupom\b",

    ]

    for pattern in patterns_percent:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = float(
                match.group(1)
                .replace(",", ".")
            )

            if 0 < value <= 100:

                return {
                    "type":
                        "percent",

                    "value":
                        value,

                    "label":
                        f"Cupom {value:g}% OFF",
                }

    return None


def money_values(text):

    values = []

    for match in re.finditer(
        r"R\$\s*"
        r"([0-9]{1,3}"
        r"(?:\.[0-9]{3})*,[0-9]{2}"
        r"|[0-9]+,[0-9]{2}"
        r"|[0-9]+(?:\.[0-9]{2})?)",
        text,
        re.I
    ):

        value = parse_money(
            match.group(0)
        )

        if value is not None:

            values.append({
                "value":
                    value,

                "start":
                    match.start(),

                "end":
                    match.end(),
            })

    return values


def clean_title(title):

    if not title:
        return ""

    title = re.sub(
        r"\s+",
        " ",
        title
    ).strip()

    # Remove lixo que estava aparecendo como
    # "is amanhã domingo".
    title = re.sub(
        r"\b(?:hoje|amanhã|amanha|domingo|segunda-feira|terça-feira|terca-feira|quarta-feira|quinta-feira|sexta-feira|sábado|sabado)\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\b(?:chegará|chegara|chega)\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\b(?:cupom|frete grátis|frete gratis|no pix|em outros meios)\b.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\b\d+x\s+R\$\s*[\d\.,]+.*$",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\s+",
        " ",
        title
    ).strip(
        " -|•"
    )

    # Não aceitar lixo como título.
    if len(title) < 5:
        return ""

    invalid = [
        "dia",
        "oferta",
        "ofertas",
        "cupom",
        "amanhã",
        "amanha",
        "domingo",
    ]

    if norm(title) in invalid:
        return ""

    return title


def extract_coupon_blocks(text):

    # Localiza cada ocorrência de CUPOM verdadeira.
    pattern = re.compile(
        r"\bCupom\s+"
        r"(?:R\$\s*[\d\.,]+\s*OFF"
        r"|\d+(?:[.,]\d+)?\s*%\s*OFF)",
        re.I
    )

    matches = list(
        pattern.finditer(text)
    )

    blocks = []

    for index, match in enumerate(
        matches
    ):

        # Não invade demais o produto anterior.
        start = max(
            0,
            match.start() - 900
        )

        if index > 0:

            start = max(
                start,
                matches[index - 1].end()
            )

        end = match.end()

        if index + 1 < len(matches):

            end = min(
                matches[index + 1].start(),
                match.end() + 250
            )

        else:

            end = min(
                len(text),
                match.end() + 250
            )

        block = text[
            start:end
        ].strip()

        coupon = coupon_from_text(
            match.group(0)
        )

        if coupon:

            blocks.append({
                "coupon":
                    coupon,

                "block":
                    block,
            })

    return blocks


# ============================================================
# EXTRAI OFERTAS DA PÁGINA PÚBLICA
# ============================================================

def parse_public_coupon_products():

    pages = fetch_coupon_pages()

    results = []

    for page in pages:

        text = clean_public_html(
            page["html"]
        )

        blocks = extract_coupon_blocks(
            text
        )

        for item in blocks:

            block = item["block"]

            coupon = item["coupon"]

            prices = money_values(
                block
            )

            if not prices:
                continue

            # ------------------------------------------------
            # PREÇO ATUAL
            # ------------------------------------------------

            current_price = None

            seller_match = re.findall(

                r"(R\$\s*[\d\.,]+)"
                r"\s+"
                r"\d+(?:[.,]\d+)?"
                r"\s*%\s*OFF",

                block,

                flags=re.I
            )

            if seller_match:

                # O último par preço + %OFF normalmente
                # pertence ao produto do bloco.
                current_price = parse_money(
                    seller_match[-1]
                )

            # Fallback:
            # tenta escolher o menor preço recente
            # antes do cupom.
            if current_price is None:

                before_coupon = block[
                    :block.lower().find(
                        "cupom"
                    )
                ]

                values = money_values(
                    before_coupon
                )

                if values:

                    # Evita pegar preço original
                    # quando existe um preço menor.
                    candidates = [
                        x["value"]
                        for x in values
                        if x["value"] >= MIN_PRODUCT_PRICE
                    ]

                    if candidates:

                        current_price = min(
                            candidates
                        )

            if (
                current_price is None
                or current_price < MIN_PRODUCT_PRICE
            ):

                continue

            # ------------------------------------------------
            # PREÇO ORIGINAL
            # ------------------------------------------------

            original_price = None

            candidates_original = [
                x["value"]
                for x in prices
                if x["value"] > current_price
            ]

            if candidates_original:

                original_price = min(
                    candidates_original
                )

            # ------------------------------------------------
            # TÍTULO
            # ------------------------------------------------

            first_money = re.search(
                r"R\$\s*[\d\.,]+",
                block
            )

            if first_money:

                raw_title = block[
                    :first_money.start()
                ]

            else:

                raw_title = block

            title = clean_title(
                raw_title
            )

            # Fallback adicional:
            # procura uma linha que tenha aparência
            # de título.
            if not title:

                parts = re.split(
                    r"[\n|•]",
                    block
                )

                for part in parts:

                    candidate = clean_title(
                        part
                    )

                    if (
                        candidate
                        and len(candidate) >= 8
                        and "cupom"
                        not in candidate.lower()
                        and "R$"
                        not in candidate
                    ):

                        title = candidate

                        break

            if not title:
                continue

            results.append({

                "title":
                    title,

                "price":
                    round(
                        current_price,
                        2
                    ),

                "original_price":
                    (
                        round(
                            original_price,
                            2
                        )
                        if original_price
                        else None
                    ),

                "coupon":
                    coupon,

                "source":
                    page["url"],
            })

    # Remove duplicados.
    unique_products = {}

    for item in results:

        key = norm(
            item["title"]
        )

        if not key:
            continue

        old = unique_products.get(
            key
        )

        if not old:

            unique_products[key] = item

        else:

            # Mantém o menor preço.
            if item["price"] < old["price"]:

                unique_products[key] = item

    return list(
        unique_products.values()
    )


# ============================================================
# ASSOCIAÇÃO PRODUTO ↔ CUPOM
# ============================================================

def title_tokens(title):

    words = norm(
        title
    ).split()

    stop = {
        "de",
        "da",
        "do",
        "das",
        "dos",
        "com",
        "para",
        "e",
        "em",
        "no",
        "na",
        "original",
        "novo",
        "oficial",
    }

    return {
        word
        for word in words
        if len(word) >= 3
        and word not in stop
    }


def match_public_coupon(
    product_title,
    product_price,
    public_products
):

    product_words = title_tokens(
        product_title
    )

    if not product_words:
        return None

    candidates = []

    for public in public_products:

        public_words = title_tokens(
            public["title"]
        )

        if not public_words:
            continue

        intersection = (
            product_words
            & public_words
        )

        # Compatibilidade por tokens.
        overlap = (
            len(intersection)
            / max(
                1,
                min(
                    len(product_words),
                    len(public_words)
                )
            )
        )

        # Palavras importantes do modelo.
        model_bonus = 0

        for word in [
            "iphone",
            "galaxy",
            "samsung",
            "motorola",
            "xiaomi",
            "redmi",
            "poco",
            "kappa",
            "nike",
            "adidas",
            "smartwatch",
            "perfume",
            "air fryer",
            "whey",
            "creatina",
        ]:

            if (
                word in norm(product_title)
                and word in norm(public["title"])
            ):

                model_bonus += 0.15

        score = (
            overlap
            + model_bonus
        )

        if score >= 0.55:

            candidates.append(
                (
                    score,
                    public
                )
            )

    if not candidates:

        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    best_score, best = candidates[0]

    # Exigência mais rígida para evitar
    # cupom de outro produto.
    if best_score < 0.55:
        return None

    coupon = dict(
        best["coupon"]
    )

    coupon[
        "match_score"
    ] = round(
        best_score,
        3
    )

    coupon[
        "public_title"
    ] = best["title"]

    return coupon


# ============================================================
# CÁLCULO DO CUPOM
# ============================================================

def calculate_coupon(
    price,
    coupon
):

    if not coupon:
        return None

    price = float(price)

    minimum = coupon.get(
        "min_purchase"
    )

    if (
        minimum
        and price < float(minimum)
    ):

        return None

    if coupon["type"] == "fixed":

        discount = float(
            coupon["value"]
        )

    else:

        discount = (
            price
            * float(
                coupon["value"]
            )
            / 100
        )

    max_discount = coupon.get(
        "max_discount"
    )

    if max_discount:

        discount = min(
            discount,
            float(max_discount)
        )

    discount = min(
        discount,
        price
    )

    final = max(
        price - discount,
        0
    )

    return {

        "discount":
            round(
                discount,
                2
            ),

        "final":
            round(
                final,
                2
            ),

        "effective_percent":
            round(
                discount
                / price
                * 100,
                2
            ),
    }


# ============================================================
# PARSER DE CONDIÇÕES DO CUPOM
# ============================================================

def parse_coupon_conditions(
    coupon,
    block
):

    if not block:
        return coupon

    # Compra mínima.
    patterns = [

        r"(?:mínimo|minimo|a partir de|partir de|compra mínima|compra minima)"
        r".{0,50}R\$\s*([\d\.,]+)",

        r"R\$\s*([\d\.,]+)"
        r".{0,30}"
        r"(?:mínimo|minimo|compra)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:

            value = parse_money(
                "R$ "
                + match.group(1)
            )

            if value:

                coupon[
                    "min_purchase"
                ] = value

                break

    # Limite de desconto.
    patterns_max = [

        r"(?:máximo|maximo|limite)"
        r".{0,50}R\$\s*([\d\.,]+)",

        r"R\$\s*([\d\.,]+)"
        r".{0,30}"
        r"(?:máximo|maximo|limite)",
    ]

    for pattern in patterns_max:

        match = re.search(
            pattern,
            block,
            re.I
        )

        if match:

            value = parse_money(
                "R$ "
                + match.group(1)
            )

            if value:

                coupon[
                    "max_discount"
                ] = value

                break

    return coupon


# ============================================================
# SALVA CUPONS
# ============================================================

def save_public_coupons(
    public_products
):

    # O produto público já traz o cupom associado.
    # Aqui mantemos apenas para diagnóstico.
    conn = get_db()

    conn.execute(
        "DELETE FROM cupons"
    )

    for product in public_products:

        coupon = product[
            "coupon"
        ]

        code = coupon.get(
            "label",
            "CUPOM"
        )

        conn.execute("""
            INSERT OR IGNORE INTO cupons(
                code,
                description,
                discount_percent,
                fixed_discount,
                min_purchase,
                max_discount,
                source_url,
                conditions,
                active
            )
            VALUES(?,?,?,?,?,?,?,?,1)
        """, (

            code,

            product[
                "title"
            ],

            coupon["value"]
            if coupon["type"]
            == "percent"
            else 0,

            coupon["value"]
            if coupon["type"]
            == "fixed"
            else 0,

            coupon.get(
                "min_purchase"
            ),

            coupon.get(
                "max_discount"
            ),

            product.get(
                "source"
            ),

            product[
                "title"
            ],
        ))

    conn.commit()

    conn.close()


# ============================================================
# NORMALIZA ITEM
# ============================================================

def normalize_item(item):

    if not isinstance(
        item,
        dict
    ):

        return None

    item_id = item.get(
        "item_id"
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

    shipping_cost = shipping.get(
        "cost"
    )

    return {

        "item_id":
            item_id,

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
    }


# ============================================================
# SALVA OFERTA
# ============================================================

def save_offer(
    offer
):

    conn = get_db()

    conn.execute("""
        INSERT INTO ofertas(

            product_id,
            item_id,
            title,
            permalink,

            price,
            original_price,
            discount,

            seller_id,
            image,

            category_id,
            category_name,

            condition,
            listing_type_id,

            free_shipping,
            shipping_cost,

            total_price,

            coupon_code,
            coupon_type,
            coupon_value,
            coupon_discount,
            coupon_final_price,

            coupon_confirmed,
            coupon_source

        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
    """, (

        offer[
            "product_id"
        ],

        offer[
            "item_id"
        ],

        offer[
            "title"
        ],

        offer[
            "permalink"
        ],

        offer[
            "price"
        ],

        offer[
            "original_price"
        ],

        offer[
            "discount"
        ],

        offer[
            "seller_id"
        ],

        offer[
            "image"
        ],

        offer[
            "category_id"
        ],

        offer[
            "category_name"
        ],

        offer[
            "condition"
        ],

        offer[
            "listing_type_id"
        ],

        1
        if offer[
            "free_shipping"
        ]
        else 0,

        offer[
            "shipping_cost"
        ],

        offer[
            "total_price"
        ],

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
            "coupon_discount"
        ],

        offer[
            "coupon_final_price"
        ],

        1,

        offer[
            "coupon_source"
        ],
    ))

    conn.commit()

    conn.close()


def clear_offers():

    conn = get_db()

    conn.execute(
        "DELETE FROM ofertas"
    )

    conn.commit()

    conn.close()


# ============================================================
# CAÇADOR
# ============================================================

def scan_categories(
    categories
):

    # --------------------------------------------------------
    # COLETA CUPONS PÚBLICOS
    # --------------------------------------------------------

    public_products = (
        parse_public_coupon_products()
    )

    print(
        "[CUPONS] Produtos públicos:",
        len(public_products)
    )

    save_public_coupons(
        public_products
    )

    # --------------------------------------------------------
    # CONSULTA CATEGORIAS ESCOLHIDAS
    # --------------------------------------------------------

    all_products = {}

    for category_name in categories:

        queries = CATALOG.get(
            category_name,
            []
        )

        for query in queries:

            discovered = (
                discover_categories(
                    query
                )
            )

            for category in discovered[:3]:

                category_id = category[
                    "category_id"
                ]

                highlights = (
                    get_highlights(
                        category_id
                    )
                )

                for highlight in highlights:

                    if (
                        highlight.get(
                            "type"
                        )
                        != "PRODUCT"
                    ):

                        continue

                    product_id = (
                        highlight.get(
                            "id"
                        )
                        or highlight.get(
                            "product_id"
                        )
                    )

                    if not product_id:
                        continue

                    all_products[
                        product_id
                    ] = {

                        "category_id":
                            category_id,

                        "category_name":
                            category_name,

                        "query":
                            query,
                    }

                    if (
                        len(
                            all_products
                        )
                        >= MAX_PRODUCTS
                    ):

                        break

                if (
                    len(
                        all_products
                    )
                    >= MAX_PRODUCTS
                ):

                    break

            if (
                len(
                    all_products
                )
                >= MAX_PRODUCTS
            ):

                break

        if (
            len(
                all_products
            )
            >= MAX_PRODUCTS
        ):

            break

    # --------------------------------------------------------
    # ANALISA PRODUTOS
    # --------------------------------------------------------

    groups = {}

    for product_id, info in (
        all_products.items()
    ):

        product = get_product(
            product_id
        )

        if not product:
            continue

        title = (
            product.get("name")
            or product.get("title")
            or ""
        )

        score = relevance(
            title,
            info["query"]
        )

        if score < 15:
            continue

        pictures = (
            product.get(
                "pictures"
            )
            or []
        )

        image = None

        if pictures:

            image = (
                pictures[0]
                .get("url")
            )

        items = (
            get_product_items(
                product_id
            )
        )

        product_offers = []

        for raw_item in items:

            item = normalize_item(
                raw_item
            )

            if not item:
                continue

            price = safe_float(
                item["price"]
            )

            if price < MIN_PRODUCT_PRICE:
                continue

            # ------------------------------------------------
            # CUPOM DO PRÓPRIO PRODUTO
            # ------------------------------------------------

            coupon = match_public_coupon(
                title,
                price,
                public_products
            )

            # SEM confirmação do cupom:
            # NÃO entra na vitrine de oportunidades.
            if not coupon:
                continue

            # ------------------------------------------------
            # CONDIÇÕES
            # ------------------------------------------------

            # Localiza bloco público correspondente.
            public_title = coupon.get(
                "public_title",
                ""
            )

            # Não temos o bloco inteiro neste estágio,
            # então respeitamos o mínimo caso tenha sido
            # identificado no cupom.
            minimum = coupon.get(
                "min_purchase"
            )

            if (
                minimum
                and price < float(
                    minimum
                )
            ):

                continue

            # ------------------------------------------------
            # CALCULA
            # ------------------------------------------------

            calculation = (
                calculate_coupon(
                    price,
                    coupon
                )
            )

            if not calculation:
                continue

            original = item.get(
                "original_price"
            )

            if original is not None:

                original = safe_float(
                    original
                )

            seller_discount = (
                discount_percent(
                    price,
                    original
                )
            )

            shipping_cost = item.get(
                "shipping_cost"
            )

            if shipping_cost is not None:

                shipping_cost = safe_float(
                    shipping_cost
                )

            total = total_price(
                price,
                shipping_cost
            )

            coupon_final = (
                total
                - calculation[
                    "discount"
                ]
            )

            if coupon_final < 0:

                coupon_final = 0

            offer = {

                "product_id":
                    product_id,

                "item_id":
                    item[
                        "item_id"
                    ],

                "title":
                    title,

                "permalink":
                    (
                        item.get(
                            "permalink"
                        )
                        or product.get(
                            "permalink"
                        )
                        or (
                            "https://www.mercadolivre.com.br/p/"
                            + str(product_id)
                        )
                    ),

                "price":
                    round(
                        price,
                        2
                    ),

                "original_price":
                    original,

                "discount":
                    seller_discount,

                "seller_id":
                    item.get(
                        "seller_id"
                    ),

                "image":
                    image,

                "category_id":
                    info[
                        "category_id"
                    ],

                "category_name":
                    info[
                        "category_name"
                    ],

                "condition":
                    item.get(
                        "condition"
                    ),

                "listing_type_id":
                    item.get(
                        "listing_type_id"
                    ),

                "free_shipping":
                    item.get(
                        "free_shipping"
                    ),

                "shipping_cost":
                    shipping_cost,

                "total_price":
                    total,

                "coupon_code":
                    coupon[
                        "label"
                    ],

                "coupon_type":
                    coupon[
                        "type"
                    ],

                "coupon_value":
                    coupon[
                        "value"
                    ],

                "coupon_discount":
                    calculation[
                        "discount"
                    ],

                "coupon_final_price":
                    round(
                        coupon_final,
                        2
                    ),

                "coupon_confirmed":
                    1,

                "coupon_source":
                    coupon.get(
                        "public_title"
                    ),

                "coupon_match_score":
                    coupon.get(
                        "match_score"
                    ),
            }

            product_offers.append(
                offer
            )

        if not product_offers:
            continue

        # ----------------------------------------------------
        # UM PRODUTO = UMA OPORTUNIDADE
        # ----------------------------------------------------

        product_offers.sort(
            key=lambda x: (

                -x[
                    "coupon_discount"
                ],

                -(
                    (
                        x[
                            "coupon_discount"
                        ]
                        / x[
                            "price"
                        ]
                    )
                    * 100
                ),

                x[
                    "coupon_final_price"
                ],

                0
                if x[
                    "free_shipping"
                ]
                else 1,
            )
        )

        best = product_offers[0]

        groups[
            product_id
        ] = {

            "product_id":
                product_id,

            "title":
                title,

            "model":
                model_name(title),

            "image":
                image,

            "category_name":
                info[
                    "category_name"
                ],

            "offer":
                best,

        }

    # --------------------------------------------------------
    # ORDENAÇÃO
    # --------------------------------------------------------

    models = list(
        groups.values()
    )

    models.sort(
        key=lambda x: (

            -x[
                "offer"
            ][
                "coupon_discount"
            ],

            -(
                x[
                    "offer"
                ][
                    "coupon_discount"
                ]
                /
                max(
                    x[
                        "offer"
                    ][
                        "price"
                    ],
                    0.01
                )
            ),

            x[
                "offer"
            ][
                "coupon_final_price"
            ],
        )
    )

    # --------------------------------------------------------
    # SALVA
    # --------------------------------------------------------

    clear_offers()

    for group in models:

        save_offer(
            group[
                "offer"
            ]
        )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    offers = [
        x[
            "offer"
        ]
        for x in models
    ]

    return {

        "stats": {

            "produtos":
                len(offers),

            "cupons":
                len(
                    [
                        x
                        for x in offers
                        if x.get(
                            "coupon_confirmed"
                        )
                    ]
                ),

            "maior_desconto":
                brl(
                    max(
                        [
                            x[
                                "coupon_discount"
                            ]
                            for x in offers
                        ]
                        or [0]
                    )
                ),

            "menor_preco_final":
                brl(
                    min(
                        [
                            x[
                                "coupon_final_price"
                            ]
                            for x in offers
                        ]
                        or [0]
                    )
                ),
        },

        "modelos":
            models,

        "ofertas":
            offers,

        "public_coupon_products":
            len(
                public_products
            ),
    }


# ============================================================
# ANÚNCIO WHATSAPP
# ============================================================

def generate_whatsapp(
    offer,
    affiliate_link=""
):

    title = offer.get(
        "title",
        "Produto"
    )

    price = offer.get(
        "price",
        0
    )

    original = offer.get(
        "original_price"
    )

    coupon = offer.get(
        "coupon_code"
    )

    coupon_discount = offer.get(
        "coupon_discount",
        0
    )

    final_price = offer.get(
        "coupon_final_price"
    )

    shipping = offer.get(
        "free_shipping"
    )

    lines = []

    lines.append(
        "🔥 OFERTA ENCONTRADA!"
    )

    lines.append("")

    lines.append(
        f"🛍️ {title}"
    )

    lines.append("")

    if original:

        lines.append(
            f"De: {brl(original)}"
        )

    lines.append(
        f"💰 Preço: {brl(price)}"
    )

    if offer.get(
        "discount",
        0
    ) > 0:

        lines.append(
            "🏷️ "
            + str(
                round(
                    offer[
                        "discount"
                    ],
                    2
                )
            )
            + "% OFF"
        )

    if shipping:

        lines.append(
            "🚚 Frete grátis"
        )

    lines.append("")

    lines.append(
        f"🎟️ CUPOM: {coupon}"
    )

    lines.append(
        f"💸 Desconto do cupom: "
        f"{brl(coupon_discount)}"
    )

    lines.append(
        f"🔥 POR APENAS: "
        f"{brl(final_price)}"
    )

    lines.append(
        f"💰 Você economiza "
        f"{brl(coupon_discount)}"
    )

    lines.append("")

    lines.append(
        "⚠️ Cupom identificado para "
        "esta oferta. Confirme a aplicação "
        "no checkout."
    )

    lines.append("")

    lines.append(
        "🛒 PEGAR OFERTA:"
    )

    lines.append(
        affiliate_link
        or offer.get(
            "permalink",
            ""
        )
    )

    return "\n".join(
        lines
    )


# ============================================================
# API
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    if not access_token():

        return jsonify({
            "ok":
                False,

            "erro":
                "Mercado Livre não conectado.",
        }), 401

    categories = request.args.getlist(
        "categoria"
    )

    # Compatibilidade com:
    # ?categoria=...
    if not categories:

        single = request.args.get(
            "categoria",
            ""
        )

        if single:
            categories = [
                single
            ]

    if not categories:

        categories = list(
            CATALOG.keys()
        )

    categories = [
        x
        for x in categories
        if x in CATALOG
    ]

    try:

        result = scan_categories(
            categories
        )

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
            "ok":
                False,

            "erro":
                str(e),
        }), 500


@app.route("/api/ofertas")
def api_ofertas():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY
            coupon_discount DESC,
            coupon_final_price ASC
    """).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


@app.route("/api/cupons")
def api_cupons():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM cupons
        WHERE active=1
        ORDER BY updated_at DESC
    """).fetchall()

    conn.close()

    return jsonify({
        "cupons": [
            dict(row)
            for row in rows
        ]
    })


@app.route("/api/gerar-anuncio")
def api_gerar_anuncio():

    offer = {

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
            ),

        "original_price":
            safe_float(
                request.args.get(
                    "original_price"
                )
            )
            or None,

        "discount":
            safe_float(
                request.args.get(
                    "discount"
                )
            ),

        "coupon_code":
            request.args.get(
                "cupom",
                ""
            ),

        "coupon_discount":
            safe_float(
                request.args.get(
                    "coupon_discount"
                )
            ),

        "coupon_final_price":
            safe_float(
                request.args.get(
                    "final"
                )
            ),

        "free_shipping":
            request.args.get(
                "shipping_free"
            ) == "1",

        "permalink":
            request.args.get(
                "link",
                ""
            ),
    }

    text = generate_whatsapp(
        offer,
        request.args.get(
            "affiliate_link",
            ""
        )
    )

    return jsonify({
        "ok":
            True,

        "anuncio":
            text,
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    data = tokens()

    result = {

        "configurado":
            bool(
                ML_CLIENT_ID
            ),

        "conectado":
            bool(
                access_token()
            ),

        "redirect_uri":
            ML_REDIRECT_URI,

    }

    if data:

        result[
            "usuario"
        ] = {

            "id":
                data.get(
                    "user_id"
                ),

            "nickname":
                data.get(
                    "nickname"
                ),

            "expires_at":
                data.get(
                    "expires_at"
                ),
        }

    if access_token():

        me, status = ml_get(
            "/users/me"
        )

        result[
            "users_me"
        ] = {

            "status":
                status,

            "resposta":
                me,
        }

    return jsonify(
        result
    )


@app.route(
    "/mercadolivre/teste-produto-itens"
)
def teste_produto_itens():

    product_id = request.args.get(
        "product_id",
        "MLB58793248"
    )

    data, status = ml_get(
        f"/products/{product_id}/items"
    )

    return jsonify({

        "product_id":
            product_id,

        "status_http":
            status,

        "resposta":
            data,
    }), status


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
            len(CATALOG),

        "produto_minimo":
            MIN_PRODUCT_PRICE,

        "cupons":
            "produto-especificos",

        "whatsapp":
            "ativo",
    })


# ============================================================
# HTML
# ============================================================

HTML = r"""
<!doctype html>

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

    background: #f4f5f7;

    color: #222;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {

    max-width: 1100px;

    margin: auto;

    padding: 18px;
}

.card {

    background: white;

    border-radius: 18px;

    padding: 20px;

    margin-bottom: 18px;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.06);
}

h1 {

    margin: 0 0 8px;

    font-size: 29px;
}

h2 {

    margin-top: 0;
}

.subtitle {

    color: #777;
}

button,
a.button {

    border: 0;

    border-radius: 12px;

    padding: 13px 17px;

    font-weight: bold;

    cursor: pointer;

    text-decoration: none;

    display: inline-block;
}

.primary {

    background: #3483fa;

    color: white;
}

.login {

    background: #ffe600;

    color: #222;
}

.select-all {

    background: #111827;

    color: white;

    width: 100%;

    margin-bottom: 12px;
}

.category-grid {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(180px,1fr)
        );

    gap: 10px;
}

.category {

    border: 2px solid #ddd;

    border-radius: 14px;

    padding: 15px;

    background: white;

    cursor: pointer;

    transition: .15s;

    text-align: left;
}

.category.active {

    border-color: #3483fa;

    background: #eef5ff;
}

.category input {

    display: none;
}

.category-title {

    font-weight: bold;

    font-size: 16px;
}

.category-count {

    color: #888;

    font-size: 12px;

    margin-top: 5px;
}

.status {

    padding: 12px;

    background: #eef8f0;

    border-radius: 10px;

    color: #24713c;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(
            auto-fit,
            minmax(150px,1fr)
        );

    gap: 10px;
}

.stat {

    background: #f3f4f6;

    padding: 15px;

    border-radius: 12px;
}

.stat b {

    display: block;

    font-size: 23px;

    margin-top: 6px;
}

.product {

    border: 2px solid #eee;

    border-radius: 16px;

    padding: 16px;

    margin-bottom: 15px;
}

.product-head {

    display: flex;

    gap: 15px;

    align-items: center;
}

.product-image {

    width: 90px;

    height: 90px;

    object-fit: contain;

    background: #fafafa;

    border-radius: 12px;
}

.product-title {

    font-size: 19px;

    font-weight: bold;
}

.category-tag {

    display: inline-block;

    background: #eef4ff;

    color: #3483fa;

    padding: 5px 8px;

    border-radius: 7px;

    font-size: 11px;

    margin-top: 6px;
}

.offer {

    margin-top: 15px;

    background: #fafafa;

    border: 1px solid #eee;

    border-radius: 13px;

    padding: 15px;
}

.price {

    font-size: 25px;

    font-weight: bold;
}

.old {

    color: #888;

    text-decoration:
        line-through;
}

.seller-discount {

    color: #008a3e;

    font-weight: bold;

    margin-top: 5px;
}

.coupon {

    background: #fff8d6;

    border: 2px dashed #d1aa00;

    border-radius: 13px;

    padding: 13px;

    margin-top: 12px;
}

.coupon-title {

    font-weight: bold;

    font-size: 18px;
}

.coupon-discount {

    margin-top: 7px;

    color: #795900;
}

.final {

    background: #eaf8ef;

    color: #008a3e;

    padding: 12px;

    border-radius: 10px;

    margin-top: 10px;

    font-size: 23px;

    font-weight: bold;
}

.confirmed {

    color: #008a3e;

    font-size: 12px;

    margin-top: 5px;
}

.warning {

    color: #777;

    font-size: 12px;

    margin-top: 8px;
}

.actions {

    display: flex;

    gap: 8px;

    flex-wrap: wrap;

    margin-top: 14px;
}

.actions a,
.actions button {

    padding: 11px 13px;

    border-radius: 9px;

    font-size: 13px;
}

.view {

    background: #3483fa;

    color: white;

    text-decoration: none;
}

.whatsapp {

    background: #16a34a;

    color: white;
}

textarea {

    width: 100%;

    min-height: 180px;

    margin-top: 10px;

    padding: 12px;

    border: 1px solid #ddd;

    border-radius: 10px;

    font-size: 14px;
}

@media(max-width:700px) {

    .product-head {

        align-items: flex-start;
    }

    .product-image {

        width: 70px;

        height: 70px;
    }

}

</style>

</head>

<body>

<div class="container">


<div class="card">

    <h1>
        🛒 Caçador de Ofertas
    </h1>

    <div class="subtitle">
        Produtos com cupom aplicável
        encontrados nas categorias selecionadas.
    </div>

    <br>

    {% if conectado %}

        <div class="status">
            🟢 Mercado Livre conectado
            {% if nickname %}
                — <b>{{ nickname }}</b>
            {% endif %}
        </div>

        <br>

        <a
            href="/mercadolivre/logout"
            class="button"
            style="background:#eee;color:#222"
        >
            Desconectar
        </a>

    {% else %}

        <a
            href="/mercadolivre/login"
            class="button login"
        >
            🔗 Conectar Mercado Livre
        </a>

    {% endif %}

</div>


<div class="card">

    <h2>
        🎯 Escolha as categorias
    </h2>

    <button
        class="select-all"
        onclick="selectAll()"
    >
        ☑️ Selecionar todas
    </button>

    <div class="category-grid">

        {% for category in categorias %}

        <label
            class="category"
            data-category="{{ category }}"
        >

            <input
                type="checkbox"
                value="{{ category }}"
                onchange="updateCategory(this)"
            >

            <div class="category-title">
                {{ category }}
            </div>

            <div class="category-count">
                Categoria ativa
            </div>

        </label>

        {% endfor %}

    </div>

    <br>

    <button
        class="primary"
        style="width:100%"
        onclick="caçar()"
    >
        🔎 CAÇAR OFERTAS DAS CATEGORIAS SELECIONADAS
    </button>

    <p
        id="status"
        class="subtitle"
    >
        Selecione uma ou mais categorias.
    </p>

</div>


<div class="card">

    <h2>
        📊 Resultado
    </h2>

    <div
        id="stats"
        class="stats"
    >
    </div>

</div>


<div class="card">

    <h2>
        🏆 Oportunidades encontradas
    </h2>

    <div
        id="results"
    >

        <p class="subtitle">
            As ofertas aparecerão aqui.
        </p>

    </div>

</div>


<div class="card">

    <a
        href="/mercadolivre/diagnostico"
        target="_blank"
    >
        🧪 Diagnóstico Mercado Livre
    </a>

    <br><br>

    <a
        href="/api/cupons"
        target="_blank"
    >
        🎟️ Cupons identificados
    </a>

</div>


</div>


<script>


let selectedCategories = [];


function updateCategory(
    checkbox
) {

    const label =
        checkbox.closest(
            ".category"
        );

    if (checkbox.checked) {

        label.classList.add(
            "active"
        );

        if (
            !selectedCategories.includes(
                checkbox.value
            )
        ) {

            selectedCategories.push(
                checkbox.value
            );
        }

    } else {

        label.classList.remove(
            "active"
        );

        selectedCategories =
            selectedCategories.filter(
                x =>
                    x !== checkbox.value
            );
    }

    document.getElementById(
        "status"
    ).textContent =
        selectedCategories.length
        + " categoria(s) selecionada(s).";
}


function selectAll() {

    const boxes =
        document.querySelectorAll(
            ".category input"
        );

    const allSelected =
        selectedCategories.length
        === boxes.length;

    boxes.forEach(
        box => {

            box.checked =
                !allSelected;

            updateCategory(
                box
            );
        }
    );
}


async function caçar() {

    if (
        selectedCategories.length
        === 0
    ) {

        alert(
            "Selecione pelo menos uma categoria."
        );

        return;
    }

    const status =
        document.getElementById(
            "status"
        );

    status.textContent =
        "🔎 Procurando produtos e cupons...";

    const params =
        new URLSearchParams();

    selectedCategories.forEach(
        category => {

            params.append(
                "categoria",
                category
            );
        }
    );

    try {

        const response =
            await fetch(
                "/api/cacar?"
                + params.toString()
            );

        const data =
            await response.json();

        if (!response.ok) {

            throw new Error(
                data.erro
                || "Erro na caça."
            );
        }

        render(
            data
        );

        status.textContent =
            "✅ Busca concluída.";

    } catch (error) {

        status.textContent =
            "❌ "
            + error.message;
    }
}


function brl(value) {

    return Number(
        value || 0
    ).toLocaleString(
        "pt-BR",
        {
            style:
                "currency",

            currency:
                "BRL"
        }
    );
}


function esc(value) {

    return String(
        value || ""
    )
    .replace(
        /[&<>"']/g,
        function(char) {

            return {

                "&":
                    "&amp;",

                "<":
                    "&lt;",

                ">":
                    "&gt;",

                '"':
                    "&quot;",

                "'":
                    "&#039;"
            }[char];
        }
    );
}


function render(
    data
) {

    const stats =
        data.stats || {};

    document.getElementById(
        "stats"
    ).innerHTML = `

        <div class="stat">

            Produtos

            <b>
                ${stats.produtos || 0}
            </b>

        </div>

        <div class="stat">

            Cupons

            <b>
                ${stats.cupons || 0}
            </b>

        </div>

        <div class="stat">

            Maior desconto

            <b>
                ${stats.maior_desconto || "R$ 0,00"}
            </b>

        </div>

        <div class="stat">

            Menor preço final

            <b>
                ${stats.menor_preco_final || "R$ 0,00"}
            </b>

        </div>

    `;


    const models =
        data.modelos || [];


    if (
        models.length === 0
    ) {

        document.getElementById(
            "results"
        ).innerHTML = `

            <p class="subtitle">
                Nenhum produto com cupom
                confirmado foi encontrado
                nessas categorias.
            </p>

        `;

        return;
    }


    document.getElementById(
        "results"
    ).innerHTML = models
        .map(
            (
                model,
                index
            ) => renderProduct(
                model,
                index
            )
        )
        .join("");
}


function renderProduct(
    model,
    index
) {

    const offer =
        model.offer;


    return `

        <div class="product">

            <div class="product-head">

                ${
                    model.image
                    ?
                    `
                    <img
                        class="product-image"
                        src="${esc(model.image)}"
                    >
                    `
                    :
                    ""
                }

                <div>

                    <div
                        class="product-title"
                    >
                        ${esc(model.title)}
                    </div>

                    <div
                        class="category-tag"
                    >
                        ${esc(
                            model.category_name
                        )}
                    </div>

                </div>

            </div>


            <div class="offer">

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
                    ""
                }


                <div class="price">

                    ${brl(
                        offer.price
                    )}

                </div>


                ${
                    offer.discount > 0
                    ?
                    `
                    <div
                        class="seller-discount"
                    >
                        🔥
                        ${offer.discount}%
                        OFF direto
                        no produto
                    </div>
                    `
                    :
                    ""
                }


                ${
                    offer.free_shipping
                    ?
                    `
                    <div
                        class="seller-discount"
                    >
                        🚚 Frete grátis
                    </div>
                    `
                    :
                    ""
                }


                <div class="coupon">

                    <div
                        class="coupon-title"
                    >
                        🎟️
                        ${esc(
                            offer.coupon_code
                        )}
                    </div>


                    <div
                        class="coupon-discount"
                    >

                        💸 Desconto do cupom:

                        <b>
                            ${brl(
                                offer.coupon_discount
                            )}
                        </b>

                    </div>


                    <div
                        class="final"
                    >

                        💥 Preço com cupom:

                        ${brl(
                            offer.coupon_final_price
                        )}

                    </div>


                    <div
                        class="confirmed"
                    >
                        ✓ Cupom associado
                        a esta oportunidade
                    </div>


                    <div
                        class="warning"
                    >
                        O valor é calculado com
                        base no cupom identificado.
                        Confirme a aplicação no
                        checkout.
                    </div>

                </div>


                <div
                    class="actions"
                >

                    <a
                        class="view"
                        href="${esc(
                            offer.permalink
                        )}"
                        target="_blank"
                    >
                        🛒 Ver produto
                    </a>


                    <button
                        class="whatsapp"
                        onclick='generateAd(
                            ${JSON.stringify(
                                offer
                            )}
                        )'
                    >
                        📲 Gerar WhatsApp
                    </button>

                </div>


                <div
                    id="ad-${index}"
                ></div>

            </div>

        </div>

    `;
}


async function generateAd(
    offer
) {

    try {

        const params =
            new URLSearchParams({

                title:
                    offer.title,

                price:
                    offer.price,

                original_price:
                    offer.original_price
                    || "",

                discount:
                    offer.discount
                    || 0,

                cupom:
                    offer.coupon_code
                    || "",

                coupon_discount:
                    offer.coupon_discount
                    || 0,

                final:
                    offer.coupon_final_price
                    || 0,

                shipping_free:
                    offer.free_shipping
                    ? "1"
                    : "0",

                link:
                    offer.permalink
                    || "",

                affiliate_link:
                    offer.permalink
                    || ""
            });


        const response =
            await fetch(
                "/api/gerar-anuncio?"
                + params.toString()
            );


        const data =
            await response.json();


        const container =
            document.querySelector(
                "#results"
            );


        const box =
            document.createElement(
                "div"
            );

        box.className =
            "card";


        box.innerHTML = `

            <h3>
                📲 Texto para WhatsApp
            </h3>

            <textarea
                id="whatsapp-text"
            >${esc(
                data.anuncio
            )}</textarea>

            <button
                class="whatsapp"
                onclick="copyWhatsApp()"
            >
                📋 Copiar texto
            </button>

        `;


        container.prepend(
            box
        );


        box.scrollIntoView({
            behavior:
                "smooth"
        });


    } catch (error) {

        alert(
            "Erro ao gerar anúncio."
        );
    }
}


async function copyWhatsApp() {

    const textarea =
        document.getElementById(
            "whatsapp-text"
        );

    if (!textarea)
        return;


    try {

        await navigator
            .clipboard
            .writeText(
                textarea.value
            );

        alert(
            "✅ Texto copiado!"
        );

    } catch (error) {

        textarea.select();

        document.execCommand(
            "copy"
        );

        alert(
            "✅ Texto copiado!"
        );
    }
}


</script>


</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    data = tokens()

    return render_template_string(

        HTML,

        conectado=bool(
            access_token()
        ),

        nickname=(
            data.get(
                "nickname"
            )
            if data
            else None
        ),

        categorias=list(
            CATALOG.keys()
        ),
    )


# ============================================================
# 404
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "erro":
            "Rota não encontrada.",

        "rota":
            request.path,

    }), 404


# ============================================================
# 500
# ============================================================

@app.errorhandler(500)
def server_error(error):

    return jsonify({

        "erro":
            "Erro interno.",

        "detalhes":
            str(error),

    }), 500


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print(
        "======================================"
    )

    print(
        "CAÇADOR DE OFERTAS"
    )

    print(
        "OAuth:",
        bool(ML_CLIENT_ID)
    )

    print(
        "Redirect:",
        ML_REDIRECT_URI
    )

    print(
        "Categorias:",
        len(CATALOG)
    )

    print(
        "Cupom por produto: ATIVO"
    )

    print(
        "Mínimo:",
        brl(MIN_PRODUCT_PRICE)
    )

    print(
        "======================================"
    )

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "8080"
            )
        ),

        debug=False,
    )