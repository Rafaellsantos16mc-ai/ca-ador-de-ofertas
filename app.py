import os
import re
import time
import sqlite3
import secrets
import hashlib
import base64
import html
import unicodedata
from urllib.parse import urlencode, quote, urljoin

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
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

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
# BUSCAS
# ============================================================

SEARCHES = [
    "celular",
    "iphone",
    "samsung",
    "xiaomi",
    "motorola",
    "notebook",
    "smart tv",
    "televisao",
    "monitor",
    "fone bluetooth",
    "air fryer",
    "geladeira",
    "microondas",
    "maquina de lavar",
    "aspirador",
    "caixa de som",
    "smartwatch",
    "tablet",
    "cadeira escritorio",
    "ferramentas",
    "parafusadeira",
    "furadeira",
    "tenis",
    "perfume",
    "mochila",
    "mala",
]


# ============================================================
# HEADERS
# ============================================================

PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),

    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
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
# PÁGINAS PÚBLICAS
# ============================================================

COUPON_HUBS = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/l/promocoes",
]


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
    return int(time.time())


def clean_text(value):

    if not value:
        return ""

    value = html.unescape(
        str(value)
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
        c for c in value
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

    value = str(value)

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
        return "R$ 0,00"

    return (
        "R$ "
        + f"{value:,.2f}"
        .replace(",", "X")
        .replace(".", ",")
        .replace("X", ".")
    )


def valid_price(value):

    return (
        value is not None
        and value >= MIN_PRICE
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
    }

    words = [
        x
        for x in text.split()
        if x not in remove
    ]

    return " ".join(
        words[:40]
    )


def extract_mlb_id(url):

    if not url:
        return None

    match = re.search(
        r"\bMLB[-_]?(\d{6,})\b",
        str(url).upper()
    )

    if not match:
        return None

    return (
        "MLB"
        + match.group(1)
    )


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
                ] = f"Bearer {token}"

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
# PRODUTO DO CATÁLOGO
# ============================================================

def get_product_detail(
    product_id
):

    response, data = ml_get(
        f"/products/{product_id}"
    )

    if (
        not response
        or response.status_code != 200
    ):
        return None

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
            timeout=30,
            allow_redirects=True
        )

        print(
            "[PUBLIC]",
            response.status_code,
            len(
                response.content
            ),
            url
        )

        if response.status_code != 200:

            return {
                "ok":
                    False,

                "status":
                    response.status_code,

                "url":
                    response.url,

                "html":
                    response.text[:5000],

                "bytes":
                    len(
                        response.content
                    ),
            }

        return {
            "ok":
                True,

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

def html_to_lines(
    source
):

    if not source:
        return []

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

    # Mantém separação entre elementos.
    source = re.sub(
        r"</(?:div|li|article|section|p|h1|h2|h3|h4|h5|a|span|br)>",
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

    raw_lines = source.splitlines()

    lines = []

    for line in raw_lines:

        line = clean_text(
            line
        )

        if not line:
            continue

        # Evita linhas gigantes.
        if len(line) > 1000:

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
    r"|\d+(?:,\d{2}))",
    re.I
)


def extract_prices(
    text
):

    result = []

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

    # --------------------------------------------------------
    # Cupom 15% OFF
    # --------------------------------------------------------

    m = re.search(
        r"cupom\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off",
        text,
        re.I
    )

    if m:

        value = float(
            m.group(1).replace(
                ",",
                "."
            )
        )

        return {
            "type":
                "percent",

            "value":
                value,

            "label":
                f"Cupom {value:g}% OFF",
        }

    # --------------------------------------------------------
    # 15% OFF com Cupom
    # --------------------------------------------------------

    m = re.search(
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off"
        r"\s+com\s+cupom",
        text,
        re.I
    )

    if m:

        value = float(
            m.group(1).replace(
                ",",
                "."
            )
        )

        return {
            "type":
                "percent",

            "value":
                value,

            "label":
                f"Cupom {value:g}% OFF",
        }

    # --------------------------------------------------------
    # Cupom R$15 OFF
    # --------------------------------------------------------

    m = re.search(
        r"cupom\s+"
        r"r\$\s*"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*off",
        text,
        re.I
    )

    if m:

        value = parse_money(
            m.group(1)
        )

        if value is not None:

            return {
                "type":
                    "fixed",

                "value":
                    value,

                "label":
                    f"Cupom {money_br(value)} OFF",
            }

    # --------------------------------------------------------
    # Cupom de R$15
    # --------------------------------------------------------

    m = re.search(
        r"cupom.*?"
        r"r\$\s*"
        r"(\d+(?:[.,]\d+)?)",
        text,
        re.I
    )

    if m:

        value = parse_money(
            m.group(1)
        )

        if (
            value is not None
            and value > 0
            and value <= 500
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
# PREÇO FINAL EXPLÍCITO
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
# CALCULO
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

    if (
        explicit_final is not None
        and explicit_final < price
    ):

        final_price = (
            explicit_final
        )

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

    else:

        discount = min(
            coupon["value"],
            price
        )

        final_price = (
            price
            - discount
        )

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
# LINK DE PRODUTO
# ============================================================

def extract_product_link(
    source_html,
    nearby_text=""
):

    # Tenta primeiro MLB.
    patterns = [
        r'https?://[^"\']*MLB\d{6,}[^"\']*',
        r'https?://[^"\']*mercadolivre\.com\.br/[^"\']+',
        r'href=["\']([^"\']*MLB\d{6,}[^"\']*)["\']',
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            source_html,
            re.I
        )

        if matches:

            link = (
                matches[0]
                if isinstance(
                    matches[0],
                    str
                )
                else matches[0][0]
            )

            if link.startswith(
                "//"
            ):

                link = (
                    "https:"
                    + link
                )

            return link

    return ""


# ============================================================
# EXTRAI OFERTAS DE UMA LINHA/BLOCO
# ============================================================

def parse_coupon_block(
    block,
    source_url
):

    block = clean_text(
        block
    )

    if not block:
        return None

    coupon = detect_coupon(
        block
    )

    if not coupon:
        return None

    prices = extract_prices(
        block
    )

    if not prices:
        return None

    explicit_final = (
        detect_coupon_final(
            block
        )
    )

    # --------------------------------------------------------
    # Preço:
    #
    # Quando existe preço antigo + atual,
    # usa o menor preço que faz sentido.
    # --------------------------------------------------------

    if explicit_final is not None:

        # O preço antes do cupom
        # deve ser maior que o final.
        candidates = [
            p
            for p in prices
            if p > explicit_final
        ]

        if candidates:

            price = min(
                candidates
            )

        else:

            price = prices[-1]

    else:

        # Se houver vários preços,
        # normalmente o último é o atual.
        price = prices[-1]

    if not valid_price(
        price
    ):
        return None

    offer = calculate_offer(
        price,
        coupon,
        explicit_final
    )

    if not offer:
        return None

    # --------------------------------------------------------
    # Título
    # --------------------------------------------------------

    title = block

    # Remove cupom.
    title = re.sub(
        r"cupom\s+"
        r"(?:r\$\s*)?"
        r"\d+(?:[.,]\d+)?"
        r"(?:\s*%\s*)?"
        r"\s*off",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\d+(?:[.,]\d+)?"
        r"\s*%\s*off"
        r"\s+com\s+cupom",
        "",
        title,
        flags=re.I
    )

    # Remove preços.
    title = re.sub(
        r"R\$\s*"
        r"\d{1,3}(?:\.\d{3})*"
        r"(?:,\d{2})"
        r"|\bR\$\s*\d+(?:,\d{2})",
        "",
        title,
        flags=re.I
    )

    # Remove informações comuns.
    title = re.sub(
        r"\+\d[\d.,]*\s+vendidos?",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"\d+(?:[.,]\d+)?\s*"
        r"(?:estrelas?|$)",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"chegará.*",
        "",
        title,
        flags=re.I
    )

    title = re.sub(
        r"frete grátis.*",
        "",
        title,
        flags=re.I
    )

    title = clean_text(
        title
    )

    # Tenta cortar prefixos ruins.
    title = re.sub(
        r"^(?:imagem|image)\s*[:\-]?\s*",
        "",
        title,
        flags=re.I
    )

    if len(title) < 8:
        return None

    # Remove títulos gigantes.
    if len(title) > 280:
        title = title[:280]

    return {
        **offer,

        "title":
            title,

        "url":
            source_url,

        "product_id":
            None,

        "source":
            source_url,
    }


# ============================================================
# PARSER DA PÁGINA OFICIAL DE CUPONS
# ============================================================

def parse_coupon_page(
    page_html,
    source_url
):

    lines = html_to_lines(
        page_html
    )

    print(
        "[PARSER] linhas:",
        len(lines)
    )

    coupon_indexes = []

    for i, line in enumerate(
        lines
    ):

        if "cupom" in (
            line.lower()
        ):

            coupon_indexes.append(
                i
            )

    print(
        "[PARSER] linhas com cupom:",
        len(coupon_indexes)
    )

    offers = []

    # --------------------------------------------------------
    # Cada cupom normalmente pertence
    # ao produto mais próximo.
    # --------------------------------------------------------

    for index in coupon_indexes:

        coupon_line = lines[
            index
        ]

        # Pega contexto pequeno.
        before = lines[
            max(
                0,
                index - 5
            ):
            index
        ]

        after = lines[
            index + 1:
            min(
                len(lines),
                index + 5
            )
        ]

        context = (
            before
            + [coupon_line]
            + after
        )

        block = clean_text(
            " ".join(
                context
            )
        )

        item = parse_coupon_block(
            block,
            source_url
        )

        if item:

            # ------------------------------------------------
            # O título fica melhor quando usamos
            # a linha imediatamente anterior/posterior.
            # ------------------------------------------------

            candidates = []

            for candidate in (
                before
                + after
            ):

                candidate = clean_text(
                    candidate
                )

                if len(candidate) < 12:
                    continue

                if "cupom" in (
                    candidate.lower()
                ):
                    continue

                if "frete" in (
                    candidate.lower()
                ):
                    continue

                if "chegará" in (
                    candidate.lower()
                ):
                    continue

                if "vendidos" in (
                    candidate.lower()
                ):
                    continue

                if (
                    "R$" in candidate
                    and len(candidate) < 30
                ):
                    continue

                candidates.append(
                    candidate
                )

            if candidates:

                # Prefere a linha mais longa
                # que parece nome de produto.
                candidates.sort(
                    key=len,
                    reverse=True
                )

                item["title"] = (
                    candidates[0][:280]
                )

            offers.append(
                item
            )

    return offers


# ============================================================
# PARSER DE LISTAGEM
# ============================================================

def parse_listing_page(
    page_html,
    source_url
):

    lines = html_to_lines(
        page_html
    )

    offers = []

    for index, line in enumerate(
        lines
    ):

        low = line.lower()

        if "cupom" not in low:
            continue

        # ----------------------------------------------------
        # Caso:
        # Cupom 15% OFF Produto ... R$42
        # ----------------------------------------------------

        context = []

        context.extend(
            lines[
                max(
                    0,
                    index - 2
                ):
                index
            ]
        )

        context.append(
            line
        )

        context.extend(
            lines[
                index + 1:
                min(
                    len(lines),
                    index + 3
                )
            ]
        )

        block = clean_text(
            " ".join(
                context
            )
        )

        item = parse_coupon_block(
            block,
            source_url
        )

        if item:

            # Tenta extrair um título
            # diretamente da linha.
            title_line = line

            title_line = re.sub(
                r"cupom\s+"
                r"(?:r\$\s*)?"
                r"\d+(?:[.,]\d+)?"
                r"(?:\s*%\s*)?"
                r"\s*off",
                "",
                title_line,
                flags=re.I
            )

            title_line = clean_text(
                title_line
            )

            if (
                len(title_line) >= 15
                and "R$" not in title_line
            ):

                item["title"] = (
                    title_line[:280]
                )

            offers.append(
                item
            )

    return offers


# ============================================================
# PARSER GERAL
# ============================================================

def extract_coupon_offers(
    page_html,
    source_url
):

    if not page_html:
        return []

    # Primeiro parser da listagem.
    offers = parse_listing_page(
        page_html,
        source_url
    )

    # Depois parser da página oficial.
    if not offers:

        offers = parse_coupon_page(
            page_html,
            source_url
        )

    # --------------------------------------------------------
    # Remove duplicados imediatamente.
    # --------------------------------------------------------

    unique = {}

    for item in offers:

        key = (
            normalize_text(
                item.get(
                    "title",
                    ""
                )
            )
            + "|"
            + str(
                item.get(
                    "price",
                    ""
                )
            )
            + "|"
            + str(
                item.get(
                    "coupon_label",
                    ""
                )
            )
        )

        if key not in unique:

            unique[key] = item

    return list(
        unique.values()
    )


# ============================================================
# BUSCAR UMA URL
# ============================================================

def search_url(
    url,
    source_name
):

    result = fetch_public_page(
        url
    )

    if not result:

        return []

    if not result.get(
        "ok"
    ):

        print(
            "[FALHA PUBLICA]",
            source_name,
            result.get(
                "status"
            )
        )

        return []

    page_html = result[
        "html"
    ]

    print(
        "[HTML]",
        source_name,
        "bytes=",
        len(page_html),
        "cupom=",
        page_html.lower().count(
            "cupom"
        )
    )

    offers = extract_coupon_offers(
        page_html,
        url
    )

    print(
        "[OFERTAS]",
        source_name,
        len(offers)
    )

    return offers


# ============================================================
# BUSCAR TODAS
# ============================================================

def hunt_public_coupons():

    all_offers = []

    # --------------------------------------------------------
    # 1. Página oficial de cupons.
    # --------------------------------------------------------

    for url in COUPON_HUBS:

        try:

            offers = search_url(
                url,
                "HUB"
            )

            all_offers.extend(
                offers
            )

        except Exception as e:

            print(
                "[HUB ERRO]",
                e
            )

        time.sleep(
            0.5
        )

    # --------------------------------------------------------
    # 2. Listagens por categoria.
    # --------------------------------------------------------

    for query in SEARCHES:

        url = (
            "https://lista.mercadolivre.com.br/"
            + quote(
                query,
                safe=""
            )
        )

        try:

            offers = search_url(
                url,
                query
            )

            all_offers.extend(
                offers
            )

        except Exception as e:

            print(
                "[LISTAGEM ERRO]",
                query,
                e
            )

        time.sleep(
            0.25
        )

    return all_offers


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

        if (
            not title
            or not valid_price(
                price
            )
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

            grouped[key] = item

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

            grouped[key] = item

    return list(
        grouped.values()
    )


# ============================================================
# ENRIQUECER
# ============================================================

def enrich_catalog(
    offers
):

    cache = {}

    for item in offers:

        product_id = item.get(
            "product_id"
        )

        if not product_id:
            continue

        if product_id in cache:

            detail = cache[
                product_id
            ]

        else:

            detail = (
                get_product_detail(
                    product_id
                )
            )

            cache[
                product_id
            ] = detail

            time.sleep(
                0.1
            )

        if not detail:
            continue

        item[
            "catalog_id"
        ] = detail.get(
            "id"
        )

        item[
            "catalog_title"
        ] = detail.get(
            "name"
        )

        buy_box = detail.get(
            "buy_box_winner"
        )

        if isinstance(
            buy_box,
            dict
        ):

            item[
                "buy_box_price"
            ] = buy_box.get(
                "price"
            )

    return offers


# ============================================================
# CAÇADOR
# ============================================================

def hunt_offers():

    print(
        ""
    )

    print(
        "=========================================="
    )

    print(
        "🤑 CAÇADOR DE OFERTAS - INÍCIO"
    )

    print(
        "=========================================="
    )

    raw = hunt_public_coupons()

    print(
        "[TOTAL RAW]",
        len(raw)
    )

    offers = deduplicate_offers(
        raw
    )

    print(
        "[PRODUTOS ÚNICOS]",
        len(offers)
    )

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

    offers = enrich_catalog(
        offers[:50]
    )

    return offers[:50]


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

        return jsonify(
            {
                "ok":
                    True,

                "total":
                    len(offers),

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
# DEBUG PUBLICO
# ============================================================

@app.route(
    "/api/debug-public"
)
def api_debug_public():

    result = []

    urls = [
        COUPON_HUBS[0],
        COUPON_HUBS[1],

        "https://lista.mercadolivre.com.br/celular",
        "https://lista.mercadolivre.com.br/notebook",
    ]

    for url in urls:

        data = fetch_public_page(
            url
        )

        if not data:

            continue

        html_data = data.get(
            "html",
            ""
        )

        result.append(
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
                    len(html_data),

                "cupom_count":
                    html_data.lower().count(
                        "cupom"
                    ),

                "produto_count":
                    len(
                        re.findall(
                            r"MLB\d{6,}",
                            html_data,
                            re.I
                        )
                    ),

                "preview":
                    clean_text(
                        html_data
                    )[:2000],
            }
        )

    return jsonify(
        {
            "ok":
                True,

            "results":
                result,
        }
    )


# ============================================================
# DIAGNOSTICO ML
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
Produtos acima de R$69,90 com cupom identificado
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
Cupom aplicável
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

                Se continuar assim, abra:
                <br><br>

                <b>
                /api/debug-public
                </b>

                <br><br>

                para verificar se o Railway
                está recebendo os cupons.

            </div>
        `;

        return;
    }


    results.innerHTML =
        offers.map(
            item => `

            <div class="card">

                <div class="badge">
                    🎟️ CUPOM IDENTIFICADO
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
                    ⚠️ Cupom identificado
                    na página pública.
                    Confirme a aplicação
                    no checkout.
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
        "Consultando cupons e ofertas do Mercado Livre...";


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
            offers.length;


        const discount =
            offers.reduce(
                (
                    total,
                    item
                ) =>
                    total +
                    Number(
                        item.discount || 0
                    ),
                0
            );


        const finalPrice =
            offers.reduce(
                (
                    total,
                    item
                ) =>
                    total +
                    Number(
                        item.final_price || 0
                    ),
                0
            );


        document.getElementById(
            "discount"
        ).innerText =
            money(
                discount
            );


        document.getElementById(
            "final"
        ).innerText =
            money(
                finalPrice
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