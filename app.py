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
from html.parser import HTMLParser


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


# ============================================================
# HEADERS
# ============================================================

PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) "
        "AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) "
        "Version/18.6 Mobile/15E148 Safari/604.1"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
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

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


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
        word
        for word in text.split()
        if word not in remove
    ]

    return " ".join(
        words[:35]
    )


def extract_mlb_id(url):

    if not url:
        return None

    match = re.search(
        r"MLB[-_]?(\d{6,})",
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
        verifier.encode("ascii")
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

        if refresh_ml_token():

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
# CATALOGO
# ============================================================

def search_products(
    query,
    limit=20
):

    response, data = ml_get(
        "/products/search",
        params={
            "site_id":
                "MLB",

            "q":
                query,

            "status":
                "active",

            "limit":
                limit,
        }
    )

    if (
        not response
        or response.status_code != 200
    ):

        print(
            "[PRODUCT SEARCH ERRO]",
            query,
            response.status_code
            if response
            else None
        )

        return []

    return data.get(
        "results",
        []
    )


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
# PARSER DE CARDS DO MERCADO LIVRE
# ============================================================

class ProductCardParser(
    HTMLParser
):

    CARD_CLASSES = (
        "poly-card",
        "ui-search-result__wrapper",
        "ui-search-result",
    )

    def __init__(self):

        super().__init__(
            convert_charrefs=True
        )

        self.depth = 0

        self.cards = []

        self.active_cards = []

        self.active_anchor = None

        self.heading_stack = []

        self.active_heading = None

        self.old_price_depth = 0

    def handle_starttag(
        self,
        tag,
        attrs
    ):

        self.depth += 1

        attrs_dict = dict(
            attrs
        )

        classes = (
            attrs_dict.get(
                "class",
                ""
            )
            or ""
        ).lower()

        is_card = any(
            marker in classes
            for marker in self.CARD_CLASSES
        )

        if is_card:

            self.active_cards.append(
                {
                    "start_depth":
                        self.depth,

                    "text":
                        [],

                    "links":
                        [],

                    "headings":
                        [],

                    "old_prices":
                        [],
                }
            )

        if self.active_cards:

            card = self.active_cards[-1]

            if tag.lower() == "a":

                self.active_anchor = {
                    "href":
                        attrs_dict.get(
                            "href",
                            ""
                        ),

                    "title":
                        attrs_dict.get(
                            "title",
                            ""
                        ),

                    "aria":
                        attrs_dict.get(
                            "aria-label",
                            ""
                        ),

                    "text":
                        [],
                }

            if tag.lower() in (
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
            ):

                self.active_heading = {
                    "tag":
                        tag.lower(),

                    "text":
                        [],
                }

                self.heading_stack.append(
                    self.active_heading
                )

            if tag.lower() in (
                "s",
                "del",
            ):

                self.old_price_depth += 1

    def handle_endtag(
        self,
        tag
    ):

        tag = tag.lower()

        if (
            tag == "a"
            and self.active_anchor
        ):

            anchor = self.active_anchor

            anchor["text"] = clean_text(
                " ".join(
                    anchor["text"]
                )
            )

            if self.active_cards:

                self.active_cards[
                    -1
                ]["links"].append(
                    anchor
                )

            self.active_anchor = None

        if (
            tag in (
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
            )
            and self.heading_stack
        ):

            heading = (
                self.heading_stack.pop()
            )

            heading["text"] = clean_text(
                " ".join(
                    heading["text"]
                )
            )

            if self.active_cards:

                self.active_cards[
                    -1
                ]["headings"].append(
                    heading
                )

            self.active_heading = (
                self.heading_stack[-1]
                if self.heading_stack
                else None
            )

        if tag in (
            "s",
            "del",
        ):

            self.old_price_depth = max(
                0,
                self.old_price_depth - 1
            )

        # Fecha cards que começaram
        # neste nível.
        if self.active_cards:

            finished = []

            for index, card in enumerate(
                self.active_cards
            ):

                if (
                    self.depth
                    <= card[
                        "start_depth"
                    ]
                    and index
                    == len(
                        self.active_cards
                    ) - 1
                ):

                    finished.append(
                        card
                    )

            for card in finished:

                self.cards.append(
                    card
                )

                self.active_cards.pop()

        self.depth = max(
            0,
            self.depth - 1
        )

    def handle_data(
        self,
        data
    ):

        if not data:
            return

        if self.active_cards:

            card = self.active_cards[
                -1
            ]

            card["text"].append(
                data
            )

            if (
                self.old_price_depth
                > 0
            ):

                card[
                    "old_prices"
                ].append(
                    data
                )

        if self.active_anchor:

            self.active_anchor[
                "text"
            ].append(
                data
            )

        if self.active_heading:

            self.active_heading[
                "text"
            ].append(
                data
            )


# ============================================================
# PREÇO
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

        if value is not None:

            result.append(
                {
                    "value":
                        value,

                    "start":
                        match.start(),

                    "end":
                        match.end(),
                }
            )

    return result


def get_current_price(
    text,
    old_prices_text=""
):

    prices = extract_prices(
        text
    )

    if not prices:
        return None

    old_values = []

    for item in extract_prices(
        old_prices_text
    ):

        old_values.append(
            round(
                item["value"],
                2
            )
        )

    # Remove preços riscados.
    candidates = [
        item
        for item in prices
        if round(
            item["value"],
            2
        ) not in old_values
    ]

    if not candidates:

        candidates = prices

    # Ignora a parte de parcelamento.
    before_installment = re.split(
        r"\bou\s+R\$",
        text,
        maxsplit=1,
        flags=re.I
    )[0]

    prices_before_installment = (
        extract_prices(
            before_installment
        )
    )

    if prices_before_installment:

        filtered = [
            item
            for item in
            prices_before_installment
            if round(
                item["value"],
                2
            ) not in old_values
        ]

        if filtered:
            candidates = filtered

    if not candidates:
        return None

    # O primeiro preço atual,
    # depois do preço antigo.
    return candidates[0][
        "value"
    ]


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
    # CUPOM 15% OFF
    # --------------------------------------------------------

    m = re.search(
        r"cupom\s+"
        r"(\d+(?:[.,]\d+)?)"
        r"\s*%\s*off",
        text,
        re.I
    )

    if m:

        return {
            "type":
                "percent",

            "value":
                float(
                    m.group(1).replace(
                        ",",
                        "."
                    )
                ),

            "label":
                "Cupom "
                + m.group(1).replace(
                    ",",
                    "."
                )
                + "% OFF",
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

        return {
            "type":
                "percent",

            "value":
                float(
                    m.group(1).replace(
                        ",",
                        "."
                    )
                ),

            "label":
                "Cupom "
                + m.group(1).replace(
                    ",",
                    "."
                )
                + "% OFF",
        }

    # --------------------------------------------------------
    # CUPOM R$ XX OFF
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

        if value:

            return {
                "type":
                    "fixed",

                "value":
                    value,

                "label":
                    "Cupom "
                    + money_br(
                        value
                    )
                    + " OFF",
            }

    return None


def detect_coupon_final_price(
    text
):

    # Exemplos:
    # R$1.605,60 com Cupom
    # R$426,64 com Cupom

    m = re.search(
        r"R\$\s*"
        r"(\d{1,3}(?:\.\d{3})*"
        r"(?:,\d{2})"
        r"|\d+(?:,\d{2}))"
        r"\s+com\s+cupom",
        text,
        re.I
    )

    if not m:
        return None

    return parse_money(
        m.group(1)
    )


# ============================================================
# TÍTULO
# ============================================================

def extract_title(
    card
):

    headings = card.get(
        "headings",
        []
    )

    candidates = []

    for heading in headings:

        text = clean_text(
            heading.get(
                "text",
                ""
            )
        )

        if len(text) >= 10:

            candidates.append(
                text
            )

    if candidates:

        candidates.sort(
            key=len,
            reverse=True
        )

        return candidates[0]

    links = card.get(
        "links",
        []
    )

    for link in links:

        text = clean_text(
            link.get(
                "text",
                ""
            )
        )

        if len(text) >= 15:

            if "cupom" not in (
                text.lower()
            ):

                return text

    text = clean_text(
        " ".join(
            card.get(
                "text",
                []
            )
        )
    )

    text = re.sub(
        r"cupom\s+\d+(?:[.,]\d+)?"
        r"\s*%\s*off",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"\d+(?:[.,]\d+)?"
        r"\s*%\s*off\s+com\s+cupom",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"r\$\s*[\d.,]+",
        "",
        text,
        flags=re.I
    )

    text = clean_text(
        text
    )

    return text[:250]


# ============================================================
# LINK
# ============================================================

def extract_product_url(
    card,
    source_url
):

    links = card.get(
        "links",
        []
    )

    possible = []

    for link in links:

        href = (
            link.get(
                "href",
                ""
            )
            or ""
        )

        title = clean_text(
            link.get(
                "title",
                ""
            )
        )

        aria = clean_text(
            link.get(
                "aria",
                ""
            )
        )

        text = clean_text(
            link.get(
                "text",
                ""
            )
        )

        if not href:
            continue

        if href.startswith("//"):

            href = (
                "https:"
                + href
            )

        elif href.startswith("/"):

            href = urljoin(
                source_url,
                href
            )

        score = 0

        if "MLB" in href.upper():
            score += 10

        if "mercadolivre.com.br" in (
            href.lower()
        ):
            score += 5

        if len(title) > 20:
            score += 2

        if len(text) > 20:
            score += 2

        if len(aria) > 20:
            score += 2

        possible.append(
            (
                score,
                href
            )
        )

    if not possible:
        return source_url

    possible.sort(
        reverse=True
    )

    return possible[0][1]


# ============================================================
# PROCESSA CARD
# ============================================================

def process_card(
    card,
    source_url
):

    text = clean_text(
        " ".join(
            card.get(
                "text",
                []
            )
        )
    )

    if "cupom" not in (
        text.lower()
    ):
        return None

    coupon = detect_coupon(
        text
    )

    if not coupon:
        return None

    old_prices_text = clean_text(
        " ".join(
            card.get(
                "old_prices",
                []
            )
        )
    )

    base_price = get_current_price(
        text,
        old_prices_text
    )

    if not valid_price(
        base_price
    ):
        return None

    coupon_final = (
        detect_coupon_final_price(
            text
        )
    )

    # --------------------------------------------------------
    # Se o Mercado Livre mostra diretamente:
    # R$ X com Cupom
    # --------------------------------------------------------

    if (
        coupon_final is not None
        and coupon_final < base_price
    ):

        final_price = (
            coupon_final
        )

        discount = (
            base_price
            - final_price
        )

        label = (
            f"{coupon['label']} "
            f"→ {money_br(final_price)}"
        )

    else:

        if coupon["type"] == "percent":

            discount = (
                base_price
                * coupon["value"]
                / 100
            )

        else:

            discount = min(
                coupon["value"],
                base_price
            )

        final_price = (
            base_price
            - discount
        )

        label = coupon[
            "label"
        ]

    if discount <= 0:
        return None

    title = extract_title(
        card
    )

    if not title:
        return None

    url = extract_product_url(
        card,
        source_url
    )

    product_id = extract_mlb_id(
        url
    )

    effective_percent = (
        discount
        / base_price
        * 100
    )

    return {
        "title":
            title,

        "price":
            round(
                base_price,
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
                effective_percent,
                2
            ),

        "coupon_label":
            label,

        "url":
            url,

        "product_id":
            product_id,

        "source":
            source_url,
    }


# ============================================================
# FALLBACK PARA BLOCOS DE CUPOM
# ============================================================

def fallback_coupon_blocks(
    page_html,
    source_url
):

    # Remove scripts/styles.
    cleaned = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        page_html,
        flags=re.I | re.S
    )

    cleaned = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        cleaned,
        flags=re.I | re.S
    )

    # Preserva separação de blocos.
    cleaned = re.sub(
        r"</(?:div|li|article|section|p|h1|h2|h3|h4)>",
        "\n",
        cleaned,
        flags=re.I
    )

    cleaned = re.sub(
        r"<[^>]+>",
        " ",
        cleaned
    )

    cleaned = html.unescape(
        cleaned
    )

    lines = []

    for line in cleaned.splitlines():

        line = clean_text(
            line
        )

        if line:
            lines.append(
                line
            )

    offers = []

    for index, line in enumerate(
        lines
    ):

        if "cupom" not in (
            line.lower()
        ):
            continue

        # A linha normalmente contém
        # produto + cupom + preço.
        window_lines = lines[
            max(0, index - 1):
            min(
                len(lines),
                index + 2
            )
        ]

        block = clean_text(
            " ".join(
                window_lines
            )
        )

        coupon = detect_coupon(
            block
        )

        if not coupon:
            continue

        prices = extract_prices(
            block
        )

        if not prices:
            continue

        # Para blocos públicos do tipo:
        # Cupom 15% OFF Produto R$733,82
        price = prices[-1][
            "value"
        ]

        if not valid_price(
            price
        ):
            continue

        final_direct = (
            detect_coupon_final_price(
                block
            )
        )

        if (
            final_direct
            and final_direct < price
        ):

            final_price = (
                final_direct
            )

        elif coupon["type"] == "percent":

            final_price = (
                price
                * (
                    1
                    - coupon["value"]
                    / 100
                )
            )

        else:

            final_price = (
                price
                - min(
                    coupon["value"],
                    price
                )
            )

        discount = (
            price
            - final_price
        )

        if discount <= 0:
            continue

        title = re.sub(
            r"cupom\s+"
            r"\d+(?:[.,]\d+)?"
            r"\s*%\s*off",
            "",
            block,
            flags=re.I
        )

        title = re.sub(
            r"\d+(?:[.,]\d+)?"
            r"\s*%\s*off\s+com\s+cupom",
            "",
            title,
            flags=re.I
        )

        title = re.sub(
            r"r\$\s*[\d.,]+",
            "",
            title,
            flags=re.I
        )

        title = clean_text(
            title
        )

        if len(title) < 10:
            continue

        offers.append(
            {
                "title":
                    title[:250],

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
                    coupon[
                        "label"
                    ],

                "url":
                    source_url,

                "product_id":
                    None,

                "source":
                    source_url,
            }
        )

    return offers


# ============================================================
# EXTRATOR PRINCIPAL
# ============================================================

def extract_coupon_offers(
    page_html,
    source_url
):

    parser = ProductCardParser()

    try:

        parser.feed(
            page_html
        )

    except Exception as e:

        print(
            "[HTML PARSER ERRO]",
            e
        )

    offers = []

    for card in parser.cards:

        try:

            item = process_card(
                card,
                source_url
            )

            if item:

                offers.append(
                    item
                )

        except Exception as e:

            print(
                "[CARD ERRO]",
                e
            )

    # Se o layout atual não
    # permitir detectar os cards,
    # usa o fallback.
    if not offers:

        offers = (
            fallback_coupon_blocks(
                page_html,
                source_url
            )
        )

    return offers


# ============================================================
# DOWNLOAD PÁGINA PÚBLICA
# ============================================================

def fetch_public_page(
    url
):

    try:

        response = requests.get(
            url,
            headers=PUBLIC_HEADERS,
            timeout=25,
            allow_redirects=True
        )

        print(
            "[PUBLIC]",
            response.status_code,
            url
        )

        if response.status_code != 200:

            return None

        return response.text

    except Exception as e:

        print(
            "[PUBLIC ERRO]",
            url,
            e
        )

        return None


# ============================================================
# BUSCA CUPONS POR CATEGORIA
# ============================================================

def search_public_coupons(
    query
):

    url = (
        "https://lista.mercadolivre.com.br/"
        + quote(
            query.strip(),
            safe=""
        )
    )

    page = fetch_public_page(
        url
    )

    if not page:
        return []

    offers = extract_coupon_offers(
        page,
        url
    )

    print(
        "[CUPONS]",
        query,
        "=>",
        len(offers)
    )

    return offers


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
# ENRIQUECER COM CATÁLOGO
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
                0.10
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
        "================================"
    )

    print(
        "🤑 CAÇADOR DE OFERTAS"
    )

    print(
        "================================"
    )

    raw_offers = []

    # --------------------------------------------------------
    # Busca pública.
    # --------------------------------------------------------

    for query in SEARCHES:

        try:

            offers = (
                search_public_coupons(
                    query
                )
            )

            raw_offers.extend(
                offers
            )

        except Exception as e:

            print(
                "[BUSCA ERRO]",
                query,
                e
            )

        time.sleep(
            0.20
        )

    print(
        "[RAW]",
        len(raw_offers)
    )

    # --------------------------------------------------------
    # Deduplica.
    # --------------------------------------------------------

    offers = deduplicate_offers(
        raw_offers
    )

    print(
        "[ÚNICOS]",
        len(offers)
    )

    # --------------------------------------------------------
    # Ordena pelo maior desconto
    # real em R$.
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

    # --------------------------------------------------------
    # Enriquecimento limitado.
    # --------------------------------------------------------

    offers = enrich_catalog(
        offers[:50]
    )

    # --------------------------------------------------------
    # Limite final.
    # --------------------------------------------------------

    offers = offers[:50]

    print(
        "[FINAL]",
        len(offers)
    )

    return offers


# ============================================================
# API STATUS
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
# API HUNT
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
# DIAGNÓSTICO
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

    # users/me
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

    # products/search
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

    font-size: 26px;

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

                Tente novamente em alguns instantes.

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
                    ⚠️ O cupom foi identificado
                    na listagem pública.
                    Confirme a aplicação no checkout.
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
        "Consultando produtos e cupons do Mercado Livre...";


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

@app.route("/health")
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