import os
import re
import time
import sqlite3
import secrets
import hashlib
import base64
import html
import unicodedata
from urllib.parse import urlencode, quote

import requests

from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    jsonify,
    session,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    secrets.token_hex(32)
)

DB_FILE = "ofertas.db"

MIN_PRICE = 69.90

MAX_OFFERS = 50

ML_API = "https://api.mercadolibre.com"

ML_AUTH = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
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


# ============================================================
# PÁGINA OFICIAL DE OFERTAS COM CUPOM
# ============================================================

COUPON_HUB = (
    "https://www.mercadolivre.com.br/l/descontaco-cupons"
)

PROMOTIONS_HUB = (
    "https://www.mercadolivre.com.br/l/promocoes"
)


# ============================================================
# HEADERS
# ============================================================

PUBLIC_HEADERS = {

    "User-Agent":
        (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0.0.0 "
            "Safari/537.36"
        ),

    "Accept":
        (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,"
            "*/*;q=0.8"
        ),

    "Accept-Language":
        "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",

    "Cache-Control":
        "no-cache",

    "Pragma":
        "no-cache",

    "Upgrade-Insecure-Requests":
        "1",
}


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ml_auth (
            id INTEGER PRIMARY KEY,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
        """
    )

    conn.commit()

    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def now_ts():

    return int(
        time.time()
    )


def clean_text(value):

    if value is None:
        return ""

    value = html.unescape(
        str(value)
    )

    replacements = {

        "\\u002F": "/",
        "\\u003A": ":",
        "\\u003F": "?",
        "\\u003D": "=",
        "\\u0026": "&",
        "\\u003C": "<",
        "\\u003E": ">",
        "\\u0022": '"',
        "\\u0027": "'",
        "\\/": "/",
    }

    for old, new in replacements.items():

        value = value.replace(
            old,
            new
        )

    value = value.replace(
        "\xa0",
        " "
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def normalize_text(value):

    value = clean_text(
        value
    ).lower()

    value = unicodedata.normalize(
        "NFKD",
        value
    )

    value = "".join(
        c
        for c in value
        if not unicodedata.combining(c)
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def parse_money(value):

    if value is None:
        return None

    value = str(
        value
    ).strip()

    value = value.replace(
        "R$",
        ""
    )

    value = value.replace(
        " ",
        ""
    )

    if "," in value:

        value = value.replace(
            ".",
            ""
        )

        value = value.replace(
            ",",
            "."
        )

    try:

        return float(
            value
        )

    except Exception:

        return None


def money_br(value):

    if value is None:
        value = 0

    return (
        "R$ "
        + f"{float(value):,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def valid_price(value):

    return (
        value is not None
        and float(value) >= MIN_PRICE
    )


def product_key(title):

    text = normalize_text(
        title
    )

    remove = {
        "oferta",
        "imperdivel",
        "novo",
        "nova",
        "original",
        "frete",
        "gratis",
        "mercado",
        "livre",
        "mais",
        "vendido",
        "vendidos",
    }

    words = [
        x
        for x in text.split()
        if x not in remove
    ]

    return " ".join(
        words[:50]
    )


def extract_mlb_ids(text):

    if not text:
        return []

    found = re.findall(
        r"\bMLB[-_]?(\d{6,})\b",
        str(text).upper()
    )

    result = []

    seen = set()

    for number in found:

        product_id = (
            "MLB"
            + number
        )

        if product_id in seen:
            continue

        seen.add(
            product_id
        )

        result.append(
            product_id
        )

    return result


# ============================================================
# OAUTH
# ============================================================

def make_code_verifier():

    return secrets.token_urlsafe(
        64
    )


def make_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode(
            "ascii"
        )
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).rstrip(
        b"="
    ).decode(
        "ascii"
    )


def save_auth(data):

    conn = db()

    conn.execute(
        "DELETE FROM ml_auth"
    )

    conn.execute(
        """
        INSERT INTO ml_auth (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (
            1, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            data.get(
                "access_token"
            ),

            data.get(
                "refresh_token"
            ),

            int(
                data.get(
                    "expires_at",
                    now_ts() + 21600
                )
            ),

            str(
                data.get(
                    "user_id",
                    ""
                )
            ),

            data.get(
                "nickname",
                ""
            ),

            now_ts(),
        )
    )

    conn.commit()

    conn.close()


def get_auth():

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM ml_auth
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    return row


# ============================================================
# TOKEN
# ============================================================

def refresh_ml_token():

    row = get_auth()

    if not row:
        return False

    refresh_token = row[
        "refresh_token"
    ]

    if not refresh_token:
        return False

    try:

        response = requests.post(
            ML_TOKEN_URL,

            data={
                "grant_type":
                    "refresh_token",

                "client_id":
                    ML_CLIENT_ID,

                "client_secret":
                    ML_CLIENT_SECRET,

                "refresh_token":
                    refresh_token,
            },

            timeout=30,
        )

        if response.status_code != 200:

            print(
                "[TOKEN REFRESH ERRO]",
                response.status_code,
                response.text[:500]
            )

            return False

        data = response.json()

        save_auth(
            {
                "access_token":
                    data.get(
                        "access_token"
                    ),

                "refresh_token":
                    data.get(
                        "refresh_token",
                        refresh_token
                    ),

                "expires_at":
                    now_ts()
                    + int(
                        data.get(
                            "expires_in",
                            21600
                        )
                    ),

                "user_id":
                    row["user_id"],

                "nickname":
                    row["nickname"],
            }
        )

        print(
            "[TOKEN] Atualizado"
        )

        return True

    except Exception as e:

        print(
            "[TOKEN REFRESH EXCEPTION]",
            e
        )

        return False


def get_access_token():

    row = get_auth()

    if not row:
        return None

    expires_at = int(
        row["expires_at"] or 0
    )

    if expires_at <= now_ts() + 120:

        refresh_ml_token()

        row = get_auth()

    if not row:
        return None

    return row[
        "access_token"
    ]


def ml_get(
    path,
    params=None,
    timeout=30
):

    token = get_access_token()

    if not token:

        return None, {
            "error":
                "Mercado Livre não conectado"
        }

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

    headers = {

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json",
    }

    try:

        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=timeout,
        )

        if response.status_code == 401:

            if refresh_ml_token():

                token = get_access_token()

                headers[
                    "Authorization"
                ] = (
                    f"Bearer {token}"
                )

                response = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=timeout,
                )

        try:

            data = response.json()

        except Exception:

            data = {
                "raw":
                    response.text[:3000]
            }

        return response, data

    except Exception as e:

        return None, {
            "error":
                str(e)
        }


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado",
            500
        )

    state = secrets.token_urlsafe(
        32
    )

    verifier = (
        make_code_verifier()
    )

    challenge = (
        make_code_challenge(
            verifier
        )
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
            "S256",
    }

    return redirect(
        ML_AUTH
        + "?"
        + urlencode(params)
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return (
            "<h2>Erro Mercado Livre</h2>"
            "<pre>"
            + clean_text(
                request.args.get(
                    "error_description",
                    error
                )
            )
            + "</pre>"
        ), 400

    state = request.args.get(
        "state"
    )

    code = request.args.get(
        "code"
    )

    if (
        not state
        or state != session.get(
            "ml_state"
        )
    ):

        return (
            "State OAuth inválido.",
            400
        )

    if not code:

        return (
            "Código OAuth não recebido.",
            400
        )

    verifier = session.get(
        "ml_code_verifier"
    )

    if not verifier:

        return (
            "Code verifier ausente.",
            400
        )

    try:

        response = requests.post(
            ML_TOKEN_URL,

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
                    verifier,
            },

            timeout=30,
        )

        if response.status_code != 200:

            return (
                "<h2>Erro ao obter token</h2>"
                "<pre>"
                + response.text
                + "</pre>"
            ), 400

        token_data = (
            response.json()
        )

        access_token = (
            token_data.get(
                "access_token"
            )
        )

        if not access_token:

            return (
                "Access token não recebido.",
                400
            )

        user_response = requests.get(
            ML_API + "/users/me",

            headers={
                "Authorization":
                    f"Bearer {access_token}"
            },

            timeout=30,
        )

        if user_response.status_code != 200:

            return (
                "<h2>Token obtido, "
                "mas /users/me falhou</h2>"
                "<pre>"
                + user_response.text
                + "</pre>"
            ), 400

        user = (
            user_response.json()
        )

        save_auth(
            {

                "access_token":
                    access_token,

                "refresh_token":
                    token_data.get(
                        "refresh_token"
                    ),

                "expires_at":
                    now_ts()
                    + int(
                        token_data.get(
                            "expires_in",
                            21600
                        )
                    ),

                "user_id":
                    user.get(
                        "id"
                    ),

                "nickname":
                    user.get(
                        "nickname"
                    ),
            }
        )

        session.pop(
            "ml_state",
            None
        )

        session.pop(
            "ml_code_verifier",
            None
        )

        return redirect("/")

    except Exception as e:

        return (
            "<h2>Erro OAuth</h2>"
            "<pre>"
            + str(e)
            + "</pre>"
        ), 500


@app.route(
    "/mercadolivre/reconnect"
)
def mercadolivre_reconnect():

    conn = db()

    conn.execute(
        "DELETE FROM ml_auth"
    )

    conn.commit()

    conn.close()

    return redirect(
        "/mercadolivre/login"
    )


# ============================================================
# CACHE PRODUTOS
# ============================================================

PRODUCT_CACHE = {}

SEARCH_CACHE = {}


# ============================================================
# DETALHE DE PRODUTO
# ============================================================

def get_product_detail(
    product_id
):

    if not product_id:
        return None

    if product_id in PRODUCT_CACHE:

        return PRODUCT_CACHE[
            product_id
        ]

    response, data = ml_get(
        f"/products/{product_id}"
    )

    if (
        not response
        or response.status_code != 200
        or not isinstance(
            data,
            dict
        )
    ):

        PRODUCT_CACHE[
            product_id
        ] = None

        return None

    PRODUCT_CACHE[
        product_id
    ] = data

    return data


# ============================================================
# BUSCA PRODUTO PELO TÍTULO
# ============================================================

def search_product_by_title(
    title
):

    title = clean_text(
        title
    )

    if len(title) < 8:
        return None

    key = normalize_text(
        title
    )

    if key in SEARCH_CACHE:

        return SEARCH_CACHE[
            key
        ]

    response, data = ml_get(

        "/products/search",

        params={

            "site_id":
                "MLB",

            "status":
                "active",

            "q":
                title[:180],

            "limit":
                5,
        }
    )

    if (
        not response
        or response.status_code != 200
        or not isinstance(
            data,
            dict
        )
    ):

        SEARCH_CACHE[
            key
        ] = None

        return None

    results = data.get(
        "results",
        []
    )

    if not results:

        SEARCH_CACHE[
            key
        ] = None

        return None

    title_words = set(
        normalize_text(
            title
        ).split()
    )

    best = None

    best_score = 0

    for result in results:

        result_name = normalize_text(
            result.get(
                "name",
                ""
            )
        )

        if not result_name:
            continue

        result_words = set(
            result_name.split()
        )

        intersection = (
            title_words
            & result_words
        )

        score = (
            len(intersection)
            / max(
                1,
                len(title_words)
            )
        )

        if score > best_score:

            best_score = score

            best = result

    SEARCH_CACHE[
        key
    ] = best

    return best


# ============================================================
# DOWNLOAD PÚBLICO
# ============================================================

def fetch_public_page(
    url
):

    try:

        response = requests.get(

            url,

            headers=PUBLIC_HEADERS,

            timeout=35,

            allow_redirects=True
        )

        print(
            "[PUBLIC]",
            response.status_code,
            len(
                response.content
            ),
            response.url
        )

        return {

            "ok":
                response.status_code == 200,

            "status":
                response.status_code,

            "url":
                response.url,

            "html":
                response.text,

            "bytes":
                len(
                    response.content
                ),
        }

    except Exception as e:

        print(
            "[PUBLIC ERRO]",
            url,
            e
        )

        return {

            "ok":
                False,

            "status":
                None,

            "url":
                url,

            "html":
                "",

            "bytes":
                0,

            "error":
                str(e),
        }


# ============================================================
# HTML -> TEXTO
# ============================================================

def normalize_public_text(
    source
):

    if not source:
        return ""

    source = re.sub(

        r"<script\b[^>]*>.*?</script>",

        " ",

        source,

        flags=re.I | re.S
    )

    source = re.sub(

        r"<style\b[^>]*>.*?</style>",

        " ",

        source,

        flags=re.I | re.S
    )

    source = re.sub(

        r"<noscript\b[^>]*>.*?</noscript>",

        " ",

        source,

        flags=re.I | re.S
    )

    source = re.sub(

        r"</(?:div|article|section|li|p|"
        r"h1|h2|h3|h4|h5|h6)>",

        "\n",

        source,

        flags=re.I
    )

    source = re.sub(

        r"<br\s*/?>",

        "\n",

        source,

        flags=re.I
    )

    source = re.sub(

        r"<[^>]+>",

        " ",

        source
    )

    source = html.unescape(
        source
    )

    source = clean_text(
        source
    )

    # Corrige HTML que separa:
    #
    # R$ 229 , 99
    #
    # para:
    #
    # R$ 229,99
    #

    source = re.sub(

        r"(\d)\s*,\s*(\d)",

        r"\1,\2",

        source
    )

    source = re.sub(

        r"(\d)\s*\.\s*(\d)",

        r"\1.\2",

        source
    )

    source = re.sub(
        r"\s+",
        " ",
        source
    )

    return source.strip()


# ============================================================
# PREÇO
# ============================================================

PRICE_PATTERN = (
    r"R\s*\$\s*"
    r"(\d{1,3}(?:\.\d{3})*"
    r"(?:,\d{2})"
    r"|\d+(?:,\d{2})"
    r"|\d+)"
)


PRICE_RE = re.compile(
    PRICE_PATTERN,
    re.I
)


# ============================================================
# CUPOM — SOMENTE CUPOM DE VERDADE
#
# IMPORTANTE:
#
# NÃO aceitar:
#
# 59% OFF
# 62% OFF
# 50% OFF
#
# como cupom.
#
# Só aceitar quando existir "Cupom"
# associado diretamente ao desconto.
# ============================================================

def detect_coupon(
    text
):

    text = clean_text(
        text
    )

    # --------------------------------------------------------
    # Cupom R$ 15 OFF
    # --------------------------------------------------------

    patterns_fixed = [

        r"cupom\s+"
        r"r\s*\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*off",

        r"r\s*\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*off"
        r"\s+com\s+cupom",
    ]

    for pattern in patterns_fixed:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = parse_money(
                match.group(1)
            )

            if (
                value is not None
                and value > 0
                and value <= 1000
            ):

                return {

                    "type":
                        "fixed",

                    "value":
                        value,

                    "label":
                        f"Cupom {money_br(value)} OFF",
                }

    # --------------------------------------------------------
    # Cupom 10% OFF
    # --------------------------------------------------------

    patterns_percent = [

        r"cupom\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off",

        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off"
        r"\s+com\s+cupom",
    ]

    for pattern in patterns_percent:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = float(
                match.group(1).replace(
                    ",",
                    "."
                )
            )

            if (
                value > 0
                and value <= 100
            ):

                return {

                    "type":
                        "percent",

                    "value":
                        value,

                    "label":
                        f"Cupom {value:g}% OFF",
                }

    return None


# ============================================================
# LIMPA TITULO
# ============================================================

def clean_product_title(
    text
):

    text = clean_text(
        text
    )

    # Preços.
    text = re.sub(

        r"R\s*\$\s*[\d.,]+",

        " ",

        text,

        flags=re.I
    )

    # Percentuais.
    text = re.sub(

        r"\d+(?:[.,]\d+)?"
        r"\s*%\s*off",

        " ",

        text,

        flags=re.I
    )

    # Parcelas.
    text = re.sub(

        r"\d+x\s*"
        r"R?\$?\s*"
        r"[\d.,]+",

        " ",

        text,

        flags=re.I
    )

    # Cupom.
    text = re.sub(

        r"cupom\s+"
        r"(?:r\s*\$\s*)?"
        r"[\d.,]+"
        r"\s*(?:off)?",

        " ",

        text,

        flags=re.I
    )

    # Frete.
    text = re.sub(

        r"frete grátis.*",

        " ",

        text,

        flags=re.I
    )

    # Entrega.
    text = re.sub(

        r"chegará.*",

        " ",

        text,

        flags=re.I
    )

    text = re.sub(

        r"por ser sua primeira compra.*",

        " ",

        text,

        flags=re.I
    )

    # Marcadores.
    text = re.sub(

        r"\bmais vendido\b",

        " ",

        text,

        flags=re.I
    )

    text = re.sub(

        r"\boferta imperdível\b",

        " ",

        text,

        flags=re.I
    )

    text = re.sub(

        r"\boferta relâmpago\b",

        " ",

        text,

        flags=re.I
    )

    text = clean_text(
        text
    )

    return text[:280]


# ============================================================
# TITULO VÁLIDO
# ============================================================

def looks_like_title(
    text
):

    text = clean_product_title(
        text
    )

    if len(text) < 12:
        return False

    low = normalize_text(
        text
    )

    bad = [

        "cupom",

        "frete",

        "chegara",

        "vendidos",

        "vendido",

        "sem juros",

        "pix",

        "oferta do dia",

        "oferta relampago",

        "mais vendido",

        "por ser sua primeira compra",

    ]

    for word in bad:

        if word in low:

            return False

    if not re.search(
        r"[a-záàãâéêíóôõúç]",
        text,
        re.I
    ):

        return False

    return True


# ============================================================
# CALCULO
# ============================================================

def calculate_offer(
    price,
    coupon
):

    if not valid_price(
        price
    ):
        return None

    if not coupon:
        return None

    if coupon["type"] == "percent":

        discount = (
            price
            * coupon["value"]
            / 100
        )

    else:

        discount = min(
            price,
            coupon["value"]
        )

    final_price = (
        price
        - discount
    )

    if final_price >= price:
        return None

    return {

        "price":
            round(
                price,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "discount":
            round(
                discount,
                2
            ),

        "effective_percent":
            round(
                discount
                / price
                * 100,
                2
            ),

        "coupon_label":
            coupon["label"],
    }


# ============================================================
# EXTRAI UMA OFERTA
#
# Aqui está a principal correção.
# ============================================================

def parse_one_coupon(
    text,
    coupon_match
):

    coupon_start = (
        coupon_match.start()
    )

    # Pega até 1.000 caracteres antes
    # do cupom.
    before_start = max(
        0,
        coupon_start - 1200
    )

    before = text[
        before_start:
        coupon_start
    ]

    coupon_context = (
        before
        + " "
        + text[
            coupon_start:
            min(
                len(text),
                coupon_start + 250
            )
        ]
    )

    coupon = detect_coupon(
        coupon_context
    )

    if not coupon:
        return None

    # ========================================================
    # 1. ENCONTRAR O PREÇO ATUAL
    #
    # O Mercado Livre normalmente entrega:
    #
    # R$ 229,99
    # R$ 85,99
    # 62% OFF
    #
    # O segundo preço é o preço atual.
    # ========================================================

    discount_price_pattern = re.compile(

        r"R\s*\$\s*"
        r"([\d.,]+)"
        r"\s+"
        r"\d+(?:[.,]\d+)?"
        r"\s*%\s*OFF",

        re.I
    )

    price_matches = list(
        discount_price_pattern.finditer(
            before
        )
    )

    current_price = None

    current_price_match = None

    if price_matches:

        current_price_match = (
            price_matches[-1]
        )

        current_price = parse_money(
            current_price_match.group(1)
        )

    # ========================================================
    # 2. FALLBACK
    #
    # Se não houver "% OFF" associado,
    # procura os preços próximos.
    # ========================================================

    if current_price is None:

        all_prices = list(
            PRICE_RE.finditer(
                before
            )
        )

        valid_prices = []

        for match in all_prices:

            value = parse_money(
                match.group(1)
            )

            if value is None:
                continue

            if value < MIN_PRICE:
                continue

            valid_prices.append(
                (
                    value,
                    match
                )
            )

        if valid_prices:

            current_price, current_price_match = (
                valid_prices[-1]
            )

    if not valid_price(
        current_price
    ):

        print(
            "[IGNORADO PREÇO]",
            current_price,
            coupon["label"]
        )

        return None

    # ========================================================
    # 3. ENCONTRAR O PREÇO ORIGINAL
    # ========================================================

    original_price = None

    original_match = None

    if current_price_match:

        prices_before_current = list(
            PRICE_RE.finditer(
                before[
                    :current_price_match.start()
                ]
            )
        )

        if prices_before_current:

            original_match = (
                prices_before_current[-1]
            )

            original_price = parse_money(
                original_match.group(1)
            )

    # ========================================================
    # 4. TITULO
    #
    # Pega o texto entre o desconto do
    # produto anterior e o preço original.
    # ========================================================

    title = ""

    if original_match:

        title_start = 0

        previous_offs = list(
            re.finditer(
                r"\d+(?:[.,]\d+)?"
                r"\s*%\s*OFF",
                before[
                    :original_match.start()
                ],
                re.I
            )
        )

        if previous_offs:

            title_start = (
                previous_offs[-1].end()
            )

        title_raw = before[
            title_start:
            original_match.start()
        ]

        title = clean_product_title(
            title_raw
        )

    # ========================================================
    # 5. FALLBACK DO TITULO
    # ========================================================

    if not looks_like_title(
        title
    ):

        # Procura um trecho menor
        # imediatamente antes do preço.
        if original_match:

            start = max(
                0,
                original_match.start() - 450
            )

            title_raw = before[
                start:
                original_match.start()
            ]

            # Pega a última frase
            # razoavelmente longa.
            parts = re.split(
                r"(?<=[.!?])\s+",
                title_raw
            )

            candidates = []

            for part in parts:

                cleaned = clean_product_title(
                    part
                )

                if looks_like_title(
                    cleaned
                ):

                    candidates.append(
                        cleaned
                    )

            if candidates:

                title = candidates[-1]

            else:

                title = clean_product_title(
                    title_raw
                )

    if not looks_like_title(
        title
    ):

        print(
            "[IGNORADO TITULO]",
            title
        )

        return None

    # ========================================================
    # 6. CALCULAR CUPOM
    # ========================================================

    offer = calculate_offer(
        current_price,
        coupon
    )

    if not offer:
        return None

    # ========================================================
    # 7. RESULTADO
    # ========================================================

    return {

        **offer,

        "title":
            title,

        "product_id":
            None,

        "url":
            "",

        "source":
            "descontaco-cupons",

        "coupon_confirmed":
            True,

        "coupon_type":
            coupon["type"],

        "coupon_value":
            coupon["value"],

        "original_price":
            (
                round(
                    original_price,
                    2
                )
                if original_price
                is not None
                else None
            ),
    }


# ============================================================
# PARSER DOS CUPONS
# ============================================================

def parse_coupon_cards(
    source
):

    text = normalize_public_text(
        source
    )

    print(
        "[PARSER] texto:",
        len(text)
    )

    # ========================================================
    # LOCALIZA SOMENTE CUPONS REAIS
    #
    # Não procura simplesmente "cupom".
    # Procura a estrutura do cupom.
    # ========================================================

    coupon_regexes = [

        re.compile(
            r"cupom\s+"
            r"r\s*\$\s*"
            r"[\d.,]+\s*"
            r"off",
            re.I
        ),

        re.compile(
            r"r\s*\$\s*"
            r"[\d.,]+\s*"
            r"off\s+"
            r"com\s+cupom",
            re.I
        ),

        re.compile(
            r"cupom\s+"
            r"\d+(?:[.,]\d+)?"
            r"\s*%\s*off",
            re.I
        ),

        re.compile(
            r"\d+(?:[.,]\d+)?"
            r"\s*%\s*off\s+"
            r"com\s+cupom",
            re.I
        ),
    ]

    coupon_matches = []

    for regex in coupon_regexes:

        coupon_matches.extend(
            regex.finditer(
                text
            )
        )

    # Ordena pela posição.
    coupon_matches.sort(
        key=lambda x:
            x.start()
    )

    print(
        "[PARSER] cupons reais:",
        len(coupon_matches)
    )

    offers = []

    seen_positions = set()

    for match in coupon_matches:

        position = (
            match.start()
        )

        if position in seen_positions:
            continue

        seen_positions.add(
            position
        )

        try:

            item = parse_one_coupon(
                text,
                match
            )

            if item:

                offers.append(
                    item
                )

                print(
                    "[CUPOM OK]",
                    item["title"][:100],
                    "|",
                    item["coupon_label"],
                    "|",
                    money_br(
                        item["price"]
                    ),
                    "=>",
                    money_br(
                        item["final_price"]
                    )
                )

        except Exception as e:

            print(
                "[CUPOM PARSE ERRO]",
                e
            )

    return offers


# ============================================================
# RESOLVER PRODUTO
# ============================================================

def resolve_product(
    offer
):

    title = clean_text(
        offer.get(
            "title",
            ""
        )
    )

    if not title:
        return offer

    result = search_product_by_title(
        title
    )

    if result:

        product_id = result.get(
            "id"
        )

        if product_id:

            offer[
                "product_id"
            ] = product_id

            offer[
                "catalog_id"
            ] = product_id

            if result.get(
                "name"
            ):

                offer[
                    "catalog_title"
                ] = result.get(
                    "name"
                )

            detail = get_product_detail(
                product_id
            )

            if detail:

                if detail.get(
                    "name"
                ):

                    offer[
                        "catalog_title"
                    ] = detail.get(
                        "name"
                    )

                if detail.get(
                    "permalink"
                ):

                    offer[
                        "url"
                    ] = detail.get(
                        "permalink"
                    )

    # Fallback.
    if not offer.get(
        "url"
    ):

        offer[
            "url"
        ] = (
            "https://lista.mercadolivre.com.br/"
            + quote(
                title,
                safe=""
            )
        )

    return offer


# ============================================================
# DEDUPLICAÇÃO
# ============================================================

def deduplicate_offers(
    offers
):

    grouped = {}

    for item in offers:

        title = clean_text(
            item.get(
                "title",
                ""
            )
        )

        price = item.get(
            "price"
        )

        if not title:
            continue

        if not valid_price(
            price
        ):
            continue

        product_id = item.get(
            "product_id"
        )

        if product_id:

            key = (
                "id:"
                + product_id
            )

        else:

            key = (
                "title:"
                + product_key(
                    title
                )
            )

        old = grouped.get(
            key
        )

        if old is None:

            grouped[
                key
            ] = item

            continue

        current_score = (

            float(
                item.get(
                    "discount",
                    0
                )
            ),

            float(
                item.get(
                    "effective_percent",
                    0
                )
            ),

            -float(
                item.get(
                    "final_price",
                    999999
                )
            ),
        )

        old_score = (

            float(
                old.get(
                    "discount",
                    0
                )
            ),

            float(
                old.get(
                    "effective_percent",
                    0
                )
            ),

            -float(
                old.get(
                    "final_price",
                    999999
                )
            ),
        )

        if current_score > old_score:

            grouped[
                key
            ] = item

    return list(
        grouped.values()
    )


# ============================================================
# CAÇADOR
# ============================================================

def hunt_offers():

    print("")
    print(
        "=========================================="
    )
    print(
        "🤑 CAÇADOR DE OFERTAS"
    )
    print(
        "🎟️ CUPOM PRIMEIRO"
    )
    print(
        "=========================================="
    )

    result = fetch_public_page(
        COUPON_HUB
    )

    if not result.get(
        "ok"
    ):

        print(
            "[HUB ERRO]",
            result.get(
                "status"
            )
        )

        return []

    source = result.get(
        "html",
        ""
    )

    print(
        "[HUB]",
        len(source),
        "bytes"
    )

    print(
        "[HUB CUPOM TEXTUAL]",
        source.lower().count(
            "cupom"
        )
    )

    # ========================================================
    # EXTRAI SOMENTE CUPONS REAIS
    # ========================================================

    offers = parse_coupon_cards(
        source
    )

    print(
        "[OFERTAS EXTRAÍDAS]",
        len(offers)
    )

    # ========================================================
    # RESOLVE OS PRODUTOS
    # ========================================================

    resolved = []

    for offer in offers:

        try:

            offer = resolve_product(
                offer
            )

            resolved.append(
                offer
            )

        except Exception as e:

            print(
                "[RESOLVE ERRO]",
                e
            )

            resolved.append(
                offer
            )

        time.sleep(
            0.08
        )

    # ========================================================
    # UM PRODUTO = UMA OPORTUNIDADE
    # ========================================================

    offers = deduplicate_offers(
        resolved
    )

    # ========================================================
    # RANKING
    #
    # 1. maior economia real em R$
    # 2. maior percentual
    # 3. menor preço final
    # ========================================================

    offers.sort(
        key=lambda x: (

            -float(
                x.get(
                    "discount",
                    0
                )
            ),

            -float(
                x.get(
                    "effective_percent",
                    0
                )
            ),

            float(
                x.get(
                    "final_price",
                    999999
                )
            ),
        )
    )

    print(
        "[PRODUTOS ÚNICOS]",
        len(offers)
    )

    return offers[
        :MAX_OFFERS
    ]


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/api/status"
)
def api_status():

    row = get_auth()

    if not row:

        return jsonify(
            {
                "connected":
                    False
            }
        )

    return jsonify(
        {

            "connected":
                True,

            "user_id":
                row["user_id"],

            "nickname":
                row["nickname"],

            "expires_at":
                row["expires_at"],
        }
    )


# ============================================================
# HUNT
# ============================================================

@app.route(
    "/api/hunt"
)
def api_hunt():

    try:

        offers = hunt_offers()

        total_discount = sum(

            float(
                item.get(
                    "discount",
                    0
                )
            )

            for item in offers
        )

        total_final = sum(

            float(
                item.get(
                    "final_price",
                    0
                )
            )

            for item in offers
        )

        return jsonify(
            {

                "ok":
                    True,

                "total":
                    len(offers),

                "coupon_confirmed":
                    len(offers),

                "total_discount":
                    round(
                        total_discount,
                        2
                    ),

                "total_final":
                    round(
                        total_final,
                        2
                    ),

                "offers":
                    offers,
            }
        )

    except Exception as e:

        print(
            "[HUNT ERRO]",
            e
        )

        return jsonify(
            {

                "ok":
                    False,

                "error":
                    str(e),
            }
        ), 500


# ============================================================
# DEBUG PÚBLICO
# ============================================================

@app.route(
    "/api/debug-public"
)
def api_debug_public():

    result = fetch_public_page(
        COUPON_HUB
    )

    if not result.get(
        "ok"
    ):

        return jsonify(
            {

                "ok":
                    False,

                "status":
                    result.get(
                        "status"
                    ),

                "error":
                    result.get(
                        "error"
                    ),
            }
        )

    source = result.get(
        "html",
        ""
    )

    visible = normalize_public_text(
        source
    )

    offers = parse_coupon_cards(
        source
    )

    product_ids = extract_mlb_ids(
        source
    )

    # ========================================================
    # MOSTRA CONTEXTOS DOS CUPONS
    # ========================================================

    coupon_regex = re.compile(

        r"cupom\s+"
        r"(?:r\s*\$\s*[\d.,]+"
        r"|[\d.,]+\s*%\s*)"
        r"off",

        re.I
    )

    matches = list(
        coupon_regex.finditer(
            visible
        )
    )

    samples = []

    for match in matches[:10]:

        start = max(
            0,
            match.start() - 350
        )

        end = min(
            len(visible),
            match.end() + 350
        )

        samples.append(
            {

                "coupon":
                    match.group(0),

                "text":
                    visible[
                        start:end
                    ],
            }
        )

    return jsonify(
        {

            "ok":
                True,

            "status":
                result.get(
                    "status"
                ),

            "bytes":
                len(source),

            "cupom_count":
                source.lower().count(
                    "cupom"
                ),

            "coupon_occurrences":
                len(matches),

            "produto_count":
                len(product_ids),

            "unique_products":
                len(
                    set(
                        product_ids
                    )
                ),

            "offers_detected":
                len(offers),

            "sample_coupon_contexts":
                samples,

            "sample_offers":
                [

                    {

                        "title":
                            item.get(
                                "title"
                            ),

                        "price":
                            item.get(
                                "price"
                            ),

                        "coupon":
                            item.get(
                                "coupon_label"
                            ),

                        "discount":
                            item.get(
                                "discount"
                            ),

                        "final":
                            item.get(
                                "final_price"
                            ),
                    }

                    for item in offers[:20]
                ],

            "sample_product_ids":
                list(
                    dict.fromkeys(
                        product_ids
                    )
                )[:20],
        }
    )


# ============================================================
# DIAGNÓSTICO ML
# ============================================================

@app.route(
    "/api/ml-diagnostic"
)
def ml_diagnostic():

    result = {

        "config": {

            "client_id":
                bool(
                    ML_CLIENT_ID
                ),

            "client_secret":
                bool(
                    ML_CLIENT_SECRET
                ),

            "redirect_uri":
                ML_REDIRECT_URI,
        },

        "auth":
            None,

        "tests":
            [],
    }

    row = get_auth()

    if not row:

        result[
            "auth"
        ] = {

            "connected":
                False
        }

        return jsonify(
            result
        )

    result[
        "auth"
    ] = {

        "connected":
            True,

        "user_id":
            row["user_id"],

        "nickname":
            row["nickname"],

        "expires_at":
            row["expires_at"],
    }

    response, data = ml_get(
        "/users/me"
    )

    result[
        "tests"
    ].append(
        {

            "name":
                "users_me",

            "status":
                response.status_code
                if response
                else None,

            "ok":
                bool(
                    response
                    and response.status_code == 200
                ),

            "data":
                data,
        }
    )

    response, data = ml_get(

        "/products/search",

        params={

            "site_id":
                "MLB",

            "q":
                "celular",

            "status":
                "active",

            "limit":
                1,
        }
    )

    result[
        "tests"
    ].append(
        {

            "name":
                "product_search",

            "status":
                response.status_code
                if response
                else None,

            "ok":
                bool(
                    response
                    and response.status_code == 200
                ),

            "data":
                (

                    {

                        "paging":
                            data.get(
                                "paging"
                            ),

                        "results":
                            data.get(
                                "results",
                                []
                            )[:1],
                    }

                    if isinstance(
                        data,
                        dict
                    )

                    else data
                ),
        }
    )

    return jsonify(
        result
    )


# ============================================================
# INTERFACE
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

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;

    background: #f3f4f6;

    color: #222;
}

.header {
    background: #ffe600;

    padding: 18px 16px;

    box-shadow:
        0 3px 10px
        rgba(0,0,0,.12);
}

.header h1 {
    margin: 0;

    font-size: 27px;

    font-weight: 900;
}

.header p {
    margin: 7px 0 0;

    font-size: 15px;
}

.container {
    max-width: 1100px;

    margin: auto;

    padding: 18px;
}

.connection {
    background: white;

    border-radius: 16px;

    padding: 18px;

    margin-bottom: 18px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.06);

    display: flex;

    justify-content:
        space-between;

    align-items: center;

    gap: 15px;

    flex-wrap: wrap;
}

.connected {
    color: #398b46;

    font-size: 18px;

    font-weight: 800;
}

.disconnected {
    color: #d32f2f;

    font-weight: 700;
}

.btn {
    display: inline-block;

    border: 0;

    border-radius: 12px;

    padding: 13px 18px;

    text-decoration: none;

    font-weight: 800;

    cursor: pointer;
}

.btn-red {
    background: #df493e;

    color: white;
}

.hunt {
    width: 100%;

    background: #ffe600;

    color: #222;

    font-size: 19px;

    padding: 19px;

    margin-bottom: 20px;
}

.hunt:disabled {
    opacity: .65;
}

.stats {
    display: grid;

    grid-template-columns:
        repeat(4,1fr);

    gap: 14px;

    margin-bottom: 18px;
}

.stat {
    background: white;

    border-radius: 16px;

    padding: 17px;

    box-shadow:
        0 3px 15px
        rgba(0,0,0,.05);
}

.stat-title {
    color: #777;

    font-size: 14px;
}

.stat-value {
    margin-top: 7px;

    font-size: 28px;

    font-weight: 900;
}

.status {
    background: white;

    border-radius: 14px;

    padding: 16px;

    margin-bottom: 18px;

    font-size: 15px;

    box-shadow:
        0 3px 12px
        rgba(0,0,0,.04);
}

.grid {
    display: grid;

    grid-template-columns:
        repeat(auto-fill,minmax(285px,1fr));

    gap: 16px;
}

.card {
    background: white;

    border-radius: 16px;

    padding: 17px;

    box-shadow:
        0 3px 14px
        rgba(0,0,0,.06);
}

.badge {
    display: inline-block;

    padding: 7px 10px;

    border-radius: 8px;

    background: #fff3bf;

    color: #7a5a00;

    font-size: 12px;

    font-weight: 900;

    margin-bottom: 11px;
}

.title {
    font-size: 16px;

    line-height: 1.4;

    font-weight: 800;

    margin-bottom: 12px;
}

.original {
    color: #999;

    font-size: 13px;

    text-decoration: line-through;

    margin-bottom: 2px;
}

.price-old {
    color: #888;

    font-size: 13px;
}

.price {
    font-size: 24px;

    font-weight: 900;

    margin-top: 3px;
}

.final {
    color: #087c32;

    font-size: 21px;

    font-weight: 900;

    margin-top: 10px;
}

.savings {
    color: #087c32;

    font-weight: 800;

    margin-top: 7px;
}

.coupon {
    background: #fff7cc;

    border: 1px solid #f0d64a;

    border-radius: 10px;

    padding: 10px;

    margin-top: 12px;

    font-weight: 900;
}

.warning {
    color: #777;

    font-size: 11px;

    margin-top: 10px;

    line-height: 1.4;
}

.open {
    width: 100%;

    margin-top: 13px;

    text-align: center;

    background: #3483fa;

    color: white;
}

.empty {
    background: white;

    border-radius: 16px;

    padding: 35px 20px;

    text-align: center;

    color: #777;

    font-size: 17px;
}

@media(max-width:700px) {

    .stats {
        grid-template-columns:
            repeat(2,1fr);
    }

    .container {
        padding: 12px;
    }

    .header h1 {
        font-size: 25px;
    }

}

</style>

</head>

<body>

<div class="header">

<h1>
💰 Caçador de Ofertas
</h1>

<p>
🎟️ Cupons reais + produtos acima de R$69,90
</p>

</div>


<div class="container">


<div class="connection">

<div id="connection">
Verificando conexão...
</div>

<a
    class="btn btn-red"
    href="/mercadolivre/reconnect"
>
Reconectar
</a>

</div>


<button
    id="hunt"
    class="btn hunt"
    onclick="hunt()"
>
🔎 CAÇAR OFERTAS
</button>


<div class="stats">

<div class="stat">

<div class="stat-title">
Ofertas
</div>

<div
    id="total"
    class="stat-value"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Cupom confirmado
</div>

<div
    id="coupons"
    class="stat-value"
>
0
</div>

</div>


<div class="stat">

<div class="stat-title">
Desconto
</div>

<div
    id="discount"
    class="stat-value"
>
R$ 0,00
</div>

</div>


<div class="stat">

<div class="stat-title">
Valor final
</div>

<div
    id="final"
    class="stat-value"
>
R$ 0,00
</div>

</div>

</div>


<div
    id="status"
    class="status"
>
Pronto para caçar.
</div>


<div
    id="results"
    class="grid"
>

<div class="empty">
Clique em "CAÇAR OFERTAS".
</div>

</div>


</div>


<script>

function money(value) {

    return new Intl.NumberFormat(
        "pt-BR",
        {
            style: "currency",
            currency: "BRL"
        }
    ).format(
        Number(value || 0)
    );
}


function esc(value) {

    return String(
        value || ""
    )
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}


async function loadStatus() {

    try {

        const response =
            await fetch(
                "/api/status"
            );

        const data =
            await response.json();

        const box =
            document.getElementById(
                "connection"
            );

        if (data.connected) {

            box.className =
                "connected";

            box.innerHTML =
                "🟢 Mercado Livre conectado como " +
                "<strong>" +
                esc(
                    data.nickname ||
                    data.user_id
                ) +
                "</strong>";

        } else {

            box.className =
                "disconnected";

            box.innerHTML =
                '🔴 Mercado Livre não conectado ' +
                '<a class="btn" ' +
                'style="background:#ffe600;color:#222" ' +
                'href="/mercadolivre/login">' +
                'Conectar' +
                '</a>';
        }

    } catch (e) {

        document.getElementById(
            "connection"
        ).innerHTML =
            "Erro ao verificar conexão.";
    }
}


function renderOffers(
    offers
) {

    const results =
        document.getElementById(
            "results"
        );

    if (
        !offers ||
        !offers.length
    ) {

        results.innerHTML = `
            <div class="empty">

                <strong>
                    Nenhum produto com cupom encontrado.
                </strong>

                <br><br>

                O sistema encontrou os cupons,
                mas nenhum produto passou pelos
                filtros atuais.

                <br><br>

                <a
                    href="/api/debug-public"
                    target="_blank"
                >
                    Ver diagnóstico
                </a>

            </div>
        `;

        return;
    }


    results.innerHTML =
        offers.map(
            item => `

            <div class="card">

                <div class="badge">
                    🎟️ CUPOM CONFIRMADO
                </div>


                <div class="title">
                    ${esc(item.title)}
                </div>


                ${
                    item.original_price
                    ? `
                        <div class="original">
                            ${money(
                                item.original_price
                            )}
                        </div>
                      `
                    : ""
                }


                <div class="price-old">
                    Preço atual antes do cupom:
                </div>


                <div class="price">
                    ${money(item.price)}
                </div>


                <div class="coupon">
                    ${esc(item.coupon_label)}
                </div>


                <div class="final">
                    Final estimado:
                    ${money(item.final_price)}
                </div>


                <div class="savings">
                    Economia do cupom:
                    ${money(item.discount)}

                    (${Number(
                        item.effective_percent || 0
                    ).toFixed(1)}%)
                </div>


                <div class="warning">
                    ⚠️ O cálculo considera
                    somente o cupom identificado.
                    Descontos diretos do vendedor
                    não foram somados. Confirme
                    a aplicação no checkout.
                </div>


                <a
                    class="btn open"
                    href="${esc(item.url)}"
                    target="_blank"
                    rel="noopener"
                >
                    🛒 VER OFERTA
                </a>

            </div>

            `
        ).join("");
}


async function hunt() {

    const button =
        document.getElementById(
            "hunt"
        );

    const status =
        document.getElementById(
            "status"
        );

    button.disabled = true;

    button.innerText =
        "⏳ CAÇANDO CUPONS...";

    status.innerText =
        "Lendo ofertas e identificando cupons reais...";


    try {

        const response =
            await fetch(
                "/api/hunt"
            );

        const data =
            await response.json();


        if (!data.ok) {

            throw new Error(
                data.error ||
                "Erro ao caçar ofertas."
            );
        }


        const offers =
            data.offers || [];


        document.getElementById(
            "total"
        ).innerText =
            offers.length;


        document.getElementById(
            "coupons"
        ).innerText =
            data.coupon_confirmed ||
            offers.length;


        document.getElementById(
            "discount"
        ).innerText =
            money(
                data.total_discount
            );


        document.getElementById(
            "final"
        ).innerText =
            money(
                data.total_final
            );


        status.innerText =
            "✅ Caçada concluída: "
            + offers.length
            + " produto(s) único(s) com cupom.";


        renderOffers(
            offers
        );


    } catch (error) {

        console.error(
            error
        );

        status.innerText =
            "❌ "
            + error.message;

        document.getElementById(
            "results"
        ).innerHTML = `
            <div class="empty">

                ❌ Erro ao caçar ofertas.

                <br><br>

                ${esc(error.message)}

            </div>
        `;

    } finally {

        button.disabled = false;

        button.innerText =
            "🔎 CAÇAR OFERTAS";
    }
}


loadStatus();

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
        HTML
    )


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify(
        {

            "status":
                "ok",

            "app":
                "cacador-de-ofertas",

            "time":
                now_ts(),
        }
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