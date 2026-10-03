import os
import re
import time
import sqlite3
import secrets
import hashlib
import base64
import html
import unicodedata
import json
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
# CONFIGURAÇÕES
# ============================================================

MAX_PRODUCTS_FROM_HUB = 120

MAX_OFFERS = 50

PUBLIC_TIMEOUT = 35

ML_TIMEOUT = 30


# ============================================================
# PÁGINAS OFICIAIS
# ============================================================

COUPON_HUBS = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/l/promocoes",
]


# ============================================================
# HEADERS PÚBLICOS
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
        "\\u002F",
        "/"
    )

    value = value.replace(
        "\\/",
        "/"
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
        "imperdivel",
        "novo",
        "nova",
        "original",
        "frete",
        "gratis",
        "mercado",
        "livre",
    }

    words = [
        word
        for word in text.split()
        if word not in remove
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


def extract_first_mlb(text):

    ids = extract_mlb_ids(
        text
    )

    if ids:
        return ids[0]

    return None


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
            1,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
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

            timeout=ML_TIMEOUT,
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
    timeout=ML_TIMEOUT
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

            timeout=ML_TIMEOUT,
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

            timeout=ML_TIMEOUT,
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
# PRODUTO
# ============================================================

PRODUCT_CACHE = {}


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

        print(
            "[PRODUCT DETAIL]",
            product_id,
            response.status_code
            if response
            else None
        )

        PRODUCT_CACHE[
            product_id
        ] = None

        return None

    PRODUCT_CACHE[
        product_id
    ] = data

    return data


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

            timeout=PUBLIC_TIMEOUT,

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
# DESCODIFICAR HTML EMBUTIDO
# ============================================================

def decode_embedded_html(
    source
):

    if not source:
        return ""

    text = source

    replacements = {

        "\\u002F":
            "/",

        "\\u003A":
            ":",

        "\\u003F":
            "?",

        "\\u003D":
            "=",

        "\\u0026":
            "&",

        "\\u003C":
            "<",

        "\\u003E":
            ">",

        "\\u0022":
            '"',

        "\\u0027":
            "'",

        "\\/":
            "/",
    }

    for old, new in replacements.items():

        text = text.replace(
            old,
            new
        )

    text = html.unescape(
        text
    )

    return text


# ============================================================
# EXTRAIR SCRIPTS
# ============================================================

def extract_script_blocks(
    source
):

    if not source:
        return []

    blocks = re.findall(
        r"<script\b[^>]*>"
        r"(.*?)"
        r"</script>",
        source,
        flags=re.I | re.S
    )

    result = []

    for block in blocks:

        block = decode_embedded_html(
            block
        )

        if block.strip():

            result.append(
                block
            )

    return result


# ============================================================
# PREÇOS
# ============================================================

PRICE_RE = re.compile(
    r"R\$\s*"
    r"(\d{1,3}(?:\.\d{3})*"
    r"(?:,\d{2})"
    r"|\d+(?:,\d{2}))",
    re.I
)


def extract_prices(
    text
):

    result = []

    if not text:
        return result

    for match in PRICE_RE.finditer(
        text
    ):

        value = parse_money(
            match.group(1)
        )

        if value is None:
            continue

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

    # --------------------------------------------------------
    # CUPOM 15% OFF
    # --------------------------------------------------------

    patterns = [

        (
            r"cupom"
            r".{0,80}?"
            r"(\d+(?:[.,]\d+)?)"
            r"\s*%\s*off"
        ),

        (
            r"(\d+(?:[.,]\d+)?)"
            r"\s*%\s*off"
            r".{0,80}?"
            r"cupom"
        ),

    ]

    for pattern in patterns:

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

    # --------------------------------------------------------
    # CUPOM R$ XX OFF
    # --------------------------------------------------------

    patterns = [

        (
            r"cupom"
            r".{0,80}?"
            r"r\$\s*"
            r"(\d+(?:[.,]\d+)?)"
            r".{0,20}?"
            r"off"
        ),

        (
            r"cupom"
            r".{0,40}?"
            r"r\$\s*"
            r"(\d+(?:[.,]\d+)?)"
        ),

    ]

    for pattern in patterns:

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

    return None


# ============================================================
# FINAL EXPLÍCITO
# ============================================================

def detect_coupon_final(
    text
):

    patterns = [

        r"R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*"
        r"(?:,\d{2})"
        r"|\d+(?:,\d{2}))"
        r"\s+com\s+cupom",

        r"por\s+"
        r"R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*"
        r"(?:,\d{2})"
        r"|\d+(?:,\d{2}))"
        r"\s+com\s+cupom",

        r"com\s+cupom"
        r".{0,40}?"
        r"R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*"
        r"(?:,\d{2})"
        r"|\d+(?:,\d{2}))",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.I
        )

        if match:

            value = parse_money(
                match.group(1)
            )

            if value is not None:

                return value

    return None


# ============================================================
# CALCULAR OFERTA
# ============================================================

def calculate_offer(
    price,
    coupon,
    explicit_final=None
):

    if not valid_price(
        price
    ):
        return None

    if not coupon:
        return None

    final_price = None

    if (
        explicit_final is not None
        and explicit_final < price
    ):

        final_price = explicit_final

    elif coupon["type"] == "percent":

        discount = (
            price
            * coupon["value"]
            / 100
        )

        final_price = (
            price
            - discount
        )

    elif coupon["type"] == "fixed":

        discount = min(
            coupon["value"],
            price
        )

        final_price = (
            price
            - discount
        )

    if final_price is None:
        return None

    discount = (
        price
        - final_price
    )

    if discount <= 0:
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
# TITULO
# ============================================================

def extract_title_from_context(
    context
):

    if not context:
        return ""

    context = decode_embedded_html(
        context
    )

    patterns = [

        r'"title"\s*:\s*"([^"]{8,300})"',

        r'"name"\s*:\s*"([^"]{8,300})"',

        r'"product_title"\s*:\s*"([^"]{8,300})"',

        r'"productName"\s*:\s*"([^"]{8,300})"',

        r'"item_title"\s*:\s*"([^"]{8,300})"',

        r'"description"\s*:\s*"([^"]{8,300})"',
    ]

    candidates = []

    for pattern in patterns:

        matches = re.findall(
            pattern,
            context,
            re.I
        )

        for value in matches:

            value = clean_text(
                value
            )

            if len(value) < 8:
                continue

            if "cupom" in normalize_text(
                value
            ):
                continue

            if value not in candidates:

                candidates.append(
                    value
                )

    if candidates:

        candidates.sort(
            key=len
        )

        return candidates[0][:280]

    # --------------------------------------------------------
    # Fallback para texto visível
    # --------------------------------------------------------

    text = clean_text(
        re.sub(
            r"<[^>]+>",
            " ",
            context
        )
    )

    text = re.sub(
        r"R\$\s*[\d.,]+",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"cupom.*?(?:off|desconto)",
        "",
        text,
        flags=re.I
    )

    text = clean_text(
        text
    )

    if (
        len(text) >= 8
        and len(text) <= 280
    ):

        return text

    return ""


# ============================================================
# URL DO PRODUTO
# ============================================================

def extract_product_url(
    context,
    product_id
):

    if context:

        patterns = [

            r'https?://[^"\']*mercadolivre\.com\.br/[^"\']+',

            r'https?://[^"\']*MLB\d{6,}[^"\']+',

            r'["\'](https?://[^"\']+)["\']',

        ]

        for pattern in patterns:

            matches = re.findall(
                pattern,
                context,
                re.I
            )

            for link in matches:

                link = clean_text(
                    link
                )

                if (
                    "mercadolivre.com.br"
                    in link
                ):

                    return link

    # --------------------------------------------------------
    # Fallback seguro.
    # Página de catálogo.
    # --------------------------------------------------------

    if product_id:

        return (
            "https://www.mercadolivre.com.br/"
            "p/"
            + product_id
        )

    return ""


# ============================================================
# CONTEXTOS DE CUPOM
# ============================================================

def find_coupon_contexts(
    source
):

    source = decode_embedded_html(
        source
    )

    contexts = []

    # Procuramos somente ocorrências
    # reais de cupom.
    coupon_pattern = re.compile(
        r"(?:cupom|cupon)"
        r".{0,1000}?"
        r"(?:\d+(?:[.,]\d+)?\s*%\s*off|"
        r"r\$\s*\d+(?:[.,]\d+)?\s*(?:off)?)",
        re.I | re.S
    )

    for match in coupon_pattern.finditer(
        source
    ):

        start = max(
            0,
            match.start() - 6000
        )

        end = min(
            len(source),
            match.end() + 6000
        )

        context = source[
            start:end
        ]

        product_ids = extract_mlb_ids(
            context
        )

        if not product_ids:
            continue

        # ----------------------------------------------------
        # Quanto mais perto o MLB estiver
        # do cupom, maior a confiança.
        # ----------------------------------------------------

        coupon_pos = (
            match.start() - start
        )

        closest_id = None

        closest_distance = None

        for id_match in re.finditer(
            r"\bMLB[-_]?\d{6,}\b",
            context,
            re.I
        ):

            distance = abs(
                id_match.start()
                - coupon_pos
            )

            if (
                closest_distance is None
                or distance < closest_distance
            ):

                closest_distance = distance

                closest_id = (
                    "MLB"
                    + re.search(
                        r"\d{6,}",
                        id_match.group(0)
                    ).group(0)
                )

        # Não associa se estiver
        # absurdamente distante.
        if (
            closest_id
            and closest_distance is not None
            and closest_distance <= 4500
        ):

            contexts.append(
                {
                    "product_id":
                        closest_id,

                    "coupon_text":
                        clean_text(
                            match.group(0)
                        ),

                    "context":
                        context,

                    "distance":
                        closest_distance,
                }
            )

    return contexts


# ============================================================
# EXTRAIR PRODUTOS DO HUB
# ============================================================

def extract_hub_products(
    source
):

    ids = extract_mlb_ids(
        source
    )

    # Preserva ordem.
    result = []

    seen = set()

    for product_id in ids:

        if product_id in seen:
            continue

        seen.add(
            product_id
        )

        result.append(
            product_id
        )

        if len(result) >= MAX_PRODUCTS_FROM_HUB:
            break

    return result


# ============================================================
# INTERPRETAR CONTEXTO
# ============================================================

def parse_coupon_context(
    context_data
):

    context = context_data.get(
        "context",
        ""
    )

    product_id = context_data.get(
        "product_id"
    )

    if not context:
        return None

    coupon = detect_coupon(
        context
    )

    if not coupon:
        return None

    prices = extract_prices(
        context
    )

    # Só considera preço de produto
    # dentro do contexto.
    valid_prices = [
        p
        for p in prices
        if valid_price(p)
    ]

    if not valid_prices:
        return None

    explicit_final = (
        detect_coupon_final(
            context
        )
    )

    if explicit_final is not None:

        candidates = [
            p
            for p in valid_prices
            if p > explicit_final
        ]

        if candidates:

            price = min(
                candidates
            )

        else:

            price = valid_prices[-1]

    else:

        price = valid_prices[-1]

    offer = calculate_offer(
        price,
        coupon,
        explicit_final
    )

    if not offer:
        return None

    title = extract_title_from_context(
        context
    )

    url = extract_product_url(
        context,
        product_id
    )

    return {

        **offer,

        "product_id":
            product_id,

        "title":
            title,

        "url":
            url,

        "source":
            "descontaco-cupons",

        "coupon_confirmed":
            True,

        "confidence":
            "contextual",

        "context_distance":
            context_data.get(
                "distance"
            ),
    }


# ============================================================
# ENRIQUECER COM API DE PRODUTO
# ============================================================

def enrich_offer(
    offer
):

    product_id = offer.get(
        "product_id"
    )

    if not product_id:
        return offer

    detail = get_product_detail(
        product_id
    )

    if not detail:
        return offer

    catalog_title = clean_text(
        detail.get(
            "name"
        )
    )

    if catalog_title:

        offer[
            "catalog_title"
        ] = catalog_title

        # Só substitui título ruim.
        if (
            not offer.get(
                "title"
            )
            or len(
                offer.get(
                    "title",
                    ""
                )
            ) < 12
        ):

            offer[
                "title"
            ] = catalog_title

    offer[
        "catalog_id"
    ] = detail.get(
        "id"
    )

    # --------------------------------------------------------
    # BUY BOX
    # --------------------------------------------------------

    buy_box = detail.get(
        "buy_box_winner"
    )

    if isinstance(
        buy_box,
        dict
    ):

        if buy_box.get(
            "price"
        ) is not None:

            offer[
                "buy_box_price"
            ] = buy_box.get(
                "price"
            )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    permalink = (
        detail.get(
            "permalink"
        )
        or detail.get(
            "url"
        )
    )

    if permalink:

        offer[
            "url"
        ] = permalink

    elif not offer.get(
        "url"
    ):

        offer[
            "url"
        ] = (
            "https://www.mercadolivre.com.br/p/"
            + product_id
        )

    return offer


# ============================================================
# PARSER PRINCIPAL DO HUB
# ============================================================

def parse_coupon_hub(
    page_html,
    source_url
):

    if not page_html:
        return {
            "offers":
                [],

            "product_ids":
                [],

            "contexts":
                [],
        }

    decoded = decode_embedded_html(
        page_html
    )

    product_ids = extract_hub_products(
        decoded
    )

    contexts = find_coupon_contexts(
        decoded
    )

    print(
        "[HUB PARSER]"
    )

    print(
        "  produtos MLB:",
        len(product_ids)
    )

    print(
        "  contextos cupom:",
        len(contexts)
    )

    offers = []

    for context_data in contexts:

        item = parse_coupon_context(
            context_data
        )

        if not item:
            continue

        item[
            "source_url"
        ] = source_url

        offers.append(
            item
        )

    return {
        "offers":
            offers,

        "product_ids":
            product_ids,

        "contexts":
            contexts,
    }


# ============================================================
# BUSCAR HUB OFICIAL
# ============================================================

def hunt_coupon_hub():

    url = COUPON_HUBS[0]

    result = fetch_public_page(
        url
    )

    if not result.get(
        "ok"
    ):

        print(
            "[HUB] Falha:",
            result.get(
                "status"
            )
        )

        return {
            "offers":
                [],

            "product_ids":
                [],

            "contexts":
                [],
        }

    html_data = result.get(
        "html",
        ""
    )

    print(
        "[HUB] bytes:",
        len(html_data)
    )

    print(
        "[HUB] cupom count:",
        html_data.lower().count(
            "cupom"
        )
    )

    parsed = parse_coupon_hub(
        html_data,
        url
    )

    return parsed


# ============================================================
# SEGUNDO HUB
# ============================================================

def inspect_promotions_page():

    url = COUPON_HUBS[1]

    result = fetch_public_page(
        url
    )

    if not result.get(
        "ok"
    ):

        return {
            "status":
                result.get(
                    "status"
                ),

            "bytes":
                result.get(
                    "bytes",
                    0
                ),

            "coupon_count":
                0,
        }

    source = result.get(
        "html",
        ""
    )

    return {
        "status":
            result.get(
                "status"
            ),

        "bytes":
            len(source),

        "coupon_count":
            source.lower().count(
                "cupom"
            ),

        "product_count":
            len(
                extract_mlb_ids(
                    source
                )
            ),
    }


# ============================================================
# DEDUPLICAÇÃO
# ============================================================

def deduplicate_offers(
    offers
):

    grouped = {}

    for item in offers:

        product_id = item.get(
            "product_id"
        )

        title = clean_text(
            item.get(
                "title",
                ""
            )
        )

        price = item.get(
            "price"
        )

        if not valid_price(
            price
        ):
            continue

        # ----------------------------------------------------
        # ID do catálogo primeiro.
        # ----------------------------------------------------

        if product_id:

            key = (
                "id:"
                + product_id
            )

        else:

            if not title:
                continue

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

        # ----------------------------------------------------
        # Maior economia em R$.
        # ----------------------------------------------------

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
        "🎟️ MODO: CUPOM PRIMEIRO"
    )
    print(
        "=========================================="
    )

    parsed = hunt_coupon_hub()

    raw_offers = parsed.get(
        "offers",
        []
    )

    product_ids = parsed.get(
        "product_ids",
        []
    )

    contexts = parsed.get(
        "contexts",
        []
    )

    print(
        "[PRODUTOS ENCONTRADOS NO HUB]",
        len(product_ids)
    )

    print(
        "[CONTEXTOS DE CUPOM]",
        len(contexts)
    )

    print(
        "[OFERTAS COM CUPOM + PRODUTO]",
        len(raw_offers)
    )

    # --------------------------------------------------------
    # Enriquece somente ofertas em que
    # o produto e o cupom foram associados
    # no mesmo contexto.
    # --------------------------------------------------------

    enriched = []

    seen_products = set()

    for item in raw_offers:

        product_id = item.get(
            "product_id"
        )

        if not product_id:
            continue

        if product_id in seen_products:

            # Ainda deixa passar para a
            # deduplicação comparar cupons.
            pass

        try:

            item = enrich_offer(
                item
            )

        except Exception as e:

            print(
                "[ENRICH ERRO]",
                product_id,
                e
            )

        enriched.append(
            item
        )

        time.sleep(
            0.08
        )

    offers = deduplicate_offers(
        enriched
    )

    print(
        "[PRODUTOS ÚNICOS COM CUPOM]",
        len(offers)
    )

    # --------------------------------------------------------
    # Ordenação:
    #
    # 1. maior economia em R$
    # 2. maior %
    # 3. menor preço final
    # --------------------------------------------------------

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

    results = []

    for url in COUPON_HUBS:

        data = fetch_public_page(
            url
        )

        source = data.get(
            "html",
            ""
        )

        parsed = {}

        if (
            data.get("ok")
            and source
        ):

            parsed = parse_coupon_hub(
                source,
                url
            )

        results.append(
            {
                "url":
                    url,

                "status":
                    data.get(
                        "status"
                    ),

                "final_url":
                    data.get(
                        "url"
                    ),

                "bytes":
                    len(source),

                "cupom_count":
                    source.lower().count(
                        "cupom"
                    ),

                "produto_count":
                    len(
                        extract_mlb_ids(
                            source
                        )
                    ),

                "unique_products":
                    len(
                        parsed.get(
                            "product_ids",
                            []
                        )
                    ),

                "coupon_contexts":
                    len(
                        parsed.get(
                            "contexts",
                            []
                        )
                    ),

                "offers_detected":
                    len(
                        parsed.get(
                            "offers",
                            []
                        )
                    ),

                "sample_contexts":
                    [
                        {
                            "product_id":
                                x.get(
                                    "product_id"
                                ),

                            "coupon_text":
                                x.get(
                                    "coupon_text"
                                ),

                            "distance":
                                x.get(
                                    "distance"
                                ),

                            "context_preview":
                                clean_text(
                                    x.get(
                                        "context",
                                        ""
                                    )
                                )[:1200],
                        }

                        for x in parsed.get(
                            "contexts",
                            []
                        )[:10]
                    ],

                "sample_product_ids":
                    parsed.get(
                        "product_ids",
                        []
                    )[:20],
            }
        )

    return jsonify(
        {
            "ok":
                True,

            "results":
                results,

            "observacao":
                (
                    "O caçador só associa um cupom "
                    "ao produto quando encontra "
                    "evidência contextual no HTML "
                    "público. Ele não aplica um "
                    "cupom genérico a todos os produtos."
                ),
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
                    and response.status_code
                    == 200
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
                    and response.status_code
                    == 200
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

    box-shadow:
        0 3px 10px
        rgba(0,0,0,.05);
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

.debug {
    background: #f8f8f8;

    border-radius: 10px;

    padding: 10px;

    margin-top: 12px;

    font-size: 12px;

    color: #666;
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
🎟️ Cupons + produtos acima de R$69,90
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
                    Nenhum produto com cupom confirmado foi encontrado.
                </strong>

                <br><br>

                O sistema agora evita aplicar
                um cupom genérico em produtos
                sem evidência de elegibilidade.

                <br><br>

                Diagnóstico:
                <br>
                <a
                    href="/api/debug-public"
                    target="_blank"
                >
                    /api/debug-public
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


                <div class="price-old">
                    Preço antes do cupom:
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
                    Economia:
                    ${money(item.discount)}

                    (${Number(
                        item.effective_percent || 0
                    ).toFixed(1)}%)
                </div>


                <div class="warning">
                    ⚠️ O cupom foi identificado
                    junto ao produto na página
                    pública. A aplicação final
                    deve ser confirmada no checkout.
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
        "Consultando a página oficial de cupons do Mercado Livre...";


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
            + " produto(s) único(s) com cupom confirmado.";


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