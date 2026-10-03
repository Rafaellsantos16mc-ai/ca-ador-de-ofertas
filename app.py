import os
import re
import time
import html as html_lib
import sqlite3
import hashlib
import secrets
import threading
from urllib.parse import urlencode

import requests

from flask import (
    Flask,
    request,
    redirect,
    session,
    render_template_string,
    jsonify,
)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-no-railway"
)

DB_PATH = os.getenv(
    "DB_PATH",
    "ofertas.db"
)


# ============================================================
# MERCADO LIVRE
# ============================================================

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

ML_AUTH_URL = (
    "https://auth.mercadolivre.com.br/authorization"
)

ML_TOKEN_URL = (
    "https://api.mercadolibre.com/oauth/token"
)

ML_API = (
    "https://api.mercadolibre.com"
)

SITE_ID = "MLB"


# ============================================================
# CONFIGURAÇÕES
# ============================================================

MIN_PRODUCT_PRICE = 69.90

REQUEST_TIMEOUT = 15

MAX_PRODUCTS_SCAN = 80

MAX_ITEMS_PER_PRODUCT = 5

COUPON_PAGE = (
    "https://www.mercadolivre.com.br/l/promocoes"
)


# ============================================================
# BUSCAS
# ============================================================

SEARCH_QUERIES = {

    "Celulares": [
        "celular",
        "smartphone",
        "iphone",
        "samsung galaxy",
    ],

    "Eletrônicos": [
        "smart tv",
        "fone bluetooth",
        "notebook",
        "tablet",
    ],

    "Casa": [
        "air fryer",
        "liquidificador",
        "aspirador",
        "cafeteira",
    ],

    "Cozinha": [
        "panela",
        "jogo de panelas",
        "microondas",
        "sanduicheira",
    ],

    "Academia": [
        "halter",
        "kit academia",
        "whey protein",
        "acessorios academia",
    ],

    "Ferramentas": [
        "furadeira",
        "parafusadeira",
        "jogo ferramentas",
        "chave impacto",
    ],

    "Automotivo": [
        "pneu",
        "central multimidia",
        "capa banco carro",
        "lampada led carro",
    ],

    "Moda": [
        "tenis",
        "tenis feminino",
        "tenis masculino",
        "mochila",
    ],

    "Perfumes": [
        "perfume",
        "perfume feminino",
        "perfume masculino",
        "kit perfume",
    ],
}


# ============================================================
# BANCO
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT,
            item_id TEXT,
            title TEXT,
            category TEXT,
            price REAL,
            coupon_code TEXT,
            coupon_type TEXT,
            coupon_value REAL,
            discount REAL,
            final_price REAL,
            url TEXT,
            seller_id TEXT,
            created_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            coupon_type TEXT,
            value REAL,
            min_purchase REAL,
            max_discount REAL,
            raw_text TEXT,
            updated_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT,
            progress INTEGER DEFAULT 0,
            message TEXT,
            total INTEGER DEFAULT 0,
            processed INTEGER DEFAULT 0,
            started_at INTEGER,
            finished_at INTEGER
        )
        """
    )

    conn.commit()
    conn.close()


init_db()


# ============================================================
# JOBS
# ============================================================

def create_job():

    conn = db()

    running = conn.execute(
        """
        SELECT id
        FROM scan_jobs
        WHERE status = 'running'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if running:

        conn.close()

        return None

    cursor = conn.execute(
        """
        INSERT INTO scan_jobs (
            status,
            progress,
            message,
            total,
            processed,
            started_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "running",
            0,
            "Iniciando caça...",
            0,
            0,
            int(time.time()),
        )
    )

    job_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return job_id


def update_job(
    job_id,
    progress=None,
    message=None,
    total=None,
    processed=None,
    status=None,
    finished=False
):

    conn = db()

    fields = []
    values = []

    if progress is not None:

        fields.append(
            "progress = ?"
        )

        values.append(
            int(progress)
        )

    if message is not None:

        fields.append(
            "message = ?"
        )

        values.append(
            str(message)
        )

    if total is not None:

        fields.append(
            "total = ?"
        )

        values.append(
            int(total)
        )

    if processed is not None:

        fields.append(
            "processed = ?"
        )

        values.append(
            int(processed)
        )

    if status is not None:

        fields.append(
            "status = ?"
        )

        values.append(
            status
        )

    if finished:

        fields.append(
            "finished_at = ?"
        )

        values.append(
            int(time.time())
        )

    if fields:

        values.append(
            job_id
        )

        conn.execute(
            f"""
            UPDATE scan_jobs
            SET {", ".join(fields)}
            WHERE id = ?
            """,
            values
        )

        conn.commit()

    conn.close()


def get_job(job_id):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM scan_jobs
        WHERE id = ?
        """,
        (job_id,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


# ============================================================
# TOKEN DA SESSÃO
# ============================================================

def get_access_token():

    return session.get(
        "ml_access_token"
    )


def get_refresh_token():

    return session.get(
        "ml_refresh_token"
    )


# ============================================================
# HEADERS
# ============================================================

def ml_headers(
    access_token
):

    headers = {
        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/3.0",
    }

    if access_token:

        headers[
            "Authorization"
        ] = (
            "Bearer "
            + access_token
        )

    return headers


# ============================================================
# SALVAR TOKENS
# ============================================================

def save_tokens(data):

    if data.get(
        "access_token"
    ):

        session[
            "ml_access_token"
        ] = data[
            "access_token"
        ]

    if data.get(
        "refresh_token"
    ):

        session[
            "ml_refresh_token"
        ] = data[
            "refresh_token"
        ]

    if data.get(
        "expires_in"
    ):

        session[
            "ml_expires_in"
        ] = data[
            "expires_in"
        ]

    session[
        "ml_token_saved_at"
    ] = int(
        time.time()
    )

    session.modified = True


# ============================================================
# REFRESH TOKEN PARA THREAD
# ============================================================

def refresh_token_for_worker(
    refresh_token
):

    if not refresh_token:
        return None

    if not ML_CLIENT_ID:
        return None

    if not ML_CLIENT_SECRET:
        return None

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

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[WORKER TOKEN REFRESH]",
                response.status_code,
                response.text[:500]
            )

            return None

        data = response.json()

        new_token = data.get(
            "access_token"
        )

        if new_token:

            return new_token

    except Exception as e:

        print(
            "[WORKER TOKEN ERROR]",
            e
        )

    return None


# ============================================================
# REQUEST ML PARA THREAD
# ============================================================

def ml_get_worker(
    path,
    auth,
    params=None
):

    access_token = auth.get(
        "access_token"
    )

    if not access_token:

        return None

    url = path

    if not url.startswith(
        "http"
    ):

        url = (
            ML_API
            + path
        )

    try:

        response = requests.get(

            url,

            headers=ml_headers(
                access_token
            ),

            params=params,

            timeout=REQUEST_TIMEOUT
        )

        # ====================================================
        # TOKEN EXPIRADO
        # ====================================================

        if response.status_code == 401:

            print(
                "[WORKER] Token expirado."
            )

            new_token = (
                refresh_token_for_worker(
                    auth.get(
                        "refresh_token"
                    )
                )
            )

            if new_token:

                auth[
                    "access_token"
                ] = new_token

                print(
                    "[WORKER] Token renovado."
                )

                response = requests.get(

                    url,

                    headers=ml_headers(
                        new_token
                    ),

                    params=params,

                    timeout=REQUEST_TIMEOUT
                )

        return response

    except requests.RequestException as e:

        print(
            "[ML WORKER GET ERRO]",
            url,
            str(e)
        )

        return None


# ============================================================
# REQUEST ML NORMAL
# ============================================================

def ml_get(
    path,
    params=None
):

    token = get_access_token()

    if not token:

        return None

    return ml_get_worker(
        path,
        {
            "access_token":
                token,

            "refresh_token":
                get_refresh_token(),
        },
        params
    )


# ============================================================
# PKCE
# ============================================================

def base64url(
    data
):

    import base64

    return (
        base64.urlsafe_b64encode(
            data
        )
        .rstrip(b"=")
        .decode()
    )


def create_pkce():

    verifier = secrets.token_urlsafe(
        64
    )

    digest = hashlib.sha256(
        verifier.encode(
            "ascii"
        )
    ).digest()

    challenge = base64url(
        digest
    )

    return (
        verifier,
        challenge
    )


# ============================================================
# OAUTH
# ============================================================

@app.route(
    "/mercadolivre/connect"
)
def mercadolivre_connect():

    if not ML_CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    state = secrets.token_urlsafe(
        32
    )

    verifier, challenge = (
        create_pkce()
    )

    session[
        "oauth_state"
    ] = state

    session[
        "oauth_verifier"
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

    url = (
        ML_AUTH_URL
        + "?"
        + urlencode(params)
    )

    return redirect(
        url
    )


@app.route(
    "/mercadolivre/callback"
)
def mercadolivre_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <h2>Erro ao conectar Mercado Livre</h2>
        <p>
        {html_lib.escape(error)}
        </p>
        <a href="/">Voltar</a>
        """

    state = request.args.get(
        "state"
    )

    code = request.args.get(
        "code"
    )

    if (
        not state
        or state != session.get(
            "oauth_state"
        )
    ):

        return (
            "Estado OAuth inválido.",
            400
        )

    if not code:

        return (
            "Código de autorização não recebido.",
            400
        )

    verifier = session.get(
        "oauth_verifier"
    )

    if not verifier:

        return (
            "PKCE verifier não encontrado.",
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

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            return f"""
            <h2>Erro ao obter token</h2>

            <pre>
            {html_lib.escape(
                response.text
            )}
            </pre>

            """, 400

        save_tokens(
            response.json()
        )

        session.pop(
            "oauth_state",
            None
        )

        session.pop(
            "oauth_verifier",
            None
        )

        return redirect(
            "/"
        )

    except Exception as e:

        return (
            f"Erro OAuth: {e}",
            500
        )


@app.route(
    "/mercadolivre/disconnect"
)
def mercadolivre_disconnect():

    for key in [

        "ml_access_token",

        "ml_refresh_token",

        "ml_expires_in",

        "ml_token_saved_at",

    ]:

        session.pop(
            key,
            None
        )

    return redirect(
        "/"
    )


# ============================================================
# USUÁRIO
# ============================================================

def get_ml_user():

    response = ml_get(
        "/users/me"
    )

    if not response:
        return None

    if response.status_code != 200:
        return None

    try:

        return response.json()

    except Exception:

        return None


# ============================================================
# BUSCA PRODUTOS
# ============================================================

def product_search(
    query,
    auth,
    limit=20
):

    response = ml_get_worker(

        "/products/search",

        auth,

        params={

            "site_id":
                SITE_ID,

            "q":
                query,

            "limit":
                limit,

            "offset":
                0,
        }
    )

    if not response:
        return []

    if response.status_code != 200:

        print(
            "[PRODUCT SEARCH]",
            query,
            response.status_code,
            response.text[:300]
        )

        return []

    try:

        data = response.json()

    except Exception:

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


# ============================================================
# DETALHE PRODUTO
# ============================================================

def get_product(
    product_id,
    auth
):

    response = ml_get_worker(

        f"/products/{product_id}",

        auth
    )

    if not response:
        return None

    if response.status_code != 200:
        return None

    try:

        return response.json()

    except Exception:

        return None


# ============================================================
# ITENS DO PRODUTO
# ============================================================

def get_product_items(
    product_id,
    auth
):

    response = ml_get_worker(

        f"/products/{product_id}/items",

        auth
    )

    if not response:
        return []

    if response.status_code != 200:

        print(
            "[PRODUCT ITEMS]",
            product_id,
            response.status_code
        )

        return []

    try:

        data = response.json()

    except Exception:

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


# ============================================================
# PREÇO SALE PRICE
# ============================================================

def get_sale_price(
    item_id,
    auth
):

    response = ml_get_worker(

        f"/items/{item_id}/sale_price",

        auth,

        params={
            "context":
                "channel_marketplace"
        }
    )

    if not response:
        return None

    if response.status_code == 200:

        try:

            data = response.json()

            amount = data.get(
                "amount"
            )

            if amount is not None:

                return {

                    "amount":
                        float(
                            amount
                        ),

                    "regular_amount":
                        (
                            float(
                                data[
                                    "regular_amount"
                                ]
                            )
                            if data.get(
                                "regular_amount"
                            ) is not None
                            else None
                        ),

                    "source":
                        "sale_price",
                }

        except Exception as e:

            print(
                "[SALE PRICE ERRO]",
                item_id,
                e
            )

    return None


# ============================================================
# PREÇOS
# ============================================================

def get_prices(
    item_id,
    auth
):

    response = ml_get_worker(

        f"/items/{item_id}/prices",

        auth
    )

    if not response:
        return None

    if response.status_code != 200:
        return None

    try:

        data = response.json()

    except Exception:

        return None

    prices = data.get(
        "prices",
        []
    )

    if not isinstance(
        prices,
        list
    ):

        return None

    valid = []

    for price in prices:

        try:

            amount = price.get(
                "amount"
            )

            if amount is None:
                continue

            context = price.get(
                "context",
                []
            )

            if isinstance(
                context,
                str
            ):

                context = [
                    context
                ]

            if (
                "channel_marketplace"
                not in context
            ):

                continue

            valid.append(
                price
            )

        except Exception:

            continue

    if not valid:
        return None

    valid.sort(
        key=lambda x:
            float(
                x.get(
                    "amount",
                    999999999
                )
            )
    )

    selected = valid[0]

    return {

        "amount":
            float(
                selected[
                    "amount"
                ]
            ),

        "regular_amount":
            (
                float(
                    selected[
                        "regular_amount"
                    ]
                )
                if selected.get(
                    "regular_amount"
                ) is not None
                else None
            ),

        "source":
            "prices",
    }


# ============================================================
# PREÇO ATUAL DO ITEM
# ============================================================

def get_current_item_price(
    item,
    auth
):

    item_id = item.get(
        "item_id"
    )

    if not item_id:
        return None

    # ========================================================
    # 1 - SALE PRICE
    # ========================================================

    sale = get_sale_price(
        item_id,
        auth
    )

    if sale:

        return sale

    # ========================================================
    # 2 - PRICES
    # ========================================================

    prices = get_prices(
        item_id,
        auth
    )

    if prices:

        return prices

    # ========================================================
    # 3 - FALLBACK
    # ========================================================

    raw_price = item.get(
        "price"
    )

    if raw_price is not None:

        try:

            return {

                "amount":
                    float(
                        raw_price
                    ),

                "regular_amount":
                    (
                        float(
                            item[
                                "original_price"
                            ]
                        )
                        if item.get(
                            "original_price"
                        ) is not None
                        else None
                    ),

                "source":
                    "product_items_fallback",
            }

        except Exception:

            pass

    return None


# ============================================================
# CUPONS
# ============================================================

def normalize_coupon_text(
    text
):

    if not text:
        return ""

    text = html_lib.unescape(
        text
    )

    text = text.replace(
        "\xa0",
        " "
    )

    text = text.replace(
        "\r",
        " "
    )

    text = text.replace(
        "\n",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def valid_coupon_code(
    code
):

    if not code:
        return False

    code = (
        code
        .strip()
        .upper()
    )

    if len(code) < 4:
        return False

    if len(code) > 30:
        return False

    return bool(
        re.fullmatch(
            r"[A-Z0-9][A-Z0-9_-]*",
            code
        )
    )


def coupon_blocks(
    text
):

    text = normalize_coupon_text(
        text
    )

    pattern = re.compile(

        r"\bCupom\s+"

        r"([A-Z0-9]"
        r"[A-Z0-9_-]{3,29})\b"

        r"(?=\s+Cupom\s+válido)",

        re.IGNORECASE
    )

    matches = list(
        pattern.finditer(
            text
        )
    )

    blocks = []

    for index, match in enumerate(
        matches
    ):

        code = (
            match.group(1)
            .upper()
        )

        if not valid_coupon_code(
            code
        ):

            continue

        start = (
            match.start()
        )

        if (
            index + 1
            < len(matches)
        ):

            end = matches[
                index + 1
            ].start()

        else:

            end = len(
                text
            )

        block = text[
            start:end
        ]

        blocks.append(
            (
                code,
                block
            )
        )

    return blocks


def parse_brazilian_money(
    value
):

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


def parse_coupon(
    code,
    block
):

    coupon = {

        "code":
            code.upper(),

        "type":
            None,

        "value":
            None,

        "min_purchase":
            0.0,

        "max_discount":
            None,

        "raw_text":
            block,
    }

    # ========================================================
    # %
    # ========================================================

    percent_patterns = [

        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*OFF",

        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*de\s*desconto",

        r"desconto\s+de\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%",

        r"até\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%",
    ]

    for pattern in (
        percent_patterns
    ):

        match = re.search(
            pattern,
            block,
            re.IGNORECASE
        )

        if not match:
            continue

        try:

            value = float(
                match.group(1)
                .replace(
                    ",",
                    "."
                )
            )

            if (
                value > 0
                and value <= 100
            ):

                coupon[
                    "type"
                ] = "percent"

                coupon[
                    "value"
                ] = value

                break

        except Exception:

            pass

    # ========================================================
    # MÍNIMO
    # ========================================================

    min_patterns = [

        r"mínimo\s+de\s+R?\$?\s*"
        r"([\d\.,]+)",

        r"valor\s+mínimo\s+de\s+"
        r"R?\$?\s*([\d\.,]+)",

        r"compras\s+a\s+partir\s+de\s+"
        r"R?\$?\s*([\d\.,]+)",

        r"acima\s+de\s+"
        r"R?\$?\s*([\d\.,]+)",
    ]

    for pattern in (
        min_patterns
    ):

        match = re.search(
            pattern,
            block,
            re.IGNORECASE
        )

        if not match:
            continue

        value = parse_brazilian_money(
            match.group(1)
        )

        if value is not None:

            coupon[
                "min_purchase"
            ] = value

            break

    # ========================================================
    # MÁXIMO
    # ========================================================

    max_patterns = [

        r"máximo\s+de\s+R?\$?\s*"
        r"([\d\.,]+)",

        r"até\s+R?\$?\s*"
        r"([\d\.,]+)\s+de\s+desconto",

        r"desconto\s+m[aá]ximo\s+de\s+"
        r"R?\$?\s*([\d\.,]+)",
    ]

    for pattern in (
        max_patterns
    ):

        match = re.search(
            pattern,
            block,
            re.IGNORECASE
        )

        if not match:
            continue

        value = parse_brazilian_money(
            match.group(1)
        )

        if value is not None:

            coupon[
                "max_discount"
            ] = value

            break

    # ========================================================
    # FIXO
    # ========================================================

    if coupon[
        "type"
    ] is None:

        fixed_patterns = [

            r"R\$\s*([\d\.,]+)"
            r"\s+de\s+desconto",

            r"desconto\s+de\s+"
            r"R\$\s*([\d\.,]+)",

            r"R\$\s*([\d\.,]+)"
            r"\s+OFF",
        ]

        for pattern in (
            fixed_patterns
        ):

            match = re.search(
                pattern,
                block,
                re.IGNORECASE
            )

            if not match:
                continue

            value = (
                parse_brazilian_money(
                    match.group(1)
                )
            )

            if (
                value is not None
                and value > 0
            ):

                coupon[
                    "type"
                ] = "fixed"

                coupon[
                    "value"
                ] = value

                break

    return coupon


def sync_coupons():

    print(
        "[CUPONS] Atualizando..."
    )

    try:

        response = requests.get(

            COUPON_PAGE,

            headers={
                "User-Agent":
                    (
                        "Mozilla/5.0 "
                        "(Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 "
                        "Chrome/140 Safari/537.36"
                    )
            },

            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[CUPONS] HTTP",
                response.status_code
            )

            return []

        text = normalize_coupon_text(
            response.text
        )

    except Exception as e:

        print(
            "[CUPONS ERRO]",
            e
        )

        return []

    blocks = coupon_blocks(
        text
    )

    coupons = []

    for code, block in blocks:

        coupon = parse_coupon(
            code,
            block
        )

        if not coupon.get(
            "type"
        ):

            continue

        coupons.append(
            coupon
        )

        print(
            "[CUPOM OK]",
            code,
            "|",
            coupon["type"],
            "|",
            coupon["value"],
            "| MIN",
            coupon["min_purchase"],
            "| MAX",
            coupon["max_discount"]
        )

    conn = db()

    for coupon in coupons:

        conn.execute(
            """
            INSERT INTO coupons (
                code,
                coupon_type,
                value,
                min_purchase,
                max_discount,
                raw_text,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(code)
            DO UPDATE SET

                coupon_type =
                    excluded.coupon_type,

                value =
                    excluded.value,

                min_purchase =
                    excluded.min_purchase,

                max_discount =
                    excluded.max_discount,

                raw_text =
                    excluded.raw_text,

                updated_at =
                    excluded.updated_at
            """,

            (
                coupon["code"],
                coupon["type"],
                coupon["value"],
                coupon["min_purchase"],
                coupon["max_discount"],
                coupon["raw_text"],
                int(time.time())
            )
        )

    conn.commit()
    conn.close()

    return coupons


def get_saved_coupons():

    conn = db()

    rows = conn.execute(
        """
        SELECT
            code,
            coupon_type,
            value,
            min_purchase,
            max_discount,
            raw_text
        FROM coupons
        ORDER BY id DESC
        """
    ).fetchall()

    conn.close()

    return [

        {
            "code":
                row["code"],

            "type":
                row["coupon_type"],

            "value":
                row["value"],

            "min_purchase":
                row["min_purchase"]
                or 0,

            "max_discount":
                row["max_discount"],

            "raw_text":
                row["raw_text"],
        }

        for row in rows
    ]


# ============================================================
# DESCONTO
# ============================================================

def coupon_discount(
    price,
    coupon
):

    if price is None:
        return 0.0

    try:

        price = float(
            price
        )

    except Exception:

        return 0.0

    minimum = float(
        coupon.get(
            "min_purchase"
        )
        or 0
    )

    if price < minimum:

        return 0.0

    value = coupon.get(
        "value"
    )

    if value is None:

        return 0.0

    try:

        value = float(
            value
        )

    except Exception:

        return 0.0

    if coupon.get(
        "type"
    ) == "percent":

        discount = (
            price
            * value
            / 100
        )

    elif coupon.get(
        "type"
    ) == "fixed":

        discount = value

    else:

        return 0.0

    maximum = coupon.get(
        "max_discount"
    )

    if maximum is not None:

        try:

            discount = min(
                discount,
                float(
                    maximum
                )
            )

        except Exception:

            pass

    discount = min(
        discount,
        price
    )

    return round(
        max(
            0,
            discount
        ),
        2
    )


def best_coupon(
    price,
    coupons
):

    best = None

    for coupon in coupons:

        discount = coupon_discount(
            price,
            coupon
        )

        if discount <= 0:
            continue

        final_price = round(
            price - discount,
            2
        )

        effective = (
            discount
            / price
            * 100
            if price > 0
            else 0
        )

        candidate = {

            **coupon,

            "discount":
                discount,

            "final_price":
                final_price,

            "effective_percent":
                effective,
        }

        if best is None:

            best = candidate

            continue

        if (
            discount
            > best["discount"]
        ):

            best = candidate

            continue

        if (
            discount
            == best["discount"]
            and final_price
            < best["final_price"]
        ):

            best = candidate

    return best


# ============================================================
# BUY BOX
# ============================================================

def get_buy_box_item(
    product
):

    if not isinstance(
        product,
        dict
    ):

        return None

    winner = product.get(
        "buy_box_winner"
    )

    if isinstance(
        winner,
        dict
    ):

        return winner.get(
            "item_id"
        )

    return None


# ============================================================
# PUBLICAÇÕES
# ============================================================

def select_candidate_items(
    product,
    items
):

    winner_id = (
        get_buy_box_item(
            product
        )
    )

    result = []

    seen = set()

    # BUY BOX
    if winner_id:

        for item in items:

            if (
                item.get(
                    "item_id"
                )
                == winner_id
            ):

                result.append(
                    item
                )

                seen.add(
                    winner_id
                )

                break

    # OUTROS
    others = []

    for item in items:

        item_id = item.get(
            "item_id"
        )

        if not item_id:
            continue

        if item_id in seen:
            continue

        others.append(
            item
        )

    others.sort(
        key=lambda x:
            float(
                x.get(
                    "price"
                )
                or 999999999
            )
    )

    result.extend(
        others[
            :MAX_ITEMS_PER_PRODUCT
        ]
    )

    return result


# ============================================================
# AVALIA ITEM
# ============================================================

def evaluate_item(
    product,
    item,
    coupons,
    price_cache,
    auth
):

    item_id = item.get(
        "item_id"
    )

    if not item_id:

        return None

    # CACHE
    if item_id in price_cache:

        price_data = (
            price_cache[
                item_id
            ]
        )

    else:

        price_data = (
            get_current_item_price(
                item,
                auth
            )
        )

        price_cache[
            item_id
        ] = price_data

    if not price_data:

        return None

    price = price_data.get(
        "amount"
    )

    if price is None:

        return None

    try:

        price = float(
            price
        )

    except Exception:

        return None

    if price < MIN_PRODUCT_PRICE:

        return None

    coupon = best_coupon(
        price,
        coupons
    )

    if coupon:

        final_price = (
            coupon[
                "final_price"
            ]
        )

        discount = (
            coupon[
                "discount"
            ]
        )

        coupon_code = (
            coupon[
                "code"
            ]
        )

        coupon_type = (
            coupon[
                "type"
            ]
        )

        coupon_value = (
            coupon[
                "value"
            ]
        )

    else:

        final_price = round(
            price,
            2
        )

        discount = 0

        coupon_code = None

        coupon_type = None

        coupon_value = None

    winner_id = (
        get_buy_box_item(
            product
        )
    )

    is_buy_box = (
        winner_id == item_id
        if winner_id
        else False
    )

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

    return {

        "product_id":
            product.get(
                "id"
            ),

        "item_id":
            item_id,

        "seller_id":
            item.get(
                "seller_id"
            ),

        "title":
            (
                product.get(
                    "name"
                )
                or product.get(
                    "title"
                )
                or item_id
            ),

        "price":
            round(
                price,
                2
            ),

        "price_source":
            price_data.get(
                "source"
            ),

        "coupon_code":
            coupon_code,

        "coupon_type":
            coupon_type,

        "coupon_value":
            coupon_value,

        "discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "effective_percent":
            (
                round(
                    discount
                    / price
                    * 100,
                    2
                )
                if price > 0
                else 0
            ),

        "free_shipping":
            free_shipping,

        "is_buy_box":
            is_buy_box,

        "url":
            (
                "https://www.mercadolivre.com.br/"
                + item_id
            ),
    }


# ============================================================
# MELHOR OFERTA
# ============================================================

def choose_best_product_offer(
    offers
):

    if not offers:

        return None

    def ranking(
        offer
    ):

        return (

            # MENOR PREÇO FINAL
            offer[
                "final_price"
            ],

            # MAIOR DESCONTO
            -offer[
                "discount"
            ],

            # MAIOR %
            -offer[
                "effective_percent"
            ],

            # FRETE GRÁTIS
            0
            if offer[
                "free_shipping"
            ]
            else 1,

            # BUY BOX
            0
            if offer[
                "is_buy_box"
            ]
            else 1,

            # MENOR PREÇO BASE
            offer[
                "price"
            ],
        )

    return sorted(
        offers,
        key=ranking
    )[0]


# ============================================================
# EXECUTAR CAÇA EM SEGUNDO PLANO
# ============================================================

def run_scan(
    job_id,
    auth
):

    try:

        # ====================================================
        # CUPONS
        # ====================================================

        update_job(
            job_id,
            progress=2,
            message=(
                "Atualizando cupons..."
            )
        )

        coupons = sync_coupons()

        if not coupons:

            coupons = (
                get_saved_coupons()
            )

        update_job(
            job_id,
            progress=5,
            message=(
                f"{len(coupons)} cupons "
                "encontrados. "
                "Procurando produtos..."
            )
        )

        # ====================================================
        # LISTA DE BUSCAS
        # ====================================================

        all_queries = []

        for category, queries in (
            SEARCH_QUERIES.items()
        ):

            for query in queries:

                all_queries.append(
                    (
                        category,
                        query
                    )
                )

        total_queries = len(
            all_queries
        )

        products_map = {}

        # ====================================================
        # BUSCAR PRODUTOS
        # ====================================================

        for index, (
            category,
            query
        ) in enumerate(
            all_queries,
            start=1
        ):

            progress = int(
                5
                + (
                    index
                    / total_queries
                    * 25
                )
            )

            update_job(
                job_id,

                progress=progress,

                message=(
                    f"Buscando "
                    f"{category}: "
                    f"{query}"
                )
            )

            results = product_search(
                query,
                auth,
                limit=20
            )

            for product in results:

                product_id = (

                    product.get(
                        "id"
                    )

                    or product.get(
                        "product_id"
                    )
                )

                if not product_id:

                    continue

                if (
                    product_id
                    not in products_map
                ):

                    products_map[
                        product_id
                    ] = {

                        "product":
                            product,

                        "category":
                            category,
                    }

            if (
                len(products_map)
                >= MAX_PRODUCTS_SCAN
            ):

                break

        products_list = list(
            products_map.items()
        )[
            :MAX_PRODUCTS_SCAN
        ]

        total_products = len(
            products_list
        )

        update_job(
            job_id,

            progress=32,

            message=(
                f"{total_products} "
                "produtos únicos encontrados. "
                "Comparando preços..."
            ),

            total=total_products,

            processed=0
        )

        # ====================================================
        # AVALIAR PRODUTOS
        # ====================================================

        selected_offers = []

        price_cache = {}

        for index, (
            product_id,
            info
        ) in enumerate(
            products_list,
            start=1
        ):

            progress = int(
                32
                + (
                    index
                    / max(
                        total_products,
                        1
                    )
                    * 63
                )
            )

            update_job(
                job_id,

                progress=progress,

                message=(
                    f"Analisando produto "
                    f"{index}/"
                    f"{total_products}"
                ),

                processed=index
            )

            # =================================================
            # DETALHE
            # =================================================

            product = get_product(
                product_id,
                auth
            )

            if not product:

                product = (
                    info[
                        "product"
                    ]
                )

            # =================================================
            # PUBLICAÇÕES
            # =================================================

            raw_items = (
                get_product_items(
                    product_id,
                    auth
                )
            )

            if not raw_items:

                continue

            candidates = (
                select_candidate_items(
                    product,
                    raw_items
                )
            )

            product_offers = []

            for item in candidates:

                offer = evaluate_item(

                    product,

                    item,

                    coupons,

                    price_cache,

                    auth
                )

                if not offer:

                    continue

                offer[
                    "category"
                ] = info[
                    "category"
                ]

                product_offers.append(
                    offer
                )

            # =================================================
            # UM PRODUTO = UMA OFERTA
            # =================================================

            best = (
                choose_best_product_offer(
                    product_offers
                )
            )

            if best:

                selected_offers.append(
                    best
                )

                print(

                    "[MELHOR]",

                    best[
                        "title"
                    ][:70],

                    "| R$",

                    best[
                        "price"
                    ],

                    "| CUPOM",

                    best[
                        "coupon_code"
                    ],

                    "| FINAL",

                    best[
                        "final_price"
                    ],

                    "| FONTE",

                    best[
                        "price_source"
                    ]
                )

        # ====================================================
        # DEDUP
        # ====================================================

        unique = {}

        for offer in selected_offers:

            key = offer[
                "product_id"
            ]

            old = unique.get(
                key
            )

            if old is None:

                unique[
                    key
                ] = offer

            elif (
                offer[
                    "final_price"
                ]
                < old[
                    "final_price"
                ]
            ):

                unique[
                    key
                ] = offer

        selected_offers = list(
            unique.values()
        )

        # ====================================================
        # ORDENAR
        # ====================================================

        selected_offers.sort(

            key=lambda x: (

                0
                if x[
                    "coupon_code"
                ]
                else 1,

                -x[
                    "discount"
                ],

                x[
                    "final_price"
                ],
            )
        )

        # ====================================================
        # SALVAR
        # ====================================================

        conn = db()

        conn.execute(
            "DELETE FROM ofertas"
        )

        for offer in selected_offers:

            conn.execute(
                """
                INSERT INTO ofertas (
                    product_id,
                    item_id,
                    title,
                    category,
                    price,
                    coupon_code,
                    coupon_type,
                    coupon_value,
                    discount,
                    final_price,
                    url,
                    seller_id,
                    created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,

                (

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
                        "category"
                    ],

                    offer[
                        "price"
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
                        "discount"
                    ],

                    offer[
                        "final_price"
                    ],

                    offer[
                        "url"
                    ],

                    offer[
                        "seller_id"
                    ],

                    int(
                        time.time()
                    ),
                )
            )

        conn.commit()
        conn.close()

        # ====================================================
        # FINALIZADO
        # ====================================================

        update_job(

            job_id,

            progress=100,

            message=(
                f"Caça finalizada: "
                f"{len(selected_offers)} "
                "produtos únicos."
            ),

            total=total_products,

            processed=total_products,

            status="finished",

            finished=True
        )

        print(
            "=" * 60
        )

        print(
            "CAÇA FINALIZADA:",
            len(
                selected_offers
            ),
            "produtos"
        )

        print(
            "=" * 60
        )

    except Exception as e:

        print(
            "[SCAN ERRO]",
            repr(e)
        )

        update_job(

            job_id,

            progress=100,

            message=(
                "Erro durante a caça: "
                + str(e)
            ),

            status="error",

            finished=True
        )


# ============================================================
# OFERTAS
# ============================================================

def load_offers():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM ofertas
        ORDER BY

            CASE
                WHEN coupon_code IS NULL
                THEN 1
                ELSE 0
            END,

            discount DESC,

            final_price ASC
        """
    ).fetchall()

    conn.close()

    result = []

    for row in rows:

        result.append({

            "product_id":
                row[
                    "product_id"
                ],

            "item_id":
                row[
                    "item_id"
                ],

            "title":
                row[
                    "title"
                ],

            "category":
                row[
                    "category"
                ],

            "price":
                row[
                    "price"
                ],

            "coupon_code":
                row[
                    "coupon_code"
                ],

            "coupon_type":
                row[
                    "coupon_type"
                ],

            "coupon_value":
                row[
                    "coupon_value"
                ],

            "discount":
                row[
                    "discount"
                ],

            "final_price":
                row[
                    "final_price"
                ],

            "url":
                row[
                    "url"
                ],

            "seller_id":
                row[
                    "seller_id"
                ],
        })

    return result


# ============================================================
# API - INICIAR
# ============================================================

@app.route(
    "/api/scan/start",
    methods=["POST"]
)
def api_scan_start():

    access_token = (
        get_access_token()
    )

    refresh_token = (
        get_refresh_token()
    )

    if not access_token:

        return jsonify({

            "ok":
                False,

            "error":
                "Conecte o Mercado Livre primeiro."
        }), 401

    job_id = create_job()

    if job_id is None:

        return jsonify({

            "ok":
                False,

            "running":
                True,

            "error":
                "Já existe uma caça em andamento."
        })

    # ========================================================
    # IMPORTANTE:
    # O TOKEN É COPIADO PARA A THREAD.
    # A THREAD NÃO ACESSA MAIS session.
    # ========================================================

    auth = {

        "access_token":
            access_token,

        "refresh_token":
            refresh_token,
    }

    thread = threading.Thread(

        target=run_scan,

        args=(
            job_id,
            auth
        ),

        daemon=True
    )

    thread.start()

    return jsonify({

        "ok":
            True,

        "job_id":
            job_id,

        "message":
            "Caça iniciada."
    })


# ============================================================
# API - STATUS
# ============================================================

@app.route(
    "/api/scan/status/<int:job_id>"
)
def api_scan_status(
    job_id
):

    job = get_job(
        job_id
    )

    if not job:

        return jsonify({

            "ok":
                False,

            "error":
                "Caça não encontrada."
        }), 404

    return jsonify({

        "ok":
            True,

        "job":
            job
    })


# ============================================================
# API - ÚLTIMA CAÇA
# ============================================================

@app.route(
    "/api/scan/last"
)
def api_scan_last():

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM scan_jobs
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    conn.close()

    if not row:

        return jsonify({

            "ok":
                True,

            "job":
                None
        })

    return jsonify({

        "ok":
            True,

        "job":
            dict(row)
    })


# ============================================================
# API OFERTAS
# ============================================================

@app.route(
    "/api/offers"
)
def api_offers():

    return jsonify({

        "ok":
            True,

        "offers":
            load_offers()
    })


# ============================================================
# API CUPONS
# ============================================================

@app.route(
    "/api/coupons"
)
def api_coupons():

    return jsonify({

        "ok":
            True,

        "coupons":
            get_saved_coupons()
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

        "mercadolivre":
            bool(
                get_access_token()
            ),
    })


# ============================================================
# GERAR ANÚNCIO
# ============================================================

@app.route(
    "/api/generate-ad",
    methods=["POST"]
)
def generate_ad():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    title = data.get(
        "title",
        "Oferta Mercado Livre"
    )

    price = data.get(
        "price"
    )

    final_price = data.get(
        "final_price"
    )

    coupon = data.get(
        "coupon_code"
    )

    url = data.get(
        "url",
        ""
    )

    text = (
        "🔥 OFERTA NO MERCADO LIVRE\n\n"
    )

    text += (
        f"{title}\n\n"
    )

    if price is not None:

        text += (

            "💰 Valor: R$ "

            + f"{float(price):.2f}"
            .replace(
                ".",
                ","
            )

            + "\n"
        )

    if final_price is not None:

        text += (

            "🔥 Com desconto: R$ "

            + f"{float(final_price):.2f}"
            .replace(
                ".",
                ","
            )

            + "\n"
        )

    if coupon:

        text += (
            f"\n🎟️ Cupom: {coupon}\n"
        )

    text += (
        "\n⚠️ Preço e disponibilidade "
        "podem mudar.\n"
        "Confira no Mercado Livre "
        "antes de comprar.\n"
    )

    if url:

        text += (
            f"\n👉 {url}"
        )

    return jsonify({

        "ok":
            True,

        "text":
            text
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

<title>
Caçador de Ofertas
</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background: #080b10;

    color: #f5f5f5;

    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {

    width:
        min(1180px, 94%);

    margin:
        auto;

    padding:
        22px 0 60px;
}

.header {

    background:
        #111722;

    border:
        1px solid #222b39;

    border-radius:
        18px;

    padding:
        20px;

    margin-bottom:
        18px;
}

.header h1 {

    margin:
        0 0 7px;

    font-size:
        25px;
}

.header p {

    margin:
        0;

    color:
        #9ca7b7;
}

.connection {

    margin-top:
        15px;

    display:
        flex;

    gap:
        10px;

    flex-wrap:
        wrap;

    align-items:
        center;
}

button,
.btn {

    border:
        0;

    border-radius:
        10px;

    padding:
        12px 16px;

    background:
        #3483fa;

    color:
        white;

    cursor:
        pointer;

    font-weight:
        700;

    text-decoration:
        none;

    display:
        inline-block;
}

button:hover,
.btn:hover {

    opacity:
        .9;
}

button:disabled {

    opacity:
        .6;

    cursor:
        wait;
}

.btn-dark {

    background:
        #202938;
}

.status {

    color:
        #72e58b;

    font-weight:
        700;
}

.stats {

    display:
        grid;

    grid-template-columns:
        repeat(4, 1fr);

    gap:
        12px;

    margin-bottom:
        18px;
}

.stat {

    background:
        #111722;

    border:
        1px solid #222b39;

    border-radius:
        15px;

    padding:
        16px;
}

.stat small {

    color:
        #8e99aa;

    display:
        block;

    margin-bottom:
        8px;
}

.stat strong {

    font-size:
        23px;
}

.scan-box {

    background:
        #111722;

    border:
        1px solid #222b39;

    border-radius:
        18px;

    padding:
        18px;

    margin-bottom:
        18px;
}

.scan-box h2 {

    margin-top:
        0;
}

.progress-wrap {

    margin-top:
        15px;
}

.progress-bar {

    width:
        100%;

    height:
        10px;

    background:
        #202938;

    border-radius:
        20px;

    overflow:
        hidden;
}

.progress-fill {

    width:
        0%;

    height:
        100%;

    background:
        #3483fa;

    transition:
        width .4s;
}

.progress-text {

    margin-top:
        10px;

    color:
        #aeb8c7;

    min-height:
        22px;
}

.offers {

    display:
        grid;

    grid-template-columns:
        repeat(
            auto-fill,
            minmax(285px, 1fr)
        );

    gap:
        14px;
}

.offer {

    background:
        #111722;

    border:
        1px solid #252f3e;

    border-radius:
        18px;

    padding:
        17px;
}

.offer h3 {

    font-size:
        16px;

    line-height:
        1.35;

    margin:
        0 0 15px;
}

.label {

    color:
        #8e99aa;

    font-size:
        12px;
}

.value {

    font-size:
        20px;

    font-weight:
        800;

    margin-top:
        4px;
}

.coupon {

    margin-top:
        14px;

    background:
        #17251d;

    border:
        1px solid #2d6840;

    padding:
        11px;

    border-radius:
        12px;
}

.coupon-code {

    color:
        #72e58b;

    font-weight:
        900;

    font-size:
        17px;
}

.final {

    margin-top:
        14px;

    background:
        #182335;

    border-radius:
        12px;

    padding:
        12px;
}

.final .value {

    color:
        #58a6ff;

    font-size:
        23px;
}

.actions {

    margin-top:
        15px;

    display:
        flex;

    gap:
        8px;

    flex-wrap:
        wrap;
}

.empty {

    background:
        #111722;

    border:
        1px solid #222b39;

    padding:
        25px;

    border-radius:
        16px;

    color:
        #9ca7b7;
}

.note {

    margin-top:
        18px;

    color:
        #7f8a9b;

    font-size:
        12px;

    line-height:
        1.5;
}

@media (
    max-width: 800px
) {

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }
}

@media (
    max-width: 480px
) {

    .stats {

        grid-template-columns:
            1fr 1fr;
    }

    .container {

        width:
            92%;
    }
}

</style>

</head>

<body>

<div class="container">


    <!-- HEADER -->

    <div class="header">

        <h1>
            🤑 Caçador de Ofertas
        </h1>

        <p>
            Produtos + preço atual + cupons aplicáveis
        </p>


        <div class="connection">

            {% if connected %}

                <span class="status">

                    🟢 Mercado Livre conectado

                    {% if user %}

                        — {{ user.nickname or user.id }}

                    {% endif %}

                </span>


                <a
                    class="btn btn-dark"
                    href="/mercadolivre/disconnect"
                >
                    Desconectar
                </a>

            {% else %}

                <a
                    class="btn"
                    href="/mercadolivre/connect"
                >
                    🔗 Conectar Mercado Livre
                </a>

            {% endif %}

        </div>

    </div>


    <!-- STATS -->

    <div class="stats">


        <div class="stat">

            <small>
                Ofertas
            </small>

            <strong
                id="statOffers"
            >
                0
            </strong>

        </div>


        <div class="stat">

            <small>
                Cupom aplicável
            </small>

            <strong
                id="statCoupons"
            >
                0
            </strong>

        </div>


        <div class="stat">

            <small>
                Valor
            </small>

            <strong
                id="statPrice"
            >
                R$ 0,00
            </strong>

        </div>


        <div class="stat">

            <small>
                Valor com desconto
            </small>

            <strong
                id="statFinal"
            >
                R$ 0,00
            </strong>

        </div>

    </div>


    <!-- CAÇA -->

    <div class="scan-box">

        <h2>
            🔎 Caçar ofertas
        </h2>


        <button
            id="scanBtn"
            onclick="startScan()"
        >
            🚀 CAÇAR OFERTAS
        </button>


        <div
            class="progress-wrap"
        >

            <div
                class="progress-bar"
            >

                <div
                    id="progressFill"
                    class="progress-fill"
                ></div>

            </div>


            <div
                id="progressText"
                class="progress-text"
            >
                Pronto para começar.
            </div>

        </div>

    </div>


    <!-- OFERTAS -->

    <div
        id="offers"
        class="offers"
    ></div>


    <div class="note">

        Os valores com cupom são estimativas.
        A aplicação do cupom depende das regras do
        Mercado Livre, elegibilidade do produto,
        conta do comprador e disponibilidade do cupom
        no momento da compra.

    </div>

</div>


<script>

let polling = null;


function money(value) {

    return Number(
        value || 0
    ).toLocaleString(
        'pt-BR',
        {
            style:
                'currency',

            currency:
                'BRL'
        }
    );
}


function escapeHtml(text) {

    return String(
        text || ''
    )

    .replaceAll(
        '&',
        '&amp;'
    )

    .replaceAll(
        '<',
        '&lt;'
    )

    .replaceAll(
        '>',
        '&gt;'
    )

    .replaceAll(
        '"',
        '&quot;'
    )

    .replaceAll(
        "'",
        '&#039;'
    );
}


function updateStats(
    offers
) {

    const total =
        offers.length;


    const couponOffers =
        offers.filter(
            x =>
                x.coupon_code
        ).length;


    const prices =
        offers

        .map(
            x =>
                Number(
                    x.price
                )
        )

        .filter(
            x =>
                !isNaN(x)
        );


    const finals =
        offers

        .map(
            x =>
                Number(
                    x.final_price
                )
        )

        .filter(
            x =>
                !isNaN(x)
        );


    const lowestPrice =
        prices.length

        ? Math.min(
            ...prices
        )

        : 0;


    const lowestFinal =
        finals.length

        ? Math.min(
            ...finals
        )

        : 0;


    document.getElementById(
        'statOffers'
    ).textContent =
        total;


    document.getElementById(
        'statCoupons'
    ).textContent =
        couponOffers;


    document.getElementById(
        'statPrice'
    ).textContent =
        money(
            lowestPrice
        );


    document.getElementById(
        'statFinal'
    ).textContent =
        money(
            lowestFinal
        );
}


function renderOffers(
    offers
) {

    const box =
        document.getElementById(
            'offers'
        );

    box.innerHTML = '';


    if (
        !offers
        || !offers.length
    ) {

        box.innerHTML = `

            <div class="empty">

                Nenhuma oferta encontrada.

            </div>

        `;

        updateStats([]);

        return;
    }


    for (
        const offer of offers
    ) {

        const couponHtml =

            offer.coupon_code

            ? `

                <div class="coupon">

                    <div class="label">
                        CUPOM APLICÁVEL
                    </div>

                    <div class="coupon-code">

                        ${escapeHtml(
                            offer.coupon_code
                        )}

                    </div>

                </div>

            `

            : `

                <div
                    class="coupon"
                    style="
                        background:#201b14;
                        border-color:#604b25;
                    "
                >

                    <div class="label">
                        CUPOM
                    </div>

                    <div
                        style="
                            color:#e8bd67;
                            font-weight:800;
                        "
                    >
                        Nenhum cupom encontrado
                    </div>

                </div>

            `;


        const card =
            document.createElement(
                'div'
            );


        card.className =
            'offer';


        card.innerHTML = `

            <h3>

                ${escapeHtml(
                    offer.title
                )}

            </h3>


            <div class="label">

                VALOR ATUAL

            </div>


            <div class="value">

                ${money(
                    offer.price
                )}

            </div>


            ${couponHtml}


            <div class="final">

                <div class="label">

                    VALOR COM DESCONTO

                </div>


                <div class="value">

                    ${money(
                        offer.final_price
                    )}

                </div>

            </div>


            <div class="actions">


                <a
                    class="btn"
                    href="${escapeHtml(
                        offer.url
                    )}"
                    target="_blank"
                    rel="noopener"
                >

                    🛒 Abrir produto

                </a>


                <button
                    class="btn-dark"
                    onclick='generateAd(
                        ${JSON.stringify(
                            offer
                        )}
                    )'
                >

                    📢 Gerar anúncio

                </button>


            </div>

        `;


        box.appendChild(
            card
        );
    }


    updateStats(
        offers
    );
}


async function loadOffers() {

    try {

        const response =
            await fetch(
                '/api/offers'
            );


        const data =
            await response.json();


        if (data.ok) {

            renderOffers(
                data.offers
            );
        }

    } catch (error) {

        console.error(
            error
        );
    }
}


async function startScan() {

    const button =
        document.getElementById(
            'scanBtn'
        );


    const progressText =
        document.getElementById(
            'progressText'
        );


    button.disabled =
        true;


    button.textContent =
        '⏳ CAÇANDO...';


    progressText.textContent =
        'Iniciando caça...';


    document.getElementById(
        'progressFill'
    ).style.width =
        '1%';


    try {

        const response =
            await fetch(

                '/api/scan/start',

                {
                    method:
                        'POST'
                }
            );


        const data =
            await response.json();


        if (!data.ok) {

            progressText.textContent =
                data.error ||
                'Não foi possível iniciar.';


            button.disabled =
                false;


            button.textContent =
                '🚀 CAÇAR OFERTAS';


            return;
        }


        progressText.textContent =
            'Caça iniciada...';


        pollScan(
            data.job_id
        );


    } catch (error) {

        console.error(
            error
        );


        progressText.textContent =
            'Erro ao iniciar a caça.';


        button.disabled =
            false;


        button.textContent =
            '🚀 CAÇAR OFERTAS';
    }
}


function pollScan(
    jobId
) {

    if (polling) {

        clearInterval(
            polling
        );
    }


    polling =
        setInterval(
            async function() {

                try {

                    const response =
                        await fetch(

                            `/api/scan/status/${jobId}`

                        );


                    const data =
                        await response.json();


                    if (!data.ok) {

                        return;
                    }


                    const job =
                        data.job;


                    const progress =
                        Number(
                            job.progress
                            || 0
                        );


                    document.getElementById(
                        'progressFill'
                    ).style.width =
                        `${progress}%`;


                    document.getElementById(
                        'progressText'
                    ).textContent =
                        job.message
                        || 'Processando...';


                    if (
                        job.status
                        === 'finished'
                    ) {

                        clearInterval(
                            polling
                        );

                        polling =
                            null;


                        document.getElementById(
                            'scanBtn'
                        ).disabled =
                            false;


                        document.getElementById(
                            'scanBtn'
                        ).textContent =
                            '🚀 CAÇAR OFERTAS';


                        await loadOffers();

                        return;
                    }


                    if (
                        job.status
                        === 'error'
                    ) {

                        clearInterval(
                            polling
                        );

                        polling =
                            null;


                        document.getElementById(
                            'scanBtn'
                        ).disabled =
                            false;


                        document.getElementById(
                            'scanBtn'
                        ).textContent =
                            '🚀 CAÇAR OFERTAS';


                        return;
                    }


                } catch (error) {

                    console.error(
                        error
                    );

                    // Não interrompe
                    // a caça por falha
                    // momentânea da interface.
                }

            },

            2000
        );
}


async function generateAd(
    offer
) {

    try {

        const response =
            await fetch(

                '/api/generate-ad',

                {
                    method:
                        'POST',

                    headers: {

                        'Content-Type':
                            'application/json'
                    },

                    body:
                        JSON.stringify(
                            offer
                        )
                }
            );


        const data =
            await response.json();


        if (!data.ok) {

            alert(
                'Não foi possível gerar o anúncio.'
            );

            return;
        }


        await navigator
            .clipboard
            .writeText(
                data.text
            );


        alert(
            'Anúncio copiado!'
        );


    } catch (error) {

        console.error(
            error
        );


        alert(
            'Não foi possível copiar.'
        );
    }
}


loadOffers();


async function checkLastScan() {

    try {

        const response =
            await fetch(
                '/api/scan/last'
            );


        const data =
            await response.json();


        if (
            !data.ok
            || !data.job
        ) {

            return;
        }


        const job =
            data.job;


        if (
            job.status
            === 'running'
        ) {

            document.getElementById(
                'scanBtn'
            ).disabled =
                true;


            document.getElementById(
                'scanBtn'
            ).textContent =
                '⏳ CAÇANDO...';


            pollScan(
                job.id
            );
        }


    } catch (error) {

        console.error(
            error
        );
    }
}


checkLastScan();

</script>

</body>

</html>
'''


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    connected = bool(
        get_access_token()
    )

    user = None

    if connected:

        user = get_ml_user()

    return render_template_string(

        HTML,

        connected=connected,

        user=user
    )


# ============================================================
# ERRO GLOBAL
# ============================================================

@app.errorhandler(
    Exception
)
def handle_exception(
    error
):

    print(
        "[ERRO GLOBAL]",
        repr(error)
    )

    return jsonify({

        "ok":
            False,

        "error":
            str(error)
    }), 500


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

    print(
        "=" * 60
    )

    print(
        "CAÇADOR DE OFERTAS"
    )

    print(
        "PORTA:",
        port
    )

    print(
        "BUSCA EM SEGUNDO PLANO: ATIVA"
    )

    print(
        "FLASK SESSION NA THREAD: DESATIVADO"
    )

    print(
        "=" * 60
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )