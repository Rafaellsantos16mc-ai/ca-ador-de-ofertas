import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
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
# CONFIGURAÇÃO
# ============================================================

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "troque-esta-chave-no-railway"
)

DATABASE = "ofertas.db"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

# ============================================================
# CAÇADOR
# ============================================================

MIN_PRODUCT_PRICE = 69.90

MAX_PRODUCTS = 24

PRODUCT_SEARCH_LIMIT = 5

CATEGORIES = [
    "celular",
    "perfume",
    "ferramentas",
    "eletronicos",
    "casa",
    "automotivo",
    "cozinha",
    "academia",
]


# ============================================================
# BANCO
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ml_auth (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# TOKEN
# ============================================================

def save_token(
    access_token,
    refresh_token=None,
    expires_in=None,
    user_id=None,
    nickname=None
):

    expires_at = (
        int(time.time())
        + int(expires_in or 21600)
    )

    conn = db()

    old = conn.execute(
        """
        SELECT refresh_token, user_id, nickname
        FROM ml_auth
        WHERE id=1
        """
    ).fetchone()

    if refresh_token is None and old:
        refresh_token = old["refresh_token"]

    if user_id is None and old:
        user_id = old["user_id"]

    if nickname is None and old:
        nickname = old["nickname"]

    conn.execute("""
        INSERT INTO ml_auth
        (
            id,
            access_token,
            refresh_token,
            expires_at,
            user_id,
            nickname,
            updated_at
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)

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
        expires_at,
        user_id,
        nickname,
        int(time.time()),
    ))

    conn.commit()
    conn.close()


def get_auth():

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM ml_auth
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def clear_auth():

    conn = db()

    conn.execute(
        "DELETE FROM ml_auth WHERE id=1"
    )

    conn.commit()
    conn.close()


# ============================================================
# REFRESH TOKEN
# ============================================================

def refresh_access_token():

    auth = get_auth()

    if not auth:
        return None

    refresh_token = auth.get(
        "refresh_token"
    )

    if not refresh_token:
        return None

    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
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
            timeout=20,
        )

        try:
            data = response.json()
        except Exception:
            data = {}

        if response.status_code != 200:

            print(
                "[REFRESH ERRO]",
                response.status_code,
                data
            )

            return None

        new_access = data.get(
            "access_token"
        )

        if not new_access:
            return None

        new_refresh = data.get(
            "refresh_token",
            refresh_token
        )

        save_token(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=data.get(
                "expires_in",
                21600
            ),
            user_id=auth.get(
                "user_id"
            ),
            nickname=auth.get(
                "nickname"
            ),
        )

        print(
            "[TOKEN] Renovado com sucesso"
        )

        return new_access

    except Exception as e:

        print(
            "[REFRESH EXCEPTION]",
            repr(e)
        )

        return None


def get_access_token(
    force_refresh=False
):

    auth = get_auth()

    if not auth:
        return None

    token = auth.get(
        "access_token"
    )

    expires_at = int(
        auth.get(
            "expires_at"
        ) or 0
    )

    if (
        not force_refresh
        and token
        and expires_at > int(time.time()) + 120
    ):
        return token

    return refresh_access_token()


# ============================================================
# HEADERS
# ============================================================

def ml_headers(token=None):

    if token is None:
        token = get_access_token()

    headers = {
        "Accept":
            "application/json",

        "User-Agent":
            "CacadorDeOfertas/3.0",
    }

    if token:
        headers["Authorization"] = (
            f"Bearer {token}"
        )

    return headers


# ============================================================
# REQUEST MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None,
    retry_refresh=True
):

    token = get_access_token()

    if not token:

        return {
            "ok": False,
            "status": 401,
            "data": {
                "error":
                    "not_authenticated",

                "message":
                    "Mercado Livre não conectado."
            }
        }

    url = ML_API + path

    try:

        response = requests.get(
            url,
            params=params,
            headers=ml_headers(token),
            timeout=25,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw":
                    response.text[:3000]
            }

        if (
            response.status_code == 401
            and retry_refresh
        ):

            print(
                "[ML] Token expirado."
            )

            new_token = (
                refresh_access_token()
            )

            if new_token:

                return ml_get(
                    path,
                    params=params,
                    retry_refresh=False
                )

        return {
            "ok":
                response.ok,

            "status":
                response.status_code,

            "data":
                data,

            "url":
                response.url,
        }

    except requests.RequestException as e:

        return {
            "ok":
                False,

            "status":
                0,

            "data": {
                "error":
                    "request_exception",

                "message":
                    str(e),
            },

            "url":
                url,
        }


# ============================================================
# OAUTH PKCE
# ============================================================

def make_code_verifier():

    return secrets.token_urlsafe(
        64
    )[:128]


def make_code_challenge(
    verifier
):

    digest = hashlib.sha256(
        verifier.encode(
            "utf-8"
        )
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).decode(
        "utf-8"
    ).rstrip("=")


@app.route(
    "/mercadolivre/login"
)
def ml_login():

    if not ML_CLIENT_ID:

        return """
        <h2>ML_CLIENT_ID não configurado</h2>
        <p>
            Configure as variáveis do Mercado Livre
            no Railway.
        </p>
        """, 500

    verifier = (
        make_code_verifier()
    )

    challenge = (
        make_code_challenge(
            verifier
        )
    )

    state = (
        secrets.token_urlsafe(
            32
        )
    )

    session[
        "ml_code_verifier"
    ] = verifier

    session[
        "ml_state"
    ] = state

    params = {
        "response_type":
            "code",

        "client_id":
            ML_CLIENT_ID,

        "redirect_uri":
            ML_REDIRECT_URI,

        "state":
            state,

        "scope":
            "read offline_access",

        "code_challenge":
            challenge,

        "code_challenge_method":
            "S256",
    }

    url = (
        ML_AUTH
        + "?"
        + urlencode(params)
    )

    return redirect(url)


# ============================================================
# CALLBACK
# ============================================================

@app.route(
    "/mercadolivre/callback"
)
def ml_callback():

    error = request.args.get(
        "error"
    )

    if error:

        return f"""
        <html>
        <body style="font-family:Arial;padding:30px">

            <h2>
                ❌ Mercado Livre recusou
                a autorização
            </h2>

            <p>
                <b>Erro:</b> {error}
            </p>

            <p>
                {request.args.get(
                    "error_description",
                    ""
                )}
            </p>

            <a href="/">
                Voltar
            </a>

        </body>
        </html>
        """

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = session.get(
        "ml_state"
    )

    verifier = session.get(
        "ml_code_verifier"
    )

    if not code:

        return (
            "Código OAuth não recebido.",
            400
        )

    if (
        not state
        or state != saved_state
    ):

        return (
            "State OAuth inválido.",
            400
        )

    if not verifier:

        return (
            "Code verifier não encontrado. "
            "Faça o login novamente.",
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
            timeout=25,
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "raw":
                    response.text
            }

        if response.status_code != 200:

            return f"""
            <html>
            <body style="font-family:Arial;padding:30px">

                <h2>
                    ❌ Erro ao obter token
                </h2>

                <pre>{
                    json.dumps(
                        data,
                        indent=2,
                        ensure_ascii=False
                    )
                }</pre>

                <a href="/">
                    Voltar
                </a>

            </body>
            </html>
            """, response.status_code

        access_token = data.get(
            "access_token"
        )

        if not access_token:

            return (
                "Mercado Livre não retornou "
                "access_token.",
                500
            )

        refresh_token = data.get(
            "refresh_token"
        )

        expires_in = data.get(
            "expires_in",
            21600
        )

        user_response = requests.get(
            ML_API + "/users/me",
            headers=ml_headers(
                access_token
            ),
            timeout=20,
        )

        user_data = {}

        try:
            user_data = (
                user_response.json()
            )
        except Exception:
            pass

        user_id = user_data.get(
            "id"
        )

        nickname = user_data.get(
            "nickname"
        )

        save_token(
            access_token=
                access_token,

            refresh_token=
                refresh_token,

            expires_in=
                expires_in,

            user_id=
                user_id,

            nickname=
                nickname,
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
            "/?connected=1"
        )

    except Exception as e:

        return f"""
        <html>
        <body style="font-family:Arial;padding:30px">

            <h2>
                ❌ Erro na conexão
            </h2>

            <pre>
                {e}
            </pre>

            <a href="/">
                Voltar
            </a>

        </body>
        </html>
        """, 500


# ============================================================
# RECONNECT
# ============================================================

@app.route(
    "/mercadolivre/reconnect"
)
def ml_reconnect():

    clear_auth()

    session.pop(
        "ml_state",
        None
    )

    session.pop(
        "ml_code_verifier",
        None
    )

    return redirect(
        "/mercadolivre/login"
    )


# ============================================================
# UTILITÁRIOS
# ============================================================

def safe_float(value):

    try:

        if value is None:
            return 0.0

        if isinstance(
            value,
            str
        ):

            value = (
                value
                .replace(
                    "R$",
                    ""
                )
                .replace(
                    " ",
                    ""
                )
                .replace(
                    ".",
                    ""
                )
                .replace(
                    ",",
                    "."
                )
            )

        return float(value)

    except Exception:

        return 0.0


def money(value):

    return (
        f"R$ {safe_float(value):,.2f}"
        .replace(
            ",",
            "X"
        )
        .replace(
            ".",
            ","
        )
        .replace(
            "X",
            "."
        )
    )


# ============================================================
# DIAGNÓSTICO
# ============================================================

def clean_diagnostic_data(
    data
):

    if not isinstance(
        data,
        dict
    ):
        return data

    result = dict(
        data
    )

    sensitive_keys = {
        "access_token",
        "refresh_token",
        "client_secret",
        "code",
        "code_verifier",
    }

    for key in list(
        result.keys()
    ):

        if key.lower() in sensitive_keys:

            result[key] = "***"

    return result


@app.route(
    "/api/ml-diagnostic"
)
def ml_diagnostic():

    auth = get_auth()

    result = {

        "generated_at":
            int(time.time()),

        "configuration": {

            "client_id_configured":
                bool(ML_CLIENT_ID),

            "client_secret_configured":
                bool(ML_CLIENT_SECRET),

            "redirect_uri":
                ML_REDIRECT_URI,

            "site_id":
                SITE_ID,

            "oauth_scope_requested":
                "read offline_access",
        },

        "token": {

            "connected":
                bool(
                    auth
                    and auth.get(
                        "access_token"
                    )
                ),

            "user_id":
                auth.get(
                    "user_id"
                )
                if auth
                else None,

            "nickname":
                auth.get(
                    "nickname"
                )
                if auth
                else None,

            "expires_at":
                auth.get(
                    "expires_at"
                )
                if auth
                else None,

            "has_refresh_token":
                bool(
                    auth
                    and auth.get(
                        "refresh_token"
                    )
                ),
        },

        "tests": {},

        "conclusion": [],
    }

    if not auth:

        result[
            "conclusion"
        ].append(
            "Nenhum token salvo."
        )

        return jsonify(
            result
        )

    # --------------------------------------------------------
    # USERS ME
    # --------------------------------------------------------

    me = ml_get(
        "/users/me"
    )

    result[
        "tests"
    ][
        "users_me"
    ] = {

        "status":
            me["status"],

        "ok":
            me["ok"],

        "data":
            clean_diagnostic_data(
                me.get(
                    "data"
                )
            ),
    }

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    if ML_CLIENT_ID:

        app_test = ml_get(
            f"/applications/{ML_CLIENT_ID}"
        )

        result[
            "tests"
        ][
            "application"
        ] = {

            "status":
                app_test[
                    "status"
                ],

            "ok":
                app_test[
                    "ok"
                ],

            "data":
                clean_diagnostic_data(
                    app_test.get(
                        "data"
                    )
                ),
        }

    # --------------------------------------------------------
    # PRODUCTS SEARCH
    # --------------------------------------------------------

    product_test = ml_get(
        "/products/search",
        params={
            "site_id":
                SITE_ID,

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
    ][
        "products_search"
    ] = {

        "status":
            product_test[
                "status"
            ],

        "ok":
            product_test[
                "ok"
            ],

        "data":
            clean_diagnostic_data(
                product_test.get(
                    "data"
                )
            ),
    }

    # --------------------------------------------------------
    # ENDPOINT ANTIGO
    # --------------------------------------------------------

    old_search = ml_get(
        "/sites/MLB/search",
        params={
            "q":
                "celular",

            "limit":
                1,
        }
    )

    result[
        "tests"
    ][
        "old_search"
    ] = {

        "status":
            old_search[
                "status"
            ],

        "ok":
            old_search[
                "ok"
            ],

        "data":
            clean_diagnostic_data(
                old_search.get(
                    "data"
                )
            ),
    }

    if old_search[
        "status"
    ] == 403:

        result[
            "conclusion"
        ].append(
            "O endpoint antigo /sites/MLB/search retorna 403. A caça atual não depende dele."
        )

    if product_test[
        "status"
    ] == 200:

        result[
            "conclusion"
        ].append(
            "O /products/search está funcionando."
        )

    elif product_test[
        "status"
    ] == 403:

        result[
            "conclusion"
        ].append(
            "O /products/search retornou 403."
        )

    return jsonify(
        result
    )


# ============================================================
# BUSCAR PRODUTOS
# ============================================================

def search_products(
    query,
    diagnostics=None
):

    if diagnostics is None:
        diagnostics = []

    response = ml_get(
        "/products/search",
        params={
            "site_id":
                SITE_ID,

            "q":
                query,

            "status":
                "active",

            "limit":
                PRODUCT_SEARCH_LIMIT,

            "offset":
                0,
        }
    )

    data = response.get(
        "data"
    )

    if response[
        "status"
    ] != 200:

        diagnostics.append({

            "type":
                "product_search",

            "query":
                query,

            "status":
                response[
                    "status"
                ],

            "count":
                0,

            "error":
                data,
        })

        return []

    results = []

    if isinstance(
        data,
        dict
    ):

        results = (
            data.get(
                "results"
            )
            or []
        )

    diagnostics.append({

        "type":
            "product_search",

        "query":
            query,

        "status":
            200,

        "count":
            len(results),
    })

    return results


# ============================================================
# DETALHE DO PRODUTO
# ============================================================

def get_product_detail(
    product_id,
    diagnostics=None
):

    response = ml_get(
        f"/products/{product_id}"
    )

    if diagnostics is not None:

        diagnostics.append({

            "type":
                "product_detail",

            "product_id":
                product_id,

            "status":
                response[
                    "status"
                ],
        })

    if not response[
        "ok"
    ]:

        return None

    data = response.get(
        "data"
    )

    if not isinstance(
        data,
        dict
    ):

        return None

    return data


# ============================================================
# ITENS DO PRODUTO
# ============================================================

def get_product_items(
    product_id,
    diagnostics=None
):

    response = ml_get(
        f"/products/{product_id}/items"
    )

    if diagnostics is not None:

        diagnostics.append({

            "type":
                "product_items",

            "product_id":
                product_id,

            "status":
                response[
                    "status"
                ],
        })

    if response[
        "status"
    ] == 404:

        # Não é erro fatal.
        # Alguns produtos usam somente
        # buy_box_winner no detalhe.
        print(
            "[ITEMS] 404:",
            product_id,
            "-> usando buy_box_winner"
        )

        return []

    if not response[
        "ok"
    ]:

        return []

    data = response.get(
        "data"
    )

    if not isinstance(
        data,
        dict
    ):

        return []

    items = (
        data.get(
            "results"
        )
        or data.get(
            "items"
        )
        or []
    )

    if not isinstance(
        items,
        list
    ):

        return []

    return items


# ============================================================
# PREÇO
# ============================================================

def get_item_price(
    item
):

    if not isinstance(
        item,
        dict
    ):
        return 0.0

    price = safe_float(
        item.get(
            "price"
        )
    )

    if price > 0:
        return price

    sale_price = item.get(
        "sale_price"
    )

    if isinstance(
        sale_price,
        dict
    ):

        price = safe_float(
            sale_price.get(
                "amount"
            )
        )

        if price > 0:
            return price

    return 0.0


# ============================================================
# ESCOLHER MELHOR ITEM
# ============================================================

def choose_best_item(
    items
):

    valid = []

    for item in items:

        if not isinstance(
            item,
            dict
        ):
            continue

        condition = (
            item.get(
                "condition"
            )
            or item.get(
                "item_condition"
            )
            or "new"
        )

        if condition != "new":
            continue

        item_id = (
            item.get(
                "item_id"
            )
            or item.get(
                "id"
            )
        )

        if not item_id:
            continue

        price = get_item_price(
            item
        )

        if price < MIN_PRODUCT_PRICE:
            continue

        shipping = (
            item.get(
                "shipping"
            )
            or {}
        )

        valid.append({

            "item_id":
                item_id,

            "price":
                price,

            "permalink":
                item.get(
                    "permalink"
                )
                or "",

            "seller_id":
                item.get(
                    "seller_id"
                ),

            "free_shipping":
                bool(
                    shipping.get(
                        "free_shipping"
                    )
                ),

            "shipping":
                shipping,

            "raw":
                item,
        })

    if not valid:
        return None

    valid.sort(
        key=lambda x: (
            x["price"],
            not x["free_shipping"]
        )
    )

    return valid[0]


# ============================================================
# BUY BOX WINNER
# ============================================================

def get_buy_box_winner(
    detail
):

    if not isinstance(
        detail,
        dict
    ):
        return None

    winner = detail.get(
        "buy_box_winner"
    )

    if not isinstance(
        winner,
        dict
    ):
        return None

    item_id = winner.get(
        "item_id"
    )

    price = safe_float(
        winner.get(
            "price"
        )
    )

    if not item_id:
        return None

    if price < MIN_PRODUCT_PRICE:
        return None

    shipping = (
        winner.get(
            "shipping"
        )
        or {}
    )

    return {

        "item_id":
            item_id,

        "price":
            price,

        "permalink":
            winner.get(
                "permalink"
            )
            or "",

        "seller_id":
            winner.get(
                "seller_id"
            ),

        "free_shipping":
            bool(
                winner.get(
                    "free_shipping"
                )
                or shipping.get(
                    "free_shipping"
                )
            ),

        "shipping":
            shipping,

        "raw":
            winner,
    }


# ============================================================
# LIMPAR HTML
# ============================================================

def clean_html_text(
    text
):

    if not text:
        return ""

    text = re.sub(
        r"<script.*?</script>",
        " ",
        text,
        flags=
            re.IGNORECASE
            | re.DOTALL
    )

    text = re.sub(
        r"<style.*?</style>",
        " ",
        text,
        flags=
            re.IGNORECASE
            | re.DOTALL
    )

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"&nbsp;",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"&amp;",
        "&",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# CUPOM
# ============================================================

def parse_coupon_text(
    text
):

    if not text:
        return None

    text = str(
        text
    )

    # --------------------------------------------------------
    # PERCENTUAL
    # --------------------------------------------------------

    percent_patterns = [

        r"""
        cupom
        .{0,180}?
        (\d+(?:[.,]\d+)?)
        \s*%
        \s*(?:OFF|desconto)
        """,

        r"""
        (\d+(?:[.,]\d+)?)
        \s*%
        \s*OFF
        """,

        r"""
        (\d+(?:[.,]\d+)?)
        \s*%
        \s*de\s+desconto
        """,
    ]

    for pattern in percent_patterns:

        match = re.search(
            pattern,
            text,
            flags=
                re.IGNORECASE
                | re.VERBOSE
        )

        if match:

            percent = safe_float(
                match.group(1)
            )

            if percent <= 0:
                continue

            around = text[
                max(
                    0,
                    match.start() - 250
                ):
                min(
                    len(text),
                    match.end() + 400
                )
            ]

            max_discount = None

            max_match = re.search(
                r"""
                (?:
                    máx(?:imo)?
                    |
                    limite
                    |
                    até
                )
                .{0,80}?
                R?\$?\s*
                (\d+(?:[.,]\d+)?)
                """,
                around,
                flags=
                    re.IGNORECASE
                    | re.VERBOSE
            )

            if max_match:

                max_discount = (
                    safe_float(
                        max_match.group(1)
                    )
                )

            min_price = None

            min_match = re.search(
                r"""
                (?:
                    mínimo
                    |
                    min
                    |
                    compras?\s+a\s+partir\s+de
                    |
                    acima\s+de
                )
                .{0,80}?
                R?\$?\s*
                (\d+(?:[.,]\d+)?)
                """,
                around,
                flags=
                    re.IGNORECASE
                    | re.VERBOSE
            )

            if min_match:

                min_price = (
                    safe_float(
                        min_match.group(1)
                    )
                )

            return {

                "type":
                    "percent",

                "value":
                    percent,

                "percent":
                    percent,

                "max_discount":
                    max_discount,

                "min_price":
                    min_price,

                "raw":
                    match.group(
                        0
                    ).strip(),

                "confirmed":
                    True,
            }

    # --------------------------------------------------------
    # VALOR FIXO
    # --------------------------------------------------------

    fixed_patterns = [

        r"""
        cupom
        .{0,180}?
        R\$\s*
        (\d+(?:[.,]\d+)?)
        \s*
        (?:OFF|de\s+desconto)
        """,

        r"""
        R\$\s*
        (\d+(?:[.,]\d+)?)
        \s*
        OFF
        """,

        r"""
        (\d+(?:[.,]\d+)?)
        \s*
        reais
        \s*
        OFF
        """,
    ]

    for pattern in fixed_patterns:

        match = re.search(
            pattern,
            text,
            flags=
                re.IGNORECASE
                | re.VERBOSE
        )

        if match:

            amount = safe_float(
                match.group(1)
            )

            if amount <= 0:
                continue

            return {

                "type":
                    "fixed",

                "value":
                    amount,

                "percent":
                    0,

                "max_discount":
                    None,

                "min_price":
                    None,

                "raw":
                    match.group(
                        0
                    ).strip(),

                "confirmed":
                    True,
            }

    return None


# ============================================================
# CUPOM NA PÁGINA
# ============================================================

def get_coupon_from_product_page(
    url
):

    if not url:
        return None

    try:

        response = requests.get(
            url,
            headers={
                "User-Agent":
                    (
                        "Mozilla/5.0 "
                        "(iPhone; CPU iPhone OS 17_0 "
                        "like Mac OS X) "
                        "AppleWebKit/605.1.15 "
                        "Version/17.0 "
                        "Mobile/15E148 "
                        "Safari/604.1"
                    ),

                "Accept-Language":
                    "pt-BR,pt;q=0.9",
            },
            timeout=12,
            allow_redirects=True,
        )

        if response.status_code != 200:

            print(
                "[CUPOM PAGE]",
                response.status_code,
                url[:100]
            )

            return None

        html = response.text

        # ----------------------------------------------------
        # PRIMEIRO:
        # texto visível
        # ----------------------------------------------------

        visible = clean_html_text(
            html
        )

        # Procuramos vários contextos
        # ao redor da palavra cupom.

        for match in re.finditer(
            r"cupom",
            visible,
            flags=re.IGNORECASE
        ):

            start = max(
                0,
                match.start() - 150
            )

            end = min(
                len(visible),
                match.end() + 700
            )

            context = visible[
                start:end
            ]

            coupon = (
                parse_coupon_text(
                    context
                )
            )

            if coupon:

                coupon[
                    "source"
                ] = "pagina_produto"

                return coupon

        # ----------------------------------------------------
        # SEGUNDO:
        # HTML bruto
        #
        # Algumas informações de ofertas
        # podem estar em estruturas JSON
        # da página.
        # ----------------------------------------------------

        lower_html = html.lower()

        positions = []

        pos = 0

        while True:

            pos = lower_html.find(
                "cupom",
                pos
            )

            if pos == -1:
                break

            positions.append(
                pos
            )

            pos += 5

            if len(positions) >= 20:
                break

        for pos in positions:

            start = max(
                0,
                pos - 300
            )

            end = min(
                len(html),
                pos + 1200
            )

            context = html[
                start:end
            ]

            context = re.sub(
                r"\\u00a0",
                " ",
                context
            )

            context = re.sub(
                r"\\/",
                "/",
                context
            )

            context = re.sub(
                r"\\u([0-9a-fA-F]{4})",
                lambda m: chr(
                    int(
                        m.group(1),
                        16
                    )
                ),
                context
            )

            context = clean_html_text(
                context
            )

            coupon = (
                parse_coupon_text(
                    context
                )
            )

            if coupon:

                coupon[
                    "source"
                ] = "pagina_produto"

                return coupon

        return None

    except Exception as e:

        print(
            "[CUPOM ERRO]",
            repr(e)
        )

        return None


# ============================================================
# CALCULAR CUPOM
# ============================================================

def calculate_coupon(
    price,
    coupon
):

    price = safe_float(
        price
    )

    if price <= 0:
        return None

    if not coupon:
        return None

    min_price = safe_float(
        coupon.get(
            "min_price"
        )
    )

    if (
        min_price > 0
        and price < min_price
    ):
        return None

    coupon_type = coupon.get(
        "type"
    )

    discount = 0.0

    if coupon_type == "percent":

        percent = safe_float(
            coupon.get(
                "percent"
            )
        )

        discount = (
            price
            * percent
            / 100
        )

        max_discount = safe_float(
            coupon.get(
                "max_discount"
            )
        )

        if max_discount > 0:

            discount = min(
                discount,
                max_discount
            )

    elif coupon_type == "fixed":

        discount = safe_float(
            coupon.get(
                "value"
            )
        )

    if discount <= 0:
        return None

    discount = min(
        discount,
        price
    )

    final_price = (
        price - discount
    )

    effective_percent = (
        discount
        / price
        * 100
    )

    result = dict(
        coupon
    )

    result.update({

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
            round(
                effective_percent,
                2
            ),
    })

    return result


# ============================================================
# ANALISAR PRODUTO
# ============================================================

def analyze_catalog_product(
    product,
    diagnostics
):

    product_id = (
        product.get(
            "id"
        )
        or product.get(
            "product_id"
        )
    )

    if not product_id:
        return None

    # --------------------------------------------------------
    # DETALHE
    # --------------------------------------------------------

    detail = get_product_detail(
        product_id,
        diagnostics
    )

    if not detail:
        return None

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if detail.get(
        "status"
    ) not in (
        None,
        "active"
    ):
        return None

    title = (
        detail.get(
            "name"
        )
        or detail.get(
            "title"
        )
        or product.get(
            "name"
        )
        or "Produto"
    )

    # --------------------------------------------------------
    # PRIMEIRA TENTATIVA:
    # BUY BOX WINNER
    # --------------------------------------------------------

    best_item = (
        get_buy_box_winner(
            detail
        )
    )

    # --------------------------------------------------------
    # SEGUNDA TENTATIVA:
    # ITEMS
    # --------------------------------------------------------

    if not best_item:

        items = get_product_items(
            product_id,
            diagnostics
        )

        best_item = (
            choose_best_item(
                items
            )
        )

    if not best_item:

        return None

    price = safe_float(
        best_item.get(
            "price"
        )
    )

    if price < MIN_PRODUCT_PRICE:

        return None

    # --------------------------------------------------------
    # PERMALINK
    # --------------------------------------------------------

    product_permalink = (
        detail.get(
            "permalink"
        )
        or ""
    )

    item_permalink = (
        best_item.get(
            "permalink"
        )
        or ""
    )

    # Para cupom preferimos a página
    # de catálogo, quando existir.
    coupon_url = (
        product_permalink
        or item_permalink
    )

    # --------------------------------------------------------
    # IMAGEM
    # --------------------------------------------------------

    thumbnail = ""

    pictures = (
        detail.get(
            "pictures"
        )
        or []
    )

    if pictures:

        first_picture = (
            pictures[0]
        )

        if isinstance(
            first_picture,
            dict
        ):

            thumbnail = (
                first_picture.get(
                    "url"
                )
                or first_picture.get(
                    "secure_url"
                )
                or ""
            )

    if not thumbnail:

        thumbnail = (
            product.get(
                "thumbnail"
            )
            or ""
        )

    # --------------------------------------------------------
    # CUPOM
    # --------------------------------------------------------

    coupon_raw = (
        get_coupon_from_product_page(
            coupon_url
        )
    )

    if not coupon_raw:

        # Se a página de catálogo não
        # trouxe cupom, tentamos a página
        # do item.
        if (
            item_permalink
            and item_permalink != coupon_url
        ):

            coupon_raw = (
                get_coupon_from_product_page(
                    item_permalink
                )
            )

    if not coupon_raw:

        return None

    # --------------------------------------------------------
    # CALCULAR
    # --------------------------------------------------------

    coupon_result = (
        calculate_coupon(
            price,
            coupon_raw
        )
    )

    if not coupon_result:

        return None

    discount = safe_float(
        coupon_result.get(
            "discount"
        )
    )

    final_price = safe_float(
        coupon_result.get(
            "final_price"
        )
    )

    if discount <= 0:
        return None

    # --------------------------------------------------------
    # RESULTADO
    # --------------------------------------------------------

    return {

        "product_id":
            product_id,

        "item_id":
            best_item.get(
                "item_id"
            ),

        "title":
            title,

        "thumbnail":
            thumbnail,

        "price":
            round(
                price,
                2
            ),

        "coupon":
            coupon_result,

        "coupon_discount":
            round(
                discount,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "effective_discount":
            round(
                discount
                / price
                * 100,
                2
            ),

        "permalink":
            item_permalink
            or product_permalink,

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

        "coupon_status":
            "confirmado_na_pagina",

        "note":
            "Valor calculado com base no cupom encontrado na página. Confirme a aplicação no checkout.",
    }


# ============================================================
# CAÇAR
# ============================================================

@app.route(
    "/api/hunt"
)
def api_hunt():

    auth = get_auth()

    if (
        not auth
        or not auth.get(
            "access_token"
        )
    ):

        return jsonify({

            "ok":
                False,

            "error":
                "Mercado Livre não conectado.",

            "login_url":
                "/mercadolivre/login",
        }), 401

    diagnostics = []

    products = []

    seen_products = set()

    # --------------------------------------------------------
    # BUSCAR CATÁLOGO
    # --------------------------------------------------------

    for query in CATEGORIES:

        if len(products) >= MAX_PRODUCTS:
            break

        results = search_products(
            query,
            diagnostics
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

            if product_id in seen_products:
                continue

            seen_products.add(
                product_id
            )

            products.append(
                product
            )

            if len(products) >= MAX_PRODUCTS:
                break

    # --------------------------------------------------------
    # NENHUM PRODUTO
    # --------------------------------------------------------

    if not products:

        has_403 = any(
            d.get(
                "status"
            ) == 403
            for d in diagnostics
        )

        message = (
            "O Mercado Livre retornou HTTP 403 para /products/search."
            if has_403
            else
            "Nenhum produto foi retornado."
        )

        return jsonify({

            "ok":
                False,

            "message":
                message,

            "stats": {

                "queries":
                    len(CATEGORIES),

                "returned":
                    0,

                "unique":
                    0,

                "analyzed":
                    0,

                "opportunities":
                    0,

                "coupons":
                    0,

                "value":
                    0,

                "final_value":
                    0,
            },

            "diagnostics":
                diagnostics,
        })

    # --------------------------------------------------------
    # ANALISAR
    # --------------------------------------------------------

    offers = []

    analyzed = 0

    for product in products:

        analyzed += 1

        try:

            offer = (
                analyze_catalog_product(
                    product,
                    diagnostics
                )
            )

            if not offer:
                continue

            if (
                offer["price"]
                < MIN_PRODUCT_PRICE
            ):
                continue

            offers.append(
                offer
            )

        except Exception as e:

            print(
                "[ERRO PRODUTO]",
                product.get(
                    "id"
                ),
                repr(e)
            )

    # --------------------------------------------------------
    # UMA OFERTA POR PRODUTO
    # --------------------------------------------------------

    unique_offers = {}

    for offer in offers:

        product_id = offer.get(
            "product_id"
        )

        if not product_id:
            continue

        current = (
            unique_offers.get(
                product_id
            )
        )

        if not current:

            unique_offers[
                product_id
            ] = offer

            continue

        new_discount = safe_float(
            offer.get(
                "coupon_discount"
            )
        )

        old_discount = safe_float(
            current.get(
                "coupon_discount"
            )
        )

        if new_discount > old_discount:

            unique_offers[
                product_id
            ] = offer

        elif (
            new_discount == old_discount
            and safe_float(
                offer.get(
                    "final_price"
                )
            )
            <
            safe_float(
                current.get(
                    "final_price"
                )
            )
        ):

            unique_offers[
                product_id
            ] = offer

    offers = list(
        unique_offers.values()
    )

    # --------------------------------------------------------
    # ORDENAR
    # --------------------------------------------------------

    offers.sort(
        key=lambda x: (
            safe_float(
                x.get(
                    "coupon_discount"
                )
            ),
            safe_float(
                x.get(
                    "effective_discount"
                )
            ),
            -safe_float(
                x.get(
                    "final_price"
                )
            ),
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # ESTATÍSTICAS
    # --------------------------------------------------------

    total_discount = sum(
        safe_float(
            x.get(
                "coupon_discount"
            )
        )
        for x in offers
    )

    total_final = sum(
        safe_float(
            x.get(
                "final_price"
            )
        )
        for x in offers
    )

    return jsonify({

        "ok":
            True,

        "message":
            (
                f"Caça finalizada — "
                f"{len(offers)} produtos "
                f"com cupom encontrados."
            ),

        "stats": {

            "queries":
                len(CATEGORIES),

            "returned":
                len(products),

            "unique":
                len(seen_products),

            "analyzed":
                analyzed,

            "opportunities":
                len(offers),

            "coupons":
                len(offers),

            "value":
                round(
                    total_discount,
                    2
                ),

            "final_value":
                round(
                    total_final,
                    2
                ),
        },

        "diagnostics":
            diagnostics,

        "offers":
            offers,
    })


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/api/status"
)
def api_status():

    auth = get_auth()

    return jsonify({

        "connected":
            bool(
                auth
                and auth.get(
                    "access_token"
                )
            ),

        "user_id":
            auth.get(
                "user_id"
            )
            if auth
            else None,

        "nickname":
            auth.get(
                "nickname"
            )
            if auth
            else None,

        "expires_at":
            auth.get(
                "expires_at"
            )
            if auth
            else None,
    })


# ============================================================
# HOME
# ============================================================

HTML = r"""
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
    background: #f3f4f6;
    color: #111827;
    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {
    width: 94%;
    max-width: 1100px;
    margin: auto;
    padding: 20px 0 60px;
}

.header {
    background: #ffe600;
    padding: 22px;
    border-radius: 18px;
    margin-bottom: 18px;
}

.header h1 {
    margin: 0 0 7px;
    font-size: 28px;
}

.header p {
    margin: 0;
    color: #333;
}

.card {
    background: white;
    border-radius: 18px;
    padding: 18px;
    margin-bottom: 18px;
    box-shadow:
        0 4px 18px
        rgba(0,0,0,.06);
}

.status {
    padding: 13px;
    border-radius: 12px;
    background: #f3f4f6;
    margin-bottom: 15px;
}

.connected {
    background: #dcfce7;
    color: #166534;
}

.disconnected {
    background: #fee2e2;
    color: #991b1b;
}

.buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
}

button,
.button {
    display: inline-block;
    border: 0;
    border-radius: 12px;
    padding: 13px 17px;
    font-size: 15px;
    font-weight: bold;
    cursor: pointer;
    text-decoration: none;
    color: white;
    background: #111827;
}

.primary {
    background: #3483fa !important;
}

button:disabled {
    opacity: .5;
}

.progress {
    width: 100%;
    height: 9px;
    background: #e5e7eb;
    border-radius: 20px;
    overflow: hidden;
    margin: 15px 0;
}

.progress-bar {
    height: 100%;
    width: 0;
    background: #3483fa;
    transition: width .3s;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(4, 1fr);
    gap: 10px;
}

.stat {
    background: #f9fafb;
    border-radius: 14px;
    padding: 14px;
}

.stat strong {
    display: block;
    font-size: 23px;
    margin-top: 5px;
}

.offers {
    display: grid;
    grid-template-columns:
        repeat(3, 1fr);
    gap: 15px;
}

.offer {
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    padding: 13px;
    background: white;
}

.offer img {
    width: 100%;
    height: 190px;
    object-fit: contain;
    border-radius: 10px;
    background: #f8fafc;
}

.offer h3 {
    font-size: 15px;
    line-height: 1.35;
    min-height: 42px;
}

.before {
    color: #6b7280;
    font-size: 14px;
    text-decoration: line-through;
}

.price-final {
    color: #111827;
    font-size: 25px;
    font-weight: bold;
    margin-top: 4px;
}

.coupon-box {
    background: #fef3c7;
    color: #92400e;
    border-radius: 10px;
    padding: 10px;
    margin: 10px 0;
}

.coupon-title {
    font-weight: bold;
}

.saving {
    color: #166534;
    font-weight: bold;
    margin-top: 6px;
}

.confirmed {
    color: #166534;
    font-size: 12px;
    margin-top: 7px;
}

.shipping {
    font-size: 13px;
    margin: 8px 0;
}

.notice {
    padding: 12px;
    border-radius: 12px;
    background: #fff7ed;
    color: #9a3412;
    margin-top: 12px;
    font-size: 13px;
}

.diagnostic {
    background: #111827;
    color: #e5e7eb;
    border-radius: 14px;
    padding: 14px;
    overflow: auto;
    font-size: 11px;
    white-space: pre-wrap;
}

@media(max-width:800px) {

    .offers {
        grid-template-columns:
            1fr 1fr;
    }

    .stats {
        grid-template-columns:
            1fr 1fr;
    }

}

@media(max-width:520px) {

    .offers {
        grid-template-columns: 1fr;
    }

    .container {
        width: 92%;
    }

}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <h1>
            🤑 Caçador de Ofertas
        </h1>

        <p>
            Produtos acima de R$ 69,90
            com cupom encontrado.
        </p>

    </div>


    <div class="card">

        <div
            id="status"
            class="status"
        >
            Verificando conexão...
        </div>

        <div class="buttons">

            <a
                class="button primary"
                href="/mercadolivre/login"
            >
                🔗 Conectar Mercado Livre
            </a>

            <a
                class="button"
                href="/mercadolivre/reconnect"
            >
                🔄 Reconectar
            </a>

            <button
                class="primary"
                id="huntBtn"
                onclick="hunt()"
            >
                🔎 CAÇAR OFERTAS
            </button>

        </div>

    </div>


    <div class="card">

        <h2>
            Caça de ofertas
        </h2>

        <div id="message">
            Pronto para começar.
        </div>

        <div class="progress">

            <div
                id="progressBar"
                class="progress-bar"
            ></div>

        </div>

        <div class="stats">

            <div class="stat">
                Ofertas
                <strong id="opportunities">
                    0
                </strong>
            </div>

            <div class="stat">
                Cupom aplicável
                <strong id="coupons">
                    0
                </strong>
            </div>

            <div class="stat">
                Desconto
                <strong id="value">
                    R$ 0,00
                </strong>
            </div>

            <div class="stat">
                Valor final
                <strong id="finalValue">
                    R$ 0,00
                </strong>
            </div>

        </div>

    </div>


    <div class="card">

        <h2>
            Ofertas encontradas
        </h2>

        <div
            id="offers"
            class="offers"
        >
            Nenhuma oferta ainda.
        </div>

    </div>


    <div class="card">

        <h2>
            Diagnóstico
        </h2>

        <div
            id="diagnostic"
            class="diagnostic"
        >
Aguardando...
        </div>

    </div>

</div>


<script>

function money(value) {

    return Number(
        value || 0
    ).toLocaleString(
        "pt-BR",
        {
            style: "currency",
            currency: "BRL"
        }
    );

}


function escapeHtml(text) {

    return String(
        text || ""
    )
    .replaceAll(
        "&",
        "&amp;"
    )
    .replaceAll(
        "<",
        "&lt;"
    )
    .replaceAll(
        ">",
        "&gt;"
    )
    .replaceAll(
        '"',
        "&quot;"
    )
    .replaceAll(
        "'",
        "&#039;"
    );

}


async function loadStatus() {

    try {

        const response =
            await fetch(
                "/api/status"
            );

        const data =
            await response.json();

        const status =
            document.getElementById(
                "status"
            );

        if (data.connected) {

            status.className =
                "status connected";

            status.innerHTML =
                "🟢 Mercado Livre conectado"
                +
                (
                    data.nickname
                    ? " — "
                      + escapeHtml(
                          data.nickname
                      )
                    : ""
                );

        } else {

            status.className =
                "status disconnected";

            status.innerHTML =
                "🔴 Mercado Livre não conectado";

        }

    } catch (error) {

        document.getElementById(
            "status"
        ).innerHTML =
            "Erro verificando conexão.";

    }

}


async function loadDiagnostic() {

    try {

        const response =
            await fetch(
                "/api/ml-diagnostic"
            );

        const data =
            await response.json();

        document.getElementById(
            "diagnostic"
        ).textContent =
            JSON.stringify(
                data,
                null,
                2
            );

    } catch (error) {

        document.getElementById(
            "diagnostic"
        ).textContent =
            "Erro: "
            + error;

    }

}


async function hunt() {

    const btn =
        document.getElementById(
            "huntBtn"
        );

    const message =
        document.getElementById(
            "message"
        );

    const progress =
        document.getElementById(
            "progressBar"
        );

    btn.disabled = true;

    progress.style.width =
        "10%";

    message.innerText =
        "🔎 Buscando produtos...";

    try {

        progress.style.width =
            "30%";

        const response =
            await fetch(
                "/api/hunt"
            );

        progress.style.width =
            "70%";

        const data =
            await response.json();

        progress.style.width =
            "100%";

        if (!data.ok) {

            message.innerText =
                "❌ "
                +
                (
                    data.message
                    || data.error
                    || "Erro"
                );

        } else {

            message.innerText =
                "✅ "
                +
                data.message;

        }

        const stats =
            data.stats || {};

        document.getElementById(
            "opportunities"
        ).innerText =
            stats.opportunities || 0;

        document.getElementById(
            "coupons"
        ).innerText =
            stats.coupons || 0;

        document.getElementById(
            "value"
        ).innerText =
            money(
                stats.value || 0
            );

        document.getElementById(
            "finalValue"
        ).innerText =
            money(
                stats.final_value || 0
            );

        renderOffers(
            data.offers || []
        );

        document.getElementById(
            "diagnostic"
        ).textContent =
            JSON.stringify(
                data.diagnostics
                || data,
                null,
                2
            );

    } catch (error) {

        message.innerText =
            "❌ Erro: "
            + error;

        document.getElementById(
            "diagnostic"
        ).textContent =
            String(error);

    } finally {

        btn.disabled = false;

        setTimeout(
            () => {
                progress.style.width =
                    "0%";
            },
            1000
        );

    }

}


function renderOffers(
    offers
) {

    const container =
        document.getElementById(
            "offers"
        );

    if (!offers.length) {

        container.innerHTML = `
            <div class="notice">
                Nenhum produto com cupom
                encontrado nesta busca.
            </div>
        `;

        return;
    }

    container.innerHTML =
        offers.map(
            offer => {

                const coupon =
                    offer.coupon
                    || {};

                const image =
                    offer.thumbnail
                    ? `
                        <img
                            src="${escapeHtml(
                                offer.thumbnail
                            )}"
                            loading="lazy"
                        >
                    `
                    : "";

                const code =
                    coupon.raw
                    || "Cupom encontrado";

                const saving =
                    Number(
                        offer.coupon_discount
                        || 0
                    );

                const finalPrice =
                    Number(
                        offer.final_price
                        || 0
                    );

                const beforePrice =
                    Number(
                        offer.price
                        || 0
                    );

                const percent =
                    Number(
                        offer.effective_discount
                        || 0
                    );

                const shipping =
                    offer.free_shipping
                    ? `
                        <div class="shipping">
                            🚚 Frete grátis
                        </div>
                    `
                    : "";

                return `
                    <div class="offer">

                        ${image}

                        <h3>
                            ${escapeHtml(
                                offer.title
                            )}
                        </h3>

                        <div class="before">
                            Antes do cupom:
                            ${money(
                                beforePrice
                            )}
                        </div>

                        <div class="price-final">
                            ${money(
                                finalPrice
                            )}
                        </div>

                        <div class="coupon-box">

                            <div class="coupon-title">
                                🎟️
                                ${escapeHtml(
                                    code
                                )}
                            </div>

                            <div class="saving">
                                Economia:
                                ${money(
                                    saving
                                )}
                                ${
                                    percent > 0
                                    ? " (" +
                                      percent +
                                      "%)"
                                    : ""
                                }
                            </div>

                            <div class="confirmed">
                                🟢 Cupom encontrado
                                na página
                            </div>

                        </div>

                        ${shipping}

                        <div class="notice">
                            Valor final estimado.
                            Confirme o cupom no
                            checkout.
                        </div>

                        <br>

                        <a
                            class="button primary"
                            target="_blank"
                            rel="noopener"
                            href="${escapeHtml(
                                offer.permalink
                                || "#"
                            )}"
                        >
                            🛒 Ver produto
                        </a>

                    </div>
                `;

            }
        ).join("");

}


loadStatus();

loadDiagnostic();

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

@app.route("/health")
def health():

    return jsonify({
        "status":
            "ok"
    })


# ============================================================
# MAIN
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
        port=port,
        debug=False
    )