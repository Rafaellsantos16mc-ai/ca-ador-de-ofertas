import os
import re
import json
import time
import html
import random
import sqlite3
import secrets
import hashlib
import base64
import unicodedata
from urllib.parse import (
    urlencode,
    urlparse,
    parse_qs,
    quote,
    urljoin,
)

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
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

DB_FILE = "ofertas.db"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback",
).strip()

MIN_PRICE = 69.90

# Buscas usadas para alimentar o caçador.
SEARCHES = [
    "celular",
    "notebook",
    "smart tv",
    "fone bluetooth",
    "air fryer",
    "máquina de lavar",
    "microondas",
    "geladeira",
    "monitor",
    "smartwatch",
    "caixa de som",
    "aspirador",
    "parafusadeira",
    "ferramentas",
    "tênis",
    "perfume",
    "cadeira escritório",
    "móveis",
]

PUBLIC_COUPON_PAGES = [
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://lista.mercadolivre.com.br/_Container_lpsm-cupons-2026",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cache-Control": "no-cache",
}


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
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
# HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def clean_text(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def normalize_text(value):
    value = clean_text(value).lower()

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        c for c in value
        if not unicodedata.combining(c)
    )

    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def parse_money(value):
    if value is None:
        return None

    value = str(value).strip()

    value = value.replace("R$", "")
    value = value.replace(" ", "")

    # Formato brasileiro:
    # 1.299,90
    if "," in value:
        value = value.replace(".", "")
        value = value.replace(",", ".")

    try:
        return float(value)
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


def slugify(value):
    value = normalize_text(value)
    return value.replace(" ", "-")


def product_key(title):
    """
    Chave usada para evitar mostrar o mesmo produto
    várias vezes com vendedores diferentes.
    """
    text = normalize_text(title)

    # Remove palavras muito genéricas.
    remove = {
        "oferta",
        "original",
        "novo",
        "nova",
        "frete",
        "gratis",
        "mercado",
        "livre",
    }

    words = [
        w for w in text.split()
        if w not in remove
    ]

    return " ".join(words[:28])


def valid_price(price):
    return price is not None and price >= MIN_PRICE


# ============================================================
# OAUTH / MERCADO LIVRE
# ============================================================

def save_auth(data):
    conn = db()

    conn.execute("DELETE FROM ml_auth")

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
        VALUES (1, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("access_token"),
            data.get("refresh_token"),
            int(data.get("expires_at", now_ts() + 21600)),
            str(data.get("user_id", "")),
            data.get("nickname", ""),
            now_ts(),
        ),
    )

    conn.commit()
    conn.close()


def get_auth():
    conn = db()

    row = conn.execute(
        "SELECT * FROM ml_auth WHERE id = 1"
    ).fetchone()

    conn.close()

    return row


def refresh_ml_token():
    row = get_auth()

    if not row:
        return False

    refresh_token = row["refresh_token"]

    if not refresh_token:
        return False

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            timeout=30,
        )

        if response.status_code != 200:
            print(
                "[ML REFRESH ERRO]",
                response.status_code,
                response.text[:1000],
            )
            return False

        data = response.json()

        save_auth(
            {
                "access_token": data.get("access_token"),
                "refresh_token": data.get(
                    "refresh_token",
                    refresh_token,
                ),
                "expires_at": now_ts() + int(
                    data.get("expires_in", 21600)
                ),
                "user_id": row["user_id"],
                "nickname": row["nickname"],
            }
        )

        print("[ML TOKEN] Atualizado")

        return True

    except Exception as e:
        print("[ML REFRESH EXCEPTION]", e)
        return False


def get_access_token():
    row = get_auth()

    if not row:
        return None

    expires_at = int(row["expires_at"] or 0)

    if expires_at <= now_ts() + 120:
        if refresh_ml_token():
            row = get_auth()

    return row["access_token"] if row else None


def ml_get(path, params=None, timeout=30):
    token = get_access_token()

    if not token:
        return None, {
            "error": "Mercado Livre não conectado"
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    url = (
        path
        if path.startswith("http")
        else ML_API + path
    )

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

                headers["Authorization"] = (
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
                "raw": response.text[:3000]
            }

        return response, data

    except Exception as e:
        return None, {
            "error": str(e)
        }


# ============================================================
# PKCE
# ============================================================

def make_code_verifier():
    return secrets.token_urlsafe(64)


def make_code_challenge(verifier):
    digest = hashlib.sha256(
        verifier.encode("ascii")
    ).digest()

    return base64.urlsafe_b64encode(
        digest
    ).rstrip(b"=").decode("ascii")


# ============================================================
# MERCADO LIVRE LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def mercadolivre_login():

    if not ML_CLIENT_ID:
        return "ML_CLIENT_ID não configurado", 500

    state = secrets.token_urlsafe(32)

    verifier = make_code_verifier()
    challenge = make_code_challenge(verifier)

    session["ml_state"] = state
    session["ml_code_verifier"] = verifier

    params = {
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    return redirect(
        ML_AUTH + "?" + urlencode(params)
    )


@app.route("/mercadolivre/callback")
def mercadolivre_callback():

    error = request.args.get("error")

    if error:
        return (
            f"<h2>Erro Mercado Livre</h2>"
            f"<pre>{clean_text(request.args.get('error_description', error))}</pre>"
        ), 400

    state = request.args.get("state")
    code = request.args.get("code")

    if not state or state != session.get("ml_state"):
        return "State OAuth inválido.", 400

    if not code:
        return "Código OAuth não recebido.", 400

    verifier = session.get("ml_code_verifier")

    if not verifier:
        return "Code verifier ausente.", 400

    try:
        response = requests.post(
            ML_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": verifier,
            },
            timeout=30,
        )

        if response.status_code != 200:
            return (
                "<h2>Erro ao obter token</h2>"
                f"<pre>{response.text}</pre>"
            ), 400

        token_data = response.json()

        access_token = token_data.get(
            "access_token"
        )

        if not access_token:
            return "Access token não recebido.", 400

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
                "<h2>Token obtido, mas não consegui "
                "consultar /users/me</h2>"
                f"<pre>{user_response.text}</pre>"
            ), 400

        user = user_response.json()

        save_auth(
            {
                "access_token": access_token,
                "refresh_token": token_data.get(
                    "refresh_token"
                ),
                "expires_at": now_ts() + int(
                    token_data.get(
                        "expires_in",
                        21600,
                    )
                ),
                "user_id": user.get("id"),
                "nickname": user.get("nickname"),
            }
        )

        session.pop("ml_state", None)
        session.pop("ml_code_verifier", None)

        return redirect("/")

    except Exception as e:
        return (
            "<h2>Erro OAuth</h2>"
            f"<pre>{e}</pre>"
        ), 500


@app.route("/mercadolivre/reconnect")
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
# CATALOGO MERCADO LIVRE
# ============================================================

def search_products(query, limit=20):
    """
    Usa o buscador atual de produtos do Mercado Livre.
    """

    response, data = ml_get(
        "/products/search",
        params={
            "site_id": "MLB",
            "q": query,
            "status": "active",
            "limit": limit,
        },
        timeout=30,
    )

    if not response or response.status_code != 200:
        print(
            "[PRODUCT SEARCH ERRO]",
            query,
            response.status_code if response else None,
            str(data)[:500],
        )

        return []

    results = data.get("results", [])

    return results


def get_product_detail(product_id):
    response, data = ml_get(
        f"/products/{product_id}",
        timeout=30,
    )

    if not response or response.status_code != 200:
        return None

    return data


def get_product_items(product_id):
    """
    Nem todo produto possui endpoint /items disponível.
    Portanto isso é complementar, não obrigatório.
    """

    response, data = ml_get(
        f"/products/{product_id}/items",
        params={
            "limit": 50,
        },
        timeout=30,
    )

    if not response or response.status_code != 200:
        return []

    return data.get("results", [])


# ============================================================
# HTML PARSER
# ============================================================

from html.parser import HTMLParser


class AnchorParser(HTMLParser):

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.links = []

        self.current_href = None
        self.current_text = []
        self.current_depth = 0

    def handle_starttag(self, tag, attrs):

        if tag.lower() == "a":

            attrs_dict = dict(attrs)

            self.current_href = (
                attrs_dict.get("href")
            )

            self.current_text = []

            self.current_depth = 1

        elif self.current_href is not None:
            self.current_depth += 1

    def handle_endtag(self, tag):

        if self.current_href is None:
            return

        if tag.lower() == "a":

            text = clean_text(
                " ".join(self.current_text)
            )

            self.links.append(
                {
                    "href":
                        self.current_href,
                    "text":
                        text,
                }
            )

            self.current_href = None
            self.current_text = []
            self.current_depth = 0

        else:
            self.current_depth = max(
                0,
                self.current_depth - 1,
            )

    def handle_data(self, data):

        if self.current_href is not None:
            self.current_text.append(
                data
            )


# ============================================================
# CUPOM PARSER
# ============================================================

def parse_coupon_text(text):
    """
    Detecta somente cupons explicitamente
    associados ao conteúdo encontrado.

    NÃO aplica cupom genérico automaticamente.
    """

    text = clean_text(text)

    coupon_percent = None
    coupon_fixed = None

    # Cupom 15% OFF
    m_percent = re.search(
        r"cupom\s+(\d{1,2}(?:[.,]\d+)?)\s*%\s*off",
        text,
        re.I,
    )

    if m_percent:
        try:
            coupon_percent = float(
                m_percent.group(1).replace(",", ".")
            )
        except Exception:
            coupon_percent = None

    # Cupom R$ 15 OFF
    m_fixed = re.search(
        r"cupom\s+r?\$?\s*"
        r"(\d{1,4}(?:[.,]\d{2})?)\s*off",
        text,
        re.I,
    )

    if m_fixed:
        coupon_fixed = parse_money(
            m_fixed.group(1)
        )

    if coupon_percent is None and coupon_fixed is None:
        return None

    return {
        "percent": coupon_percent,
        "fixed": coupon_fixed,
        "raw": (
            m_percent.group(0)
            if m_percent
            else m_fixed.group(0)
        ),
    }


def extract_prices(text):
    """
    Retorna preços encontrados no texto.
    """

    prices = []

    pattern = re.compile(
        r"R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*(?:,\d{2})"
        r"|\d+(?:,\d{2}))",
        re.I,
    )

    for match in pattern.finditer(text):

        value = parse_money(
            match.group(1)
        )

        if value is not None:
            prices.append(value)

    return prices


def extract_coupon_offers_from_html(
    page_html,
    source_url,
):
    """
    Extrator tolerante para as páginas públicas
    de listagem do Mercado Livre.

    O Mercado Livre muda classes HTML com frequência,
    então não dependemos de uma classe CSS específica.
    """

    parser = AnchorParser()

    try:
        parser.feed(page_html)
    except Exception as e:
        print(
            "[HTML PARSER]",
            e,
        )

    offers = []

    # --------------------------------------------------------
    # 1. Tenta primeiro os links individuais.
    # --------------------------------------------------------

    for link in parser.links:

        text = clean_text(
            link.get("text", "")
        )

        if not text:
            continue

        coupon = parse_coupon_text(text)

        if not coupon:
            continue

        prices = extract_prices(text)

        if not prices:
            continue

        # Pega o último preço encontrado.
        price = prices[-1]

        if not valid_price(price):
            continue

        href = link.get("href") or ""

        if href.startswith("//"):
            href = "https:" + href

        elif href.startswith("/"):
            href = urljoin(
                source_url,
                href,
            )

        if not href.startswith("http"):
            continue

        # Ignora links que não parecem produto.
        if (
            "mercadolivre" not in href
            and "mercadolibre" not in href
        ):
            continue

        title = text

        # Remove partes conhecidas do card.
        title = re.sub(
            r"cupom\s+"
            r"(?:r\$\s*)?"
            r"\d+(?:[.,]\d+)?"
            r"\s*%\s*off",
            "",
            title,
            flags=re.I,
        )

        title = re.sub(
            r"cupom\s+r?\$?\s*"
            r"\d+(?:[.,]\d+)?\s*off",
            "",
            title,
            flags=re.I,
        )

        title = clean_text(title)

        # Tenta remover preços.
        title = re.sub(
            r"r\$\s*"
            r"\d{1,3}(?:\.\d{3})*(?:,\d{2})",
            "",
            title,
            flags=re.I,
        )

        title = clean_text(title)

        if len(title) < 8:
            continue

        offers.append(
            {
                "title": title,
                "price": price,
                "url": href,
                "coupon": coupon,
                "source": source_url,
            }
        )

    # --------------------------------------------------------
    # 2. Fallback: procura no HTML bruto.
    # --------------------------------------------------------

    if not offers:

        decoded = html.unescape(
            page_html
        )

        decoded = re.sub(
            r"<script\b[^>]*>.*?</script>",
            " ",
            decoded,
            flags=re.I | re.S,
        )

        decoded = re.sub(
            r"<style\b[^>]*>.*?</style>",
            " ",
            decoded,
            flags=re.I | re.S,
        )

        plain = clean_text(decoded)

        coupon_matches = list(
            re.finditer(
                r"cupom\s+"
                r"(?:(?:r?\$)\s*)?"
                r"\d+(?:[.,]\d+)?"
                r"(?:\s*%\s*)?"
                r"\s*off",
                plain,
                re.I,
            )
        )

        for match in coupon_matches:

            start = max(
                0,
                match.start() - 700,
            )

            end = min(
                len(plain),
                match.end() + 900,
            )

            chunk = plain[start:end]

            coupon = parse_coupon_text(
                chunk
            )

            if not coupon:
                continue

            prices = extract_prices(
                chunk
            )

            if not prices:
                continue

            price = None

            # Procura um preço plausível >= mínimo.
            for p in reversed(prices):
                if p >= MIN_PRICE:
                    price = p
                    break

            if price is None:
                continue

            # Tenta identificar um título
            # antes do cupom.
            before = plain[
                start:match.start()
            ]

            pieces = re.split(
                r"(?:\bOFERTA\b|\bImage\b|\bChegará\b|\bFrete\b)",
                before,
                flags=re.I,
            )

            title = (
                pieces[-1]
                if pieces
                else before
            )

            title = clean_text(title)

            title = re.sub(
                r"r\$\s*[\d.,]+",
                "",
                title,
                flags=re.I,
            )

            title = clean_text(title)

            if len(title) < 8:
                title = (
                    "Produto com cupom"
                )

            offers.append(
                {
                    "title": title,
                    "price": price,
                    "url": source_url,
                    "coupon": coupon,
                    "source": source_url,
                }
            )

    return offers


def fetch_public_page(url):
    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True,
        )

        print(
            "[PUBLIC PAGE]",
            response.status_code,
            url,
        )

        if response.status_code != 200:
            return None

        return response.text

    except Exception as e:

        print(
            "[PUBLIC PAGE ERRO]",
            url,
            e,
        )

        return None


# ============================================================
# CUPONS PUBLICOS
# ============================================================

def search_public_coupon_page(query):
    """
    Pesquisa produtos dentro da listagem pública
    do Mercado Livre.

    Exemplo:
    lista.mercadolivre.com.br/celular
    """

    slug = quote(
        query.strip(),
        safe="",
    )

    url = (
        "https://lista.mercadolivre.com.br/"
        + slug
    )

    page = fetch_public_page(url)

    if not page:
        return []

    return extract_coupon_offers_from_html(
        page,
        url,
    )


def search_coupon_hub():
    """
    Página pública oficial de cupons/ofertas.
    """

    all_offers = []

    for url in PUBLIC_COUPON_PAGES:

        page = fetch_public_page(
            url
        )

        if not page:
            continue

        offers = (
            extract_coupon_offers_from_html(
                page,
                url,
            )
        )

        all_offers.extend(
            offers
        )

        time.sleep(0.3)

    return all_offers


# ============================================================
# CALCULO DO CUPOM
# ============================================================

def calculate_coupon(price, coupon):
    if not coupon:
        return None

    percent = coupon.get(
        "percent"
    )

    fixed = coupon.get(
        "fixed"
    )

    discount = 0

    label = "Cupom"

    if percent is not None:

        discount = price * (
            percent / 100.0
        )

        label = (
            f"Cupom {percent:g}% OFF"
        )

    elif fixed is not None:

        discount = min(
            fixed,
            price,
        )

        label = (
            f"Cupom {money_br(fixed)} OFF"
        )

    if discount <= 0:
        return None

    final_price = max(
        0,
        price - discount,
    )

    effective_percent = (
        discount / price * 100
        if price > 0
        else 0
    )

    return {
        "discount": round(
            discount,
            2,
        ),
        "final_price": round(
            final_price,
            2,
        ),
        "effective_percent": round(
            effective_percent,
            2,
        ),
        "label": label,
    }


# ============================================================
# NORMALIZAÇÃO / AGRUPAMENTO
# ============================================================

def extract_mlb_id(url):
    if not url:
        return None

    match = re.search(
        r"\b(MLB\d{6,})\b",
        url.upper(),
    )

    if match:
        return match.group(1)

    return None


def deduplicate_coupon_offers(
    offers
):
    """
    1 produto = 1 oportunidade.

    Se o mesmo produto aparecer várias vezes,
    mantém a combinação com maior desconto real.
    """

    grouped = {}

    for offer in offers:

        title = clean_text(
            offer.get("title")
        )

        price = offer.get(
            "price"
        )

        coupon = offer.get(
            "coupon"
        )

        if not title or not valid_price(price):
            continue

        calculation = calculate_coupon(
            price,
            coupon,
        )

        if not calculation:
            continue

        mlb_id = extract_mlb_id(
            offer.get("url")
        )

        if mlb_id:
            key = mlb_id
        else:
            key = product_key(
                title
            )

        item = {
            "title": title,
            "price": round(
                price,
                2,
            ),
            "final_price": calculation[
                "final_price"
            ],
            "discount": calculation[
                "discount"
            ],
            "effective_percent":
                calculation[
                    "effective_percent"
                ],
            "coupon_label":
                calculation[
                    "label"
                ],
            "url": offer.get(
                "url"
            ),
            "source": offer.get(
                "source"
            ),
            "product_id": mlb_id,
        }

        old = grouped.get(
            key
        )

        if old is None:
            grouped[key] = item
            continue

        # Prioridade:
        # 1. maior desconto em R$
        # 2. maior %
        # 3. menor preço final
        current_score = (
            item["discount"],
            item[
                "effective_percent"
            ],
            -item[
                "final_price"
            ],
        )

        old_score = (
            old["discount"],
            old[
                "effective_percent"
            ],
            -old[
                "final_price"
            ],
        )

        if current_score > old_score:
            grouped[key] = item

    return list(
        grouped.values()
    )


# ============================================================
# ENRIQUECIMENTO COM CATALOGO
# ============================================================

def enrich_with_catalog(
    offers,
):
    """
    Tenta relacionar a oferta pública com
    produtos do catálogo.

    A falha aqui não elimina a oferta.
    """

    cache = {}

    for offer in offers:

        product_id = offer.get(
            "product_id"
        )

        if not product_id:
            continue

        if product_id in cache:
            detail = cache[
                product_id
            ]
        else:
            detail = get_product_detail(
                product_id
            )

            cache[
                product_id
            ] = detail

            time.sleep(
                0.15
            )

        if not detail:
            continue

        offer[
            "catalog_id"
        ] = detail.get(
            "id"
        )

        offer[
            "catalog_title"
        ] = detail.get(
            "name"
        )

        # Não substituímos o preço público
        # do cupom. O preço da listagem é
        # a base da oportunidade encontrada.
        buy_box = detail.get(
            "buy_box_winner"
        )

        if isinstance(
            buy_box,
            dict,
        ):

            offer[
                "buy_box_price"
            ] = buy_box.get(
                "price"
            )

    return offers


# ============================================================
# CAÇADOR PRINCIPAL
# ============================================================

def hunt_offers():

    print(
        "\n=============================="
    )

    print(
        "[CAÇADOR] Iniciando..."
    )

    print(
        "=============================="
    )

    raw_offers = []

    # --------------------------------------------------------
    # PRIMEIRA FONTE:
    # página oficial de cupons
    # --------------------------------------------------------

    print(
        "[CUPONS] Consultando páginas públicas..."
    )

    try:

        hub_offers = search_coupon_hub()

        print(
            "[CUPONS] Encontrados:",
            len(hub_offers),
        )

        raw_offers.extend(
            hub_offers
        )

    except Exception as e:

        print(
            "[CUPONS HUB ERRO]",
            e,
        )

    # --------------------------------------------------------
    # SEGUNDA FONTE:
    # buscas públicas do Mercado Livre
    # --------------------------------------------------------

    for query in SEARCHES:

        print(
            "[BUSCA CUPOM]",
            query,
        )

        try:

            offers = (
                search_public_coupon_page(
                    query
                )
            )

            print(
                "[RESULTADO]",
                query,
                len(offers),
            )

            raw_offers.extend(
                offers
            )

        except Exception as e:

            print(
                "[BUSCA ERRO]",
                query,
                e,
            )

        # Evita sequência agressiva
        # de requisições.
        time.sleep(
            random.uniform(
                0.25,
                0.55,
            )
        )

    print(
        "[RAW CUPONS]",
        len(raw_offers),
    )

    # --------------------------------------------------------
    # Deduplicação
    # --------------------------------------------------------

    offers = deduplicate_coupon_offers(
        raw_offers
    )

    print(
        "[PRODUTOS ÚNICOS]",
        len(offers),
    )

    # --------------------------------------------------------
    # Ordenação
    # --------------------------------------------------------

    offers.sort(
        key=lambda x: (
            -x["discount"],
            -x[
                "effective_percent"
            ],
            x[
                "final_price"
            ],
        )
    )

    # --------------------------------------------------------
    # Limite final
    # --------------------------------------------------------

    offers = offers[:80]

    # --------------------------------------------------------
    # Enriquece somente os primeiros
    # para não sobrecarregar API.
    # --------------------------------------------------------

    offers = enrich_with_catalog(
        offers[:40]
    )

    print(
        "[FINAL]",
        len(offers),
        "ofertas",
    )

    return offers


# ============================================================
# DIAGNOSTICO
# ============================================================

@app.route("/api/ml-diagnostic")
def ml_diagnostic():

    result = {
        "config": {
            "client_id":
                bool(ML_CLIENT_ID),
            "client_secret":
                bool(ML_CLIENT_SECRET),
            "redirect_uri":
                ML_REDIRECT_URI,
        },
        "auth": None,
        "tests": [],
    }

    row = get_auth()

    if row:

        result["auth"] = {
            "connected": True,
            "user_id":
                row["user_id"],
            "nickname":
                row["nickname"],
            "expires_at":
                row["expires_at"],
        }

    else:

        result["auth"] = {
            "connected": False
        }

        return jsonify(
            result
        )

    # /users/me
    response, data = ml_get(
        "/users/me"
    )

    result["tests"].append(
        {
            "name": "users_me",
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
            "data": data,
        }
    )

    # product search
    response, data = ml_get(
        "/products/search",
        params={
            "site_id": "MLB",
            "q": "celular",
            "status": "active",
            "limit": 1,
        },
    )

    result["tests"].append(
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
                                [],
                            )[:1],
                    }
                    if isinstance(
                        data,
                        dict,
                    )
                    else data
                ),
        }
    )

    # produto de teste
    test_product_id = None

    if isinstance(
        data,
        dict,
    ):

        results = data.get(
            "results",
            [],
        )

        if results:

            test_product_id = (
                results[0]
            )

    if test_product_id:

        response, pdata = ml_get(
            f"/products/{test_product_id}"
        )

        result["tests"].append(
            {
                "name":
                    "product_detail",
                "product_id":
                    test_product_id,
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
                    pdata,
            }
        )

        response, idata = ml_get(
            f"/products/{test_product_id}/items",
            params={
                "limit": 5
            },
        )

        result["tests"].append(
            {
                "name":
                    "product_items",
                "product_id":
                    test_product_id,
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
                    idata,
            }
        )

    return jsonify(
        result
    )


# ============================================================
# API CAÇAR OFERTAS
# ============================================================

@app.route("/api/hunt")
def api_hunt():

    try:

        offers = hunt_offers()

        return jsonify(
            {
                "ok": True,
                "total":
                    len(offers),
                "offers":
                    offers,
            }
        )

    except Exception as e:

        print(
            "[HUNT ERRO]",
            e,
        )

        return jsonify(
            {
                "ok": False,
                "error":
                    str(e),
            }
        ), 500


# ============================================================
# API STATUS
# ============================================================

@app.route("/api/status")
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
# INTERFACE
# ============================================================

HTML = """
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
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;

    background: #f4f5f7;
    color: #222;
}

.header {
    background: #ffe600;
    padding: 18px 16px;
    box-shadow:
        0 2px 8px rgba(0,0,0,.12);
}

.header h1 {
    margin: 0;
    font-size: 23px;
    font-weight: 800;
}

.header p {
    margin: 5px 0 0;
    font-size: 13px;
}

.container {
    max-width: 1100px;
    margin: auto;
    padding: 18px;
}

.connection {
    background: white;
    border-radius: 14px;
    padding: 15px;
    margin-bottom: 15px;

    display: flex;
    align-items: center;
    justify-content: space-between;

    gap: 12px;
    flex-wrap: wrap;

    box-shadow:
        0 2px 10px rgba(0,0,0,.06);
}

.connected {
    color: #168a3a;
    font-weight: 700;
}

.disconnected {
    color: #c62828;
    font-weight: 700;
}

button,
.btn {
    border: 0;
    border-radius: 10px;
    padding: 12px 16px;
    cursor: pointer;

    font-weight: 700;
    text-decoration: none;
    display: inline-block;
}

.btn-yellow {
    background: #ffe600;
    color: #222;
}

.btn-dark {
    background: #222;
    color: white;
}

.btn-red {
    background: #e53935;
    color: white;
}

.hunt {
    width: 100%;
    font-size: 17px;
    padding: 17px;
    margin-bottom: 18px;
}

.stats {
    display: grid;
    grid-template-columns:
        repeat(4, 1fr);

    gap: 12px;
    margin-bottom: 18px;
}

.stat {
    background: white;
    border-radius: 14px;
    padding: 15px;

    box-shadow:
        0 2px 10px rgba(0,0,0,.06);
}

.stat-title {
    font-size: 12px;
    color: #777;
}

.stat-value {
    font-size: 24px;
    font-weight: 800;
    margin-top: 5px;
}

.status {
    background: #fff;
    border-radius: 12px;
    padding: 12px;
    margin-bottom: 18px;

    font-size: 13px;
    white-space: pre-wrap;
}

.grid {
    display: grid;

    grid-template-columns:
        repeat(auto-fill, minmax(280px, 1fr));

    gap: 15px;
}

.card {
    background: white;
    border-radius: 15px;
    padding: 16px;

    box-shadow:
        0 2px 12px rgba(0,0,0,.07);
}

.badge {
    display: inline-block;

    background: #dff7e5;
    color: #157333;

    padding: 6px 9px;
    border-radius: 8px;

    font-size: 12px;
    font-weight: 800;

    margin-bottom: 10px;
}

.title {
    font-size: 16px;
    font-weight: 750;

    line-height: 1.35;

    margin-bottom: 12px;
}

.price-old {
    color: #888;
    font-size: 13px;
    text-decoration: line-through;
}

.price {
    font-size: 24px;
    font-weight: 900;
    margin-top: 3px;
}

.final {
    color: #0b7a35;
    font-size: 20px;
    font-weight: 900;
    margin-top: 7px;
}

.savings {
    color: #087f23;
    font-weight: 800;
    margin-top: 7px;
}

.coupon {
    margin-top: 12px;
    padding: 10px;

    border-radius: 9px;

    background: #fff7d1;
    border: 1px solid #f2d64b;

    font-weight: 800;
}

.source {
    margin-top: 8px;
    color: #777;
    font-size: 11px;
}

.open {
    width: 100%;
    margin-top: 13px;

    background: #3483fa;
    color: white;

    text-align: center;
}

.empty {
    background: white;
    border-radius: 14px;
    padding: 30px;
    text-align: center;
    color: #777;
}

@media (max-width: 700px) {

    .stats {
        grid-template-columns:
            repeat(2, 1fr);
    }

    .container {
        padding: 12px;
    }

}

</style>

</head>

<body>

<div class="header">

    <h1>🤑 Caçador de Ofertas</h1>

    <p>
        Produtos acima de R$69,90 com cupom identificado
    </p>

</div>


<div class="container">

    <div class="connection">

        <div id="connection">
            Verificando Mercado Livre...
        </div>

        <div>

            <a
                class="btn btn-red"
                href="/mercadolivre/reconnect"
            >
                Reconectar
            </a>

        </div>

    </div>


    <button
        id="hunt"
        class="btn btn-yellow hunt"
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


async function loadStatus() {

    try {

        const response =
            await fetch("/api/status");

        const data =
            await response.json();

        const box =
            document.getElementById(
                "connection"
            );

        if (data.connected) {

            box.innerHTML =
                "🟢 Mercado Livre conectado como " +
                "<strong>" +
                (
                    data.nickname ||
                    data.user_id ||
                    ""
                ) +
                "</strong>";

            box.className =
                "connected";

        } else {

            box.innerHTML =
                '🔴 Mercado Livre não conectado ' +
                '<a class="btn btn-yellow" href="/mercadolivre/login">' +
                'Conectar' +
                '</a>';

            box.className =
                "disconnected";
        }

    } catch (error) {

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
        offers.length === 0
    ) {

        results.innerHTML = `
            <div class="empty">
                <strong>
                    Nenhum produto com cupom encontrado.
                </strong>

                <br><br>

                O Mercado Livre pode ter bloqueado
                temporariamente a página pública ou
                não há cupons identificados neste momento.
            </div>
        `;

        return;
    }


    results.innerHTML =
        offers.map(
            (item) => `

            <div class="card">

                <div class="badge">
                    🎟️ CUPOM IDENTIFICADO
                </div>


                <div class="title">
                    ${escapeHtml(
                        item.title
                    )}
                </div>


                <div class="price-old">
                    Preço encontrado:
                </div>


                <div class="price">
                    ${money(
                        item.price
                    )}
                </div>


                <div class="coupon">
                    ${escapeHtml(
                        item.coupon_label
                    )}
                </div>


                <div class="final">
                    Final estimado:
                    ${money(
                        item.final_price
                    )}
                </div>


                <div class="savings">
                    Economia estimada:
                    ${money(
                        item.discount
                    )}

                    (${Number(
                        item.effective_percent || 0
                    ).toFixed(1)}%)
                </div>


                <div class="source">
                    ⚠️ Cupom identificado na
                    listagem pública.
                    Confirme no checkout.
                </div>


                <a
                    class="btn open"
                    href="${escapeAttribute(
                        item.url
                    )}"
                    target="_blank"
                    rel="noopener"
                >
                    🛒 VER OFERTA
                </a>

            </div>

        `
        ).join("");
}


function escapeHtml(
    value
) {

    return String(
        value || ""
    )
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function escapeAttribute(
    value
) {

    return escapeHtml(
        value
    );
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
        "Consultando produtos e páginas públicas do Mercado Livre...";


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
                "Erro desconhecido"
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


        const totalDiscount =
            offers.reduce(
                (
                    total,
                    item
                ) =>
                    total +
                    Number(
                        item.discount ||
                        0
                    ),
                0
            );


        const totalFinal =
            offers.reduce(
                (
                    total,
                    item
                ) =>
                    total +
                    Number(
                        item.final_price ||
                        0
                    ),
                0
            );


        document.getElementById(
            "discount"
        ).innerText =
            money(
                totalDiscount
            );


        document.getElementById(
            "final"
        ).innerText =
            money(
                totalFinal
            );


        status.innerText =
            "✅ Caçada concluída: " +
            offers.length +
            " produto(s) único(s) com cupom identificado.";


        renderOffers(
            offers
        );


    } catch (error) {

        console.error(
            error
        );

        status.innerText =
            "❌ Erro: " +
            error.message;


        document.getElementById(
            "results"
        ).innerHTML = `
            <div class="empty">
                Erro ao caçar ofertas.
                <br><br>
                ${escapeHtml(
                    error.message
                )}
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
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
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
            "8080",
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )