import os
import re
import json
import time
import base64
import hashlib
import secrets
import sqlite3
import threading
from urllib.parse import urlencode, quote

import requests
from flask import Flask, request, jsonify, redirect, render_template_string


# ============================================================
# APP
# ============================================================

app = Flask(__name__)

APP_NAME = "Cacador de Ofertas"

ML_API = "https://api.mercadolibre.com"
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"

SITE_ID = "MLB"

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()

ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

DATABASE = "ofertas.db"

REQUEST_TIMEOUT = 10

# ============================================================
# REGRAS DO CAÇADOR
# ============================================================

# Produto mínimo
MIN_PRODUCT_PRICE = 69.90

# Quantidade de produtos pesquisados por busca
MAX_PRODUCTS_PER_QUERY = 10

# Quantidade de vendedores por produto
MAX_ITEMS_PER_PRODUCT = 10

# Máximo de ofertas exibidas
MAX_FINAL_OFFERS = 80

# Tempo máximo aproximado da caça
MAX_SCAN_SECONDS = 150


# ============================================================
# CATEGORIAS
# ============================================================

CATEGORIES = {
    "📱 Celulares": [
        "celular smartphone",
        "iphone",
        "samsung galaxy",
        "xiaomi redmi",
        "motorola",
    ],

    "🌸 Perfumes": [
        "perfume masculino",
        "perfume feminino",
        "perfume importado",
        "kit perfume",
        "perfume original",
    ],

    "🏋️ Academia": [
        "tenis academia",
        "tenis corrida",
        "camiseta academia",
        "roupa academia masculina",
        "roupa academia feminina",
        "whey protein",
        "creatina",
        "suplemento",
    ],

    "🔧 Ferramentas": [
        "parafusadeira",
        "furadeira",
        "kit ferramentas",
        "chave de impacto",
        "ferramenta eletrica",
    ],

    "🎧 Eletrônicos": [
        "fone bluetooth",
        "smartwatch",
        "caixa de som bluetooth",
        "tablet",
        "monitor",
        "teclado mecanico",
        "mouse gamer",
    ],

    "🏠 Casa": [
        "aspirador",
        "air fryer",
        "liquidificador",
        "cafeteira",
        "organizador",
        "utensilios cozinha",
        "ventilador",
    ],

    "🚗 Automotivo": [
        "central multimidia",
        "camera de ré",
        "tapete automotivo",
        "acessorios carro",
        "lampada automotiva",
        "carregador veicular",
    ],

    "🍳 Cozinha": [
        "air fryer",
        "jogo de panelas",
        "panela",
        "liquidificador",
        "cafeteira",
        "sandwicheira",
    ],

    "👕 Moda": [
        "tenis masculino",
        "tenis feminino",
        "camiseta masculina",
        "calca masculina",
        "vestido feminino",
        "mochila",
    ],
}


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
})


# ============================================================
# OAUTH
# ============================================================

OAUTH_STATE = {}
OAUTH_LOCK = threading.Lock()


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT,
            updated_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT,
            product_id TEXT,
            title TEXT,
            category TEXT,
            seller_id TEXT,
            seller_nickname TEXT,
            product_price REAL,
            seller_discount REAL,
            coupon_code TEXT,
            coupon_percent REAL,
            coupon_fixed REAL,
            coupon_discount REAL,
            cash_discount REAL,
            total_discount REAL,
            effective_percent REAL,
            final_price REAL,
            final_total REAL,
            shipping REAL,
            free_shipping INTEGER,
            affiliate_url TEXT,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


init_db()


# ============================================================
# UTILITÁRIOS
# ============================================================

def now():
    return int(time.time())


def num(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        text = str(value).strip()

        text = text.replace("R$", "")
        text = text.replace("%", "")
        text = text.strip()

        if "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", ".")

        return float(text)

    except Exception:
        return default


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


def clean_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "")
    ).strip()


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


# ============================================================
# OAUTH PKCE
# ============================================================

def create_pkce():

    verifier = base64.urlsafe_b64encode(
        secrets.token_bytes(48)
    ).decode().rstrip("=")

    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(
            verifier.encode()
        ).digest()
    ).decode().rstrip("=")

    return verifier, challenge


def ml_token():

    conn = db()

    row = conn.execute(
        "SELECT * FROM oauth_tokens WHERE id=1"
    ).fetchone()

    conn.close()

    if not row:
        return None

    access = row["access_token"]
    expires_at = row["expires_at"] or 0

    if access and expires_at > now() + 120:
        return access

    refresh = row["refresh_token"]

    if not refresh:
        return access

    try:

        response = SESSION.post(
            ML_TOKEN,
            data={
                "grant_type": "refresh_token",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "refresh_token": refresh,
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = safe_json(response)

        if response.status_code != 200:
            return access

        new_access = data.get("access_token")

        if not new_access:
            return access

        new_refresh = data.get(
            "refresh_token",
            refresh
        )

        expires = int(
            data.get(
                "expires_in",
                21600
            )
        )

        conn = db()

        conn.execute("""
            UPDATE oauth_tokens
            SET access_token=?,
                refresh_token=?,
                expires_at=?,
                updated_at=?
            WHERE id=1
        """, (
            new_access,
            new_refresh,
            now() + expires,
            now(),
        ))

        conn.commit()
        conn.close()

        return new_access

    except Exception:
        return access


def ml_headers():

    token = ml_token()

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if token:
        headers["Authorization"] = (
            f"Bearer {token}"
        )

    return headers


# ============================================================
# OAUTH ROUTES
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():

    if not ML_CLIENT_ID:
        return (
            "ML_CLIENT_ID não configurado",
            500
        )

    verifier, challenge = create_pkce()

    state = secrets.token_urlsafe(32)

    with OAUTH_LOCK:
        OAUTH_STATE[state] = {
            "verifier": verifier,
            "created_at": now(),
        }

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
def ml_callback():

    error = request.args.get("error")

    if error:
        return f"""
        <h2>Erro no Mercado Livre</h2>
        <p>{error}</p>
        <p><a href="/">Voltar</a></p>
        """

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        return (
            "Código/state ausente",
            400
        )

    with OAUTH_LOCK:
        state_data = OAUTH_STATE.pop(
            state,
            None
        )

    if not state_data:
        return (
            "State inválido ou expirado",
            400
        )

    try:

        response = SESSION.post(
            ML_TOKEN,
            data={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": state_data["verifier"],
            },
            timeout=REQUEST_TIMEOUT,
        )

        data = safe_json(response)

        if response.status_code != 200:

            return f"""
            <h2>Erro ao conectar</h2>
            <pre>
            {json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            )}
            </pre>
            <a href="/">Voltar</a>
            """, 400

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

        user_id = ""
        nickname = ""

        try:

            me = SESSION.get(
                ML_API + "/users/me",
                headers={
                    "Authorization":
                    f"Bearer {access_token}"
                },
                timeout=REQUEST_TIMEOUT,
            )

            me_data = safe_json(me)

            user_id = str(
                me_data.get(
                    "id",
                    ""
                )
            )

            nickname = me_data.get(
                "nickname",
                ""
            )

        except Exception:
            pass

        conn = db()

        conn.execute("""
            INSERT INTO oauth_tokens (
                id,
                access_token,
                refresh_token,
                expires_at,
                user_id,
                nickname,
                updated_at
            )
            VALUES (1,?,?,?,?,?,?)
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
            now() + expires_in,
            user_id,
            nickname,
            now(),
        ))

        conn.commit()
        conn.close()

        return redirect("/")

    except Exception as e:

        return (
            f"Erro OAuth: {e}",
            500
        )


# ============================================================
# MERCADO LIVRE API
# ============================================================

def ml_get(
    path,
    params=None,
    timeout=REQUEST_TIMEOUT
):

    try:

        return SESSION.get(
            ML_API + path,
            headers=ml_headers(),
            params=params or {},
            timeout=timeout,
        )

    except Exception:
        return None


def product_search(
    query,
    limit=10
):

    response = ml_get(
        "/products/search",
        {
            "site_id": SITE_ID,
            "q": query,
            "status": "active",
            "limit": min(
                limit,
                50
            ),
        }
    )

    if not response:
        return []

    if response.status_code != 200:
        return []

    data = safe_json(response)

    results = data.get(
        "results",
        []
    )

    return (
        results
        if isinstance(results, list)
        else []
    )


def product_detail(product_id):

    response = ml_get(
        "/products/" +
        quote(
            str(product_id),
            safe=""
        )
    )

    if not response:
        return {}

    if response.status_code != 200:
        return {}

    return safe_json(response)


def product_items(product_id):

    response = ml_get(
        "/products/" +
        quote(
            str(product_id),
            safe=""
        ) +
        "/items",
        {
            "limit":
                MAX_ITEMS_PER_PRODUCT
        }
    )

    if not response:
        return []

    if response.status_code != 200:
        return []

    data = safe_json(response)

    results = data.get(
        "results",
        []
    )

    return (
        results
        if isinstance(results, list)
        else []
    )


# ============================================================
# CUPONS
# ============================================================

COUPON_PAGES = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/ofertas/cupons",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
]


def fetch_coupon_pages():

    pages = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 "
            "like Mac OS X) AppleWebKit/605.1.15 "
            "Version/17 Mobile/15E148 Safari/604.1"
        ),
        "Accept-Language":
            "pt-BR,pt;q=0.9",
    }

    for url in COUPON_PAGES:

        try:

            response = SESSION.get(
                url,
                headers=headers,
                timeout=15,
            )

            if response.status_code == 200:
                pages.append(
                    response.text
                )

        except Exception:
            continue

    return pages


def html_to_text(html):

    if not html:
        return ""

    # Mantém separação entre blocos HTML.
    html = re.sub(
        r"<br\s*/?>",
        "\n",
        html,
        flags=re.I
    )

    html = re.sub(
        r"</(div|p|li|section|article|h[1-6])>",
        "\n",
        html,
        flags=re.I
    )

    html = re.sub(
        r"<script\b[^>]*>.*?</script>",
        " ",
        html,
        flags=re.I | re.S
    )

    html = re.sub(
        r"<style\b[^>]*>.*?</style>",
        " ",
        html,
        flags=re.I | re.S
    )

    html = re.sub(
        r"<[^>]+>",
        " ",
        html
    )

    html = html.replace(
        "&nbsp;",
        " "
    )

    html = html.replace(
        "&amp;",
        "&"
    )

    html = html.replace(
        "&quot;",
        '"'
    )

    html = html.replace(
        "&#39;",
        "'"
    )

    lines = []

    for line in html.splitlines():

        line = clean_text(line)

        if line:
            lines.append(line)

    return "\n".join(lines)


# ============================================================
# VALIDAÇÃO RIGOROSA DE CUPOM
# ============================================================

def normalize_coupon_code(code):

    code = str(
        code or ""
    ).upper().strip()

    code = re.sub(
        r"[^A-Z0-9_-]",
        "",
        code
    )

    return code


def looks_like_real_coupon_code(code):

    """
    REGRA IMPORTANTE:

    Não aceita coisas como:

    1400W
    128GB
    220V
    500ML
    256GB

    como cupom.

    O código precisa ter:
    - pelo menos 6 caracteres
    - letras e números
    - pelo menos 2 letras
    - estar em contexto explícito de cupom
    """

    code = normalize_coupon_code(
        code
    )

    if len(code) < 6:
        return False

    if len(code) > 30:
        return False

    letters = re.findall(
        r"[A-Z]",
        code
    )

    digits = re.findall(
        r"\d",
        code
    )

    if len(letters) < 2:
        return False

    if len(digits) < 1:
        return False

    # Bloqueia padrões típicos de especificação
    # de produto/modelo.
    bad_suffixes = (
        "GB",
        "TB",
        "MB",
        "ML",
        "CM",
        "MM",
        "KG",
        "G",
        "W",
        "V",
        "HZ",
        "MP",
        "MPH",
        "MAH",
    )

    if code.endswith(
        bad_suffixes
    ):
        return False

    # Não aceita somente números/letras
    # de formato muito parecido com especificação.
    if re.fullmatch(
        r"\d{3,5}[A-Z]{1,3}",
        code
    ):
        return False

    return True


def find_coupon_code_from_context(
    context
):

    context = clean_text(
        context
    )

    if not context:
        return None

    # ========================================================
    # FORMATO 1
    # "Cupom: P4NOR4M1C"
    # "Cupom P4NOR4M1C"
    # "Código: P4NOR4M1C"
    # ========================================================

    patterns = [

        r"\b(?:cupom|código|codigo)"
        r"\s*(?:promocional)?"
        r"\s*[:\-]?\s*"
        r"([A-Z0-9][A-Z0-9_-]{5,29})\b",

        r"\b([A-Z0-9][A-Z0-9_-]{5,29})\b"
        r"\s*(?:é\s*)?"
        r"(?:cupom|código|codigo)\b",

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            context,
            flags=re.I
        )

        if not match:
            continue

        code = normalize_coupon_code(
            match.group(1)
        )

        if looks_like_real_coupon_code(
            code
        ):
            return code

    # ========================================================
    # FORMATO 2
    # Procura candidatos somente em um trecho que
    # contenha explicitamente "cupom".
    # ========================================================

    if not re.search(
        r"\b(?:cupom|código|codigo)\b",
        context,
        flags=re.I
    ):
        return None

    candidates = re.findall(
        r"\b[A-Z0-9][A-Z0-9_-]{5,29}\b",
        context.upper()
    )

    for candidate in candidates:

        code = normalize_coupon_code(
            candidate
        )

        if looks_like_real_coupon_code(
            code
        ):
            return code

    return None


def extract_percent(context):

    values = []

    for match in re.finditer(
        r"(\d+(?:[.,]\d+)?)\s*%\s*(?:OFF|DE\s*DESCONTO|DE\s*DESC)",
        context,
        flags=re.I
    ):

        value = num(
            match.group(1)
        )

        if 1 <= value <= 100:
            values.append(
                value
            )

    # Alguns textos usam apenas "12%".
    if not values:

        for match in re.finditer(
            r"(\d+(?:[.,]\d+)?)\s*%",
            context
        ):

            value = num(
                match.group(1)
            )

            if 1 <= value <= 100:
                values.append(
                    value
                )

    return max(
        values
    ) if values else 0.0


def extract_fixed_discount(context):

    patterns = [

        r"R\$\s*([\d\.,]+)"
        r"\s*(?:OFF|DE\s*DESCONTO)",

        r"(?:desconto|economize|ganhe)"
        r"\s*(?:de)?\s*"
        r"R\$\s*([\d\.,]+)",

        r"R\$\s*([\d\.,]+)"
        r"\s*"
        r"(?:DE\s*)?"
        r"(?:DESCONTO|OFF)",

    ]

    values = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            context,
            flags=re.I
        ):

            value = num(
                match.group(1)
            )

            if 0 < value < 100000:
                values.append(
                    value
                )

    return max(
        values
    ) if values else 0.0


def extract_minimum(context):

    patterns = [

        r"(?:compra|compras|pedido)"
        r"\s*m[ií]nima?\s*(?:de)?"
        r"\s*R\$\s*([\d\.,]+)",

        r"(?:m[ií]nimo|min\.?)"
        r"\s*(?:de)?"
        r"\s*R\$\s*([\d\.,]+)",

        r"(?:a partir de)"
        r"\s*R\$\s*([\d\.,]+)",

        r"R\$\s*([\d\.,]+)"
        r"\s*(?:ou mais|em compras?)",

    ]

    values = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            context,
            flags=re.I
        ):

            value = num(
                match.group(1)
            )

            if value > 0:
                values.append(
                    value
                )

    return max(
        values
    ) if values else 0.0


def extract_maximum(context):

    patterns = [

        r"(?:m[aá]ximo|m[aá]x\.?|limite)"
        r"\s*(?:de|do desconto)?"
        r"\s*R\$\s*([\d\.,]+)",

        r"limitado\s*(?:a|em)"
        r"\s*R\$\s*([\d\.,]+)",

        r"at[eé]"
        r"\s*R\$\s*([\d\.,]+)"
        r"\s*(?:de desconto)?",

    ]

    values = []

    for pattern in patterns:

        for match in re.finditer(
            pattern,
            context,
            flags=re.I
        ):

            value = num(
                match.group(1)
            )

            if value > 0:
                values.append(
                    value
                )

    return max(
        values
    ) if values else 0.0


def coupon_contexts_from_text(text):

    """
    Em vez de procurar códigos aleatoriamente na página,
    procuramos SOMENTE blocos que tenham contexto explícito
    de cupom.

    Isso evita o problema anterior:
    "1400W" -> falso cupom.
    """

    text = text or ""

    contexts = []

    # Divide por linhas/blocos.
    blocks = re.split(
        r"\n+",
        text
    )

    for index, block in enumerate(blocks):

        block = clean_text(
            block
        )

        if not block:
            continue

        # Precisa mencionar cupom/código.
        if not re.search(
            r"\b(?:cupom|código|codigo)\b",
            block,
            flags=re.I
        ):
            continue

        context_parts = [
            block
        ]

        # Junta algumas linhas próximas.
        for offset in range(
            1,
            5
        ):

            next_index = (
                index + offset
            )

            if next_index >= len(
                blocks
            ):
                break

            next_block = clean_text(
                blocks[next_index]
            )

            if next_block:
                context_parts.append(
                    next_block
                )

        context = " ".join(
            context_parts
        )

        contexts.append(
            context[:2500]
        )

    # Também tenta blocos maiores quando
    # o HTML não separou corretamente.
    for match in re.finditer(
        r"(?:cupom|código|codigo)",
        text,
        flags=re.I
    ):

        start = max(
            0,
            match.start() - 300
        )

        end = min(
            len(text),
            match.end() + 1000
        )

        context = clean_text(
            text[start:end]
        )

        if context:
            contexts.append(
                context
            )

    return contexts


def parse_coupon_context(context):

    context = clean_text(
        context
    )

    if not context:
        return None

    # Precisa existir contexto explícito.
    if not re.search(
        r"\b(?:cupom|código|codigo)\b",
        context,
        flags=re.I
    ):
        return None

    code = find_coupon_code_from_context(
        context
    )

    if not code:
        return None

    percent = extract_percent(
        context
    )

    fixed = extract_fixed_discount(
        context
    )

    minimum = extract_minimum(
        context
    )

    maximum = extract_maximum(
        context
    )

    # ========================================================
    # MUITO IMPORTANTE:
    # Se não encontramos nenhuma informação de desconto
    # associada ao cupom, descartamos.
    # ========================================================

    if percent <= 0 and fixed <= 0:
        return None

    # Um cupom percentual não deve ter teto
    # maior que o próprio preço posteriormente.
    return {
        "code": code,
        "percent": round(
            percent,
            2
        ),
        "fixed": round(
            fixed,
            2
        ),
        "minimum": round(
            minimum,
            2
        ),
        "maximum": round(
            maximum,
            2
        ),
        "source_text": context,
    }


def parse_coupon_text(text):

    coupons = []

    contexts = coupon_contexts_from_text(
        text
    )

    for context in contexts:

        coupon = parse_coupon_context(
            context
        )

        if coupon:
            coupons.append(
                coupon
            )

    # ========================================================
    # DEDUPLICAÇÃO
    # ========================================================

    unique = {}

    for coupon in coupons:

        code = coupon["code"]

        key = (
            code,
            coupon["percent"],
            coupon["fixed"],
            coupon["minimum"],
            coupon["maximum"],
        )

        unique[key] = coupon

    # Agrupa pelo código e mantém a versão
    # mais completa.
    by_code = {}

    for coupon in unique.values():

        code = coupon["code"]

        current = by_code.get(
            code
        )

        if current is None:
            by_code[code] = coupon
            continue

        score_current = sum([
            bool(
                current["percent"]
            ),
            bool(
                current["fixed"]
            ),
            bool(
                current["minimum"]
            ),
            bool(
                current["maximum"]
            ),
        ])

        score_new = sum([
            bool(
                coupon["percent"]
            ),
            bool(
                coupon["fixed"]
            ),
            bool(
                coupon["minimum"]
            ),
            bool(
                coupon["maximum"]
            ),
        ])

        if score_new > score_current:
            by_code[code] = coupon

    return list(
        by_code.values()
    )


def get_coupons():

    coupons = []

    pages = fetch_coupon_pages()

    for html in pages:

        text = html_to_text(
            html
        )

        if not text:
            continue

        coupons.extend(
            parse_coupon_text(
                text
            )
        )

    # ========================================================
    # ÚLTIMA PROTEÇÃO CONTRA FALSO CUPOM
    # ========================================================

    valid = []

    for coupon in coupons:

        code = coupon.get(
            "code",
            ""
        )

        if not looks_like_real_coupon_code(
            code
        ):
            continue

        if (
            coupon.get("percent", 0) <= 0
            and
            coupon.get("fixed", 0) <= 0
        ):
            continue

        valid.append(
            coupon
        )

    # ========================================================
    # DEDUP FINAL
    # ========================================================

    unique = {}

    for coupon in valid:

        code = coupon["code"]

        current = unique.get(
            code
        )

        if current is None:
            unique[code] = coupon
            continue

        current_score = (
            bool(
                current.get("percent")
            )
            +
            bool(
                current.get("fixed")
            )
            +
            bool(
                current.get("minimum")
            )
            +
            bool(
                current.get("maximum")
            )
        )

        new_score = (
            bool(
                coupon.get("percent")
            )
            +
            bool(
                coupon.get("fixed")
            )
            +
            bool(
                coupon.get("minimum")
            )
            +
            bool(
                coupon.get("maximum")
            )
        )

        if new_score > current_score:
            unique[code] = coupon

    return list(
        unique.values()
    )


# ============================================================
# CÁLCULO DO CUPOM
# ============================================================

def calculate_coupon_discount(
    price,
    coupon
):

    price = num(
        price
    )

    if price < MIN_PRODUCT_PRICE:
        return 0.0

    minimum = num(
        coupon.get(
            "minimum"
        )
    )

    if (
        minimum > 0
        and
        price < minimum
    ):
        return 0.0

    percent = num(
        coupon.get(
            "percent"
        )
    )

    fixed = num(
        coupon.get(
            "fixed"
        )
    )

    maximum = num(
        coupon.get(
            "maximum"
        )
    )

    percent_discount = 0.0

    if percent > 0:

        percent_discount = (
            price *
            percent /
            100
        )

        if maximum > 0:
            percent_discount = min(
                percent_discount,
                maximum
            )

    fixed_discount = 0.0

    if fixed > 0:

        fixed_discount = min(
            fixed,
            price
        )

    # ========================================================
    # REGRA PRINCIPAL:
    # se houver uma alternativa percentual e uma fixa,
    # usa a que der maior desconto REAL em R$.
    # ========================================================

    discount = max(
        percent_discount,
        fixed_discount
    )

    return round(
        min(
            discount,
            price
        ),
        2
    )


# ============================================================
# DESCONTO À VISTA
# ============================================================

def detect_cash_discount(
    item,
    product_detail_data=None
):

    """
    Só aceita desconto de pagamento à vista/Pix
    quando a API trouxer explicitamente uma informação
    relacionada a isso.

    Não assume que todo Pix possui desconto.
    """

    item = item or {}
    product_detail_data = (
        product_detail_data or {}
    )

    candidates = []

    def scan(obj):

        if isinstance(obj, dict):

            for key, value in obj.items():

                key_low = str(
                    key
                ).lower()

                is_cash_key = any(
                    term in key_low
                    for term in [
                        "cash_discount",
                        "pix_discount",
                        "discount_pix",
                        "desconto_pix",
                        "cashdiscount",
                    ]
                )

                if is_cash_key:

                    if isinstance(
                        value,
                        (int, float)
                    ):

                        candidates.append(
                            float(value)
                        )

                    elif isinstance(
                        value,
                        str
                    ):

                        value_num = num(
                            value,
                            -1
                        )

                        if value_num >= 0:
                            candidates.append(
                                value_num
                            )

                if isinstance(
                    value,
                    (dict, list)
                ):
                    scan(value)

        elif isinstance(
            obj,
            list
        ):

            for value in obj:
                scan(value)

    scan(item)
    scan(product_detail_data)

    if not candidates:
        return 0.0

    return round(
        max(candidates),
        2
    )


# ============================================================
# FRETE
# ============================================================

def shipping_info(item):

    shipping = (
        item.get("shipping")
        or {}
    )

    cost = num(
        shipping.get(
            "cost"
        )
    )

    free = bool(
        shipping.get(
            "free_shipping"
        )
    )

    return cost, free


# ============================================================
# DESCONTO DO VENDEDOR
# ============================================================

def seller_discount(item):

    price = num(
        item.get(
            "price"
        )
    )

    original = num(
        item.get(
            "original_price"
        )
    )

    if (
        original > price
        and
        price > 0
    ):
        return round(
            original - price,
            2
        )

    return 0.0


# ============================================================
# AVALIAÇÃO
# ============================================================

def evaluate_coupon(
    item,
    product,
    coupon,
    category,
    detail=None
):

    price = num(
        item.get(
            "price"
        )
    )

    # Produto mínimo
    if price < MIN_PRODUCT_PRICE:
        return None

    coupon_discount = (
        calculate_coupon_discount(
            price,
            coupon
        )
    )

    # Sem desconto real = não entra.
    if coupon_discount <= 0:
        return None

    shipping, free_shipping = (
        shipping_info(
            item
        )
    )

    cash_discount = (
        detect_cash_discount(
            item,
            detail
        )
    )

    # ========================================================
    # Comparamos cupom x pagamento à vista.
    #
    # NÃO somamos automaticamente os dois.
    # Só seria possível somar se houver informação explícita
    # de que são cumulativos.
    # ========================================================

    coupon_final = max(
        0,
        price - coupon_discount
    )

    cash_final = max(
        0,
        price - cash_discount
    )

    if (
        cash_discount > coupon_discount
        and
        cash_discount > 0
    ):

        chosen_discount = (
            cash_discount
        )

        final_price = (
            cash_final
        )

        chosen_cash = True

    else:

        chosen_discount = (
            coupon_discount
        )

        final_price = (
            coupon_final
        )

        chosen_cash = False

    final_total = (
        final_price +
        shipping
    )

    effective_percent = (
        chosen_discount /
        price *
        100
        if price > 0
        else 0
    )

    return {

        "item_id":
            item.get(
                "item_id"
            ),

        "product_id":
            product.get(
                "id"
            )
            or
            product.get(
                "product_id"
            ),

        "title":
            product.get(
                "name"
            )
            or
            product.get(
                "title"
            )
            or
            item.get(
                "title"
            )
            or
            "Produto",

        "category":
            category,

        "seller_id":
            item.get(
                "seller_id"
            ),

        "product_price":
            round(
                price,
                2
            ),

        "seller_discount":
            seller_discount(
                item
            ),

        "coupon_code":
            coupon.get(
                "code",
                ""
            ),

        "coupon_percent":
            num(
                coupon.get(
                    "percent"
                )
            ),

        "coupon_fixed":
            num(
                coupon.get(
                    "fixed"
                )
            ),

        "coupon_discount":
            round(
                coupon_discount,
                2
            ),

        "cash_discount":
            round(
                cash_discount,
                2
            ),

        "chosen_cash":
            chosen_cash,

        "total_discount":
            round(
                chosen_discount,
                2
            ),

        "effective_percent":
            round(
                effective_percent,
                2
            ),

        "final_price":
            round(
                final_price,
                2
            ),

        "final_total":
            round(
                final_total,
                2
            ),

        "shipping":
            round(
                shipping,
                2
            ),

        "free_shipping":
            free_shipping,

        "coupon_minimum":
            num(
                coupon.get(
                    "minimum"
                )
            ),

        "coupon_maximum":
            num(
                coupon.get(
                    "maximum"
                )
            ),

        "thumbnail":
            item.get(
                "thumbnail"
            )
            or
            product.get(
                "thumbnail"
            )
            or
            "",

        "permalink":
            item.get(
                "permalink"
            )
            or
            product.get(
                "permalink"
            )
            or
            (
                "https://www.mercadolivre.com.br/"
                +
                str(
                    item.get(
                        "item_id",
                        ""
                    )
                )
            ),

        "coupon_source":
            coupon.get(
                "source_text",
                ""
            ),
    }


# ============================================================
# COMPARAÇÃO
# ============================================================

def better_offer(
    current,
    candidate
):

    if not current:
        return candidate

    if not candidate:
        return current

    # ========================================================
    # PRIMEIRO:
    # maior desconto REAL em R$
    # ========================================================

    if (
        candidate["total_discount"]
        >
        current["total_discount"]
    ):
        return candidate

    if (
        candidate["total_discount"]
        <
        current["total_discount"]
    ):
        return current

    # ========================================================
    # SEGUNDO:
    # maior percentual real
    # ========================================================

    if (
        candidate["effective_percent"]
        >
        current["effective_percent"]
    ):
        return candidate

    if (
        candidate["effective_percent"]
        <
        current["effective_percent"]
    ):
        return current

    # ========================================================
    # TERCEIRO:
    # menor preço final
    # ========================================================

    if (
        candidate["final_total"]
        <
        current["final_total"]
    ):
        return candidate

    if (
        candidate["final_total"]
        >
        current["final_total"]
    ):
        return current

    # ========================================================
    # QUARTO:
    # frete grátis
    # ========================================================

    if (
        candidate["free_shipping"]
        and
        not current["free_shipping"]
    ):
        return candidate

    return current


def best_offer_for_item(
    item,
    product,
    coupons,
    category,
    detail=None
):

    best = None

    # Analisa TODOS os cupons.
    for coupon in coupons:

        offer = evaluate_coupon(
            item,
            product,
            coupon,
            category,
            detail
        )

        if not offer:
            continue

        best = better_offer(
            best,
            offer
        )

    return best


# ============================================================
# DEDUP
# ============================================================

def deduplicate_offers(
    offers
):

    unique = {}

    for offer in offers:

        item_id = str(
            offer.get(
                "item_id"
            )
            or ""
        )

        if item_id:
            key = item_id
        else:
            key = (
                offer.get(
                    "product_id"
                ),
                offer.get(
                    "seller_id"
                ),
                offer.get(
                    "title"
                ),
            )

        current = unique.get(
            key
        )

        if current is None:
            unique[key] = offer
        else:
            unique[key] = better_offer(
                current,
                offer
            )

    return list(
        unique.values()
    )


# ============================================================
# RANKING
# ============================================================

def sort_offers(
    offers
):

    return sorted(
        offers,
        key=lambda x: (
            # 1. maior desconto em R$
            -num(
                x.get(
                    "total_discount"
                )
            ),

            # 2. maior economia percentual
            -num(
                x.get(
                    "effective_percent"
                )
            ),

            # 3. menor preço final
            num(
                x.get(
                    "final_total"
                )
            ),

            # 4. frete grátis
            -int(
                bool(
                    x.get(
                        "free_shipping"
                    )
                )
            ),

            # 5. menor preço original
            num(
                x.get(
                    "product_price"
                )
            ),
        )
    )


# ============================================================
# CAÇA COMPLETA
# ============================================================

def run_full_scan():

    started = time.time()

    print(
        "[CUPONS] Procurando cupons reais..."
    )

    coupons = get_coupons()

    print(
        f"[CUPONS] {len(coupons)} cupons válidos encontrados."
    )

    for coupon in coupons:
        print(
            "[CUPOM]",
            coupon["code"],
            "|",
            coupon["percent"],
            "%",
            "| FIXO",
            coupon["fixed"],
            "| MIN",
            coupon["minimum"],
            "| MAX",
            coupon["maximum"]
        )

    if not coupons:

        return {
            "ok": False,
            "message": (
                "Nenhum cupom real foi identificado. "
                "O sistema bloqueou códigos suspeitos "
                "para evitar falsos cupons."
            ),
            "offers": [],
            "coupons": [],
        }

    all_offers = []

    seen_products = set()

    for category, queries in CATEGORIES.items():

        print(
            f"[CATEGORIA] {category}"
        )

        for query in queries:

            if (
                time.time() -
                started
                >
                MAX_SCAN_SECONDS
            ):
                break

            print(
                "[BUSCA]",
                query
            )

            products = product_search(
                query,
                MAX_PRODUCTS_PER_QUERY
            )

            for product in products:

                if (
                    time.time() -
                    started
                    >
                    MAX_SCAN_SECONDS
                ):
                    break

                product_id = (
                    product.get(
                        "id"
                    )
                    or
                    product.get(
                        "product_id"
                    )
                )

                if not product_id:
                    continue

                product_id = str(
                    product_id
                )

                if product_id in seen_products:
                    continue

                seen_products.add(
                    product_id
                )

                detail = product_detail(
                    product_id
                )

                items = product_items(
                    product_id
                )

                if not items:
                    continue

                for item in items:

                    price = num(
                        item.get(
                            "price"
                        )
                    )

                    # REGRA:
                    # produto precisa ser >= R$ 69,90
                    if (
                        price <
                        MIN_PRODUCT_PRICE
                    ):
                        continue

                    best = best_offer_for_item(
                        item,
                        product,
                        coupons,
                        category,
                        detail
                    )

                    if best:

                        print(
                            "[OFERTA]",
                            best["title"],
                            "|",
                            brl(
                                best["product_price"]
                            ),
                            "| CUPOM",
                            best["coupon_code"],
                            "| DESCONTO",
                            brl(
                                best["total_discount"]
                            )
                        )

                        all_offers.append(
                            best
                        )

    all_offers = deduplicate_offers(
        all_offers
    )

    all_offers = sort_offers(
        all_offers
    )

    all_offers = all_offers[
        :MAX_FINAL_OFFERS
    ]

    save_offers(
        all_offers
    )

    return {
        "ok": True,
        "message":
            "Caça finalizada",
        "offers":
            all_offers,
        "coupons":
            coupons,
        "elapsed":
            round(
                time.time() -
                started,
                1
            ),
    }


# ============================================================
# BANCO
# ============================================================

def save_offers(
    offers
):

    conn = db()

    conn.execute(
        "DELETE FROM ofertas"
    )

    for offer in offers:

        conn.execute("""
            INSERT INTO ofertas (
                item_id,
                product_id,
                title,
                category,
                seller_id,
                seller_nickname,
                product_price,
                seller_discount,
                coupon_code,
                coupon_percent,
                coupon_fixed,
                coupon_discount,
                cash_discount,
                total_discount,
                effective_percent,
                final_price,
                final_total,
                shipping,
                free_shipping,
                affiliate_url,
                created_at
            )
            VALUES (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?,?
            )
        """, (
            offer.get(
                "item_id"
            ),

            offer.get(
                "product_id"
            ),

            offer.get(
                "title"
            ),

            offer.get(
                "category"
            ),

            str(
                offer.get(
                    "seller_id"
                )
                or ""
            ),

            "",

            offer.get(
                "product_price"
            ),

            offer.get(
                "seller_discount"
            ),

            offer.get(
                "coupon_code"
            ),

            offer.get(
                "coupon_percent"
            ),

            offer.get(
                "coupon_fixed"
            ),

            offer.get(
                "coupon_discount"
            ),

            offer.get(
                "cash_discount"
            ),

            offer.get(
                "total_discount"
            ),

            offer.get(
                "effective_percent"
            ),

            offer.get(
                "final_price"
            ),

            offer.get(
                "final_total"
            ),

            offer.get(
                "shipping"
            ),

            int(
                bool(
                    offer.get(
                        "free_shipping"
                    )
                )
            ),

            offer.get(
                "permalink"
            ),

            now(),
        ))

    conn.commit()
    conn.close()


# ============================================================
# STATUS
# ============================================================

@app.route("/health")
def health():

    connected = bool(
        ml_token()
    )

    return jsonify({
        "status": "ok",
        "app": APP_NAME,
        "mercadolivre": connected,
        "minimum_product_price":
            MIN_PRODUCT_PRICE,
    })


@app.route("/api/status")
def api_status():

    token = ml_token()

    connected = bool(
        token
    )

    user = {}

    conn = db()

    row = conn.execute(
        """
        SELECT user_id,nickname
        FROM oauth_tokens
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    if row:

        user = {
            "id":
                row["user_id"],
            "nickname":
                row["nickname"],
        }

    return jsonify({
        "connected":
            connected,
        "user":
            user,
    })


# ============================================================
# CUPONS
# ============================================================

@app.route("/api/coupons")
def api_coupons():

    coupons = get_coupons()

    return jsonify({
        "ok": True,
        "count":
            len(coupons),
        "coupons":
            coupons,
    })


# ============================================================
# CAÇAR
# ============================================================

@app.route("/api/cacar")
def api_cacar():

    result = run_full_scan()

    return jsonify(
        result
    )


# ============================================================
# BUSCA MANUAL
# ============================================================

@app.route("/api/search")
def api_search():

    query = clean_text(
        request.args.get(
            "q",
            ""
        )
    )

    if not query:

        return jsonify({
            "ok": False,
            "message":
                "Informe uma busca.",
            "offers": [],
        }), 400

    coupons = get_coupons()

    products = product_search(
        query,
        20
    )

    offers = []

    for product in products:

        product_id = (
            product.get(
                "id"
            )
            or
            product.get(
                "product_id"
            )
        )

        if not product_id:
            continue

        detail = product_detail(
            product_id
        )

        items = product_items(
            product_id
        )

        for item in items:

            price = num(
                item.get(
                    "price"
                )
            )

            if (
                price <
                MIN_PRODUCT_PRICE
            ):
                continue

            best = best_offer_for_item(
                item,
                product,
                coupons,
                "🔎 Busca manual",
                detail
            )

            if best:
                offers.append(
                    best
                )

    offers = deduplicate_offers(
        offers
    )

    offers = sort_offers(
        offers
    )

    return jsonify({
        "ok": True,
        "query":
            query,
        "count":
            len(offers),
        "offers":
            offers[
                :MAX_FINAL_OFFERS
            ],
        "coupons":
            coupons,
    })


# ============================================================
# HISTÓRICO
# ============================================================

@app.route("/api/historico")
def historico():

    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM ofertas
        ORDER BY total_discount DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    offers = []

    for row in rows:

        offers.append({

            "item_id":
                row["item_id"],

            "product_id":
                row["product_id"],

            "title":
                row["title"],

            "category":
                row["category"],

            "product_price":
                row["product_price"],

            "seller_discount":
                row["seller_discount"],

            "coupon_code":
                row["coupon_code"],

            "coupon_percent":
                row["coupon_percent"],

            "coupon_fixed":
                row["coupon_fixed"],

            "coupon_discount":
                row["coupon_discount"],

            "cash_discount":
                row["cash_discount"],

            "total_discount":
                row["total_discount"],

            "effective_percent":
                row["effective_percent"],

            "final_price":
                row["final_price"],

            "final_total":
                row["final_total"],

            "shipping":
                row["shipping"],

            "free_shipping":
                bool(
                    row["free_shipping"]
                ),

            "permalink":
                row["affiliate_url"],
        })

    return jsonify({
        "ok": True,
        "offers":
            offers,
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
    content="width=device-width,initial-scale=1"
>

<title>Caçador de Ofertas</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #f5f6f8;
    color: #17202a;
    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

header {
    background: #ffe600;
    padding: 18px 16px;
    border-bottom:
        1px solid #e0c900;
}

.header-inner {
    max-width: 1150px;
    margin: auto;
}

h1 {
    margin: 0;
    font-size: 27px;
}

.subtitle {
    margin-top: 6px;
    font-size: 14px;
    color: #4a4a4a;
}

.container {
    max-width: 1150px;
    margin: auto;
    padding: 18px 14px 60px;
}

.topbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 15px;
}

button,
input {
    border: 0;
    border-radius: 10px;
    padding: 13px 15px;
    font-size: 15px;
}

button {
    cursor: pointer;
    font-weight: 700;
}

.primary {
    background: #3483fa;
    color: white;
}

.secondary {
    background: white;
    border: 1px solid #ddd;
}

.search {
    display: flex;
    flex: 1;
    min-width: 250px;
}

.search input {
    width: 100%;
    border: 1px solid #ddd;
}

.status {
    background: white;
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 15px;
    border: 1px solid #e5e5e5;
}

.warning {
    background: #fff8db;
    border: 1px solid #f0d66a;
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 15px;
    font-size: 14px;
}

.summary {
    display: grid;
    grid-template-columns:
        repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}

.stat {
    background: white;
    padding: 14px;
    border-radius: 12px;
    border: 1px solid #e5e5e5;
}

.stat b {
    display: block;
    font-size: 21px;
    margin-top: 5px;
}

.grid {
    display: grid;
    grid-template-columns:
        repeat(
            auto-fill,
            minmax(280px, 1fr)
        );
    gap: 14px;
}

.card {
    background: white;
    border-radius: 14px;
    border: 1px solid #e2e2e2;
    overflow: hidden;
}

.card-body {
    padding: 15px;
}

.card h3 {
    font-size: 17px;
    line-height: 1.35;
    margin: 0 0 12px;
}

.category {
    font-size: 12px;
    color: #777;
    margin-bottom: 8px;
}

.original {
    color: #777;
    text-decoration: line-through;
    font-size: 13px;
}

.price {
    font-size: 25px;
    font-weight: 800;
    margin-top: 4px;
}

.coupon {
    background: #e8f7ed;
    border:
        1px solid #b9e6c6;
    border-radius: 10px;
    padding: 13px;
    margin-top: 12px;
}

.coupon-title {
    font-size: 14px;
    color: #555;
}

.coupon-code {
    font-size: 20px;
    font-weight: 900;
    color: #16803c;
    margin-top: 4px;
}

.discount {
    font-size: 16px;
    font-weight: 800;
    color: #16803c;
    margin-top: 8px;
}

.effective {
    font-size: 14px;
    margin-top: 5px;
}

.final {
    margin-top: 12px;
    background: #fff0f0;
    border-radius: 10px;
    padding: 13px;
}

.final-label {
    font-size: 14px;
}

.final-price {
    font-size: 25px;
    font-weight: 900;
    color: #d70000;
    margin-top: 4px;
}

.cash {
    margin-top: 9px;
    color: #147a37;
    font-weight: 800;
}

.shipping {
    margin-top: 10px;
    font-size: 14px;
}

.seller-info {
    margin-top: 8px;
    font-size: 12px;
    color: #777;
}

.actions {
    margin-top: 14px;
}

.actions a {
    display: block;
    text-align: center;
    text-decoration: none;
    padding: 12px;
    border-radius: 9px;
    background: #3483fa;
    color: white;
    font-weight: 800;
}

.empty {
    background: white;
    padding: 30px;
    border-radius: 14px;
    text-align: center;
    grid-column: 1 / -1;
}

.loader {
    display: none;
    padding: 20px;
    text-align: center;
    background: white;
    border-radius: 12px;
    margin-bottom: 15px;
}

@media(max-width:700px) {

    .summary {
        grid-template-columns:
            repeat(2, 1fr);
    }

    h1 {
        font-size: 23px;
    }

}

</style>

</head>

<body>

<header>

<div class="header-inner">

<h1>
🔥 Caçador de Ofertas
</h1>

<div class="subtitle">
Cupons primeiro • produtos a partir de R$ 69,90
</div>

</div>

</header>

<div class="container">

<div class="topbar">

<button
    class="primary"
    onclick="cacar()"
>
🏹 CAÇAR OFERTAS
</button>

<button
    class="secondary"
    onclick="carregarCupons()"
>
🏷️ VER CUPONS
</button>

<div class="search">

<input
    id="search"
    placeholder="Pesquisar produto..."
>

<button
    class="primary"
    onclick="buscar()"
>
🔎 Buscar
</button>

</div>

</div>

<div
    id="status"
    class="status"
>
Carregando...
</div>

<div class="warning">

<b>🏷️ Como o caçador funciona:</b>

<br><br>

O produto precisa custar pelo menos
<b>R$ 69,90</b>.

<br><br>

O sistema analisa os cupons encontrados
e escolhe o que proporciona o
<b>maior desconto real em reais</b>
para cada produto.

<br><br>

Cupom percentual e cupom de valor fixo
são comparados pelo desconto REAL.

<br><br>

Desconto normal do vendedor
<b>não é considerado cupom</b>.

<br><br>

⚠️ O preço com cupom é uma estimativa.
A aplicação do benefício deve ser confirmada
no checkout do Mercado Livre.

</div>

<div
    id="summary"
    class="summary"
></div>

<div
    id="loader"
    class="loader"
>
🔥 Procurando produtos e comparando
cupons reais...
</div>

<div
    id="results"
    class="grid"
></div>

</div>


<script>

function money(value) {

    value = Number(
        value || 0
    );

    return value.toLocaleString(
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
    .replace(
        /&/g,
        "&amp;"
    )
    .replace(
        /</g,
        "&lt;"
    )
    .replace(
        />/g,
        "&gt;"
    )
    .replace(
        /"/g,
        "&quot;"
    )
    .replace(
        /'/g,
        "&#039;"
    );
}


async function atualizarStatus() {

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

            const nick =
                data.user?.nickname ||
                "";

            status.innerHTML =
                "🟢 <b>Mercado Livre conectado</b>" +
                (
                    nick
                    ?
                    " — " +
                    escapeHtml(
                        nick
                    )
                    :
                    ""
                );

        } else {

            status.innerHTML =
                "🔴 Mercado Livre não conectado — " +
                "<a href='/mercadolivre/login'>" +
                "Conectar Mercado Livre" +
                "</a>";
        }

    } catch (error) {

        document.getElementById(
            "status"
        ).innerHTML =
            "⚠️ Não foi possível consultar o status.";
    }
}


function renderSummary(
    offers
) {

    const summary =
        document.getElementById(
            "summary"
        );

    if (!offers.length) {

        summary.innerHTML = "";

        return;
    }

    const biggest =
        Math.max(
            ...offers.map(
                x =>
                    Number(
                        x.total_discount ||
                        0
                    )
            )
        );

    const lowest =
        Math.min(
            ...offers.map(
                x =>
                    Number(
                        x.final_total ||
                        x.final_price ||
                        0
                    )
            )
        );

    const average =
        offers.reduce(
            (
                sum,
                x
            ) =>
                sum +
                Number(
                    x.effective_percent ||
                    0
                ),
            0
        ) / offers.length;

    summary.innerHTML = `

        <div class="stat">
            Ofertas
            <b>
                ${offers.length}
            </b>
        </div>

        <div class="stat">
            Maior desconto
            <b>
                ${money(
                    biggest
                )}
            </b>
        </div>

        <div class="stat">
            Menor preço final
            <b>
                ${money(
                    lowest
                )}
            </b>
        </div>

        <div class="stat">
            Economia média
            <b>
                ${average.toFixed(1)}%
            </b>
        </div>

    `;
}


function renderOffers(
    offers
) {

    const results =
        document.getElementById(
            "results"
        );

    if (!offers.length) {

        results.innerHTML = `

            <div class="empty">

                <h3>
                    Nenhuma oferta encontrada
                </h3>

                <p>
                    Nenhum cupom real foi identificado
                    como aplicável aos produtos pesquisados.
                </p>

            </div>

        `;

        return;
    }

    results.innerHTML =
        offers.map(
            (
                offer,
                index
            ) => {

                const coupon =
                    escapeHtml(
                        offer.coupon_code
                    );

                const productPrice =
                    money(
                        offer.product_price
                    );

                const discount =
                    money(
                        offer.total_discount
                    );

                const finalTotal =
                    money(
                        offer.final_total ||
                        offer.final_price
                    );

                const shipping =
                    Number(
                        offer.shipping ||
                        0
                    );

                const effective =
                    Number(
                        offer.effective_percent ||
                        0
                    );

                const chosenCash =
                    Boolean(
                        offer.chosen_cash
                    );

                return `

                <div class="card">

                    <div class="card-body">

                        <div class="category">

                            ${escapeHtml(
                                offer.category
                            )}

                        </div>

                        <h3>

                            ${
                                index === 0
                                ? "🏆 "
                                : ""
                            }

                            ${escapeHtml(
                                offer.title
                            )}

                        </h3>

                        <div class="original">

                            Preço do produto:
                            ${productPrice}

                        </div>

                        <div class="coupon">

                            <div class="coupon-title">

                                🏷️ CUPOM

                            </div>

                            <div class="coupon-code">

                                ${coupon}

                            </div>

                            ${
                                offer.coupon_percent > 0
                                ?
                                `
                                <div>
                                    ${Number(
                                        offer.coupon_percent
                                    ).toFixed(0)}%
                                    OFF
                                </div>
                                `
                                :
                                ""
                            }

                            ${
                                offer.coupon_fixed > 0
                                ?
                                `
                                <div>
                                    ${money(
                                        offer.coupon_fixed
                                    )}
                                    OFF
                                </div>
                                `
                                :
                                ""
                            }

                            <div class="discount">

                                💰 Desconto real:
                                ${discount}

                            </div>

                            <div class="effective">

                                📊 Economia real:
                                <b>
                                    ${effective.toFixed(2)}%
                                </b>

                            </div>

                        </div>

                        ${
                            chosenCash
                            ?
                            `
                            <div class="cash">

                                💳 Melhor condição:
                                pagamento à vista

                            </div>
                            `
                            :
                            ""
                        }

                        <div class="final">

                            <div class="final-label">

                                🔥 PREÇO FINAL ESTIMADO

                            </div>

                            <div class="final-price">

                                ${finalTotal}

                            </div>

                        </div>

                        <div class="shipping">

                            ${
                                offer.free_shipping
                                ?
                                "🚚 Frete grátis"
                                :
                                (
                                    "🚚 Frete: " +
                                    money(
                                        shipping
                                    )
                                )
                            }

                        </div>

                        ${
                            Number(
                                offer.seller_discount ||
                                0
                            ) > 0
                            ?
                            `
                            <div class="seller-info">

                                ℹ️ O preço do vendedor
                                já possui
                                ${money(
                                    offer.seller_discount
                                )}
                                de desconto.
                                Isso é separado do cupom.

                            </div>
                            `
                            :
                            ""
                        }

                        <div class="actions">

                            <a
                                href="${escapeHtml(
                                    offer.permalink
                                )}"
                                target="_blank"
                                rel="noopener"
                            >

                                🛒 Ver produto

                            </a>

                        </div>

                    </div>

                </div>

                `;

            }
        ).join("");
}


async function cacar() {

    const loader =
        document.getElementById(
            "loader"
        );

    const results =
        document.getElementById(
            "results"
        );

    loader.style.display =
        "block";

    results.innerHTML = "";

    try {

        const response =
            await fetch(
                "/api/cacar"
            );

        const data =
            await response.json();

        if (!data.ok) {

            results.innerHTML = `

                <div class="empty">

                    ❌
                    ${escapeHtml(
                        data.message
                    )}

                </div>

            `;

            return;
        }

        renderSummary(
            data.offers || []
        );

        renderOffers(
            data.offers || []
        );

    } catch (error) {

        results.innerHTML = `

            <div class="empty">

                ❌ Erro ao realizar a caça.

            </div>

        `;

    } finally {

        loader.style.display =
            "none";
    }
}


async function buscar() {

    const input =
        document.getElementById(
            "search"
        );

    const query =
        input.value.trim();

    if (!query) {
        return;
    }

    const loader =
        document.getElementById(
            "loader"
        );

    loader.style.display =
        "block";

    try {

        const response =
            await fetch(
                "/api/search?q=" +
                encodeURIComponent(
                    query
                )
            );

        const data =
            await response.json();

        renderSummary(
            data.offers || []
        );

        renderOffers(
            data.offers || []
        );

    } catch (error) {

        document.getElementById(
            "results"
        ).innerHTML = `

            <div class="empty">

                ❌ Erro na busca.

            </div>

        `;

    } finally {

        loader.style.display =
            "none";
    }
}


async function carregarCupons() {

    const loader =
        document.getElementById(
            "loader"
        );

    const results =
        document.getElementById(
            "results"
        );

    loader.style.display =
        "block";

    try {

        const response =
            await fetch(
                "/api/coupons"
            );

        const data =
            await response.json();

        const coupons =
            data.coupons || [];

        if (!coupons.length) {

            results.innerHTML = `

                <div class="empty">

                    Nenhum cupom real foi identificado.

                </div>

            `;

            return;
        }

        results.innerHTML =
            coupons.map(
                coupon => `

                <div class="card">

                    <div class="card-body">

                        <div class="coupon">

                            <div class="coupon-title">

                                🏷️ CUPOM REAL

                            </div>

                            <div class="coupon-code">

                                ${escapeHtml(
                                    coupon.code
                                )}

                            </div>

                            ${
                                coupon.percent > 0
                                ?
                                `
                                <div class="discount">

                                    ${Number(
                                        coupon.percent
                                    ).toFixed(0)}%
                                    OFF

                                </div>
                                `
                                :
                                ""
                            }

                            ${
                                coupon.fixed > 0
                                ?
                                `
                                <div class="discount">

                                    ${money(
                                        coupon.fixed
                                    )}
                                    OFF

                                </div>
                                `
                                :
                                ""
                            }

                            ${
                                coupon.minimum > 0
                                ?
                                `
                                <div
                                    style="
                                    margin-top:8px;
                                    "
                                >

                                    Compra mínima:
                                    <b>
                                        ${money(
                                            coupon.minimum
                                        )}
                                    </b>

                                </div>
                                `
                                :
                                ""
                            }

                            ${
                                coupon.maximum > 0
                                ?
                                `
                                <div
                                    style="
                                    margin-top:5px;
                                    "
                                >

                                    Limite:
                                    <b>
                                        ${money(
                                            coupon.maximum
                                        )}
                                    </b>

                                </div>
                                `
                                :
                                ""
                            }

                        </div>

                    </div>

                </div>

                `
            ).join("");

    } catch (error) {

        results.innerHTML = `

            <div class="empty">

                ❌ Erro ao buscar cupons.

            </div>

        `;

    } finally {

        loader.style.display =
            "none";
    }
}


document
    .getElementById("search")
    .addEventListener(
        "keydown",
        function(event) {

            if (
                event.key === "Enter"
            ) {
                buscar();
            }

        }
    );


atualizarStatus();

</script>

</body>

</html>
'''


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return render_template_string(
        HTML
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