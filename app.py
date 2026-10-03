import os
import re
import time
import json
import sqlite3
import secrets
import hashlib
import base64
import html
import unicodedata

import requests

from urllib.parse import (
    urlencode,
    quote,
    unquote,
)

from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    jsonify,
)

# ============================================================
# PLAYWRIGHT
# ============================================================

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except Exception as e:
    sync_playwright = None
    PLAYWRIGHT_OK = False
    PLAYWRIGHT_ERROR = str(e)


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

DB_PATH = "ofertas.db"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br"

SITE_ID = "MLB"

CLIENT_ID = os.getenv(
    "ML_CLIENT_ID",
    ""
).strip()

CLIENT_SECRET = os.getenv(
    "ML_CLIENT_SECRET",
    ""
).strip()

REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

PORT = int(
    os.getenv(
        "PORT",
        "8080"
    )
)

HTTP_TIMEOUT = 25

MAX_IDS = 80

MAX_ITEMS = 50


# ============================================================
# NAVEGADOR
# ============================================================

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
    "AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) "
    "Version/17.6 Mobile/15E148 Safari/604.1"
)


# ============================================================
# BANCO
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS oauth (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT UNIQUE,
            titulo TEXT,
            preco_atual REAL,
            preco_original REAL,
            desconto REAL,
            permalink TEXT,
            imagem TEXT,
            criado_em INTEGER
        )
        """
    )

    conn.commit()

    conn.close()


init_db()


# ============================================================
# CONFIG
# ============================================================

def salvar_config(
    chave,
    valor
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO config(
            chave,
            valor
        )
        VALUES (?, ?)

        ON CONFLICT(chave)
        DO UPDATE SET
            valor = excluded.valor
        """,
        (
            chave,
            valor,
        )
    )

    conn.commit()

    conn.close()


def ler_config(
    chave,
    default=None
):

    conn = get_db()

    row = conn.execute(
        """
        SELECT valor
        FROM config
        WHERE chave = ?
        """,
        (
            chave,
        )
    ).fetchone()

    conn.close()

    if row:

        return row["valor"]

    return default


# ============================================================
# OAUTH
# ============================================================

def salvar_oauth(data):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO oauth(
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
                excluded.refresh_token,

            expires_at =
                excluded.expires_at,

            user_id =
                excluded.user_id,

            nickname =
                excluded.nickname
        """,
        (
            data.get(
                "access_token"
            ),

            data.get(
                "refresh_token"
            ),

            data.get(
                "expires_at"
            ),

            data.get(
                "user_id"
            ),

            data.get(
                "nickname"
            ),
        )
    )

    conn.commit()

    conn.close()


def carregar_oauth():

    conn = get_db()

    row = conn.execute(
        """
        SELECT *
        FROM oauth
        WHERE id = 1
        """
    ).fetchone()

    conn.close()

    return row


def gerar_code_verifier():

    return secrets.token_urlsafe(
        64
    )[:128]


def gerar_code_challenge(
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


def token_valido():

    row = carregar_oauth()

    if not row:
        return False

    if not row["access_token"]:
        return False

    expires_at = (
        row["expires_at"]
        or 0
    )

    return (
        int(time.time())
        <
        expires_at - 120
    )


def refresh_access_token():

    row = carregar_oauth()

    if not row:
        return False

    refresh_token = (
        row["refresh_token"]
    )

    if not refresh_token:
        return False

    payload = {

        "grant_type":
            "refresh_token",

        "client_id":
            CLIENT_ID,

        "client_secret":
            CLIENT_SECRET,

        "refresh_token":
            refresh_token,
    }

    try:

        response = requests.post(
            f"{ML_API}/oauth/token",
            data=payload,
            timeout=HTTP_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                "[REFRESH ERRO]",
                response.status_code,
                response.text[:1000],
            )

            return False

        data = response.json()

        access_token = data.get(
            "access_token"
        )

        novo_refresh = data.get(
            "refresh_token",
            refresh_token,
        )

        expires_in = int(
            data.get(
                "expires_in",
                21600
            )
        )

        user_id = row["user_id"]

        nickname = row["nickname"]

        try:

            me = requests.get(
                f"{ML_API}/users/me",

                headers={
                    "Authorization":
                        f"Bearer {access_token}"
                },

                timeout=HTTP_TIMEOUT,
            )

            if me.status_code == 200:

                user = me.json()

                user_id = str(
                    user.get(
                        "id",
                        user_id
                    )
                )

                nickname = user.get(
                    "nickname",
                    nickname
                )

        except Exception:
            pass

        salvar_oauth({

            "access_token":
                access_token,

            "refresh_token":
                novo_refresh,

            "expires_at":
                int(time.time())
                + expires_in,

            "user_id":
                user_id,

            "nickname":
                nickname,
        })

        return True

    except Exception as e:

        print(
            "[REFRESH EXCEPTION]",
            e
        )

        return False


def obter_access_token():

    row = carregar_oauth()

    if not row:
        return None

    if token_valido():

        return row[
            "access_token"
        ]

    if refresh_access_token():

        row = carregar_oauth()

        if row:

            return row[
                "access_token"
            ]

    return None


# ============================================================
# API MERCADO LIVRE
# ============================================================

def ml_get(
    path,
    params=None
):

    token = obter_access_token()

    if not token:

        return None, 401

    headers = {

        "Authorization":
            f"Bearer {token}",

        "Accept":
            "application/json",
    }

    try:

        response = requests.get(

            f"{ML_API}{path}",

            headers=headers,

            params=params,

            timeout=HTTP_TIMEOUT,
        )

        if response.status_code == 401:

            if refresh_access_token():

                token = (
                    obter_access_token()
                )

                if token:

                    headers[
                        "Authorization"
                    ] = (
                        f"Bearer {token}"
                    )

                    response = requests.get(

                        f"{ML_API}{path}",

                        headers=headers,

                        params=params,

                        timeout=HTTP_TIMEOUT,
                    )

        try:

            data = response.json()

        except Exception:

            data = {
                "raw":
                    response.text
            }

        return (
            data,
            response.status_code
        )

    except Exception as e:

        return (
            {
                "error":
                    str(e)
            },
            500
        )


# ============================================================
# HELPERS
# ============================================================

def slugify(
    texto
):

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = texto.encode(
        "ascii",
        "ignore"
    ).decode(
        "ascii"
    )

    texto = texto.lower()

    texto = re.sub(
        r"[^a-z0-9\s-]",
        "",
        texto
    )

    texto = re.sub(
        r"\s+",
        "-",
        texto
    )

    texto = re.sub(
        r"-+",
        "-",
        texto
    )

    return texto.strip("-")


def numero(
    valor
):

    if valor is None:

        return None

    try:

        return float(
            valor
        )

    except Exception:

        return None


def calcular_desconto(
    original,
    atual
):

    original = numero(
        original
    )

    atual = numero(
        atual
    )

    if not original:
        return 0

    if not atual:
        return 0

    if original <= atual:
        return 0

    return round(
        (
            (
                original - atual
            )
            /
            original
        )
        * 100,
        2
    )


def moeda(
    valor
):

    valor = numero(
        valor
    )

    if valor is None:

        return "-"

    return (
        "R$ "
        +
        f"{valor:,.2f}"
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
# MLB
# ============================================================

def normalizar_mlb(
    valor
):

    if not valor:
        return None

    valor = unquote(
        str(valor)
    )

    valor = html.unescape(
        valor
    )

    valor = valor.upper()

    valor = valor.replace(
        "MLB-",
        "MLB"
    )

    valor = valor.replace(
        "MLB_",
        "MLB"
    )

    match = re.search(
        r"MLB\d{6,12}",
        valor
    )

    if match:

        return match.group(
            0
        )

    return None


def extrair_ids_mlb(
    texto
):

    ids = []

    if not texto:

        return ids

    texto = html.unescape(
        texto
    )

    texto = unquote(
        texto
    )

    padroes = [

        r"MLB-\d{6,12}",

        r"MLB\d{6,12}",

        r"/MLB-\d{6,12}",

        r"/MLB\d{6,12}",

        r"item[_-]?id.{0,200}?MLB[-_]?\d{6,12}",

        r"itemId.{0,200}?MLB[-_]?\d{6,12}",

        r"listing[_-]?item.{0,200}?MLB[-_]?\d{6,12}",

        r"catalog_product_id.{0,200}?MLB\d{6,12}",
    ]

    for padrao in padroes:

        encontrados = re.findall(
            padrao,
            texto,
            flags=re.IGNORECASE
        )

        for encontrado in encontrados:

            item_id = normalizar_mlb(
                encontrado
            )

            if not item_id:
                continue

            if item_id not in ids:

                ids.append(
                    item_id
                )

    return ids


# ============================================================
# PLAYWRIGHT - BUSCA REAL
# ============================================================

def buscar_com_playwright(
    consulta
):

    diagnostico = {

        "playwright_instalado":
            PLAYWRIGHT_OK,

        "browser":
            "chromium",

        "url":
            None,

        "status":
            None,

        "titulo":
            None,

        "url_final":
            None,

        "html_bytes":
            0,

        "texto_bytes":
            0,

        "links":
            0,

        "ids_html":
            0,

        "ids_links":
            0,

        "ids_texto":
            0,

        "ids_json":
            0,

        "ids_finais":
            0,

        "erro":
            None,
    }

    if not PLAYWRIGHT_OK:

        diagnostico[
            "erro"
        ] = (
            "Playwright não está instalado: "
            +
            PLAYWRIGHT_ERROR
        )

        return [], diagnostico

    slug = slugify(
        consulta
    )

    url = (
        "https://lista.mercadolivre.com.br/"
        +
        quote(slug)
    )

    diagnostico[
        "url"
    ] = url

    ids = []

    browser = None

    try:

        with sync_playwright() as p:

            browser = p.chromium.launch(

                headless=True,

                args=[

                    "--no-sandbox",

                    "--disable-setuid-sandbox",

                    "--disable-dev-shm-usage",

                    "--disable-gpu",

                    "--no-zygote",

                    "--single-process",
                ]
            )

            context = browser.new_context(

                user_agent=
                    BROWSER_USER_AGENT,

                viewport={
                    "width": 390,
                    "height": 844,
                },

                locale="pt-BR",

                timezone_id=
                    "America/Sao_Paulo",

                java_script_enabled=True,

                ignore_https_errors=True,
            )

            page = context.new_page()

            page.set_default_timeout(
                15000
            )

            response = page.goto(

                url,

                wait_until=
                    "domcontentloaded",

                timeout=30000,
            )

            if response:

                diagnostico[
                    "status"
                ] = response.status

            diagnostico[
                "url_final"
            ] = page.url

            try:

                page.wait_for_load_state(
                    "networkidle",
                    timeout=12000
                )

            except Exception:

                pass

            # ------------------------------------------------
            # Rola para carregar lazy loading.
            # ------------------------------------------------

            for _ in range(5):

                try:

                    page.mouse.wheel(
                        0,
                        1800
                    )

                    page.wait_for_timeout(
                        800
                    )

                except Exception:

                    break

            # ------------------------------------------------
            # HTML completo
            # ------------------------------------------------

            try:

                html_page = page.content()

            except Exception:

                html_page = ""

            diagnostico[
                "html_bytes"
            ] = len(
                html_page
            )

            ids_html = extrair_ids_mlb(
                html_page
            )

            diagnostico[
                "ids_html"
            ] = len(
                ids_html
            )

            for item_id in ids_html:

                if item_id not in ids:

                    ids.append(
                        item_id
                    )

            # ------------------------------------------------
            # Título
            # ------------------------------------------------

            try:

                diagnostico[
                    "titulo"
                ] = page.title()

            except Exception:

                pass

            # ------------------------------------------------
            # LINKS REAIS
            # ------------------------------------------------

            try:

                links = page.locator(
                    "a"
                ).evaluate_all(
                    """
                    els => els.map(a => ({
                        href: a.href || "",
                        text: (a.innerText || "").trim()
                    }))
                    """
                )

            except Exception:

                links = []

            diagnostico[
                "links"
            ] = len(
                links
            )

            for link in links:

                if not isinstance(
                    link,
                    dict
                ):

                    continue

                href = (
                    link.get(
                        "href"
                    )
                    or ""
                )

                link_text = (
                    link.get(
                        "text"
                    )
                    or ""
                )

                combinado = (
                    href
                    +
                    " "
                    +
                    link_text
                )

                encontrados = (
                    extrair_ids_mlb(
                        combinado
                    )
                )

                for item_id in encontrados:

                    if item_id not in ids:

                        ids.append(
                            item_id
                        )

            diagnostico[
                "ids_links"
            ] = len(
                ids
            )

            # ------------------------------------------------
            # TEXTO RENDERIZADO
            # ------------------------------------------------

            try:

                texto = page.locator(
                    "body"
                ).inner_text(
                    timeout=10000
                )

            except Exception:

                texto = ""

            diagnostico[
                "texto_bytes"
            ] = len(
                texto
            )

            ids_texto = extrair_ids_mlb(
                texto
            )

            diagnostico[
                "ids_texto"
            ] = len(
                ids_texto
            )

            for item_id in ids_texto:

                if item_id not in ids:

                    ids.append(
                        item_id
                    )

            # ------------------------------------------------
            # JSON / SCRIPTS
            # ------------------------------------------------

            try:

                scripts = page.locator(
                    "script"
                ).evaluate_all(
                    """
                    els => els.map(
                        e => e.textContent || ""
                    )
                    """
                )

            except Exception:

                scripts = []

            ids_antes_json = len(
                ids
            )

            for script in scripts:

                if not script:
                    continue

                encontrados = (
                    extrair_ids_mlb(
                        script
                    )
                )

                for item_id in encontrados:

                    if item_id not in ids:

                        ids.append(
                            item_id
                        )

            diagnostico[
                "ids_json"
            ] = (
                len(ids)
                -
                ids_antes_json
            )

            # ------------------------------------------------
            # CAPTURA DE LINKS ESPECÍFICOS
            # ------------------------------------------------

            try:

                hrefs = page.locator(
                    "a[href]"
                ).evaluate_all(
                    """
                    els => els
                        .map(a => a.getAttribute("href") || "")
                        .filter(Boolean)
                    """
                )

                for href in hrefs:

                    decoded = unquote(
                        href
                    )

                    encontrados = (
                        extrair_ids_mlb(
                            decoded
                        )
                    )

                    for item_id in encontrados:

                        if item_id not in ids:

                            ids.append(
                                item_id
                            )

            except Exception:

                pass

            diagnostico[
                "ids_finais"
            ] = len(
                ids
            )

            # ------------------------------------------------
            # DEBUG
            # ------------------------------------------------

            try:

                diagnostico[
                    "amostra_links"
                ] = [

                    x.get(
                        "href",
                        ""
                    )[:300]

                    for x in links[:20]

                    if isinstance(
                        x,
                        dict
                    )
                ]

            except Exception:

                diagnostico[
                    "amostra_links"
                ] = []

            context.close()

            browser.close()

            browser = None

            return (
                ids[:MAX_IDS],
                diagnostico
            )

    except Exception as e:

        diagnostico[
            "erro"
        ] = str(e)

        if browser:

            try:
                browser.close()
            except Exception:
                pass

        return (
            ids[:MAX_IDS],
            diagnostico
        )


# ============================================================
# API ITEM
# ============================================================

def obter_item(
    item_id
):

    data, status = ml_get(
        f"/items/{item_id}"
    )

    if status != 200:

        return (
            None,
            status,
            data
        )

    if not isinstance(
        data,
        dict
    ):

        return (
            None,
            status,
            data
        )

    if not data.get(
        "id"
    ):

        return (
            None,
            status,
            data
        )

    return (
        data,
        status,
        None
    )


# ============================================================
# PREÇOS
# ============================================================

def obter_sale_price(
    item_id
):

    data, status = ml_get(

        f"/items/{item_id}/sale_price",

        params={
            "context":
                "channel_marketplace"
        }
    )

    if status != 200:

        return None

    if not isinstance(
        data,
        dict
    ):

        return None

    return {

        "amount":
            numero(
                data.get(
                    "amount"
                )
            ),

        "regular_amount":
            numero(
                data.get(
                    "regular_amount"
                )
            ),

        "raw":
            data,
    }


def obter_prices(
    item_id
):

    data, status = ml_get(
        f"/items/{item_id}/prices"
    )

    if status != 200:

        return []

    if not isinstance(
        data,
        dict
    ):

        return []

    prices = data.get(
        "prices"
    )

    if not isinstance(
        prices,
        list
    ):

        return []

    return prices


def obter_precos(
    item
):

    item_id = item.get(
        "id"
    )

    if not item_id:

        return None

    atual = numero(
        item.get(
            "price"
        )
    )

    original = numero(
        item.get(
            "original_price"
        )
    )

    # --------------------------------------------------------
    # SALE PRICE
    # --------------------------------------------------------

    sale = obter_sale_price(
        item_id
    )

    if sale:

        if sale.get(
            "amount"
        ) is not None:

            atual = sale[
                "amount"
            ]

        if sale.get(
            "regular_amount"
        ) is not None:

            original = sale[
                "regular_amount"
            ]

    # --------------------------------------------------------
    # PRICES
    # --------------------------------------------------------

    prices = obter_prices(
        item_id
    )

    promocoes = []

    standards = []

    for preco in prices:

        if not isinstance(
            preco,
            dict
        ):

            continue

        tipo = str(
            preco.get(
                "type",
                ""
            )
        ).lower()

        amount = numero(
            preco.get(
                "amount"
            )
        )

        regular = numero(
            preco.get(
                "regular_amount"
            )
        )

        if amount is None:

            continue

        if tipo == "promotion":

            promocoes.append({

                "amount":
                    amount,

                "regular_amount":
                    regular,
            })

        elif tipo == "standard":

            standards.append(
                amount
            )

    if promocoes:

        promocoes.sort(
            key=lambda x:
                x["amount"]
        )

        promo = promocoes[0]

        atual = promo[
            "amount"
        ]

        if promo.get(
            "regular_amount"
        ):

            original = promo[
                "regular_amount"
            ]

    if (
        original is None
        and standards
        and atual is not None
    ):

        maior = max(
            standards
        )

        if maior > atual:

            original = maior

    if atual is None:

        return None

    desconto = calcular_desconto(
        original,
        atual
    )

    return {

        "atual":
            atual,

        "original":
            original,

        "desconto":
            desconto,

        "sale_price":
            sale,

        "prices":
            prices,
    }


# ============================================================
# IMAGEM
# ============================================================

def obter_imagem(
    item
):

    pictures = item.get(
        "pictures",
        []
    )

    if (
        isinstance(
            pictures,
            list
        )
        and pictures
    ):

        primeira = pictures[0]

        if isinstance(
            primeira,
            dict
        ):

            return (
                primeira.get(
                    "secure_url"
                )
                or
                primeira.get(
                    "url"
                )
                or
                ""
            )

    return ""


# ============================================================
# SALVAR OFERTA
# ============================================================

def salvar_oferta(
    oferta
):

    conn = get_db()

    conn.execute(
        """
        INSERT INTO ofertas(
            item_id,
            titulo,
            preco_atual,
            preco_original,
            desconto,
            permalink,
            imagem,
            criado_em
        )

        VALUES(
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )

        ON CONFLICT(item_id)
        DO UPDATE SET

            titulo =
                excluded.titulo,

            preco_atual =
                excluded.preco_atual,

            preco_original =
                excluded.preco_original,

            desconto =
                excluded.desconto,

            permalink =
                excluded.permalink,

            imagem =
                excluded.imagem,

            criado_em =
                excluded.criado_em
        """,

        (
            oferta[
                "item_id"
            ],

            oferta[
                "titulo"
            ],

            oferta[
                "preco_atual"
            ],

            oferta[
                "preco_original"
            ],

            oferta[
                "desconto"
            ],

            oferta[
                "permalink"
            ],

            oferta[
                "imagem"
            ],

            int(
                time.time()
            ),
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# TEXTO DA OFERTA
# ============================================================

def texto_oferta(
    oferta
):

    return (
        "🔥 OFERTA!\n\n"

        f"🛍️ {oferta['titulo']}\n\n"

        f"❌ De: "
        f"{moeda(oferta['preco_original'])}\n"

        f"✅ Por: "
        f"{moeda(oferta['preco_atual'])}\n"

        f"📉 "
        f"{oferta['desconto']:.0f}% OFF\n\n"

        "🛒 Comprar:\n"

        f"{oferta['permalink']}"
    )


# ============================================================
# HTML
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

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    background:
        linear-gradient(
            180deg,
            #090909,
            #141414
        );

    color: #fff;

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;
}

.container {

    width:
        min(1100px, 94%);

    margin: auto;

    padding:
        25px 0 50px;
}

h1 {
    margin-bottom: 5px;
}

.sub {
    color: #999;
}

.card {

    background: #1d1d1d;

    border:
        1px solid #303030;

    border-radius:
        16px;

    padding:
        18px;

    margin-top:
        18px;
}

.form-grid {

    display: grid;

    grid-template-columns:
        1fr 160px 190px;

    gap: 10px;
}

input,
select,
button {

    width: 100%;

    border: 0;

    border-radius:
        10px;

    padding:
        14px;

    font-size:
        16px;
}

input,
select {

    background:
        #292929;

    color:
        white;
}

button {

    background:
        #00a650;

    color:
        white;

    font-weight:
        800;

    cursor:
        pointer;
}

button:hover {
    opacity: .9;
}

.stats {

    display: grid;

    grid-template-columns:
        repeat(6, 1fr);

    gap: 10px;

    margin-top:
        15px;
}

.stat {

    background:
        #222;

    border-radius:
        12px;

    padding:
        14px;
}

.stat strong {

    display:
        block;

    font-size:
        25px;
}

.stat span {

    color:
        #999;

    font-size:
        12px;
}

.notice {

    background:
        #332800;

    border:
        1px solid #665300;

    color:
        #ffe7a0;

    padding:
        15px;

    border-radius:
        12px;

    margin-top:
        15px;
}

.error {

    background:
        #3b1111;

    border:
        1px solid #7d2525;

    color:
        #ffb8b8;

    padding:
        15px;

    border-radius:
        12px;

    margin-top:
        15px;
}

.success {

    background:
        #10351f;

    border:
        1px solid #176b39;

    color:
        #b1ffcc;

    padding:
        15px;

    border-radius:
        12px;

    margin-top:
        15px;
}

.offer {

    display:
        grid;

    grid-template-columns:
        160px 1fr;

    gap:
        18px;

    background:
        #1c1c1c;

    border:
        1px solid #303030;

    border-radius:
        16px;

    padding:
        15px;

    margin-top:
        15px;
}

.offer img {

    width:
        160px;

    height:
        160px;

    object-fit:
        contain;

    background:
        white;

    border-radius:
        10px;
}

.title {

    font-size:
        18px;

    font-weight:
        700;
}

.old {

    color:
        #999;

    text-decoration:
        line-through;

    margin-top:
        12px;
}

.price {

    color:
        #00dc6a;

    font-size:
        28px;

    font-weight:
        800;
}

.discount {

    display:
        inline-block;

    background:
        #00a650;

    padding:
        5px 9px;

    border-radius:
        8px;

    font-weight:
        800;
}

.actions {

    display:
        flex;

    flex-wrap:
        wrap;

    gap:
        8px;

    margin-top:
        12px;
}

.actions a,
.actions button {

    width:
        auto;

    text-decoration:
        none;

    background:
        #333;

    color:
        white;

    padding:
        10px 13px;

    border-radius:
        9px;

    font-size:
        14px;
}

.actions .green {
    background:
        #00a650;
}

.small {

    color:
        #888;

    font-size:
        12px;
}

.debug {

    background:
        #0d0d0d;

    border:
        1px solid #292929;

    border-radius:
        12px;

    padding:
        12px;

    overflow:
        auto;

    font-size:
        12px;

    white-space:
        pre-wrap;

    word-break:
        break-word;
}

@media(max-width: 800px) {

    .form-grid {

        grid-template-columns:
            1fr;
    }

    .stats {

        grid-template-columns:
            repeat(2, 1fr);
    }

    .offer {

        grid-template-columns:
            1fr;
    }

    .offer img {

        width:
            100%;

        height:
            220px;
    }
}

</style>

</head>

<body>

<div class="container">

<h1>
🔥 Caçador de Ofertas
</h1>

<p class="sub">
Mercado Livre • navegador real • filtro de desconto
</p>


<div class="card">

<form
    action="/buscar"
    method="get"
>

<div class="form-grid">

<input
    type="text"
    name="q"
    value="{{ consulta }}"
    placeholder="Ex.: celular, fone, air fryer..."
    required
>

<select name="desconto">

<option value="5"
{% if desconto_min == 5 %}selected{% endif %}
>
5% ou mais
</option>

<option value="10"
{% if desconto_min == 10 %}selected{% endif %}
>
10% ou mais
</option>

<option value="15"
{% if desconto_min == 15 %}selected{% endif %}
>
15% ou mais
</option>

<option value="20"
{% if desconto_min == 20 %}selected{% endif %}
>
20% ou mais
</option>

<option value="30"
{% if desconto_min == 30 %}selected{% endif %}
>
30% ou mais
</option>

<option value="40"
{% if desconto_min == 40 %}selected{% endif %}
>
40% ou mais
</option>

<option value="50"
{% if desconto_min == 50 %}selected{% endif %}
>
50% ou mais
</option>

</select>

<button type="submit">
CAÇAR OFERTAS
</button>

</div>

</form>

</div>


{% if aviso %}

<div class="notice">
{{ aviso }}
</div>

{% endif %}


{% if erro %}

<div class="error">
{{ erro }}
</div>

{% endif %}


{% if stats %}

<div class="stats">

<div class="stat">
<strong>{{ stats.descobertos }}</strong>
<span>IDs descobertos</span>
</div>

<div class="stat">
<strong>{{ stats.anuncios }}</strong>
<span>Anúncios consultados</span>
</div>

<div class="stat">
<strong>{{ stats.ofertas }}</strong>
<span>Ofertas</span>
</div>

<div class="stat">
<strong>{{ stats.com_desconto }}</strong>
<span>Com desconto</span>
</div>

<div class="stat">
<strong>{{ stats.sem_original }}</strong>
<span>Sem preço original</span>
</div>

<div class="stat">
<strong>{{ stats.erros }}</strong>
<span>Erros</span>
</div>

</div>

{% endif %}


{% if ofertas %}

{% for oferta in ofertas %}

<div class="offer">

<img
    src="{{ oferta.imagem }}"
    alt=""
    loading="lazy"
>

<div>

<div class="title">
{{ oferta.titulo }}
</div>

<div class="old">
De: {{ oferta.preco_original_fmt }}
</div>

<div class="price">
Por: {{ oferta.preco_atual_fmt }}
</div>

<div class="discount">
{{ oferta.desconto_fmt }} OFF
</div>

<div class="small">
ID: {{ oferta.item_id }}
</div>

<div class="actions">

<a
    class="green"
    href="{{ oferta.permalink }}"
    target="_blank"
>
🛒 Abrir produto
</a>

<a
    href="https://www.mercadolivre.com.br/afiliados"
    target="_blank"
>
🔗 Gerar afiliado
</a>

<button
    onclick='copiarOferta({{ oferta.texto_js|tojson }})'
>
📋 Copiar
</button>

</div>

</div>

</div>

{% endfor %}

{% elif buscou %}

<div class="card">

<h2>
Nenhuma oferta encontrada.
</h2>

<p class="small">

A busca foi executada, mas nenhuma publicação
com desconto suficiente foi confirmada.

</p>

</div>

{% endif %}


{% if diagnostico %}

<div class="card">

<h3>
🔎 Diagnóstico do navegador
</h3>

<div class="debug">

<pre>{{ diagnostico_json }}</pre>

</div>

</div>

{% endif %}


<div class="card">

<h3>
🔐 Mercado Livre
</h3>

{% if oauth %}

<div class="success">

Conectado como:

<strong>
{{ oauth.nickname or oauth.user_id }}
</strong>

<br><br>

User ID:
{{ oauth.user_id }}

</div>

{% else %}

<a
    href="/mercadolivre/login"
    style="
        display:inline-block;
        background:#00a650;
        color:#fff;
        padding:12px 15px;
        border-radius:9px;
        text-decoration:none;
        font-weight:700;
    "
>
🔐 Conectar Mercado Livre
</a>

{% endif %}

</div>


</div>


<script>

function copiarOferta(texto) {

    navigator.clipboard.writeText(texto)
        .then(function() {

            alert(
                "Oferta copiada!"
            );

        })
        .catch(function() {

            alert(
                "Não foi possível copiar."
            );

        });

}

</script>

</body>

</html>
"""


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    return render_template_string(

        HTML,

        oauth=
            carregar_oauth(),

        consulta=
            "",

        desconto_min=
            10,

        ofertas=
            [],

        stats=
            None,

        aviso=
            None,

        erro=
            None,

        buscou=
            False,

        diagnostico=
            None,

        diagnostico_json=
            None,
    )


# ============================================================
# BUSCAR
# ============================================================

@app.route("/buscar")
def buscar():

    consulta = request.args.get(
        "q",
        ""
    ).strip()

    try:

        desconto_min = float(
            request.args.get(
                "desconto",
                "10"
            )
        )

    except Exception:

        desconto_min = 10

    stats = {

        "descobertos":
            0,

        "anuncios":
            0,

        "ofertas":
            0,

        "com_desconto":
            0,

        "sem_original":
            0,

        "erros":
            0,
    }

    ofertas = []

    erro = None

    aviso = None

    diagnostico = None

    if not consulta:

        return render_template_string(

            HTML,

            oauth=
                carregar_oauth(),

            consulta=
                "",

            desconto_min=
                desconto_min,

            ofertas=
                [],

            stats=
                None,

            aviso=
                None,

            erro=
                "Digite um produto.",

            buscou=
                True,

            diagnostico=
                None,

            diagnostico_json=
                None,
        )

    # --------------------------------------------------------
    # PLAYWRIGHT
    # --------------------------------------------------------

    ids, diagnostico = (
        buscar_com_playwright(
            consulta
        )
    )

    stats[
        "descobertos"
    ] = len(ids)

    if not ids:

        erro = (
            "O navegador abriu o Mercado Livre, "
            "mas não encontrou IDs de anúncios."
        )

        diagnostico_json = json.dumps(
            diagnostico,
            indent=2,
            ensure_ascii=False
        )

        return render_template_string(

            HTML,

            oauth=
                carregar_oauth(),

            consulta=
                consulta,

            desconto_min=
                desconto_min,

            ofertas=
                [],

            stats=
                stats,

            aviso=
                None,

            erro=
                erro,

            buscou=
                True,

            diagnostico=
                diagnostico,

            diagnostico_json=
                diagnostico_json,
        )

    # --------------------------------------------------------
    # CONSULTA OS ANÚNCIOS
    # --------------------------------------------------------

    for item_id in ids[:MAX_ITEMS]:

        stats[
            "anuncios"
        ] += 1

        item, status, raw = (
            obter_item(
                item_id
            )
        )

        if not item:

            stats[
                "erros"
            ] += 1

            continue

        precos = obter_precos(
            item
        )

        if not precos:

            stats[
                "erros"
            ] += 1

            continue

        atual = precos.get(
            "atual"
        )

        original = precos.get(
            "original"
        )

        desconto = precos.get(
            "desconto",
            0
        )

        if original is None:

            stats[
                "sem_original"
            ] += 1

            continue

        stats[
            "com_desconto"
        ] += 1

        if desconto < desconto_min:

            continue

        titulo = (
            item.get(
                "title"
            )
            or
            "Produto Mercado Livre"
        )

        permalink = (
            item.get(
                "permalink"
            )
            or
            f"https://www.mercadolivre.com.br/"
            f"{item_id}"
        )

        imagem = obter_imagem(
            item
        )

        oferta = {

            "item_id":
                item_id,

            "titulo":
                titulo,

            "preco_atual":
                atual,

            "preco_original":
                original,

            "desconto":
                desconto,

            "permalink":
                permalink,

            "imagem":
                imagem,
        }

        oferta[
            "preco_atual_fmt"
        ] = moeda(
            atual
        )

        oferta[
            "preco_original_fmt"
        ] = moeda(
            original
        )

        oferta[
            "desconto_fmt"
        ] = (
            f"{desconto:.0f}%"
        )

        oferta[
            "texto_js"
        ] = texto_oferta(
            oferta
        )

        ofertas.append(
            oferta
        )

        salvar_oferta(
            oferta
        )

        stats[
            "ofertas"
        ] += 1

    # --------------------------------------------------------
    # ORDENA
    # --------------------------------------------------------

    ofertas.sort(

        key=lambda x:
            x["desconto"],

        reverse=True
    )

    if ofertas:

        aviso = (
            f"🔥 Encontradas "
            f"{len(ofertas)} oferta(s) "
            f"com {desconto_min:.0f}% "
            f"ou mais de desconto."
        )

    elif stats[
        "com_desconto"
    ]:

        aviso = (
            "Anúncios com preço original "
            "foram encontrados, mas nenhum "
            "atingiu o desconto mínimo."
        )

    else:

        aviso = (
            "Os anúncios foram encontrados, "
            "mas não foi possível confirmar "
            "um preço original suficiente."
        )

    diagnostico_json = json.dumps(

        diagnostico,

        indent=2,

        ensure_ascii=False
    )

    return render_template_string(

        HTML,

        oauth=
            carregar_oauth(),

        consulta=
            consulta,

        desconto_min=
            desconto_min,

        ofertas=
            ofertas,

        stats=
            stats,

        aviso=
            aviso,

        erro=
            erro,

        buscou=
            True,

        diagnostico=
            diagnostico,

        diagnostico_json=
            diagnostico_json,
    )


# ============================================================
# TESTE DE BUSCA
# ============================================================

@app.route(
    "/mercadolivre/teste-busca"
)
def teste_busca():

    consulta = request.args.get(
        "q",
        "celular"
    ).strip()

    ids, diagnostico = (
        buscar_com_playwright(
            consulta
        )
    )

    testes_api = []

    for item_id in ids[:5]:

        item, status, raw = (
            obter_item(
                item_id
            )
        )

        if item:

            dados = obter_precos(
                item
            )

            testes_api.append({

                "item_id":
                    item_id,

                "http":
                    status,

                "titulo":
                    item.get(
                        "title"
                    ),

                "price":
                    item.get(
                        "price"
                    ),

                "original_price":
                    item.get(
                        "original_price"
                    ),

                "precos":
                    dados,

                "permalink":
                    item.get(
                        "permalink"
                    ),
            })

        else:

            testes_api.append({

                "item_id":
                    item_id,

                "http":
                    status,

                "erro":
                    raw,
            })

    return jsonify({

        "consulta":
            consulta,

        "playwright":
            PLAYWRIGHT_OK,

        "total_ids":
            len(ids),

        "ids":
            ids,

        "diagnostico":
            diagnostico,

        "testes_api":
            testes_api,
    })


# ============================================================
# TESTE PREÇO
# ============================================================

@app.route(
    "/mercadolivre/teste-preco"
)
def teste_preco():

    item_id = request.args.get(
        "item_id",
        ""
    ).strip().upper()

    if not re.fullmatch(
        r"MLB\d{6,12}",
        item_id
    ):

        return jsonify({

            "erro":
                "ID inválido.",

            "exemplo":
                "MLB123456789",
        }), 400

    item, status, raw = (
        obter_item(
            item_id
        )
    )

    if not item:

        return jsonify({

            "http":
                status,

            "erro":
                raw,
        }), 400

    dados = obter_precos(
        item
    )

    return jsonify({

        "item": {

            "id":
                item.get(
                    "id"
                ),

            "title":
                item.get(
                    "title"
                ),

            "price":
                item.get(
                    "price"
                ),

            "original_price":
                item.get(
                    "original_price"
                ),

            "permalink":
                item.get(
                    "permalink"
                ),
        },

        "precos":
            dados,
    })


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/mercadolivre/login"
)
def mercadolivre_login():

    if not CLIENT_ID:

        return (
            "ML_CLIENT_ID não configurado.",
            500
        )

    state = secrets.token_urlsafe(
        32
    )

    verifier = (
        gerar_code_verifier()
    )

    challenge = (
        gerar_code_challenge(
            verifier
        )
    )

    salvar_config(
        "oauth_state",
        state
    )

    salvar_config(
        "oauth_code_verifier",
        verifier
    )

    params = {

        "response_type":
            "code",

        "client_id":
            CLIENT_ID,

        "redirect_uri":
            REDIRECT_URI,

        "state":
            state,

        "code_challenge":
            challenge,

        "code_challenge_method":
            "S256",
    }

    url = (
        f"{ML_AUTH}/authorization?"
        +
        urlencode(
            params
        )
    )

    return redirect(
        url
    )


# ============================================================
# CALLBACK
# ============================================================

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
            f"<pre>{html.escape(error)}</pre>",
            400
        )

    code = request.args.get(
        "code"
    )

    state = request.args.get(
        "state"
    )

    saved_state = ler_config(
        "oauth_state"
    )

    verifier = ler_config(
        "oauth_code_verifier"
    )

    if not code:

        return (
            "Código não recebido.",
            400
        )

    if state != saved_state:

        return (
            "STATE inválido.",
            400
        )

    if not verifier:

        return (
            "Code verifier não encontrado.",
            400
        )

    payload = {

        "grant_type":
            "authorization_code",

        "client_id":
            CLIENT_ID,

        "client_secret":
            CLIENT_SECRET,

        "code":
            code,

        "redirect_uri":
            REDIRECT_URI,

        "code_verifier":
            verifier,
    }

    try:

        response = requests.post(

            f"{ML_API}/oauth/token",

            data=payload,

            timeout=HTTP_TIMEOUT
        )

        if response.status_code != 200:

            return (

                "<h2>Erro ao obter token</h2>"

                f"<pre>"
                f"{html.escape(response.text)}"
                f"</pre>",

                500
            )

        data = response.json()

        access_token = data.get(
            "access_token"
        )

        refresh_token = data.get(
            "refresh_token"
        )

        expires_in = int(
            data.get(
                "expires_in",
                21600
            )
        )

        if not access_token:

            return (
                "access_token não recebido.",
                500
            )

        user_id = None

        nickname = None

        try:

            me = requests.get(

                f"{ML_API}/users/me",

                headers={
                    "Authorization":
                        f"Bearer {access_token}"
                },

                timeout=HTTP_TIMEOUT
            )

            if me.status_code == 200:

                user = me.json()

                user_id = str(
                    user.get(
                        "id"
                    )
                )

                nickname = user.get(
                    "nickname"
                )

        except Exception:

            pass

        salvar_oauth({

            "access_token":
                access_token,

            "refresh_token":
                refresh_token,

            "expires_at":
                int(time.time())
                +
                expires_in,

            "user_id":
                user_id,

            "nickname":
                nickname,
        })

        return redirect(
            "/"
        )

    except Exception as e:

        return (

            "<h2>Erro</h2>"

            f"<pre>"
            f"{html.escape(str(e))}"
            f"</pre>",

            500
        )


# ============================================================
# STATUS
# ============================================================

@app.route(
    "/mercadolivre/status"
)
def mercadolivre_status():

    row = carregar_oauth()

    if not row:

        return jsonify({

            "conectado":
                False,

            "mensagem":
                "Nenhum OAuth salvo.",
        })

    token = obter_access_token()

    if not token:

        return jsonify({

            "conectado":
                False,

            "mensagem":
                "Token ausente ou expirado.",

            "user_id":
                row[
                    "user_id"
                ],

            "nickname":
                row[
                    "nickname"
                ],
        })

    me, status = ml_get(
        "/users/me"
    )

    return jsonify({

        "conectado":
            status == 200,

        "http":
            status,

        "user_id":
            row[
                "user_id"
            ],

        "nickname":
            row[
                "nickname"
            ],

        "me":
            me,
    })


# ============================================================
# DIAGNÓSTICO
# ============================================================

@app.route(
    "/mercadolivre/diagnostico"
)
def diagnostico():

    resultado = {

        "playwright_instalado":
            PLAYWRIGHT_OK,

        "playwright_erro":
            (
                PLAYWRIGHT_ERROR
                if not PLAYWRIGHT_OK
                else None
            ),

        "client_id":
            bool(
                CLIENT_ID
            ),

        "client_secret":
            bool(
                CLIENT_SECRET
            ),

        "redirect_uri":
            REDIRECT_URI,

        "oauth":
            bool(
                carregar_oauth()
            ),

        "token_valido":
            token_valido(),
    }

    me, status = ml_get(
        "/users/me"
    )

    resultado[
        "users_me"
    ] = {

        "http":
            status,

        "resultado":
            me,
    }

    return jsonify(
        resultado
    )


# ============================================================
# HISTÓRICO
# ============================================================

@app.route(
    "/ofertas"
)
def ofertas():

    conn = get_db()

    rows = conn.execute(
        """
        SELECT *
        FROM ofertas
        ORDER BY desconto DESC, criado_em DESC
        LIMIT 200
        """
    ).fetchall()

    conn.close()

    return jsonify([
        dict(row)
        for row in rows
    ])


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
            "cacador-de-ofertas",

        "playwright":
            PLAYWRIGHT_OK,

        "oauth":
            bool(
                carregar_oauth()
            ),
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False
    )