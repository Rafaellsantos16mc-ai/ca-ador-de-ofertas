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
# PÁGINA OFICIAL DE CUPONS
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

    value = value.replace(
        "\xa0",
        " "
    )

    value = value.replace(
        "\\/",
        "/"
    )

    value = value.replace(
        "\\u002F",
        "/"
    )

    value = value.replace(
        "\\u003A",
        ":"
    )

    value = value.replace(
        "\\u0026",
        "&"
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
    )

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
# LOGIN MERCADO LIVRE
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
# PRODUTO DO CATÁLOGO
# ============================================================

PRODUCT_CACHE = {}

SEARCH_CACHE = {}


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
# ENCONTRAR PRODUTO PELO TÍTULO
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

    normalized_title = normalize_text(
        title
    )

    best = None

    best_score = 0

    title_words = set(
        normalized_title.split()
    )

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

        if not title_words:
            continue

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

    if (
        best is not None
        and best_score >= 0.45
    ):

        SEARCH_CACHE[
            key
        ] = best

        return best

    # Se o resultado for muito próximo,
    # ainda podemos usar o primeiro.
    first = results[0]

    if isinstance(
        first,
        dict
    ):

        SEARCH_CACHE[
            key
        ] = first

        return first

    SEARCH_CACHE[
        key
    ] = None

    return None


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
# HTML VISÍVEL
# ============================================================

def visible_html(
    source
):

    if not source:
        return ""

    source = re.sub(
        r"<script\b[^>]*>.*?</script>",
        "\n",
        source,
        flags=re.I | re.S
    )

    source = re.sub(
        r"<style\b[^>]*>.*?</style>",
        "\n",
        source,
        flags=re.I | re.S
    )

    source = re.sub(
        r"<noscript\b[^>]*>.*?</noscript>",
        "\n",
        source,
        flags=re.I | re.S
    )

    # Cria quebras de linha nos elementos
    # que normalmente separam os cards.
    source = re.sub(
        r"</(?:h1|h2|h3|h4|h5|h6|div|article|section|li|p|a|br|span)>",
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

    source = source.replace(
        "\xa0",
        " "
    )

    return source


def html_lines(
    source
):

    text = visible_html(
        source
    )

    lines = []

    for line in text.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        if len(line) > 800:

            pieces = re.split(
                r"\s{2,}",
                line
            )

            for piece in pieces:

                piece = clean_text(
                    piece
                )

                if piece:
                    lines.append(
                        piece
                    )

        else:

            lines.append(
                line
            )

    return lines


# ============================================================
# PREÇOS
# ============================================================

PRICE_RE = re.compile(
    r"R\$\s*"
    r"(\d{1,3}(?:\.\d{3})*"
    r"(?:,\d{2})"
    r"|\d+(?:,\d{2})"
    r"|\d+)",
    re.I
)


def extract_prices(
    text
):

    result = []

    for match in PRICE_RE.finditer(
        text or ""
    ):

        value = parse_money(
            match.group(1)
        )

        if value is not None:

            result.append(
                value
            )

    return result


# ============================================================
# CUPOM
# ============================================================

def detect_coupon(
    text
):

    text = clean_text(
        text
    )

    if not text:
        return None

    # Cupom 15% OFF
    match = re.search(
        r"cupom"
        r".{0,100}?"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off",
        text,
        re.I
    )

    if not match:

        match = re.search(
            r"(\d+(?:[.,]\d+)?)"
            r"\s*%\s*off"
            r".{0,100}?"
            r"cupom",
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

        if 0 < value <= 100:

            return {
                "type":
                    "percent",

                "value":
                    value,

                "label":
                    f"Cupom {value:g}% OFF",
            }

    # Cupom R$15 OFF
    match = re.search(
        r"cupom"
        r".{0,100}?"
        r"r\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"(?:\s*off)?",
        text,
        re.I
    )

    if match:

        value = parse_money(
            match.group(1)
        )

        if (
            value is not None
            and 0 < value <= 1000
        ):

            return {
                "type":
                    "fixed",

                "value":
                    value,

                "label":
                    f"Cupom {money_br(value)} OFF",
            }

    return None


# ============================================================
# CALCULAR DESCONTO
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
# FILTRAR LINHA
# ============================================================

def looks_like_product_title(
    line
):

    line = clean_text(
        line
    )

    if len(line) < 12:
        return False

    low = normalize_text(
        line
    )

    bad = [

        "cupom",

        "frete",

        "chegara",

        "vendidos",

        "oferta do dia",

        "oferta imperdivel",

        "oferta relampago",

        "mais vendido",

        "disponivel em",

        "ver mais",

        "ir para produto",

        "sem juros",

        "pix",

        "classificacao",

        "avaliacao",

        "veja aqui",

        "ofertas com cupons",

        "ofertas do dia",
    ]

    for word in bad:

        if word in low:

            return False

    if "R$" in line:

        return False

    # Horários / contadores.
    if re.fullmatch(
        r"[\d\s:.-]+",
        line
    ):

        return False

    return True


# ============================================================
# PARSE DOS CARDS VISÍVEIS
# ============================================================

def parse_visible_coupon_cards(
    source
):

    lines = html_lines(
        source
    )

    print(
        "[VISIBLE] linhas:",
        len(lines)
    )

    coupon_indexes = []

    for index, line in enumerate(
        lines
    ):

        if "cupom" in line.lower():

            coupon_indexes.append(
                index
            )

    print(
        "[VISIBLE] ocorrências cupom:",
        len(coupon_indexes)
    )

    offers = []

    for index in coupon_indexes:

        start = max(
            0,
            index - 18
        )

        end = min(
            len(lines),
            index + 8
        )

        context = lines[
            start:end
        ]

        coupon_text = lines[
            index
        ]

        coupon = detect_coupon(
            coupon_text
        )

        if not coupon:

            # Às vezes o texto do cupom
            # fica dividido entre elementos.
            coupon = detect_coupon(
                " ".join(
                    context
                )
            )

        if not coupon:
            continue

        # ----------------------------------------------------
        # Preço atual:
        #
        # usamos o último preço ANTES do cupom.
        # Isso evita usar preço de frete,
        # parcela ou produto seguinte.
        # ----------------------------------------------------

        price = None

        price_line_index = None

        for i in range(
            index - 1,
            start - 1,
            -1
        ):

            candidate = lines[
                i
            ]

            prices = extract_prices(
                candidate
            )

            if not prices:
                continue

            # Último