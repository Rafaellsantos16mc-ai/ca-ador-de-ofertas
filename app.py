VERSAO_CACADOR = "V67_AFILIADO_FALLBACK_E_LIMITES"
import random
import os
import sqlite3
import secrets
import hashlib
import base64
import json
import time
import re
import html as html_lib
import threading
import uuid
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlencode, quote, urlparse, parse_qs

import requests

# Processamento de imagem para melhorar as fotos antes do WhatsApp.
# Pillow é opcional: se não estiver instalado, o original continua sendo usado.
try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
except Exception:
    Image = None
    ImageEnhance = None
    ImageFilter = None
    ImageOps = None
from flask import Flask, request, redirect, session, jsonify, render_template_string

app = Flask(__name__)

# TESTE TEMPORARIO: somente as duas categorias de perfumes solicitadas.
# A ativação efetiva acontece logo após o CATALOG, preservando o catálogo
# completo no mesmo arquivo para reativação posterior.
TESTE_SOMENTE_PERFUMES = False
app.secret_key = os.getenv("FLASK_SECRET_KEY", "chave-cacador-ofertas")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "").strip()
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "").strip()
ML_REDIRECT_URI = os.getenv(
    "ML_REDIRECT_URI",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app/mercadolivre/callback"
).strip()

ML_API = "https://api.mercadolibre.com"

# ============================================================
# GERADOR AUTOMÁTICO DE LINK DE AFILIADO (SERVIDOR)
# ============================================================
# Esta integração usa a sessão do Mercado Livre fornecida por cookie.
# NÃO é uma API pública documentada do Mercado Livre. É um acesso ao
# endpoint interno que também é usado pelo fluxo do portal de afiliados.
#
# Para ativar no Railway:
#   ML_AFFILIATE_TAG=sara89164
#   ML_AFFILIATE_COOKIES=<Cookie ou JSON de cookies da sua sessão>
#
# Se ML_AFFILIATE_COOKIES não estiver configurado, o fluxo antigo
# (Safari + favorito) continua funcionando como fallback.
ML_AFFILIATE_TAG = os.getenv("ML_AFFILIATE_TAG", "sara89164").strip()
ML_AFFILIATE_COOKIES = os.getenv("ML_AFFILIATE_COOKIES", "").strip()
ML_AFFILIATE_TIMEOUT = int(os.getenv("ML_AFFILIATE_TIMEOUT", "25") or "25")
ML_AFFILIATE_URL = "https://www.mercadolivre.com.br/affiliate-program/api/v2/stripe/user/links"

# ============================================================
# WHATSAPP BOT
# ============================================================

WHATSAPP_BOT_URL = os.getenv(
    "WHATSAPP_BOT_URL",
    "https://whatsapp-bot-production-c647.up.railway.app"
).strip().rstrip("/")
WHATSAPP_BOT_KEY = os.getenv("WHATSAPP_BOT_KEY", "").strip()

# ============================================================
# IMAGEM NATURAL PARA WHATSAPP / OPENAI
# ============================================================

OPENAI_API_KEY = ""  # Desativado: o app usa somente fotos originais do Mercado Livre.
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2").strip() or "gpt-image-2"
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://ca-ador-de-ofertas-production-ad83.up.railway.app"
).strip().rstrip("/")
WHATSAPP_IMAGE_DIR = os.path.join("/tmp", "cacador_whatsapp_images")
os.makedirs(WHATSAPP_IMAGE_DIR, exist_ok=True)
ML_AUTH = "https://auth.mercadolivre.com.br/authorization"
ML_TOKEN = "https://api.mercadolibre.com/oauth/token"
SITE_ID = "MLB"
# Banco persistente: no Railway, monte um Volume em /data.
# Fora do Railway/sem /data gravável, mantém fallback local para não quebrar.
PERSISTENT_DATA_DIR = "/data" if os.path.isdir("/data") and os.access("/data", os.W_OK) else os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(PERSISTENT_DATA_DIR, "ofertas.db")
COUPONS_URL = "https://www.mercadolivre.com.br/l/promocoes"
COUPON_SOURCE_URLS = [
    "https://www.mercadolivre.com.br/l/promocoes",
    "https://www.mercadolivre.com.br/l/descontaco-cupons",
    "https://www.mercadolivre.com.br/ofertas/cupons",
]

# Fontes públicas de parceiros/curadores que divulgam cupons amplos
# usados em anúncios como "Cupom: MELIBAIXOU". Servem para descobrir
# códigos de campanha; a aplicação continua condicionada às regras.
AFFILIATE_COUPON_SOURCE_URLS = [
    "https://www.descontosml.com/cupons",
    "https://baixoubonito.com.br/cupons/mercado-livre",
    "https://www.meliuz.com.br/desconto/cupom-desconto-mercado-livre",
]

# Principais termos das páginas de exclusão informadas pelo Mercado Livre
# para cupons divulgados por afiliados.
AFFILIATE_COUPON_BLOCKLIST_TERMS = (
    "puma", "pandora", "mizuno", "dream fitness", "nike", "natura",
    "decathlon", "casas bahia", "olympikus", "under armour", "wct fitness",
    "converse", "pampers", "hp", "max titanium", "probiotica", "epay",
    "anker", "assai", "sony", "nespresso", "principia", "rockstar games",
    "stanley", "dewalt", "black & decker", "growth", "web continental",
    "krw bikes", "ogm bikes", "south bikes", "menegotti", "deca", "esab",
    "vonder", "razr", "tork tools", "nintendo", "playstation", "xbox",
    "steam", "spotify", "uber", "roblox", "level up", "fragrances",
    "fragrance",
)

AFFILIATE_COUPON_CACHE = {"at": 0.0, "coupons": []}
AFFILIATE_COUPON_CACHE_LOCK = threading.Lock()
MIN_PRODUCT_PRICE = 69.90

# Modo enxuto somente para "Buscar todas": reduz chamadas redundantes.
FAST_ALL_CATEGORIES = True

# Expansão da busca: o objetivo é formar um universo grande de candidatos
# antes dos filtros finais de preço, coerência, imagem e desconto.
# Com 6 categorias, 65 candidatos por categoria = até 390 candidatos
# para o enriquecimento, permitindo ultrapassar 230 ofertas quando houver
# estoque suficiente de anúncios promocionais.
SEARCH_TARGET_OFFERS = 230
SEARCH_CANDIDATES_PER_CATEGORY_ALL = 80
SEARCH_CANDIDATES_PER_CATEGORY_SINGLE = 500
SEARCH_RAW_POOL_PER_CATEGORY = 180
SEARCH_SEEDS_FAST_PER_CATEGORY = 10
SEARCH_RESULTS_PER_QUERY_FAST = 30
# V42: fonte primária de anúncios reais para categorias comuns.
# Em uma categoria isolada, consultamos uma amostra ampla dos micro-nichos
# diretamente em /sites/MLB/search para obter IDs MLB reais, em vez de
# depender somente do catálogo /products/search.
SEARCH_REAL_ITEM_QUERIES_SINGLE = 45
SEARCH_REAL_ITEM_QUERIES_ALL = 12
SEARCH_REAL_ITEM_RESULTS_PER_QUERY = 30

# ============================================================
# FILTRO RIGOROSO DE ALTO GIRO / QUALIDADE
# ============================================================
# Os filtros de Full, Gold/Platinum e 100 vendas foram removidos.
# A posição em Mais Vendidos, tendências e buscas continua sendo usada
# para ordenar os anúncios, mas esses três critérios não eliminam ofertas.
# Filtros de giro/reputação DESATIVADOS para não eliminar ofertas válidas.
# O Caçador continua validando que o anúncio é uma publicação real.
MIN_ITEM_SOLD_QUANTITY = 0
ALLOWED_POWER_SELLER_STATUS = set()
REQUIRE_FULL_LOGISTICS = False

_ITEM_QUALITY_CACHE = {}
_ITEM_QUALITY_CACHE_LOCK = threading.Lock()
_SELLER_QUALITY_CACHE = {}
_SELLER_QUALITY_CACHE_LOCK = threading.Lock()

# ============================================================
# CATÁLOGO AUTOMÁTICO
# ============================================================

CATALOG = {
    "📱 Tecnologia": [
        "iPhone 15", "iPhone 16", "iPhone 17", "iPhone 15 Pro", "iPhone 16 Pro", "iPhone 17 Pro",
        "Samsung Galaxy S", "Samsung Galaxy A", "Samsung Galaxy M",
        "Motorola Edge", "Motorola Moto G", "Xiaomi Redmi", "Xiaomi Poco", "Realme smartphone",
        "capas para celular", "carregador turbo", "carregador USB-C", "carregador sem fio",
        "cabos USB-C", "power bank", "fone Bluetooth", "AirPods", "headset gamer",
        "caixa de som JBL", "soundbar", "smartwatch", "tablet", "TV smart", "console videogame",
        "notebook", "MacBook", "monitor", "impressora",
    ],
    "🏠 Casa e Organização": [
        "guarda-roupa", "guarda roupa", "guarda-roupas", "potes herméticos", "prateleiras",
        "utensílios de cozinha", "air fryer", "aspirador de pó", "cafeteira", "liquidificador",
        "panelas elétricas", "lâmpadas LED", "lâmpadas inteligentes", "fitas LED", "jogos de cama",
    ],
    "💪 Academia & Fitness": [
        "coqueteleira", "coqueteleira fitness", "garrafa térmica 500ml", "garrafa térmica 750ml",
        "garrafa térmica 1 litro", "garrafa térmica até 1 litro", "whey protein", "creatina",
        "pré-treino", "hipercalórico", "proteína esportiva", "BCAA", "vitaminas esportivas", "isotônico",
    ],
    "💇 Saúde & Beleza": [
        "secador de cabelo", "chapinha", "escova secadora", "Wella", "L'Oréal", "Kérastase", "Truss", "Salon Line",
        "esmaltes", "kit manicure", "unhas em gel", "cabine UV unhas", "cabine LED unhas", "lixa elétrica unhas", "nail art",
        "protetor solar", "hidratante facial", "vitamina C facial", "niacinamida", "ácido hialurônico",
        "CeraVe", "La Roche-Posay", "Principia skincare", "Neutrogena", "Vichy",
        "barbeador", "aparador de barba", "kit barba", "hidratante corporal", "creme corporal", "esfoliante corporal",
        "desodorante", "massageador corporal", "depilador elétrico", "óleo corporal",
    ],
    "👕 Moda": [
        "camiseta Nike", "camiseta Adidas", "camiseta Puma", "camiseta Lacoste", "camiseta Calvin Klein",
        "camiseta Tommy Hilfiger", "camiseta Reserva", "camiseta Fila", "camiseta New Balance",
        "camiseta Hering", "camiseta Reebok", "camiseta Jordan", "camiseta Under Armour",
        "roupa casual", "jaqueta", "corta-vento", "moletom", "casaco", "bermuda", "shorts", "calça jeans", "jeans",
        "legging", "regata dry fit", "conjunto fitness", "vestido feminino", "blusa feminina", "cropped", "conjunto feminino",
        "camisa polo Lacoste", "camisa polo Tommy Hilfiger", "camisa polo Ralph Lauren", "camisa polo Nike", "camisa polo Adidas",
        "roupa de praia", "biquíni", "sunga", "maiô", "boné", "óculos de sol", "mochila", "carteira",
        "camiseta oversized", "roupa plus size feminina", "roupa plus size masculina",
    ],
    "👟 Tênis & Calçados": [
        "Nike tênis corrida", "Adidas tênis corrida", "Asics tênis corrida", "Mizuno tênis corrida",
        "Olympikus tênis corrida", "New Balance tênis corrida", "Fila tênis corrida", "Puma tênis corrida",
        "Nike tênis casual", "Adidas tênis casual", "Vans tênis casual", "Converse tênis casual",
        "Lacoste tênis casual", "Puma tênis casual", "Skechers tênis casual", "Nike LeBron", "Jordan tênis basquete",
        "Adidas Harden", "Under Armour Curry", "Vans skate", "Converse skate", "Nike SB", "DC Shoes skate",
        "Lacoste tênis premium", "Oakley tênis", "New Balance premium", "Converse premium",
        "Nike slide", "Adidas slide", "Puma slide", "Havaianas", "Rider", "Ipanema",
        "tênis feminino", "tênis masculino", "tênis infantil",
    ],
    "🌸 Perfumes": [
        "Natura Kaiak", "Natura Essencial", "Natura Luna", "Natura Homem", "Natura Una", "Natura Humor",
        "O Boticário Malbec", "Boticário Egeo", "Boticário Lily", "Boticário Coffee", "Boticário Quasar", "Boticário Zaad",
        "Eudora Club 6", "Eudora La Victorie", "Eudora Rouge", "Eudora Impression",
        "Dior Sauvage", "Dior Homme", "Carolina Herrera Good Girl", "Carolina Herrera 212 VIP",
        "Chanel perfume", "Yves Saint Laurent perfume", "Giorgio Armani perfume", "Versace Eros",
        "Paco Rabanne Invictus", "Paco Rabanne 1 Million", "Jean Paul Gaultier perfume", "Prada perfume",
        "Gucci perfume", "Valentino perfume", "Givenchy perfume", "Lancôme perfume", "Hugo Boss perfume",
        "Montblanc perfume", "Azzaro perfume", "Bvlgari perfume", "Burberry perfume", "Calvin Klein perfume", "Kenzo perfume",
        "perfume masculino", "perfume feminino", "perfume nacional", "perfume importado", "perfumes mais vendidos",
    ],
    "🌙 Perfumes Árabes": [
        "Lattafa Asad", "Lattafa Asad Zanzibar", "Lattafa Yara", "Lattafa Khamrah", "Lattafa Oud for Glory",
        "Lattafa Fakhar", "Lattafa Raghba", "Lattafa Najdia", "Lattafa Nebras", "Lattafa Teriaq",
        "Afnan 9PM", "Afnan Supremacy", "Afnan Turathi Blue", "Armaf Club de Nuit", "Armaf Odyssey",
        "Rasasi Hawas", "Rasasi Hawas Ice", "Rasasi Fattan", "Al Haramain Amber Oud", "Al Haramain L'Aventure",
        "Al Wataniah Sabah Al Ward", "Al Wataniah Kayaan Classic", "Maison Alhambra Detour Noir",
        "Maison Alhambra Kismet", "Maison Alhambra Porto Neroli", "Fragrance World perfume", "Paris Corner perfume",
        "French Avenue perfume", "Khadlaj perfume", "Zimaya perfume", "Ajmal perfume", "Swiss Arabian perfume",
        "Ard Al Zaafaran perfume", "Ahmed Al Maghribi perfume", "Orientica perfume", "Al Rehab perfume",
    ],
}


# ============================================================
# MODO COMPLETO# ============================================================
# MODO COMPLETO — TODAS AS CATEGORIAS ATIVAS
# ============================================================
# Mantemos TODO o restante do projeto no arquivo, mas durante este teste
# todas as categorias do catálogo ficam ativas no scanner, na busca manual,
# nos botões e no fluxo automático. O modo de teste exclusivo de perfumes
# fica desativado para que as demais categorias apareçam normalmente.

CATALOG_COMPLETO = dict(CATALOG)

if TESTE_SOMENTE_PERFUMES:
    _CATEGORIAS_TESTE_PERFUMES = (
        "🌸 Perfumes",
        "🌙 Perfumes Árabes",
    )
    CATALOG = {
        categoria: CATALOG_COMPLETO[categoria]
        for categoria in _CATEGORIAS_TESTE_PERFUMES
        if categoria in CATALOG_COMPLETO
    }

# ============================================================
# BANCO
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id INTEGER PRIMARY KEY CHECK(id=1),
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER,
            user_id TEXT,
            nickname TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ofertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT, item_id TEXT, title TEXT, permalink TEXT,
            price REAL, original_price REAL, discount REAL,
            seller_id TEXT, image TEXT, category_id TEXT,
            category_name TEXT, condition TEXT, listing_type_id TEXT,
            free_shipping INTEGER DEFAULT 0, shipping_cost REAL,
            total_price REAL, relevance_score REAL DEFAULT 0,
            affiliate_link TEXT, extra_earnings REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE, description TEXT, discount_percent REAL,
            fixed_discount REAL DEFAULT 0, min_purchase REAL, max_discount REAL, valid_until TEXT,
            source_url TEXT, conditions TEXT, active INTEGER DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_publicacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT UNIQUE,
            last_price REAL NOT NULL,
            last_permalink TEXT,
            last_title TEXT,
            published_count INTEGER DEFAULT 1,
            last_published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Compatibilidade com bancos criados pelas versões anteriores.
    try:
        conn.execute("ALTER TABLE cupons ADD COLUMN fixed_discount REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE cupons ADD COLUMN usage_limit INTEGER")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

# ============================================================
# UTILIDADES
# ============================================================

def json_safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [json_safe(x) for x in v]
    return str(v)

def brl(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "R$ 0,00"

def norm(s):
    if not s:
        return ""
    s = str(s).lower()
    trans = str.maketrans("áàãâäéèêëíìîïóòõôöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    s = s.translate(trans)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", s)).strip()

def discount(price, original):
    try:
        p, o = float(price), float(original)
        return round((1 - p / o) * 100, 2) if o > p > 0 else 0
    except Exception:
        return 0

def total(price, shipping):
    try:
        return round(float(price) + float(shipping or 0), 2)
    except Exception:
        return None

def model_name(title):
    if not title:
        return "Produto"
    t = re.sub(r"\b(novo|original|oficial|promoção|frete grátis)\b", "", str(title), flags=re.I)
    return re.sub(r"\s+", " ", t).strip()

def specs(title):
    if not title:
        return []
    t = str(title)
    out = []
    for x in re.findall(r"\b\d+(?:GB|TB)\b", t, re.I):
        x = x.upper()
        if x not in out:
            out.append(x)
    for x in re.findall(r"\b\d+\s*GB\s*(?:RAM|MEMORIA|DE MEMORIA)\b", t, re.I):
        x = re.sub(r"\s+", " ", x.upper())
        if x not in out:
            out.append(x)
    for x in re.findall(r"\b(?:2G|3G|4G|5G)\b", t, re.I):
        x = x.upper()
        if x not in out:
            out.append(x)
    for label, pattern in [
        ("Dual SIM", r"dual\s*sim"),
        ("NFC", r"\bnfc\b")
    ]:
        if re.search(pattern, t, re.I) and label not in out:
            out.append(label)
    return out

# ============================================================
# PERFIS / RELEVÂNCIA
# ============================================================

PROFILES = {
    "celular": {
        "strong": ["smartphone","iphone","galaxy","samsung","motorola","xiaomi","redmi","poco","realme"],
        "bad": ["capa","capinha","pelicula","suporte","ventosa","carregador","cabo","adaptador","bateria","case","holder"]
    },
    "perfume": {
        "strong": ["perfume","eau de parfum","eau de toilette","parfum"],
        "bad": ["frasco vazio","decant","amostra","porta perfume","refil vazio"]
    },
    "academia": {
        "strong": [
            "roupa", "camiseta", "short", "legging",
            "tenis", "corrida", "treino",
            "whey", "creatina", "pre treino",
            "suplemento", "suplementos"
        ],
        "bad": [
            "halter", "halteres", "anilha", "barra",
            "banco musculacao", "caneleira", "elastico",
            "adesivo", "capa", "suporte", "peca de reposicao"
        ]
    },
    "ferramenta": {
        "strong": ["furadeira","parafusadeira","esmerilhadeira","ferramenta","serra","impacto"],
        "bad": ["broca avulsa","peca","carvao","bateria avulsa","capa"]
    },
    "marcas": {
        "strong": [
            "camiseta", "camisa", "nike", "adidas", "puma", "lacoste",
            "tommy hilfiger", "calvin klein", "levi", "fila", "under armour",
            "new balance", "hering", "reserva"
        ],
        "bad": [
            "falsa", "falsificada", "replica", "réplica", "pirata",
            "segunda linha", "inspirada", "similar", "sem etiqueta"
        ]
    },
}

def is_requested_product(title, query, category=None):
    """Filtra acessórios/peças e garante que o título pertence à categoria pedida."""
    t = norm(title)
    qn = norm(query)
    cat = category or query_category(query) or _demand_category_from_text(title)

    # Itens que normalmente contaminam buscas de produto principal.
    generic_bad = [
        "capa", "capinha", "pelicula", "película", "suporte", "holder",
        "cabo", "adaptador", "adesivo", "peca de reposicao", "peca avulsa",
        "refil vazio", "frasco vazio", "amostra", "decant", "decants", "miniatura",
        "contratipo", "contratipos", "pingente", "chaveiro", "brinde", "molde", "manual digital",
        "pelucia", "pelúcia", "plush", "bichinho de pelucia", "bicho de pelucia",
        "boneco", "boneca", "brinquedo", "action figure", "figura de acao", "figura de ação",
        "almofada", "pantufa de pelucia", "enfeite", "decorativo", "colecionavel", "colecionável"
    ]

    category_rules = {
        "📱 Tecnologia": (
            ["iphone", "celular", "smartphone", "samsung", "motorola", "xiaomi", "redmi", "poco", "realme",
             "fone", "headset", "smartwatch", "tablet", "notebook", "macbook", "monitor", "teclado", "mouse",
             "carregador", "power bank", "caixa de som", "impressora"],
            []
        ),
        "🏠 Casa e Organização": (
            ["guarda roupa", "guarda-roupa", "pote", "prateleira", "utensilio", "air fryer", "aspirador",
             "cafeteira", "liquidificador", "panela eletrica", "lampada", "fita led", "jogo de cama"],
            []
        ),
        "💪 Academia & Fitness": (
            ["coqueteleira", "garrafa termica", "garrafa térmica", "whey", "creatina", "pre treino", "hipercalorico",
             "proteina", "bcaa", "vitamina esportiva", "isotonico", "isotônico", "suplemento"],
            []
        ),
        "💇 Saúde & Beleza": (
            ["cabelo", "shampoo", "condicionador", "máscara capilar", "manicure", "esmalte", "unhas", "skincare",
             "facial", "serum", "protetor solar", "barbearia", "barbeador", "aparador", "barba", "hidratante",
             "corporal", "esfoliante", "depilador"],
            ["falsificado", "falsa", "falsificada", "replica", "réplica", "pirata", "segunda linha"]
        ),
        "👕 Moda": (
            ["camiseta", "camisa esportiva", "futebol", "jaqueta", "corta vento", "bermuda", "short", "calça", "jeans",
             "roupa fitness", "roupa esportiva", "legging", "moletom", "casaco", "moda feminina", "vestido", "blusa",
             "polo", "moda praia", "biquíni", "sunga", "maiô", "boné", "nike", "adidas", "puma", "lacoste",
             "tommy hilfiger", "calvin klein", "new balance", "under armour", "fila", "reserva", "hering"],
            ["falsificado", "falsa", "falsificada", "replica", "réplica", "pirata", "segunda linha", "camisa social",
             "camisa social manga longa", "social manga longa", "social de manga longa"]
        ),
        "👟 Tênis & Calçados": (
            ["tênis", "tenis", "calçado", "calcado", "sapato", "sapatênis", "sapatenis", "mocassim",
             "bota", "coturno", "sandália", "sandalia", "chinelo", "slide", "crocs", "nike", "adidas",
             "puma", "asics", "new balance", "mizuno", "olympikus", "fila", "reebok", "vans", "converse",
             "under armour", "skechers", "oakley", "lacoste", "brooks", "saucony", "hoka", "salomon",
             "columbia", "timberland", "democrata", "ferracini", "pegada", "freeway", "west coast",
             "kildare", "moleca", "vizzano", "beira rio", "modare", "anacapri", "arezzo", "schutz",
             "bottero", "dakota", "via marte", "ramarim", "usaflex", "corrida", "academia", "casual",
             "basquete", "trilha", "adventure", "skate", "premium"],
            ["falsificado", "falsa", "falsificada", "replica", "réplica", "pirata", "segunda linha",
             "chuteira", "trava society", "trava campo", "bola de futebol", "bola de basquete", "bola de vôlei"]
        ),
        "🌙 Perfumes Árabes": (
            list(ARABIC_PERFUME_TERMS) + ["perfume árabe", "perfume arabe", "eau de parfum", "parfum"],
            list(PERFUME_EXCLUDED_TERMS) + ["kit", "combo", "duo", "trio", "conjunto", "pack"]
        ),
    }

    strong, bad = category_rules.get(cat, ([], generic_bad))
    # Bloqueio global de brinquedos/itens de pelúcia, mesmo quando o título
    # contém marcas/modelos como Nike ou Air Jordan.
    non_product_terms = (
        "pelucia", "pelúcia", "plush", "bichinho de pelucia", "bicho de pelucia",
        "boneco", "boneca", "brinquedo", "action figure", "figura de acao", "figura de ação",
        "almofada", "pantufa de pelucia", "enfeite", "decorativo", "colecionavel", "colecionável"
    )
    if any(x in t for x in non_product_terms):
        return False
    if any(x in t for x in bad):
        return False

    # Moda: excluir camisas sociais tradicionais, especialmente manga longa.
    if cat == "👕 Moda" and any(x in t for x in (
        "camisa social", "social manga longa", "social de manga longa", "camisa manga longa social"
    )):
        return False

    # Para consultas específicas, exige que pelo menos uma palavra/expressão
    # importante da consulta apareça no título.
    q_terms = [x for x in qn.split() if len(x) >= 4]
    if q_terms and not any(x in t for x in q_terms):
        # A categoria ainda pode validar o produto quando a consulta é um
        # termo genérico como "smartphone" ou "perfume".
        if not any(x in t for x in strong):
            return False

    if strong and not any(x in t for x in strong):
        return False

    return True


def query_category(q):
    """Retorna a categoria do catálogo que corresponde à consulta."""
    nq = norm(q)
    if not nq:
        return None

    # Primeiro procura a consulta exata entre as buscas cadastradas.
    for category, queries in CATALOG.items():
        for item in queries:
            if nq == norm(item):
                return category

    # Depois usa correspondência por palavras para consultas extras.
    best_category = None
    best_score = 0
    q_words = {w for w in nq.split() if len(w) >= 3}
    for category, queries in CATALOG.items():
        for item in queries:
            iw = {w for w in norm(item).split() if len(w) >= 3}
            score = len(q_words & iw)
            if score > best_score:
                best_score = score
                best_category = category

    return best_category


def profile_for(q):
    t = norm(q)
    if any(x in t for x in [
        "iphone", "celular", "smartphone", "fone", "smartwatch", "tablet", "notebook", "macbook",
        "carregador", "power bank", "caixa de som", "monitor", "teclado", "mouse", "impressora"
    ]):
        return "eletronicos"
    if any(x in t for x in [
        "air fryer", "aspirador", "cafeteira", "liquidificador", "organizador",
        "pote", "utensilio", "lampada", "fita led", "eletrodomestico"
    ]):
        return "casa"
    if any(x in t for x in [
        "tenis", "roupa", "camiseta", "camisa", "bermuda", "short", "jaqueta", "moletom", "polo",
        "moda praia", "bone", "bolsa", "relogio", "perfume", "maquiagem", "skincare", "barbeador", "moda"
    ]):
        return "moda"
    if any(x in t for x in [
        "creatina", "whey", "halter", "academia", "fitness", "corrida",
        "treino", "esportivo", "bicicleta"
    ]):
        return "academia"
    if any(x in t for x in [
        "furadeira", "parafusadeira", "esmerilhadeira", "serra circular",
        "serra tico tico", "ferramenta", "maleta de ferramentas",
        "lavadora de alta pressão", "compressor de ar"
    ]):
        return "ferramentas"
    if any(x in t for x in [
        "carrinho de bebê", "bebê conforto", "cadeirinha", "cadeira de alimentação",
        "berço", "brinquedo", "bicicleta infantil", "roupa infantil", "calçado infantil"
    ]):
        return "bebe_familia"
    if any(norm(x) in t for x in ARABIC_PERFUME_TERMS) and _is_real_perfume(q):
        return "perfumes_arabes"
    if any(x in t for x in [
        "automotivo", "carro", "multimidia", "multimídia", "veicular",
        "tapete automotivo", "camera de re", "câmera de ré", "som automotivo"
    ]):
        return "automotivo"
    return None

def relevance(title, q):
    t, b = norm(title), norm(q)
    p = profile_for(q)
    score = sum(35 for x in (PROFILES.get(p, {}).get("strong", []) if p else []) if x in t)
    score += sum(10 for x in b.split() if len(x) >= 3 and x in t)
    score -= sum(90 for x in PROFILES.get(p, {}).get("bad", []) if x in t)
    return score

# ============================================================
# OAUTH
# ============================================================

def pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge

def tokens():
    c = get_db()
    r = c.execute("SELECT * FROM oauth_tokens WHERE id=1").fetchone()
    c.close()
    return dict(r) if r else None

def save_tokens(data, user=None):
    old = tokens() or {}
    c = get_db()
    c.execute("""
        INSERT INTO oauth_tokens(id,access_token,refresh_token,expires_at,user_id,nickname)
        VALUES(1,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
        access_token=excluded.access_token,
        refresh_token=COALESCE(excluded.refresh_token,oauth_tokens.refresh_token),
        expires_at=excluded.expires_at,
        user_id=COALESCE(excluded.user_id,oauth_tokens.user_id),
        nickname=COALESCE(excluded.nickname,oauth_tokens.nickname)
    """, (
        data.get("access_token"),
        data.get("refresh_token"),
        int(time.time()) + int(data.get("expires_in", 21600)),
        str(user.get("id")) if user and user.get("id") else old.get("user_id"),
        user.get("nickname") if user else old.get("nickname")
    ))
    c.commit()
    c.close()

def refresh():
    t = tokens()
    if not t or not t.get("refresh_token"):
        return None
    try:
        r = requests.post(ML_TOKEN, data={
            "grant_type":"refresh_token",
            "client_id":ML_CLIENT_ID,
            "client_secret":ML_CLIENT_SECRET,
            "refresh_token":t["refresh_token"]
        }, timeout=30)
        if r.status_code != 200:
            return None
        save_tokens(r.json(), {"id": t.get("user_id"), "nickname": t.get("nickname")})
        return r.json().get("access_token")
    except Exception:
        return None

def access_token():
    t = tokens()
    if not t:
        return None
    if t.get("access_token") and time.time() < (t.get("expires_at") or 0) - 120:
        return t["access_token"]
    return refresh() or t.get("access_token")

# Limitador GLOBAL das chamadas à API do Mercado Livre.
# As buscas de categorias podem rodar em paralelo; sem este bloqueio, várias
# threads disparam requisições simultâneas e provocam HTTP 429.
_ML_API_REQUEST_LOCK = threading.RLock()
_ML_API_LAST_REQUEST_AT = 0.0
_ML_API_MIN_INTERVAL = 0.75
_ML_API_429_COOLDOWN = 0.0


def ml_get(path, params=None):
    """GET autenticado com espaçamento global e retentativa controlada no 429."""
    global _ML_API_LAST_REQUEST_AT, _ML_API_429_COOLDOWN
    token = access_token()
    if not token:
        return {}, 401, {}
    url = path if path.startswith("http") else ML_API + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Mantém uma única fila para todas as categorias, inclusive quando os
    # scanners de tênis/moda/beleza usam ThreadPoolExecutor.
    with _ML_API_REQUEST_LOCK:
        # Se outra chamada acabou de receber 429, não repetimos a mesma
        # pancada na API durante a janela de cooldown. Retornamos 429 para
        # que a busca use cache/fontes alternativas; a própria chamada que
        # recebeu o 429 já fez suas retentativas controladas abaixo.
        if time.monotonic() < _ML_API_429_COOLDOWN:
            return {"error": "rate_limit_cooldown"}, 429, {"Retry-After": str(max(1, int(_ML_API_429_COOLDOWN - time.monotonic())))}
        for attempt in range(4):
            now = time.monotonic()
            wait = max(_ML_API_MIN_INTERVAL - (now - _ML_API_LAST_REQUEST_AT),
                       _ML_API_429_COOLDOWN - now, 0.0)
            if wait > 0:
                time.sleep(wait)
            _ML_API_LAST_REQUEST_AT = time.monotonic()
            try:
                r = requests.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:
                return {"error": str(exc)}, 500, {}

            response_headers = dict(r.headers)
            try:
                data = r.json()
            except Exception:
                data = {"message": r.text[:2000]}

            if r.status_code != 429:
                _ML_API_429_COOLDOWN = 0.0
                return data, r.status_code, response_headers

            # Honra Retry-After quando válido. Se ausente/inválido, usa espera
            # exponencial crescente e partilhada por todas as threads.
            retry_after = response_headers.get("Retry-After") or response_headers.get("retry-after")
            try:
                delay = float(retry_after) if retry_after is not None else (1.5 * (2 ** attempt))
            except (TypeError, ValueError):
                delay = 1.5 * (2 ** attempt)
            delay = max(1.0, min(delay, 20.0))
            _ML_API_429_COOLDOWN = time.monotonic() + delay
            print(f"[ML API 429] limite atingido em {path}; tentativa {attempt + 1}/4; aguardando {delay:.1f}s")
            if attempt == 3:
                return data, 429, response_headers

    return {}, 429, {}

# ============================================================
# LOGIN
# ============================================================

@app.route("/mercadolivre/login")
def ml_login():
    if not ML_CLIENT_ID:
        return jsonify({"erro":"ML_CLIENT_ID não configurado."}), 500
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(32)
    session["ml_state"] = state
    session["ml_code_verifier"] = verifier
    params = {
        "response_type":"code","client_id":ML_CLIENT_ID,
        "redirect_uri":ML_REDIRECT_URI,"state":state,
        "code_challenge":challenge,"code_challenge_method":"S256"
    }
    return redirect(ML_AUTH + "?" + urlencode(params))

@app.route("/mercadolivre/callback")
def ml_callback():
    if request.args.get("error"):
        return jsonify({"erro":request.args.get("error"),"descricao":request.args.get("error_description")}), 400
    code, state = request.args.get("code"), request.args.get("state")
    if not code or state != session.get("ml_state"):
        return jsonify({"erro":"Código ou state inválido."}), 400
    try:
        r = requests.post(ML_TOKEN, data={
            "grant_type":"authorization_code","client_id":ML_CLIENT_ID,
            "client_secret":ML_CLIENT_SECRET,"code":code,
            "redirect_uri":ML_REDIRECT_URI,
            "code_verifier":session.get("ml_code_verifier")
        }, timeout=30)
        if r.status_code != 200:
            return jsonify({"erro":"Falha ao obter token.","status":r.status_code,"resposta":r.text}), r.status_code
        data = r.json()
        user = None
        if data.get("access_token"):
            me = requests.get(ML_API+"/users/me", headers={"Authorization":"Bearer "+data["access_token"]}, timeout=30)
            if me.status_code == 200:
                user = me.json()
        save_tokens(data, user)
        session.pop("ml_state", None)
        session.pop("ml_code_verifier", None)
        return redirect("/?conectado=1")
    except Exception as e:
        return jsonify({"erro":str(e)}), 500

@app.route("/mercadolivre/logout")
def ml_logout():
    c = get_db()
    c.execute("DELETE FROM oauth_tokens WHERE id=1")
    c.commit()
    c.close()
    session.clear()
    return redirect("/")

# ============================================================
# MERCADO LIVRE - PRODUTOS
# ============================================================

def discover_categories(q):
    data, status, _ = ml_get(f"/sites/{SITE_ID}/domain_discovery/search", {"q":q})
    if status != 200 or not isinstance(data, list):
        return []
    out = []
    for x in data:
        cid = x.get("category_id") or x.get("id")
        name = x.get("category_name") or x.get("name") or cid
        if cid:
            out.append({"category_id":cid,"category_name":name})
    return out

def highlights(category_id):
    data, status, _ = ml_get(f"/highlights/{SITE_ID}/category/{category_id}")
    if status != 200:
        return []
    if isinstance(data, list):
        return data
    return data.get("content", data.get("results", [])) if isinstance(data, dict) else []

def product(pid):
    data, status, _ = ml_get(f"/products/{pid}")
    return data if status == 200 and isinstance(data, dict) else None

def _build_item_from_buy_box(bb):
    """Normaliza o buy_box_winner retornado pelo catálogo em formato de item."""
    if not isinstance(bb, dict):
        return None

    item_id = (
        bb.get("item_id")
        or bb.get("id")
        or (bb.get("item") or {}).get("item_id") if isinstance(bb.get("item"), dict) else None
    )
    if not item_id:
        # Alguns retornos podem trazer o item dentro de winner
        winner = bb.get("winner")
        if isinstance(winner, dict):
            item_id = winner.get("item_id") or winner.get("id")

    if not item_id:
        return None

    shipping = bb.get("shipping") or {}
    if not isinstance(shipping, dict):
        shipping = {}

    free = bool(
        shipping.get("free_shipping")
        or bb.get("free_shipping") is True
        or bb.get("shipping_free") is True
    )

    cost = 0 if free else (
        shipping.get("cost")
        if shipping.get("cost") is not None
        else bb.get("shipping_cost")
    )

    price = bb.get("price")
    if price is None:
        price = bb.get("sale_price")
    if price is None:
        price = bb.get("regular_price")

    original = bb.get("original_price")
    if original is None:
        original = bb.get("regular_price")

    return {
        "item_id": item_id,
        "seller_id": bb.get("seller_id") or bb.get("seller", {}).get("id") if isinstance(bb.get("seller"), dict) else bb.get("seller_id"),
        "price": price,
        "original_price": original,
        "condition": bb.get("condition"),
        "listing_type_id": bb.get("listing_type_id"),
        "free_shipping": free,
        "shipping_cost": cost,
        "logistic_type": shipping.get("logistic_type") or bb.get("logistic_type"),
        "shipping_mode": shipping.get("mode") or bb.get("shipping_mode"),
        "permalink": bb.get("permalink"),
        "user_product_id": bb.get("user_product_id"),
        "sold_quantity": bb.get("sold_quantity") or bb.get("sales") or 0,
    }

def product_items(pid):
    data, status, _ = ml_get(f"/products/{pid}/items")
    if status != 200:
        return []
    if isinstance(data, list):
        return data
    return data.get("results", []) if isinstance(data, dict) else []


def _is_catalog_permalink(url):
    """Retorna True somente para URLs de catálogo /p/MLB..., nunca para publicação."""
    return bool(re.search(r"/p/MLB\d+(?:[/?#]|$)", str(url or ""), re.I))


def _is_real_publication_item(item):
    """Garante que o registro representa uma publicação real, não catálogo."""
    if not isinstance(item, dict):
        return False
    iid = str(item.get("item_id") or item.get("id") or "").strip().upper()
    permalink = str(item.get("permalink") or "").strip()
    return bool(re.fullmatch(r"MLB\d+", iid) and permalink and not _is_catalog_permalink(permalink))


def _hydrate_real_item_permalink(item):
    """Converte catálogo em publicação real ou deixa o item sem validade.

    O erro mostrado no Safari acontecia porque o catálogo podia sobreviver
    até a tela final com /p/MLB... e depois era enviado ao gerador de afiliado.
    Aqui só aceitamos um ITEM com permalink de publicação.
    """
    if not isinstance(item, dict):
        return item

    item_id = str(item.get("item_id") or "").strip().upper()
    if not item_id:
        return item

    permalink = str(item.get("permalink") or "").strip()
    if _is_real_publication_item(item):
        return item

    # Primeiro tenta as publicações vinculadas diretamente ao produto/catálogo.
    try:
        candidates = product_items(item_id) or []
    except Exception as exc:
        print("[PERMALINK CATALOGO] product_items", item_id, repr(exc))
        candidates = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("item_id") or candidate.get("id") or "").strip().upper()
        cp = str(candidate.get("permalink") or "").strip()
        if not re.fullmatch(r"MLB\d+", cid):
            continue
        if cid == item_id and _is_catalog_permalink(cp):
            continue
        if cp and not _is_catalog_permalink(cp):
            normalized = normalize_item(candidate)
            if normalized:
                normalized.update({
                    "permalink": cp,
                    "item_id": cid,
                })
                return normalized

    # Depois consulta o detalhe do catálogo e procura o buy box real.
    try:
        pdata = product(item_id)
    except Exception as exc:
        print("[PERMALINK CATALOGO] product", item_id, repr(exc))
        pdata = None

    if isinstance(pdata, dict):
        bb = pdata.get("buy_box_winner") or pdata.get("buy_box")
        candidates = []
        if isinstance(bb, dict):
            candidates.append(bb)
            if isinstance(bb.get("item"), dict):
                candidates.append(bb["item"])
            if isinstance(bb.get("winner"), dict):
                candidates.append(bb["winner"])

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            cid = str(candidate.get("item_id") or candidate.get("id") or "").strip().upper()
            cp = str(candidate.get("permalink") or "").strip()
            if not re.fullmatch(r"MLB\d+", cid) or cid == item_id:
                continue
            if cp and not _is_catalog_permalink(cp):
                normalized = normalize_item(candidate)
                if normalized:
                    normalized.update({"item_id": cid, "permalink": cp})
                    return normalized
            if re.fullmatch(r"MLB\d+", cid):
                try:
                    real_data, real_status, _ = ml_get(f"/items/{cid}")
                except Exception:
                    real_data, real_status = None, 0
                if real_status == 200 and isinstance(real_data, dict):
                    rp = str(real_data.get("permalink") or "").strip()
                    if rp and not _is_catalog_permalink(rp):
                        normalized = normalize_item(real_data)
                        if normalized:
                            normalized["permalink"] = rp
                            return normalized

    # Por segurança, nunca transforma um catálogo em uma URL inventada.
    return item

PRICE_CACHE = {}

def get_current_sale_price(item_id):
    if not item_id:
        return None, None
    if item_id in PRICE_CACHE:
        return PRICE_CACHE[item_id]
    data, status, _ = ml_get(
        f"/items/{item_id}/sale_price",
        {"context": "channel_marketplace"}
    )
    if status == 200 and isinstance(data, dict):
        try:
            amount = float(data.get("amount")) if data.get("amount") is not None else None
        except Exception:
            amount = None
        try:
            regular = float(data.get("regular_amount")) if data.get("regular_amount") is not None else None
        except Exception:
            regular = None
        if data.get("currency_id") in (None, "BRL") and amount is not None and amount > 0:
            PRICE_CACHE[item_id] = (amount, regular)
            return amount, regular
    PRICE_CACHE[item_id] = (None, None)
    return None, None

def valid_catalog_price(price):
    try:
        value = float(price)
    except Exception:
        return False
    if value < MIN_PRODUCT_PRICE:
        return False
    # Bloqueia valores claramente corrompidos, como R$1.169.000 para um celular.
    if value > 100000:
        return False
    return True

def _extract_official_store_id(obj):
    """Retorna o ID da Loja Oficial quando o Mercado Livre informa esse selo."""
    if not isinstance(obj, dict):
        return None

    candidates = [
        obj.get("official_store_id"),
        obj.get("official_store"),
    ]
    seller = obj.get("seller")
    if isinstance(seller, dict):
        candidates.extend([seller.get("official_store_id"), seller.get("official_store")])

    for value in candidates:
        if isinstance(value, dict):
            value = value.get("id") or value.get("official_store_id")
        if value not in (None, "", 0, "0", False):
            return str(value).strip()
    return None


def _is_official_store(obj):
    """Informa se a publicação traz o selo de Loja Oficial (não é filtro)."""
    return bool(_extract_official_store_id(obj))


def _discount_is_real(price, original_price):
    """Exige preço promocional real: preço atual menor que o preço anterior."""
    try:
        price = float(price)
        original_price = float(original_price)
    except Exception:
        return False
    return price > 0 and original_price > price


def normalize_item(x):
    """Normaliza uma publicação real do Mercado Livre.

    /sites/MLB/search e /items/{id} usam chaves diferentes para o ID:
    a busca normalmente traz `id`, enquanto outros pontos do código usam
    `item_id`. Aceitamos os dois para não descartar anúncios reais.
    """
    if not isinstance(x, dict):
        return None

    item_id = x.get("item_id") or x.get("id")
    if not item_id:
        return None

    sh = x.get("shipping") or {}
    free = bool(sh.get("free_shipping"))
    cost = 0 if free else sh.get("cost")

    seller = x.get("seller")
    if isinstance(seller, dict):
        seller_id = x.get("seller_id") or seller.get("id")
    else:
        seller_id = x.get("seller_id")

    return {
        "item_id": str(item_id),
        "seller_id": seller_id,
        "official_store_id": _extract_official_store_id(x),
        "price": x.get("price") or x.get("sale_price"),
        "original_price": x.get("original_price") or x.get("regular_price"),
        "condition": x.get("condition"),
        "listing_type_id": x.get("listing_type_id"),
        "free_shipping": free,
        "shipping_cost": cost,
        "logistic_type": sh.get("logistic_type"),
        "shipping_mode": sh.get("mode"),
        "permalink": x.get("permalink"),
        "user_product_id": x.get("user_product_id"),
        "sold_quantity": x.get("sold_quantity") or x.get("sales") or 0,
    }

# ============================================================
# CUPONS
# ============================================================

def number(s):
    if s is None:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(s))
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def normalize_coupon_html(raw_html):
    """Converte HTML do Mercado Livre em texto preservando ALT das imagens.

    As páginas públicas de ofertas frequentemente repetem o título no ALT da
    imagem e no conteúdo do card. Preservar o ALT deixa o parser capaz de
    separar corretamente produto + preço + cupom sem misturar cards vizinhos.
    """
    text = html_lib.unescape(raw_html or "")

    # Remove scripts/styles que não são conteúdo visual do card, mas preserva
    # texto útil de imagens antes de remover as demais tags.
    def img_alt(m):
        tag = m.group(0)
        alt = re.search(r'\balt\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if alt and alt.group(1).strip():
            return "\nImage: " + alt.group(1).strip() + "\n"
        return " "

    text = re.sub(r"<img\b[^>]*>", img_alt, text, flags=re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</(?:div|p|li|h1|h2|h3|h4|h5|h6|section|article|br|tr|header|footer)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def looks_like_coupon_code(code):
    code = (code or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{5,29}", code):
        return False
    # Evita palavras que aparecem em frases como "Cupom não cumulativo".
    if not re.search(r"[A-Z]", code) or not re.search(r"\d", code):
        return False
    bad = {"NAOCOUPOM", "CUPOMVALIDO", "VALIDO", "DESCONTO"}
    return code not in bad


def coupon_blocks(text):
    """Retorna blocos individuais iniciados por 'Cupom CODE'.

    A versão anterior usava janelas grandes ao redor da palavra 'cupom'.
    Isso fazia 1FRUIT herdar os 15%/R$70 do S5PRUNK. Aqui cada cupom fica
    isolado do próximo cupom.
    """
    # O cabeçalho real é seguido por "Cupom válido". Isso elimina ocorrências
    # como "Cupom não cumulativo" dentro das condições.
    # O padrão não depende de quebra de linha porque a página pode entregar
    # alguns blocos em uma única linha após a remoção das tags HTML.
    pat = re.compile(
        r"\bCupom\s+([A-Z0-9][A-Z0-9_-]{5,29})\b(?=\s+Cupom\s+v[aá]lido)",
        re.I
    )
    candidates = []
    for m in pat.finditer(text):
        code = m.group(1).upper()
        if not looks_like_coupon_code(code):
            continue
        candidates.append((m.start(), m.end(), code))

    blocks = []
    for i, (a, b, code) in enumerate(candidates):
        end = candidates[i+1][0] if i+1 < len(candidates) else len(text)
        block = text[b:end].strip()
        # Limita o bloco a seções gerais que não pertencem ao cupom.
        for marker in ["Restrições de Uso", "Termos e Condições"]:
            pos = block.lower().find(marker.lower())
            if pos >= 0:
                block = block[:pos].strip()
        blocks.append((code, block))
    return blocks


def parse_coupon_block(code, block, source_url=COUPONS_URL):
    # Percentual: procura a primeira oferta explícita do bloco.
    pct = None
    for pat in [
        r"(?:até\s+)?(\d+(?:[.,]\d+)?)\s*%\s*(?:off|de desconto)?",
        r"(?:desconto de|desconto)\s+(\d+(?:[.,]\d+)?)\s*%",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            pct = number(m.group(1))
            break

    # Cupom em dinheiro: captura frases do tipo R$ 30 OFF / R$ 30 de desconto.
    fixed = None
    fixed_patterns = [
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:OFF|de desconto|de\s+desconto)",
        r"(?:desconto de|ganhe)\s*R\$\s*(\d+(?:[.,]\d+)?)",
    ]
    for pat in fixed_patterns:
        m = re.search(pat, block, re.I)
        if m:
            fixed = number(m.group(1))
            break

    mn = None
    for pat in [
        r"(?:a partir de|partir de|compra mínima de|mínimo de|valor mínimo de)\s*R?\$?\s*(\d+(?:[.,]\d+)?)",
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:ou mais|em compras)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            mn = number(m.group(1))
            break

    mx = None
    for pat in [
        r"(?:desconto\s+)?(?:máximo de|maximo de|limitado a)\s*R?\$?\s*(\d+(?:[.,]\d+)?)\b",
        r"R\$\s*(\d+(?:[.,]\d+)?)\s*(?:de desconto no máximo|máximo de desconto)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            mx = number(m.group(1))
            break

    # Evita confundir preço mínimo com teto quando a frase contém "até X%".
    if mx is None:
        m = re.search(r"(?:máximo|limite).*?R\$\s*(\d+(?:[.,]\d+)?)", block, re.I)
        if m:
            mx = number(m.group(1))

    usage_limit = None
    for pat in [
        r"(?:limite de|limite:|até)\s*([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:usos|utiliza(?:ções|coes)|cupons|clientes)",
        r"([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:usos|utiliza(?:ções|coes)|cupons|clientes)",
        r"(?:disponível|disponiveis)\s*para\s*(?:os )?([0-9]{1,3}(?:[\.,][0-9]{3})*|[0-9]{2,7})\s*(?:primeiros )?(?:usos|clientes|cupons)",
    ]:
        m = re.search(pat, block, re.I)
        if m:
            try:
                usage_limit = int(str(m.group(1)).replace('.', '').replace(',', ''))
            except Exception:
                usage_limit = None
            break

    return {
        "code": code,
        "description": block[:2500],
        "discount_percent": pct,
        "fixed_discount": fixed or 0,
        "min_purchase": mn,
        "max_discount": mx,
        "usage_limit": usage_limit,
        "source_url": source_url,
        "conditions": block[:2500],
    }


def sync_coupons():
    """Caça cupons em várias áreas públicas do Mercado Livre.

    Não existe um teto artificial de desconto no programa. Todos os cupons
    encontrados são considerados. O único limite aplicado ao cálculo é o
    próprio limite/condição informado pelo Mercado Livre para aquele cupom.
    """
    headers = {
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept-Language":"pt-BR,pt;q=0.9,en;q=0.8",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    parsed_by_code = {}
    errors = []

    for source_url in COUPON_SOURCE_URLS:
        try:
            r = requests.get(source_url, headers=headers, timeout=30)
            if r.status_code != 200:
                errors.append(f"{source_url}: HTTP {r.status_code}")
                continue

            text = normalize_coupon_html(r.text)
            blocks = coupon_blocks(text)
            for code, block in blocks:
                c = parse_coupon_block(code, block, source_url)
                if not c.get("discount_percent") and not c.get("fixed_discount"):
                    continue

                # Se o mesmo código aparecer em mais de uma página, mantém
                # a versão com mais informação/condições.
                old = parsed_by_code.get(code)
                if not old or len(c.get("conditions", "")) > len(old.get("conditions", "")):
                    parsed_by_code[code] = c

        except Exception as e:
            errors.append(f"{source_url}: {e}")

    parsed = list(parsed_by_code.values())

    if not parsed:
        return {
            "ok":False,
            "erro":"Nenhum cupom público válido foi identificado nas fontes consultadas.",
            "erros":errors
        }

    conn = get_db()
    conn.execute("UPDATE cupons SET active=0")

    for c in parsed:
        conn.execute("""
            INSERT INTO cupons(
                code,description,discount_percent,fixed_discount,
                min_purchase,max_discount,usage_limit,source_url,conditions,active,updated_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(code) DO UPDATE SET
                description=excluded.description,
                discount_percent=excluded.discount_percent,
                fixed_discount=excluded.fixed_discount,
                min_purchase=excluded.min_purchase,
                max_discount=excluded.max_discount,
                usage_limit=excluded.usage_limit,
                source_url=excluded.source_url,
                conditions=excluded.conditions,
                active=1,
                updated_at=CURRENT_TIMESTAMP
        """,(
            c["code"],c["description"],c["discount_percent"],c["fixed_discount"],
            c["min_purchase"],c["max_discount"],c.get("usage_limit"),c["source_url"],c["conditions"]
        ))

    conn.commit()
    conn.close()

    print("[CUPONS] Cupons encontrados em todas as fontes:")
    for c in sorted(parsed, key=lambda x: (-(x.get("max_discount") or 0), -(x.get("discount_percent") or 0))):
        print(
            f"[CUPOM OK] {c['code']} | {c.get('discount_percent') or 0}% | "
            f"fixo R$ {c.get('fixed_discount') or 0:.2f} | "
            f"mín R$ {c.get('min_purchase') or 0:.2f} | "
            f"máx R$ {c.get('max_discount') or 0:.2f} | "
            f"limite usos {c.get('usage_limit') or 'não informado'} | {c.get('source_url')}"
        )

    return {
        "ok":True,
        "cupons_encontrados":len(parsed),
        "fontes_consultadas":len(COUPON_SOURCE_URLS),
        "erros":errors
    }


def coupons():
    c = get_db()
    rows = c.execute(
        "SELECT * FROM cupons WHERE active=1 ORDER BY updated_at DESC, discount_percent DESC, max_discount DESC"
    ).fetchall()
    c.close()
    return [dict(x) for x in rows]


def coupon_discount(cupom, price):
    try:
        price = float(price)
        minimum = cupom.get("min_purchase")
        if minimum and price < float(minimum):
            return 0

        candidates = []
        pct = float(cupom.get("discount_percent") or 0)
        fixed = float(cupom.get("fixed_discount") or 0)

        if pct > 0:
            d = price * pct / 100
            if cupom.get("max_discount"):
                d = min(d, float(cupom["max_discount"]))
            candidates.append(d)

        if fixed > 0:
            d = fixed
            if cupom.get("max_discount"):
                d = min(d, float(cupom["max_discount"]))
            candidates.append(d)

        return round(max(0, max(candidates, default=0)), 2)
    except Exception:
        return 0


def best_coupon(price):
    choices = []
    for c in coupons():
        d = coupon_discount(c, price)
        if d > 0:
            x = dict(c); x["desconto_estimado"] = d
            x["percentual_efetivo"] = round((d / float(price)) * 100, 2) if float(price) > 0 else 0
            x["match_type"] = "regras_de_preco"
            choices.append(x)
    return max(choices, key=lambda x:(x["desconto_estimado"],x["percentual_efetivo"],-(float(x.get("min_purchase") or 0))), default=None)

PUBLIC_COUPON_CARDS_CACHE = {"at": 0.0, "cards": []}
PUBLIC_COUPON_CARDS_CACHE_LOCK = threading.Lock()


def get_public_coupon_cards_cached(ttl=600):
    """Carrega os cards públicos de cupom no máximo uma vez a cada 10 minutos."""
    now = time.time()
    with PUBLIC_COUPON_CARDS_CACHE_LOCK:
        if now - float(PUBLIC_COUPON_CARDS_CACHE.get("at") or 0) < ttl:
            return list(PUBLIC_COUPON_CARDS_CACHE.get("cards") or [])

    try:
        cards = public_coupon_product_cards()
    except Exception as exc:
        print("[CUPOM PRODUTO] Erro ao carregar cards:", repr(exc))
        cards = []

    with PUBLIC_COUPON_CARDS_CACHE_LOCK:
        PUBLIC_COUPON_CARDS_CACHE["at"] = time.time()
        PUBLIC_COUPON_CARDS_CACHE["cards"] = list(cards or [])
    return list(cards or [])


def choose_best_coupon(title, price, public_cards=None, item_id=None, permalink=None, allow_fallback=True, preferred_code=None):
    """Escolhe somente cupons com associação pública ao produto.

    IMPORTANTE: não aplicamos mais um cupom genérico só porque o preço
    atende ao mínimo/máximo. Isso foi o que fazia o S5PRUNK/R$70 aparecer
    em praticamente todos os produtos. O Mercado Livre informa que cupons
    são condicionados a produtos selecionados; portanto, sem uma associação
    pública produto->cupom, o app não chama o cupom de aplicável.

    O mesmo cupom pode continuar sendo usado em vários produtos quando cada
    produto tiver sua própria associação pública.
    """
    if not public_cards:
        return None

    candidates = match_public_coupons(title, price, public_cards)
    matched = None
    preferred = str(preferred_code or "").strip().upper()
    if preferred:
        for candidate in candidates:
            code = str(candidate.get("code") or candidate.get("label") or "").strip().upper()
            if code == preferred:
                matched = candidate
                break
    if matched is None and candidates:
        matched = max(candidates, key=lambda x: (
            float(x.get("desconto_estimado") or 0),
            float(x.get("match_score") or 0),
        ))

    # Se os cards públicos não trouxeram o produto, mantém o fallback já
    # existente, mas somente para esse produto específico. Nunca transforma
    # o fallback em cupom universal.
    if matched is None and allow_fallback:
        matched = match_public_coupon(title, price, public_cards, item_id, permalink=permalink, allow_fallback=True)
    if not matched:
        return None

    d = calculate_public_coupon(matched, price)
    if d <= 0:
        return None

    x = dict(matched)
    x["desconto_estimado"] = d
    x["preco_base_produto"] = round(float(price), 2)
    x["preco_final_estimado"] = round(float(price) - d, 2)
    x["percentual_efetivo"] = round((d / float(price)) * 100, 2) if float(price) > 0 else 0
    x["match_type"] = "produto_publico"
    return x

def detect_cash_discount(item, price):
    """Só aceita desconto à vista/Pix quando o próprio dado da API o informa.
    Não assume que todo Pix tem desconto e não soma com cupom sem indicação de cumulatividade.
    """
    if not isinstance(item, dict):
        return 0, None
    p = float(price or 0)
    if p <= 0:
        return 0, None

    explicit = []
    for key in ("pix_discount", "cash_discount", "discount_pix", "payment_discount", "cashback_discount"):
        v = item.get(key)
        if isinstance(v, (int,float)) and float(v) > 0:
            explicit.append(float(v))

    for key in ("pix_price", "cash_price", "price_pix", "price_cash"):
        v = item.get(key)
        if isinstance(v, (int,float)) and 0 < float(v) < p:
            explicit.append(p - float(v))

    payments = item.get("payment_methods") or item.get("payments") or {}
    if isinstance(payments, dict):
        for k, v in payments.items():
            if "pix" not in str(k).lower() and "avista" not in norm(k):
                continue
            if isinstance(v, dict):
                for key in ("discount", "discount_amount", "amount_discount"):
                    n = v.get(key)
                    if isinstance(n,(int,float)) and float(n)>0:
                        explicit.append(float(n))
                for key in ("price", "final_price"):
                    n = v.get(key)
                    if isinstance(n,(int,float)) and 0 < float(n) < p:
                        explicit.append(p-float(n))

    d = round(max(explicit, default=0),2)
    return d, ("Pix/à vista" if d > 0 else None)

# ============================================================
# CAÇADOR
# ============================================================

def _extract_public_coupon_cards_from_text(text, source_url):
    """Extrai associações produto -> cupom de páginas públicas.

    O front do Mercado Livre muda bastante o HTML. Por isso o parser não
    exige mais que o cupom esteja em uma linha isolada nem que exista código.
    Ele aceita tanto ``Cupom R$15 OFF`` quanto ``Cupom 10% OFF`` e procura o
    título/preço mais próximos dentro do mesmo card.
    """
    cards = []
    raw = html_lib.unescape(text or "")
    clean = normalize_coupon_html(raw)

    def add_card(title, price, original, coupon):
        if not title or not coupon or price is None:
            return
        try:
            price = float(price)
        except Exception:
            return
        if price < MIN_PRODUCT_PRICE:
            return
        cards.append({
            "title": re.sub(r"\s+", " ", str(title)).strip(),
            "price": round(price, 2),
            "original_price": round(float(original), 2) if original and float(original) > price else None,
            "coupon": coupon,
            "source_url": source_url,
        })

    # 1) Primeiro tenta os blocos já normalizados por linhas.
    lines = [re.sub(r"\s+", " ", x).strip() for x in clean.splitlines()]
    lines = [x for x in lines if x]
    coupon_re = re.compile(
        r"(?:\bCupom\s+(?:R\$\s*[\d\.]+(?:,[\d]{2})?|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF)\b|\bR\$\s*[\d\.]+(?:,[\d]{2})?\s*OFF\s+com\s+Cupom\b|\b\d+(?:[.,]\d+)?\s*%\s*OFF\s+com\s+Cupom\b)",
        re.I,
    )
    money_re = re.compile(r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)", re.I)
    percent_off_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*%\s*OFF", re.I)

    for i, line in enumerate(lines):
        m_coupon = coupon_re.search(line)
        if not m_coupon:
            continue
        coupon = detect_public_coupon(m_coupon.group(0))
        if not coupon:
            continue

        # Procura o título mais próximo acima. Não exige Image: exatamente na
        # linha imediatamente anterior porque o HTML pode inserir etiquetas.
        title = ""
        title_idx = None
        for j in range(i, max(-1, i - 35), -1):
            if lines[j].lower().startswith("image:"):
                candidate = lines[j].split(":", 1)[1].strip()
                if len(candidate) >= 8 and not re.search(r"^(logo|mercado livre|cupom|oferta)", candidate, re.I):
                    title = candidate
                    title_idx = j
                    break
        if not title:
            # Última tentativa: texto próximo que parece nome de produto.
            for j in range(i - 1, max(-1, i - 12), -1):
                candidate = lines[j].strip()
                if len(candidate) >= 12 and not re.search(r"^(r\$|oferta|mais vendido|cupom|frete|chegará|10x|\d+% off)", candidate, re.I):
                    title = candidate
                    title_idx = j
                    break
        if not title:
            continue

        # Preço atual: prioriza a linha com percentual OFF do próprio card.
        price = None
        original = None
        search_start = title_idx if title_idx is not None else max(0, i - 20)
        for j in range(search_start, i + 1):
            vals = [parse_public_money(x.group(0)) for x in money_re.finditer(lines[j])]
            vals = [v for v in vals if v is not None]
            if percent_off_re.search(lines[j]) and vals:
                price = vals[-1]
                bigger = [v for v in vals[:-1] if v > price]
                if bigger:
                    original = min(bigger)
                break

        if price is None:
            # Procura os últimos preços antes do cupom, ignorando parcelamento.
            vals = []
            for j in range(search_start, i):
                vals.extend(parse_public_money(x.group(0)) for x in money_re.finditer(lines[j]))
            vals = [v for v in vals if v is not None and v >= MIN_PRODUCT_PRICE]
            if vals:
                price = vals[-1]
                bigger = [v for v in vals if v > price]
                if bigger:
                    original = min(bigger)

        add_card(title, price, original, coupon)

    # 2) O servidor pode devolver o card em uma única linha ou dentro de JSON.
    # Procura "título ... preço ... OFF ... Cupom" em janelas próximas.
    flat = re.sub(r"\s+", " ", clean)
    patterns = [
        re.compile(r"(?:Image:\s*)?(.{8,220}?)\s+(R\$\s*[\d\.]+,[\d]{2}|R\$\s*\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s*%\s*OFF\s+(Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF))", re.I),
        re.compile(r"(?:Image:\s*)?(.{8,220}?)\s+(Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF))\s+(?:Chegará|Frete|Mais vendido|Oferta)", re.I),
    ]
    for pat in patterns:
        for m in pat.finditer(flat):
            title = m.group(1).strip()
            # Remove rótulos que claramente não são produto.
            title = re.sub(r"^(?:OFERTA DO DIA|OFERTA IMPERDÍVEL|OFERTA RELÂMPAGO)\s+", "", title, flags=re.I)
            coupon_text = m.group(4) if m.lastindex and m.lastindex >= 4 else m.group(2)
            coupon = detect_public_coupon(coupon_text)
            if not coupon:
                continue
            price_text = m.group(2) if m.lastindex and m.lastindex >= 4 else None
            price = parse_public_money(price_text) if price_text else None
            add_card(title, price, None, coupon)

    # 3) Último fallback: para cada ocorrência de Cupom, pega a janela de
    # texto anterior e tenta identificar um título e um preço nela.
    if not cards:
        for m in re.finditer(r"Cupom\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF)", flat, re.I):
            coupon = detect_public_coupon(m.group(0))
            if not coupon:
                continue
            before = flat[max(0, m.start() - 900):m.start()]
            money = list(money_re.finditer(before))
            if not money:
                continue
            price = parse_public_money(money[-1].group(0))
            title_candidates = re.findall(r"(?:Image:\s*)?([A-Za-zÀ-ÿ0-9][^|]{12,180}?)\s+(?:R\$|\d+%\s*OFF)", before, re.I)
            title = title_candidates[-1].strip() if title_candidates else ""
            if title:
                add_card(title, price, None, coupon)

    # 4) Fallback para páginas dinâmicas: alguns cards aparecem dentro de
    # JSON/atributos HTML e não sobrevivem ao parser de linhas. Aqui usamos
    # somente marcadores explícitos de cupom e uma janela curta ao redor deles.
    # Isso evita inventar cupom genérico para produtos que não o exibem.
    if not cards:
        raw_flat = re.sub(r"\s+", " ", html_lib.unescape(str(text or "")))
        raw_flat = re.sub(r"<[^>]+>", " ", raw_flat)
        marker_re = re.compile(
            r"(?:Cupom(?:\s+de)?\s+(?:R\$\s*[\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|%\s*OFF|de\s+desconto)|"
            r"R\$\s*[\d\.]+,[\d]{2}\s*OFF\s+com\s+Cupom|"
            r"\d+(?:[.,]\d+)?\s*%\s*OFF\s+com\s+Cupom)", re.I)
        for mm in marker_re.finditer(raw_flat):
            coupon = detect_public_coupon(mm.group(0))
            if not coupon:
                continue
            window = raw_flat[max(0, mm.start()-1400):min(len(raw_flat), mm.end()+300)]
            prices = [parse_public_money(x.group(0)) for x in re.finditer(r"R\$\s*[0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|R\$\s*[0-9]+,[0-9]{2}", window)]
            prices = [x for x in prices if x is not None and x >= MIN_PRODUCT_PRICE]
            if not prices:
                continue
            price = prices[-1]
            image_titles = re.findall(r"Image:\s*([^|]{8,220})", window, re.I)
            title = image_titles[-1].strip() if image_titles else ""
            if not title:
                candidates = re.findall(r"([A-Za-zÀ-ÿ0-9][^|]{15,180}?)\s+(?:R\$|Cupom|%\s*OFF)", window, re.I)
                title = candidates[-1].strip() if candidates else ""
            if title and not re.search(r"^(?:logo|mercado livre|cupom|oferta|frete)", title, re.I):
                add_card(title, price, None, coupon)

    # Deduplica cards muito semelhantes.
    unique = {}
    for c in cards:
        key = (norm(c["title"]), round(c["price"], 2), c["coupon"].get("label"))
        unique[key] = c
    return list(unique.values())

def public_coupon_product_cards():
    """Lê associações produto -> cupom da página pública.

    A página pública pode ser renderizada de formas diferentes. Tentamos
    primeiro a página principal e depois páginas públicas de busca do Mercado
    Livre quando a página de cupons não entregar cards no HTML recebido.
    """
    urls = [
        "https://www.mercadolivre.com.br/l/descontaco-cupons",
        "https://www.mercadolivre.com.br/l/promocoes",
        "https://www.mercadolivre.com.br/ofertas/cupons",
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    cards = []
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=25, allow_redirects=True)
            if r.status_code != 200:
                print("[CUPOM PRODUTO]", url, "HTTP", r.status_code)
                continue
            text = normalize_coupon_html(r.text)
            found = _extract_public_coupon_cards_from_text(text, r.url or url)
            print("[CUPOM PRODUTO]", url, "cards=", len(found))
            cards.extend(found)
        except Exception as e:
            print("[CUPOM PRODUTO] ERRO", url, repr(e))

    unique = {}
    for c in cards:
        key = (norm(c["title"]), round(float(c["price"]), 2), c["coupon"]["label"])
        unique[key] = c

    out = list(unique.values())
    print("[CARDS DE CUPOM]", len(out))
    for c in out[:25]:
        print("[CARD]", c["title"][:90], "|", brl(c["price"]), "|", c["coupon"]["label"])
    return out


PUBLIC_PRODUCT_COUPON_CACHE = {}
PUBLIC_PRODUCT_COUPON_LOCK = threading.Lock()


def _looks_like_affiliate_coupon_code(code):
    """Aceita códigos como MELIBAIXOU, inclusive códigos só com letras."""
    code = re.sub(r"[^A-Z0-9_-]", "", str(code or "").upper())
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{5,29}", code):
        return False
    bad = {
        "MERCADOLIVRE", "MERCADOLIVREBR", "CUPOMVALIDO", "DESCONTO",
        "COPIAR", "VERCUPOM", "CUPOMMERCADOLIVRE", "NOVOCUPOM",
        "CUPOMATIVO", "ATIVAR", "APROVEITE", "OFERTADODIA",
    }
    return code not in bad


def _extract_affiliate_coupon_catalog_from_text(text, source_url):
    """Extrai códigos de cupom de parceiros e as regras próximas ao código."""
    clean = normalize_coupon_html(text or "")
    flat = re.sub(r"\s+", " ", html_lib.unescape(clean)).strip()
    found = {}
    patterns = [
        r"(?:cupom|c[oó]digo(?:\s+promocional)?|use(?:\s+o)?|utilize(?:\s+o)?)\s*[:\-]?\s*[`\[]?([A-Z][A-Z0-9_-]{5,29})",
        r"[\[`]([A-Z][A-Z0-9_-]{7,29})[\]`](?=\s*(?:copiar|ver|usar|ir))",
    ]
    for pat in patterns:
        for m in re.finditer(pat, flat, re.I):
            code = str(m.group(1) or "").strip().upper()
            if not _looks_like_affiliate_coupon_code(code):
                continue
            context = flat[max(0, m.start()-700):min(len(flat), m.end()+900)]
            if not re.search(r"%\s*(?:OFF|de desconto)|R\$\s*[\d\.]+(?:,[\d]{2})?\s*(?:OFF|de desconto)|desconto", context, re.I):
                continue
            parsed = parse_coupon_block(code, context, source_url)
            if not (parsed.get("discount_percent") or parsed.get("fixed_discount")):
                continue
            parsed["source_type"] = "parceiro_afiliado"
            parsed["source_verified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            parsed["discovery_context"] = context[:2200]
            old = found.get(code)
            if old is None:
                found[code] = parsed
            else:
                old_score = sum(1 for k in ("discount_percent", "fixed_discount", "min_purchase", "max_discount", "usage_limit") if old.get(k))
                new_score = sum(1 for k in ("discount_percent", "fixed_discount", "min_purchase", "max_discount", "usage_limit") if parsed.get(k))
                if new_score > old_score or len(parsed.get("conditions", "")) > len(old.get("conditions", "")):
                    found[code] = parsed
    return list(found.values())


def get_affiliate_coupon_catalog_cached(ttl=900):
    """Busca códigos de parceiros a cada 15 minutos."""
    now = time.time()
    with AFFILIATE_COUPON_CACHE_LOCK:
        if now - float(AFFILIATE_COUPON_CACHE.get("at") or 0) < ttl:
            return list(AFFILIATE_COUPON_CACHE.get("coupons") or [])
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    by_code = {}
    for url in AFFILIATE_COUPON_SOURCE_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
            if r.status_code != 200:
                print("[CUPONS AFILIADOS]", url, "HTTP", r.status_code)
                continue
            found = _extract_affiliate_coupon_catalog_from_text(r.text, r.url or url)
            print("[CUPONS AFILIADOS]", url, "codes=", len(found))
            for c in found:
                code = str(c.get("code") or "").strip().upper()
                if not code:
                    continue
                if code not in by_code:
                    c["source_count"] = 1
                    by_code[code] = c
                else:
                    by_code[code]["source_count"] = int(by_code[code].get("source_count") or 1) + 1
                    old = by_code[code]
                    old_score = (float(old.get("discount_percent") or 0), float(old.get("max_discount") or 0), -float(old.get("min_purchase") or 0))
                    new_score = (float(c.get("discount_percent") or 0), float(c.get("max_discount") or 0), -float(c.get("min_purchase") or 0))
                    if new_score > old_score:
                        c["source_count"] = old["source_count"]
                        by_code[code] = c
        except Exception as exc:
            print("[CUPONS AFILIADOS] ERRO", url, repr(exc))
    out = list(by_code.values())
    out.sort(key=lambda x: (-int(x.get("source_count") or 0), -float(x.get("discount_percent") or 0), -float(x.get("max_discount") or 0), float(x.get("min_purchase") or 0)))
    with AFFILIATE_COUPON_CACHE_LOCK:
        AFFILIATE_COUPON_CACHE["at"] = time.time()
        AFFILIATE_COUPON_CACHE["coupons"] = list(out)
    print("[CUPONS AFILIADOS] TOTAL CÓDIGOS:", len(out))
    for c in out[:20]:
        print(f"[CUPOM AFILIADO] {c.get('code')} | {c.get('discount_percent') or 0}% | mín R$ {float(c.get('min_purchase') or 0):.2f} | máx R$ {float(c.get('max_discount') or 0):.2f} | fontes={c.get('source_count') or 1}")
    return out


def _affiliate_coupon_allowed_for_offer(coupon, offer):
    """Valida preço e exclusões conhecidas antes de sugerir cupom amplo."""
    title = norm(offer.get("title") or "")
    category = norm(offer.get("category_name") or "")
    # A página oficial informa exclusão de fragrâncias para cupons divulgados
    # por afiliados; portanto não forçamos esses cupons em perfumes.
    if "perfume" in category or "fragrance" in category or any(x in title for x in ("perfume", "parfum", "eau de parfum", "eau de toilette", "body splash", "body mist")):
        return False
    if any(term in title for term in AFFILIATE_COUPON_BLOCKLIST_TERMS):
        return False
    return coupon_discount(coupon, float(offer.get("price") or 0)) > 0


def choose_broad_affiliate_coupon_for_offer(offer, catalog):
    candidates = []
    for coupon in catalog or []:
        if not _affiliate_coupon_allowed_for_offer(coupon, offer):
            continue
        d = coupon_discount(coupon, float(offer.get("price") or 0))
        if d <= 0:
            continue
        x = dict(coupon)
        x["desconto_estimado"] = round(d, 2)
        x["preco_base_produto"] = round(float(offer.get("price") or 0), 2)
        x["preco_final_estimado"] = round(float(offer.get("price") or 0) - d, 2)
        x["percentual_efetivo"] = round((d / float(offer.get("price") or 1)) * 100, 2)
        x["match_type"] = "cupom_afiliado_amplo"
        candidates.append(x)
    return max(candidates, key=lambda x: (int(x.get("source_count") or 0), float(x.get("desconto_estimado") or 0), float(x.get("discount_percent") or 0), -float(x.get("min_purchase") or 0)), default=None)



def _search_public_listing_for_coupon(title, price, item_id=None, permalink=None):
    """Fallback por busca/anúncio público do Mercado Livre.

    A página geral de cupons pode chegar sem os cards para IPs de nuvem.
    Nesse caso consultamos o anúncio específico (quando temos permalink) e,
    em seguida, a busca pública pelo título. Só aceitamos cupom quando o
    resultado também combina fortemente com título e preço do produto.
    """
    key = f"{norm(title)}|{round(float(price or 0),2)}"
    with PUBLIC_PRODUCT_COUPON_LOCK:
        if key in PUBLIC_PRODUCT_COUPON_CACHE:
            return PUBLIC_PRODUCT_COUPON_CACHE[key]

    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", norm(title))).strip("-")[:180]
    if not slug:
        return None

    urls = []
    if permalink and str(permalink).startswith("http"):
        urls.append(str(permalink))
    urls.append("https://lista.mercadolivre.com.br/" + quote(slug))

    headers_list = [
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Referer": "https://www.mercadolivre.com.br/",
        },
        {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.mercadolivre.com.br/",
        },
    ]

    result = None
    try:
        for url in urls:
            for headers in headers_list:
                try:
                    r = requests.get(url, headers=headers, timeout=12, allow_redirects=True)
                except Exception:
                    continue
                if r.status_code != 200 or len(r.text or "") < 500:
                    continue

                text = normalize_coupon_html(r.text)
                cards = _extract_public_coupon_cards_from_text(text, r.url or url)
                best = None
                best_score = 0
                for card in cards:
                    sim = title_similarity(title, card["title"])
                    diff = abs(float(price) - float(card["price"]))
                    tolerance = max(20.0, float(price) * 0.25)
                    if diff <= tolerance:
                        sim += 0.18
                    elif diff <= max(35.0, float(price) * 0.35):
                        sim += 0.05
                    if sim > best_score:
                        best_score = sim
                        best = card

                if best is not None and best_score >= 0.72:
                    result = dict(best["coupon"])
                    result["match_score"] = round(best_score, 3)
                    result["public_title"] = best["title"]
                    result["public_price"] = best["price"]
                    result["source_url"] = best["source_url"]
                    with PUBLIC_PRODUCT_COUPON_LOCK:
                        PUBLIC_PRODUCT_COUPON_CACHE[key] = result
                    return result
    except Exception as e:
        print("[CUPOM BUSCA PÚBLICA]", repr(e))

    with PUBLIC_PRODUCT_COUPON_LOCK:
        PUBLIC_PRODUCT_COUPON_CACHE[key] = result
    return result

def detect_public_coupon(text):
    """Detecta formatos atuais de cupom exibidos nas páginas públicas.

    O Mercado Livre alterna entre ``Cupom R$ X OFF``, ``R$ X OFF com
    Cupom``, ``Cupom X% OFF`` e variações com ``de desconto``.
    """
    text = re.sub(r"\s+", " ", html_lib.unescape(str(text or ""))).strip()

    fixed_patterns = [
        r"Cupom\s+(?:de\s+)?R\$\s*([\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|de\s+desconto)",
        r"R\$\s*([\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s*(?:OFF|de\s+desconto)\s+com\s+Cupom",
        r"(?:desconto|cupom)\s+de\s+R\$\s*([\d\.]+,[\d]{2}|\d+(?:[.,]\d+)?)\s+.*?Cupom",
    ]
    for pattern in fixed_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            value = parse_public_money("R$ " + m.group(1))
            if value and value > 0:
                return {"type":"fixed", "value":value, "label":f"Cupom {brl(value)} OFF"}

    percent_patterns = [
        r"Cupom\s+(?:de\s+)?(\d+(?:[.,]\d+)?)\s*%\s*(?:OFF|de\s+desconto)",
        r"(\d+(?:[.,]\d+)?)\s*%\s*(?:OFF|de\s+desconto)\s+com\s+Cupom",
        r"Cupom\s+.*?(\d+(?:[.,]\d+)?)\s*%\s+de\s+desconto",
    ]
    for pattern in percent_patterns:
        m = re.search(pattern, text, re.I)
        if m:
            value = float(m.group(1).replace(",", "."))
            if value > 0:
                return {"type":"percent", "value":value, "label":f"Cupom {value:g}% OFF"}

    return None


def parse_public_money(text):
    if not text:
        return None
    m = re.search(r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+,[0-9]{2}|[0-9]+(?:\.[0-9]{2})?)", str(text))
    if not m:
        return None
    v = m.group(1)
    try:
        return float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)
    except Exception:
        return None


def title_similarity(a, b):
    ta = {x for x in norm(a).split() if len(x) >= 3 and x not in STOP_WORDS}
    tb = {x for x in norm(b).split() if len(x) >= 3 and x not in STOP_WORDS}
    if not ta or not tb:
        return 0.0
    common = ta & tb
    score = len(common) / max(1, min(len(ta), len(tb)))
    na, nb = norm(a), norm(b)
    for word in ("iphone","galaxy","samsung","motorola","xiaomi","redmi","poco","kappa","nike","adidas","smartwatch","air","fryer","whey","creatina","perfume"):
        if word in na and word in nb:
            score += 0.10
    return min(score, 1.0)


STOP_WORDS = {"de","da","do","das","dos","com","para","por","e","em","no","na","um","uma","original","novo","oficial"}


def match_public_coupons(title, price, cards):
    """Retorna TODOS os cupons publicamente associados ao produto.

    Diferente de match_public_coupon(), esta função não para no primeiro/
    melhor cupom. Ela é usada para descobrir qual código possui a maior
    cobertura real entre as ofertas da rodada, sem transformar um cupom
    genérico em cupom universal.
    """
    try:
        target_price = float(price)
    except Exception:
        target_price = 0.0
    if target_price <= 0:
        return []

    generic = {
        "perfume", "parfum", "eau", "de", "toilette", "fragrance",
        "original", "novo", "oficial", "kit", "com", "para", "masculino",
        "feminino", "unissex", "produto", "promocao", "oferta", "ml",
        "un", "unidade", "cor", "tamanho", "modelo", "premium"
    }

    target_tokens = [x for x in norm(title).split()
                     if len(x) >= 3 and x not in generic and x not in STOP_WORDS]
    target_set = set(target_tokens)
    by_code = {}

    for card in cards or []:
        try:
            card_price = float(card.get("price") or 0)
        except Exception:
            continue
        if card_price <= 0:
            continue

        card_title = str(card.get("title") or "")
        card_tokens = {x for x in norm(card_title).split()
                       if len(x) >= 3 and x not in generic and x not in STOP_WORDS}
        common = target_set & card_tokens
        sim = float(title_similarity(title, card_title))
        price_diff_pct = abs(target_price - card_price) / max(target_price, 1.0)

        if not common:
            continue
        if len(target_set) <= 2 and len(common) < 2 and sim < 0.80:
            continue
        if price_diff_pct <= 0.08:
            price_bonus = 0.25
        elif price_diff_pct <= 0.18:
            price_bonus = 0.16
        elif price_diff_pct <= 0.30:
            price_bonus = 0.08
        else:
            continue

        score = sim + min(0.20, len(common) * 0.04) + price_bonus
        if sim < 0.72 or price_diff_pct > 0.30:
            continue

        coupon = dict(card.get("coupon") or {})
        code = str(coupon.get("code") or coupon.get("label") or "").strip().upper()
        if not code:
            continue
        d = calculate_public_coupon(coupon, target_price)
        if d <= 0:
            continue

        candidate = dict(coupon)
        candidate["desconto_estimado"] = d
        candidate["preco_base_produto"] = round(target_price, 2)
        candidate["preco_final_estimado"] = round(target_price - d, 2)
        candidate["percentual_efetivo"] = round((d / target_price) * 100, 2)
        candidate["match_type"] = "produto_publico"
        candidate["match_score"] = round(score, 3)
        candidate["public_title"] = card_title
        candidate["public_price"] = card_price
        candidate["source_url"] = card.get("source_url") or coupon.get("source_url")

        old = by_code.get(code)
        if old is None or (candidate["match_score"], candidate["desconto_estimado"]) > (old["match_score"], old["desconto_estimado"]):
            by_code[code] = candidate

    return sorted(by_code.values(), key=lambda x: (
        -float(x.get("match_score") or 0),
        -float(x.get("desconto_estimado") or 0),
    ))


def match_public_coupon(title, price, cards, item_id=None, permalink=None, allow_fallback=True):
    """Associa cupom a produto real usando título + proximidade de preço.

    A página pública do Mercado Livre mostra cupons vinculados a produtos,
    mas o preço exibido pode variar em relação ao preço retornado pela API
    (Pix, promoção, atualização do anúncio). Por isso a versão anterior ficou
    rígida demais e passou a rejeitar TODOS os cupons.

    Aqui mantemos a proteção contra cupom genérico:
      - exige palavras relevantes do produto em comum;
      - rejeita títulos com conflito claro de marca/modelo;
      - usa preço como confirmação, não como igualdade exata;
      - nunca cria cupom apenas porque o preço atende a uma faixa.
    """
    try:
        target_price = float(price)
    except Exception:
        target_price = 0.0
    if target_price <= 0:
        return None

    generic = {
        "perfume", "parfum", "eau", "de", "toilette", "fragrance",
        "original", "novo", "oficial", "kit", "com", "para", "masculino",
        "feminino", "unissex", "produto", "promocao", "oferta", "ml",
        "un", "unidade", "cor", "tamanho", "modelo", "premium"
    }

    target_tokens = [x for x in norm(title).split() if len(x) >= 3 and x not in generic and x not in STOP_WORDS]
    target_set = set(target_tokens)

    best = None
    best_score = 0.0

    for card in cards or []:
        try:
            card_price = float(card.get("price") or 0)
        except Exception:
            continue
        if card_price <= 0:
            continue

        card_title = str(card.get("title") or "")
        card_tokens = {x for x in norm(card_title).split() if len(x) >= 3 and x not in generic and x not in STOP_WORDS}
        common = target_set & card_tokens
        sim = float(title_similarity(title, card_title))
        price_diff_pct = abs(target_price - card_price) / max(target_price, 1.0)

        # Sem palavra relevante em comum não há associação produto->cupom.
        if not common:
            continue

        # Se o título é suficientemente específico, pelo menos uma palavra
        # relevante já basta; para títulos genéricos exigimos duas.
        if len(target_set) <= 2 and len(common) < 2 and sim < 0.80:
            continue

        # Preço próximo reforça a associação, mas não precisa ser idêntico.
        price_bonus = 0.0
        if price_diff_pct <= 0.08:
            price_bonus = 0.25
        elif price_diff_pct <= 0.18:
            price_bonus = 0.16
        elif price_diff_pct <= 0.30:
            price_bonus = 0.08
        else:
            continue

        score = sim + min(0.20, len(common) * 0.04) + price_bonus

        # Um título muito parecido com preço muito próximo é a melhor situação.
        if sim >= 0.72 and price_diff_pct <= 0.30 and score > best_score:
            best_score = score
            best = card

    # Limite deliberadamente moderado: ainda exige associação textual e preço,
    # mas não elimina cupons legítimos quando o preço da API mudou.
    if best is None or best_score < 0.78:
        if allow_fallback:
            fallback = _search_public_listing_for_coupon(title, price, item_id, permalink)
            return fallback
        return None

    c = dict(best["coupon"])
    c["match_score"] = round(best_score, 3)
    c["public_title"] = best["title"]
    c["public_price"] = best["price"]
    c["source_url"] = best["source_url"]
    return c

def calculate_public_coupon(coupon, price):
    if not coupon:
        return 0
    d = 0
    if coupon["type"] == "fixed":
        d = float(coupon["value"])
    else:
        d = float(price) * float(coupon["value"]) / 100
    return round(min(max(d, 0), float(price)), 2)


_PRODUCT_SEARCH_CACHE = {}
_PRODUCT_SEARCH_CACHE_LOCK = threading.Lock()
_PRODUCT_SEARCH_CACHE_TTL = 900  # 15 minutos; reduz chamadas repetidas por categoria.


def search_products_direct(q, limit=40):
    """Busca candidatos com cache curto e proteção global contra HTTP 429."""
    query = str(q or "").strip()
    lim = min(int(limit or 40), 50)
    cache_key = (norm(query), lim)
    now = time.time()
    with _PRODUCT_SEARCH_CACHE_LOCK:
        cached = _PRODUCT_SEARCH_CACHE.get(cache_key)
        if cached and now - cached[0] < _PRODUCT_SEARCH_CACHE_TTL:
            print(f"[BUSCA CACHE] {query} -> {len(cached[1])} candidatos")
            return list(cached[1])

    data, status, _ = ml_get("/products/search", {
        "site_id": SITE_ID,
        "q": query,
        "status": "active",
        "limit": lim,
        "offset": 0,
    })
    if status != 200 or not isinstance(data, dict):
        print(f"[BUSCA] {query} -> HTTP {status}")
        return []
    results = data.get("results") or []
    with _PRODUCT_SEARCH_CACHE_LOCK:
        _PRODUCT_SEARCH_CACHE[cache_key] = (time.time(), list(results))
    print(f"[BUSCA] {query} -> {len(results)} candidatos")
    return results


PUBLIC_SEARCH_FILTERS = ""  # sem obrigar frete grátis, produto novo ou origem local

def public_search_url(query):
    """Monta o link público equivalente ao teste enviado pelo usuário.

    O app continua usando a API do Mercado Livre para coletar os anúncios;
    este link serve como referência da busca pública/ordenação proposta.
    """
    clean_query = str(query or "").strip()  # não restringe os resultados da busca por filtros extras
    encoded = quote(clean_query)
    return f"https://lista.mercadolivre.com.br/{encoded}{PUBLIC_SEARCH_FILTERS}_NoIndex_True"


def search_real_listings(q, limit=50):
    """Busca produtos pelo catálogo e converte em publicações reais.

    /sites/MLB/search está retornando 403 para esta aplicação. Portanto,
    NÃO usamos essa rota para descobrir anúncios. /products/search foi
    testado com sucesso (HTTP 200) e passa a ser a fonte principal.
    """
    query = str(q or "").strip()
    lim = min(int(limit or 50), 50)

    products = search_products_direct(query, limit=lim)
    if not products:
        print(f"[BUSCA PRODUTOS] {query} -> 0 produtos")
        return []

    listings = []
    seen_items = set()

    for product_row in products:
        if not isinstance(product_row, dict):
            continue
        pid = str(product_row.get("id") or product_row.get("product_id") or "").strip()
        if not pid:
            continue

        # Primeiro aproveita eventual buy_box_winner já entregue pelo catálogo.
        candidates = []
        bb = product_row.get("buy_box_winner") or product_row.get("buy_box")
        if isinstance(bb, dict):
            candidates.append(bb)

        # Depois consulta as publicações vinculadas ao produto.
        try:
            items = product_items(pid) or []
        except Exception as exc:
            print("[BUSCA PRODUTOS] items", pid, repr(exc))
            items = []
        candidates.extend(items)

        for item in candidates:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or item.get("item_id") or "").strip()
            if not iid or iid in seen_items:
                continue

            price = item.get("price")
            if price is None:
                price = item.get("sale_price")
            try:
                price = float(price) if price is not None else None
            except Exception:
                price = None
            if price is None or price < MIN_PRODUCT_PRICE:
                continue

            # Loja Oficial NÃO é mais obrigatória. Mantemos o campo apenas
            # como informação quando o Mercado Livre o fornecer.
            official_store_id = _extract_official_store_id(item) or _extract_official_store_id(product_row)
            shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
            pictures = item.get("pictures") or product_row.get("pictures") or []
            thumbnail = item.get("thumbnail") or product_row.get("thumbnail") or ""
            seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
            seller_id = item.get("seller_id") or seller.get("id")

            row = {
                "id": iid,
                "item_id": iid,
                "product_id": pid,
                "title": item.get("title") or product_row.get("title") or product_row.get("name") or pid,
                "name": item.get("title") or product_row.get("title") or product_row.get("name") or pid,
                "permalink": item.get("permalink") or "",
                "thumbnail": item.get("thumbnail") or thumbnail,
                "pictures": pictures,
                "price": price,
                "original_price": item.get("original_price") or item.get("regular_price"),
                "seller_id": seller_id,
                "official_store_id": official_store_id,
                "sold_quantity": item.get("sold_quantity") or 0,
                "shipping": shipping,
                "free_shipping": bool(shipping.get("free_shipping") or item.get("free_shipping")),
                "logistic_type": shipping.get("logistic_type") or item.get("logistic_type"),
                "condition": item.get("condition") or "new",
                "product": product_row,
            }
            seen_items.add(iid)
            listings.append(row)
            if len(listings) >= lim:
                break
        if len(listings) >= lim:
            break

    print(f"[BUSCA PRODUTOS -> PUBLICAÇÕES] {query} -> {len(listings)} anúncios")
    return listings


# ============================================================
# DEMANDA + BUSCA — VERSÃO CORRIGIDA
# ============================================================
# O erro das versões anteriores estava aqui:
# - uma única busca por categoria;
# - uma única categoria descoberta para Highlights;
# - Highlights mistura ITEM / PRODUCT / USER_PRODUCT;
# - o buy_box_winner era tratado como condição para o produto existir.
#
# Agora:
# 1) cada categoria usa várias buscas de produtos;
# 2) tentamos várias categorias descobertas e escolhemos a que realmente
#    devolve ranking de mais vendidos;
# 3) produto sem buy_box NÃO é descartado imediatamente;
# 4) o ranking de mais vendidos também é consultado pelo PRODUCT_ID;
# 5) só no enriquecimento final tentamos descobrir uma publicação/preço;
# 6) o mínimo de R$69,90 continua sendo o único piso de preço.

DEMAND_TTL = 3600
CATEGORY_TTL = 86400
_DEMAND_CACHE = {"at": 0.0, "categories": {}}
_CATEGORY_CACHE = {"at": 0.0, "ids": {}}
_DEMAND_LOCK = threading.Lock()

CATEGORY_SEED = {cat: list(queries) for cat, queries in CATALOG.items()}

DEMAND_ANCHORS = {
    "📱 Eletrônicos": [
        "iphone", "celular", "smartphone", "fones bluetooth", "smartwatch",
        "tablet", "notebook", "carregador turbo", "power bank", "caixa de som"
    ],
    "🏠 Casa e Cozinha": [
        "air fryer", "aspirador", "cafeteira", "liquidificador", "organizador",
        "pote hermético", "utensílios", "lâmpada inteligente", "fita led"
    ],
    "👕 Moda e Beleza": [
        "tênis", "roupa", "camiseta", "bolsa", "relógio", "perfume", "maquiagem",
        "skincare", "barbeador", "acessório de moda"
    ],
    "🏋️ Academia e Esportes": [
        "creatina", "whey", "halter", "equipamento de academia", "roupa fitness",
        "tênis esportivo", "tênis corrida", "bicicleta", "acessório esportivo"
    ],
    "🌙 Perfumes Árabes": [
        "perfume árabe", "Lattafa", "Afnan", "Armaf", "Rasasi", "Al Wataniah",
        "Maison Alhambra", "Al Haramain", "French Avenue", "Fragrance World",
        "Paris Corner", "Rayhaan", "Khadlaj", "Zimaya", "Ajmal", "Swiss Arabian"
    ],
}

_PRODUCT_CACHE = {}
_PRODUCT_CACHE_LOCK = threading.Lock()
_ITEMS_CACHE = {}
_ITEMS_CACHE_LOCK = threading.Lock()
_BESTSELLER_CACHE = {}
_BESTSELLER_CACHE_LOCK = threading.Lock()


def _demand_category_from_text(text):
    t = norm(text)
    best, hits = None, 0
    for cat, anchors in DEMAND_ANCHORS.items():
        h = sum(1 for a in anchors if norm(a) in t)
        if h > hits:
            best, hits = cat, h
    return best


def _category_candidates(cat):
    """Descobre várias categorias e não fica preso à primeira resposta."""
    rows = []
    seen = set()
    for q in CATEGORY_SEED.get(cat, [])[:4]:
        try:
            found = discover_categories(q)
        except Exception as e:
            print("[CATEGORY]", cat, q, repr(e))
            continue
        for row in found:
            cid = row.get("category_id")
            name = row.get("category_name") or cid
            if not cid or cid in seen:
                continue
            seen.add(cid)
            score = sum(1 for a in DEMAND_ANCHORS.get(cat, []) if norm(a) in norm(name))
            rows.append({"category_id": cid, "category_name": name, "score": score})
    rows.sort(key=lambda x: (-x["score"], x["category_id"]))
    return rows[:8]


def _category_id_for(cat):
    now = time.time()
    with _DEMAND_LOCK:
        if now - _CATEGORY_CACHE["at"] < CATEGORY_TTL and cat in _CATEGORY_CACHE["ids"]:
            return _CATEGORY_CACHE["ids"][cat]

    candidates = _category_candidates(cat)
    if not candidates:
        return None

    # Não escolhe simplesmente a categoria com nome mais parecido.
    # Testa as categorias e usa a que realmente possui ranking Highlights.
    best_id = None
    best_count = -1
    best_score = -1
    for row in candidates:
        cid = row["category_id"]
        try:
            content = highlights(cid)
            count = len(content or [])
        except Exception:
            count = 0
        score = row["score"]
        if count > best_count or (count == best_count and score > best_score):
            best_id = cid
            best_count = count
            best_score = score
        if count >= 20:
            break

    if best_id:
        with _DEMAND_LOCK:
            _CATEGORY_CACHE["ids"][cat] = best_id
            _CATEGORY_CACHE["at"] = now
    return best_id


def _load_category_signals(cat):
    # Tenta todas as categorias candidatas até encontrar um ranking útil.
    candidates = _category_candidates(cat)
    if not candidates:
        cid = _category_id_for(cat)
        candidates = [{"category_id": cid, "category_name": cat, "score": 0}] if cid else []

    best = {}
    selected_cid = None
    selected_count = -1

    for row in candidates:
        cid = row.get("category_id")
        if not cid:
            continue
        try:
            rows = highlights(cid)
        except Exception as e:
            print("[HIGHLIGHTS]", cat, cid, repr(e))
            continue
        if len(rows or []) > selected_count:
            selected_count = len(rows or [])
            selected_cid = cid
            tmp = {}
            for item in (rows or [])[:20]:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or "").strip()
                if pid:
                    tmp[pid] = {
                        "position": int(item.get("position") or 99),
                        "type": item.get("type"),
                        "category_id": cid,
                    }
            best = tmp
        if selected_count >= 20:
            break

    trends = []
    if selected_cid:
        try:
            data, status, _ = ml_get(f"/trends/{SITE_ID}/{selected_cid}")
            if status == 200 and isinstance(data, list):
                for pos, row in enumerate(data[:50], start=1):
                    if not isinstance(row, dict):
                        continue
                    kw = str(row.get("keyword") or "").strip()
                    if not kw:
                        continue
                    trends.append({
                        "keyword": kw,
                        "rank": pos,
                        "score": 140 if pos <= 10 else 100 if pos <= 30 else 70,
                        "bucket": "ALTA FORTE" if pos <= 10 else "MAIS PROCURADO" if pos <= 30 else "TENDÊNCIA",
                    })
        except Exception as e:
            print("[TRENDS]", cat, repr(e))

    if selected_cid:
        with _DEMAND_LOCK:
            _CATEGORY_CACHE["ids"][cat] = selected_cid
            _CATEGORY_CACHE["at"] = time.time()

    return {"category_id": selected_cid, "best": best, "trends": trends}


def load_demand_signals(force=False):
    now = time.time()
    with _DEMAND_LOCK:
        if not force and _DEMAND_CACHE["categories"] and now - _DEMAND_CACHE["at"] < DEMAND_TTL:
            return dict(_DEMAND_CACHE["categories"])

    result = {}
    cats = list(CATALOG.keys())
    with _ThreadPoolExecutor(max_workers=min(6, max(1, len(cats)))) as ex:
        fmap = {ex.submit(_load_category_signals, cat): cat for cat in cats}
        for fut in as_completed(fmap):
            cat = fmap[fut]
            try:
                result[cat] = fut.result()
            except Exception as e:
                print("[DEMANDA]", cat, repr(e))
                result[cat] = {"category_id": None, "best": {}, "trends": []}

    with _DEMAND_LOCK:
        _DEMAND_CACHE["at"] = now
        _DEMAND_CACHE["categories"] = result
    return result


def _keyword_match(title, keyword):
    t = norm(title)
    k = norm(keyword)
    if not k:
        return 0
    if k in t:
        return 1.0
    words = [w for w in k.split() if len(w) >= 4]
    if not words:
        return 0
    hits = sum(1 for w in words if w in t)
    return hits / len(words)


def _direct_best_seller(product_id):
    """Consulta a posição do produto no ranking sem depender do /highlights/category."""
    pid = str(product_id or "").strip()
    if not pid:
        return None
    with _BESTSELLER_CACHE_LOCK:
        if pid in _BESTSELLER_CACHE:
            return _BESTSELLER_CACHE[pid]
    data, status, _ = ml_get(f"/highlights/{SITE_ID}/product/{pid}")
    result = None
    if status == 200 and isinstance(data, dict):
        try:
            result = {
                "position": int(data.get("position")),
                "category_id": data.get("id"),
                "label": data.get("label"),
            }
        except Exception:
            result = None
    with _BESTSELLER_CACHE_LOCK:
        _BESTSELLER_CACHE[pid] = result
    return result


def demand_score(title, category, product_id, signals, allow_direct=False):
    sig = signals.get(category, {}) if isinstance(signals, dict) else {}
    best_map = sig.get("best", {}) or {}
    trends = sig.get("trends", []) or []

    seller = best_map.get(str(product_id))
    direct = _direct_best_seller(product_id) if seller is None and allow_direct else None
    if seller is None and direct is not None:
        seller = direct

    best_pos = seller.get("position") if seller else None
    best_score = max(0.0, 100.0 - (float(best_pos or 99) - 1) * 5.0) if best_pos else 0.0

    trend_score = 0.0
    trend_rank = None
    trend_keyword = None
    trend_bucket = None
    for row in trends:
        m = _keyword_match(title, row.get("keyword"))
        if m >= 0.5:
            score = float(row.get("score") or 0) * m
            if score > trend_score:
                trend_score = score
                trend_rank = row.get("rank")
                trend_keyword = row.get("keyword")
                trend_bucket = row.get("bucket")

    both = best_pos is not None and trend_score > 0
    return {
        "trend_score": trend_score,
        "trend_rank": trend_rank,
        "trend_keyword": trend_keyword,
        "trend_bucket": trend_bucket,
        "best_seller_position": best_pos,
        "best_seller_category": (seller.get("category_id") if isinstance(seller, dict) else None),
        "best_seller_score": best_score,
        "appears_both": both,
        "demand_score": (1000 if both else 0) + best_score * 8 + trend_score * 4,
    }


BEST_SELLER_CATEGORY_IDS = {
    "📱 Celulares": "MLB1055",
    "🌸 Perfumes": "MLB178938",
    # Não existe uma categoria folha oficial separada de “Perfumes Árabes”.
    # Usamos o ranking oficial de Perfumes e, depois do enriquecimento,
    # mantemos apenas os itens claramente árabes/ligados às marcas árabes.
    "🌙 Perfumes Árabes": "MLB178938",
    "🏋️ Academia": "MLB122102",
    "🔧 Ferramentas": "MLB271379",
    "🎧 Eletrônicos": "MLB135384",
    "🏠 Casa": "MLB1645",
    "🍳 Cozinha": "MLB120373",
    "👕 Moda": "MLB1398",
}


ARABIC_PERFUME_TERMS = (
    "lattafa", "maison alhambra", "afnan", "al wataniah", "armaf", "rasasi",
    "al haramain", "french avenue", "fragrance world", "paris corner",
    "rayhaan", "khadlaj", "zimaya", "ajmal", "swiss arabian",
    "ard al zaafaran", "ahmed al maghribi", "orientica", "al rehab", "emir",
)

# Termos permitidos para a categoria de perfumes.
# Body Splash/Body Mist são permitidos pelo usuário; contratipo NÃO é.
PERFUME_POSITIVE_TERMS = (
    "perfume", "parfum", "eau de parfum", "eau de toilette", "eau de cologne",
    "fragrance", "body splash", "body mist", "colonia corporal", "colônia corporal",
    "deo colônia", "deo colonia", "desodorante colônia", "desodorante colonia",
    "spray perfumado",
)

# Itens que não devem entrar como perfume.
PERFUME_EXCLUDED_TERMS = (
    "contratipo", "contratipos", "refil", "refill", "amostra", "decant", "decante", "decants", "miniatura",
    "kit", "combo", "duo", "trio", "conjunto", "pack", "par de", "2 perfumes", "2 perfume", "dois perfumes",
    "atacado", "atacadista", "revenda", "revendedor", "lote", "caixa fechada", "caixa com", "distribuidor",
    "porta perfume", "necessaire", "estojo vazio", "frasco vazio",
    "desodorante aerosol", "pet perfume", "perfume pet", "perfume para cachorro",
    "perfume para gato", "colonia pet", "colônia pet", "perfume cachorro",
    "perfume gato", "colonia cachorro", "colônia cachorro", "colonia gato",
    "colônia gato",
)

# Marcas amplas para que a categoria de perfumes não fique presa a poucos
# termos genéricos. A busca continua limitada a fragrâncias individuais e
# o ranking/tendência é aplicado depois do enriquecimento.
PERFUME_BRAND_QUERIES = [
    # 🇧🇷 Nacionais
    "Natura perfume", "O Boticário perfume", "Eudora perfume",
    # 🌎 Importados — marcas de grande procura
    "Carolina Herrera perfume", "Rabanne perfume", "Paco Rabanne perfume",
    "Dior perfume", "Chanel perfume", "Yves Saint Laurent perfume",
    "YSL perfume", "Armani perfume", "Giorgio Armani perfume",
    "Versace perfume", "Calvin Klein perfume", "Dolce Gabbana perfume",
    "Gucci perfume", "Prada perfume", "Valentino perfume",
    "Burberry perfume", "Givenchy perfume", "Lancôme perfume",
    "Jean Paul Gaultier perfume", "JPG perfume", "Hugo Boss perfume",
    "Montblanc perfume", "Narciso Rodriguez perfume", "Mugler perfume",
    "Issey Miyake perfume", "Kenzo perfume", "Azzaro perfume",
    "Bvlgari perfume", "Jovan perfume", "Elizabeth Arden perfume",
    "Narciso Rodriguez perfume", "Jo Malone perfume", "Tom Ford perfume",
    "Creed perfume", "Parfums de Marly perfume", "Xerjoff perfume",
    "Mancera perfume", "Montale perfume", "Amouage perfume",
    "Nishane perfume", "Byredo perfume", "Maison Francis Kurkdjian perfume",
    "Initio perfume", "Diptyque perfume", "Hermès perfume", "Hermes perfume",
    "Chloé perfume", "Moschino perfume", "Marc Jacobs perfume",
    "Michael Kors perfume", "Coach perfume", "Jimmy Choo perfume",
    "Ralph Lauren perfume", "DKNY perfume", "Ferragamo perfume",
    "Jil Sander perfume", "Lacoste perfume",
    # Modelos populares nacionais
    "Natura Kaiak", "Natura Essencial", "Natura Luna", "Natura Homem",
    "Natura Una", "Natura Humor", "Natura Biografia", "Natura Ilía",
    "Natura Kriska", "Natura Águas",
    "Boticário Malbec", "Boticário Malbec Gold", "Boticário Malbec Black",
    "Boticário Malbec Bleu", "Boticário Egeo", "Boticário Lily",
    "Boticário Coffee", "Boticário Quasar", "Boticário The Blend",
    "Boticário Zaad", "Boticário Floratta", "Boticário Glamour",
    "Boticário Botica 214",
    "Eudora Club 6", "Eudora La Victorie", "Eudora Lyra", "Eudora Rouge",
    "Eudora Impression", "Eudora Instance", "Eudora Velvet Cristal",
    # Modelos populares importados
    "Dior Sauvage", "Dior J'adore", "Dior Miss Dior", "Dior Homme",
    "Chanel Bleu de Chanel", "Chanel Coco Mademoiselle", "Chanel Chance",
    "Chanel Allure", "YSL Libre", "YSL Black Opium", "YSL Y",
    "Armani Acqua di Gio", "Armani Stronger With You", "Armani My Way",
    "Carolina Herrera 212 VIP", "Carolina Herrera Good Girl", "Carolina Herrera CH",
    "Paco Rabanne 1 Million", "Paco Rabanne Invictus", "Paco Rabanne Phantom",
    "Versace Eros", "Versace Bright Crystal", "Dolce Gabbana Light Blue",
    "Dolce Gabbana The One", "Prada Luna Rossa", "Prada Paradoxe",
    "Valentino Born in Roma", "Jean Paul Gaultier Le Male", "Jean Paul Gaultier Scandal",
    "Givenchy Gentleman", "Givenchy L'Interdit", "Hugo Boss Bottled",
    "Montblanc Explorer", "Azzaro Wanted", "Azzaro The Most Wanted",
]

# Marcas árabes que aparecem nas buscas atuais do Mercado Livre, além das
# três marcas que já estavam no projeto. A categoria árabe continua exigindo
# que o título seja uma fragrância individual.
ARABIC_BRAND_QUERIES = [
    "Lattafa perfume", "Maison Alhambra perfume", "Afnan perfume",
    "Al Wataniah perfume", "Armaf perfume", "Rasasi perfume",
    "Al Haramain perfume", "French Avenue perfume", "Fragrance World perfume",
    "Paris Corner perfume", "Rayhaan perfume", "Khadlaj perfume",
    "Zimaya perfume", "Ajmal perfume", "Swiss Arabian perfume",
    "Ard Al Zaafaran perfume", "Ahmed Al Maghribi perfume",
    "Orientica perfume", "Al Rehab perfume", "Emir perfume",
]

PERFUME_TREND_QUERIES = [
    "perfumes mais vendidos", "perfumes em alta", "perfumes mais procurados",
    "perfume feminino mais vendido", "perfume masculino mais vendido",
    "perfume importado mais vendido", "perfume nacional mais vendido",
]

# Modelos adicionais para aumentar a diversidade sem depender apenas de buscas genéricas por marca.
ARABIC_MODEL_QUERIES = [
    "Lattafa Asad Zanzibar", "Lattafa Asad Bourbon", "Lattafa Yara Moi", "Lattafa Yara Tous",
    "Lattafa Nebras", "Lattafa Liam Grey", "Lattafa Liam Blue Shine", "Lattafa Teriaq",
    "Lattafa Liquid Brun", "Lattafa Eclaire", "Lattafa Fakhar Extrait", "Lattafa Najdia Tribute",
    "Lattafa Maahir Black", "Lattafa Maahir Gold", "Lattafa Qaed Al Fursan Unlimited",
    "Lattafa Ramz Silver", "Lattafa Ramz Gold", "Lattafa Vintage Radio", "Lattafa Honor and Glory",
    "Lattafa Ishq Al Shuyukh Gold", "Lattafa Sheikh Shuyukh Final Edition",
    "Afnan 9PM Pour Femme", "Afnan 9PM Dive", "Afnan Supremacy Silver", "Afnan Turathi Brown",
    "Afnan Rare Carbon", "Afnan Modest Une", "Afnan Historic Olmeda",
    "Armaf Club de Nuit Woman", "Armaf Club de Nuit Milestone", "Armaf Club de Nuit Sillage",
    "Armaf Club de Nuit Untold", "Armaf Odyssey Homme", "Armaf Odyssey Mandarin Sky",
    "Rasasi Hawas Ice", "Rasasi Hawas Black", "Rasasi Daarej", "Rasasi La Yuqawam",
    "Maison Alhambra Jean Lowe Immortal", "Maison Alhambra Jean Lowe Noir",
    "Maison Alhambra Hercules", "Maison Alhambra Yeah!", "Maison Alhambra Galatea",
    "Maison Alhambra Fabulo Intense", "Maison Alhambra Lovely Cherie",
    "Al Haramain Amber Oud Gold Edition", "Al Haramain Amber Oud Tobacco Edition",
    "Al Haramain Detour Eco", "Al Wataniah Attar Al Wesal", "Al Wataniah Kayaan Classic",
    "French Avenue Imperium", "French Avenue Liquid Brun", "French Avenue After Effect",
    "Paris Corner Khair Confection", "Paris Corner Khair Fusion", "Paris Corner Emir Celestial",
    "Khadlaj Hareem Al Sultan Gold", "Khadlaj Shiyaaka Red", "Zimaya Sharaf The Club",
    "Swiss Arabian Shaghaf Oud Azraq", "Swiss Arabian Shaghaf Oud Tonka",
]

ARABIC_TREND_QUERIES = [
    "perfumes árabes mais vendidos", "perfumes árabes em alta",
    "perfume árabe mais vendido", "perfume árabe mais procurado",
]

# Lista ampliada de fragrâncias árabes de alta procura para direcionar a busca.
# A lista combina nomes recorrentes em rankings/lojas brasileiras e não
# representa um ranking oficial nacional único, já que não existe uma base
# pública consolidada de vendas do Mercado Livre para todos os vendedores.
ARABIC_BESTSELLERS_35 = [
    "Lattafa Asad",
    "Lattafa Yara",
    "Lattafa Khamrah",
    "Lattafa Khamrah Qahwa",
    "Afnan 9PM",
    "Armaf Club de Nuit Intense Man",
    "Rasasi Hawas",
    "Lattafa Oud for Glory",
    "Lattafa Bade'e Al Oud Amethyst",
    "Lattafa Fakhar Black",
    "Lattafa Fakhar Rose",
    "Lattafa Raghba",
    "Lattafa Ana Abiyedh",
    "Lattafa Ana Abiyedh Rouge",
    "Lattafa Qaed Al Fursan",
    "Lattafa Najdia",
    "Lattafa Haya",
    "Lattafa Hayaati",
    "Lattafa Maahir Legacy",
    "Afnan 9PM Rebel",
    "Afnan Supremacy Not Only Intense",
    "Afnan Turathi Blue",
    "Al Haramain L'Aventure",
    "Maison Alhambra Detour Noir",
    "Maison Alhambra Kismet Angel",
    "Maison Alhambra Porto Neroli",
    "Maison Alhambra Bright Peach",
    "Maison Alhambra Tobacco Touch",
    "Maison Alhambra Amber & Leather",
    "Maison Alhambra Lovely Cherie",
    "Maison Alhambra Delilah",
    "Maison Alhambra Perseus",
    "Maison Alhambra The Tux",
    "Maison Alhambra Barakkat Rouge 540",
    "Maison Alhambra Woody Oud",
    "Maison Alhambra Glacier Ultra",
    "Maison Alhambra The Tux",
    "French Avenue Liquid Brun",
    "French Avenue After Effect",
    "Paris Corner Khair Pistachio",
    "Paris Corner Emir Voux Elegante",
    "Khadlaj Island",
    "Khadlaj Hareem Al Sultan",
    "Zimaya Sharaf Blend",
    "Ajmal Evoke Gold",
    "Swiss Arabian Shaghaf Oud",
    "Ard Al Zaafaran Dirham",
    "Ahmed Al Maghribi Kaaf",
    "Orientica Royal Amber",
    "Al Wataniah Sabah Al Ward",
    "Al Wataniah Kayaan Classic",
]

def _is_real_perfume(title):
    text = norm(title or "")
    if not text:
        return False
    if any(norm(term) in text for term in PERFUME_EXCLUDED_TERMS):
        return False

    # Combos explícitos de duas ou mais fragrâncias não entram.
    # Ex.: "Asad 100ml + Asad Zanzibar 100ml".
    if re.search(r"\b\d+\s*[x×]\s*\d+", text):
        return False
    if re.search(r"\b(?:2|3|4|5|6|10|12)\s*(?:unidades?|frascos?|perfumes?)\b", text):
        return False
    if re.search(r"\b(?:duas|dois|tres|três|quatro|cinco)\s*(?:unidades?|frascos?|perfumes?)\b", text):
        return False
    if re.search(r"(?:perfume|parfum|edp|edt)[^+]{0,60}\+[^+]{0,60}(?:perfume|parfum|edp|edt)", text):
        return False
    if " + " in str(title or ""):
        return False

    # A categoria 🌸 Perfumes mostra somente uma fragrância normal por oferta.
    # Não entram atacado, revenda, lotes, caixas fechadas ou anúncios de múltiplas
    # unidades, mesmo quando o título contém a palavra "perfume".
    commercial_terms = (
        "atacado", "atacadista", "revenda", "revendedor", "lote",
        "caixa fechada", "caixa com", "distribuidor", "kit atacado",
    )
    if any(norm(term) in text for term in commercial_terms):
        return False

    # Mais de uma unidade/fragrância não é uma fragrância normal individual.
    if re.search(r"\b(?:2|3|4|5|6|10|12)\s*(?:unid(?:ade|ades)?|frascos?|perfumes?|un)\b", text):
        return False
    if re.search(r"\b(?:kit|combo|pack|duo|trio|conjunto)\b", text):
        return False

    # Perfumes, EDP/EDT, Body Splash, Body Mist e colônias entram.
    if any(norm(term) in text for term in PERFUME_POSITIVE_TERMS):
        return True

    return False

def _is_arabic_perfume(title):
    text = norm(title or "")
    return _is_real_perfume(title) and any(norm(term) in text for term in ARABIC_PERFUME_TERMS)

def _is_normal_perfume_for_query(title, query=""):
    """Aceita perfumes cujo título traz marca/modelo, mas não a palavra perfume."""
    title_text = norm(title or "")
    query_text = norm(query or "")
    if not title_text:
        return False
    if any(norm(term) in title_text for term in PERFUME_EXCLUDED_TERMS):
        return False
    if any(norm(term) in title_text for term in ARABIC_PERFUME_TERMS):
        return False
    if _is_real_perfume(title):
        return True

    perfume_brands = []
    for brand_query in PERFUME_BRAND_QUERIES:
        brand = re.sub(r"\s+perfume$", "", norm(brand_query)).strip()
        if brand and brand not in perfume_brands:
            perfume_brands.append(brand)

    brand_match = any(brand in title_text for brand in perfume_brands)

    # Marcas e modelos conhecidos também podem aparecer sem a palavra
    # "perfume" no título. Ex.: "Malbec Gold 100ml", "212 VIP 100ml".
    known_model_terms = [
        "malbec", "212 vip", "212 men", "212 heroes", "212 sexy",
        "egeo", "lily", "quasar", "coffee woman", "coffee man",
        "essencial", "kaiak", "biografia", "una", "humor", "homem",
        "homem essence", "her code", "la vie est belle", "good girl",
        "carolina herrera", "sauvage", "bleu de chanel", "chance",
        "coco mademoiselle", "coco chanel", "allure", "light blue",
        "eros", "bright crystal", "libre", "black opium", "acqua di gio",
        "1 million", "one million", "phantom", "invictus", "olympea",
        "212", "boss bottled", "the scent", "wanted", "gentleman",
    ]
    model_brand_match = any(term in title_text for term in known_model_terms)

    query_words = [
        w for w in query_text.split()
        if len(w) >= 3 and w not in {
            "perfume", "perfumes", "masculino", "feminino", "importado",
            "nacional", "mais", "vendido", "vendidos", "alta",
            "procurado", "procurados",
        }
    ]
    query_match = bool(query_words) and any(w in title_text for w in query_words)

    volume_match = bool(re.search(
        r"\b(?:20|25|30|35|40|50|60|75|80|90|100|105|110|120|125|150|200|250)\s*ml\b",
        title_text,
        re.I,
    ))
    fragrance_signal = any(
        term in title_text
        for term in (
            "parfum", "eau de parfum", "eau de toilette", "eau de cologne",
            "edp", "edt", "fragrance", "body splash", "body mist",
            "colonia", "colonia corporal", "deo colonia", "spray perfumado",
        )
    )
    return bool(
        (brand_match and (volume_match or fragrance_signal))
        or (model_brand_match and (volume_match or fragrance_signal))
        or (query_match and (volume_match or fragrance_signal))
    )


_ARABIC_BRAND_CACHE = {"at": 0.0, "ids": []}
_ARABIC_BRAND_CACHE_LOCK = threading.Lock()

def _arabic_brand_ids():
    """Descobre IDs oficiais das marcas árabes na categoria de perfumes."""
    now = time.time()
    with _ARABIC_BRAND_CACHE_LOCK:
        if now - float(_ARABIC_BRAND_CACHE.get("at") or 0) < 86400:
            return list(_ARABIC_BRAND_CACHE.get("ids") or [])

    wanted = {norm(x) for x in (
        "Lattafa", "Maison Alhambra", "Afnan", "Al Wataniah", "Armaf",
        "Rasasi", "Al Haramain", "French Avenue", "Fragrance World",
        "Paris Corner", "Rayhaan", "Khadlaj", "Zimaya", "Ajmal",
        "Swiss Arabian", "Ard Al Zaafaran", "Ahmed Al Maghribi",
        "Orientica", "Al Rehab", "Emir",
    )}
    found = []
    data, status, _ = ml_get(f"/categories/{BEST_SELLER_CATEGORY_IDS['🌸 Perfumes']}/attributes")
    if status == 200 and isinstance(data, list):
        for attr in data:
            if not isinstance(attr, dict) or str(attr.get("id") or "").upper() != "BRAND":
                continue
            for value in attr.get("values") or []:
                if not isinstance(value, dict):
                    continue
                name = norm(value.get("name") or "")
                if name in wanted:
                    found.append((str(value.get("id") or ""), str(value.get("name") or "")))
            break

    with _ARABIC_BRAND_CACHE_LOCK:
        _ARABIC_BRAND_CACHE["at"] = time.time()
        _ARABIC_BRAND_CACHE["ids"] = found
    print(f"[PERFUMES ÁRABES] marcas oficiais encontradas: {len(found)}")
    return list(found)

def _search_arabic_real_listings(q, limit=80):
    """Busca perfumes usando dados de catálogo sem depender de /items/{id}.

    MOTIVO DA ALTERAÇÃO:
    o Mercado Livre pode bloquear /items/{id} e /sites/MLB/search para apps
    que não têm a liberação necessária. Nesse cenário, procurar um MLB real
    e depois confirmá-lo com /items faz a busca virar zero, mesmo quando
    /products/search e /products/{id}/items encontram a publicação.

    Para perfumes, usamos somente os dados que o próprio catálogo fornece:
      1) /products/search para descobrir os modelos;
      2) /products/{id} para buy_box_winner;
      3) /products/{id}/items para obter as publicações ligadas ao produto.

    Não fazemos GET /items/{id} nesta etapa.
    """
    query = str(q or "").strip()
    limit = min(max(int(limit or 60), 1), 60)
    if not query:
        return []

    listings = []
    seen = set()

    def add_listing(candidate, fallback_title="", fallback_product=None, position=99):
        if not isinstance(candidate, dict):
            return

        iid = str(candidate.get("item_id") or candidate.get("id") or "").strip().upper()
        if not re.fullmatch(r"MLB\d+", iid) or iid in seen:
            return

        title = str(
            candidate.get("title")
            or fallback_title
            or (fallback_product or {}).get("title")
            or (fallback_product or {}).get("name")
            or ""
        ).strip()
        if not title:
            return

        permalink = str(candidate.get("permalink") or "").strip()
        # Se o catálogo fornecer URL de produto, ela não pode ser usada como
        # anúncio. Só aceitamos permalink de publicação quando vier no item.
        if permalink and _is_catalog_permalink(permalink):
            permalink = ""

        try:
            price = float(
                candidate.get("price")
                if candidate.get("price") is not None
                else candidate.get("sale_price") or 0
            )
        except Exception:
            price = 0

        if price < MIN_PRODUCT_PRICE or price > 100000:
            return

        # Loja Oficial NÃO é mais obrigatória.
        official_store_id = _extract_official_store_id(candidate) or _extract_official_store_id(fallback_product)
        # Não consultar /items/{id} só para obter preço original: em alguns
        # ambientes essa rota responde 403. Mantemos o anúncio encontrado pelo
        # catálogo e usamos o preço original se a própria resposta o fornecer.

        shipping = candidate.get("shipping") or {}
        if not isinstance(shipping, dict):
            shipping = {}

        seller = candidate.get("seller") or {}
        if not isinstance(seller, dict):
            seller = {}

        # A publicação ligada ao catálogo nem sempre devolve "pictures".
        # Porém o próprio produto de catálogo normalmente já traz thumbnail
        # e/ou pictures. Como não podemos depender de GET /items/{id}, usamos
        # também essas imagens do produto pai como fallback.
        pictures = candidate.get("pictures") or []
        thumbnail = str(
            candidate.get("thumbnail")
            or candidate.get("secure_thumbnail")
            or candidate.get("picture_url")
            or ""
        ).strip()

        if not thumbnail and pictures and isinstance(pictures[0], dict):
            thumbnail = str(
                pictures[0].get("secure_url")
                or pictures[0].get("url")
                or pictures[0].get("secure_thumbnail")
                or pictures[0].get("thumbnail")
                or ""
            ).strip()

        fallback = fallback_product if isinstance(fallback_product, dict) else {}
        if not thumbnail:
            thumbnail = str(
                fallback.get("thumbnail")
                or fallback.get("secure_thumbnail")
                or fallback.get("picture_url")
                or ""
            ).strip()

        if not pictures:
            fpictures = fallback.get("pictures") or []
            if isinstance(fpictures, list):
                pictures = fpictures

        if not thumbnail and pictures and isinstance(pictures[0], dict):
            thumbnail = str(
                pictures[0].get("secure_url")
                or pictures[0].get("url")
                or pictures[0].get("secure_thumbnail")
                or pictures[0].get("thumbnail")
                or ""
            ).strip()

        # /products/{id}/items normalmente já fornece seller, preço, envio e
        # permalink. Se não houver permalink, ainda mantemos o candidato na
        # fila: o fluxo de geração poderá resolver a publicação posteriormente.
        listings.append({
            "id": iid,
            "item_id": iid,
            "product_id": str(
                candidate.get("catalog_product_id")
                or candidate.get("product_id")
                or (fallback_product or {}).get("id")
                or ""
            ).strip(),
            "title": title,
            "name": title,
            "permalink": permalink,
            "thumbnail": thumbnail,
            "pictures": pictures,
            "price": price,
            "original_price": candidate.get("original_price") or candidate.get("regular_price") or candidate.get("base_price"),
            "seller_id": candidate.get("seller_id") or seller.get("id"),
            "sold_quantity": candidate.get("sold_quantity") or candidate.get("sales") or 0,
            "shipping": shipping,
            "free_shipping": bool(shipping.get("free_shipping") or candidate.get("free_shipping")),
            "logistic_type": shipping.get("logistic_type") or candidate.get("logistic_type"),
            "condition": candidate.get("condition") or "new",
            "highlight_position": position,
            "source_type": "ITEM",
        })
        seen.add(iid)

    # Busca catálogo. É a rota que continua funcionando para o token atual.
    try:
        products = search_products_direct(query, limit=min(12, max(6, limit)))
    except Exception as exc:
        print("[ARABES CATALOGO]", query, repr(exc))
        products = []

    # Processa até 8 produtos de catálogo por consulta para aumentar a diversidade,
    # mantendo limite para não multiplicar chamadas sem controle.
    for product_row in products[:8]:
        if len(listings) >= limit:
            break
        if not isinstance(product_row, dict):
            continue

        pid = str(product_row.get("id") or product_row.get("product_id") or "").strip()
        if not pid:
            continue

        # 1) Buy box já presente na resposta da busca.
        bb = product_row.get("buy_box_winner") or product_row.get("buy_box")
        if isinstance(bb, dict):
            add_listing(bb, product_row.get("title") or product_row.get("name") or "", product_row, len(listings) + 1)

        # 2) Detalhe do produto: uma chamada, sem /items/{id}.
        detail = None
        try:
            detail = product(pid)
        except Exception as exc:
            print("[ARABES PRODUTO DETALHE]", pid, repr(exc))

        if isinstance(detail, dict):
            dbb = detail.get("buy_box_winner") or detail.get("buy_box")
            if isinstance(dbb, dict):
                add_listing(dbb, detail.get("name") or product_row.get("title") or "", detail, len(listings) + 1)

        # 3) Publicações ligadas ao produto. Não consultamos /items/{id}.
        try:
            items = product_items(pid) or []
        except Exception as exc:
            items = []
            print("[ARABES ITEMS]", pid, repr(exc))

        print(
            "[ARABES ITEMS STATUS]",
            pid,
            "qtd=", len(items) if isinstance(items, list) else 0,
            "produto_img=", bool(
                product_row.get("thumbnail")
                or product_row.get("secure_thumbnail")
                or product_row.get("picture_url")
                or product_row.get("pictures")
            ),
            "detalhe_img=", bool(
                isinstance(detail, dict)
                and (
                    detail.get("thumbnail")
                    or detail.get("secure_thumbnail")
                    or detail.get("picture_url")
                    or detail.get("pictures")
                )
            ),
        )

        if isinstance(items, list):
            for item in items[:10]:
                if len(listings) >= limit:
                    break
                add_listing(
                    item,
                    (detail or {}).get("name")
                    or product_row.get("title")
                    or product_row.get("name")
                    or "",
                    detail or product_row,
                    len(listings) + 1,
                )

    print(f"[ARABES ITEM REAL] {query} -> {len(listings)} anúncios (catálogo sem /items)")
    return listings[:limit]

_PUBLIC_PERFUME_CACHE = {}
_PUBLIC_PERFUME_CACHE_LOCK = threading.Lock()


def _search_perfume_public_fallback(q, limit=20):
    """Descobre ITEMs reais pela página pública do Mercado Livre.

    Tem cache curto em memória e tratamento de 429 para evitar que 35 nichos
    consecutivos derrubem a busca inteira. A função só devolve IDs MLB reais.
    """
    query = str(q or "").strip()
    if not query:
        return []

    cache_key = norm(query)
    with _PUBLIC_PERFUME_CACHE_LOCK:
        cached = _PUBLIC_PERFUME_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)[:int(limit or 20)]

    url = "https://lista.mercadolivre.com.br/" + quote(query.replace(" ", "-"))
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Cache-Control": "no-cache",
    }

    page = ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=headers, timeout=18, allow_redirects=True)
            if r.status_code == 429:
                wait = min(3, max(0.5, float(r.headers.get("Retry-After") or 1)))
                print(f"[PERFUMES FALLBACK PUBLICO] HTTP 429 {query} - aguardando {wait:.1f}s")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                print("[PERFUMES FALLBACK PUBLICO] HTTP", r.status_code, query)
                break
            page = r.text or ""
            break
        except Exception as exc:
            print("[PERFUMES FALLBACK PUBLICO] erro", query, repr(exc))
            if attempt < 2:
                time.sleep(0.6)

    if not page:
        with _PUBLIC_PERFUME_CACHE_LOCK:
            _PUBLIC_PERFUME_CACHE[cache_key] = []
        return []

    ids = []
    seen = set()
    patterns = (
        r"(?:MLB[-_]?)(\d{6,})",
        r"(?:wid|item_id|itemId|item-id)[\"'=:\s]+(?:MLB[-_]?)(\d{6,})",
        r"/MLB[-_]?(\d{6,})(?:[/?#\"'])",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, page, re.I):
            iid = "MLB" + match.group(1)
            if iid not in seen:
                seen.add(iid)
                ids.append(iid)
            if len(ids) >= max(40, int(limit or 20) * 3):
                break
        if len(ids) >= max(40, int(limit or 20) * 3):
            break

    out = []
    for pos, iid in enumerate(ids, start=1):
        if len(out) >= int(limit or 20):
            break
        try:
            data, status, _ = ml_get(f"/items/{iid}")
        except Exception as exc:
            print("[PERFUMES FALLBACK ITEM]", iid, repr(exc))
            continue
        if status != 200 or not isinstance(data, dict):
            continue

        permalink = str(data.get("permalink") or "").strip()
        if not permalink or _is_catalog_permalink(permalink):
            continue
        title = str(data.get("title") or "").strip()
        if not title:
            continue
        try:
            price = float(data.get("price") or data.get("sale_price") or 0)
        except Exception:
            price = 0
        if price < MIN_PRODUCT_PRICE or price > 100000:
            continue

        shipping = data.get("shipping") or {}
        if not isinstance(shipping, dict):
            shipping = {}
        seller = data.get("seller") or {}
        if not isinstance(seller, dict):
            seller = {}
        # Loja Oficial NÃO é obrigatória. O campo é apenas informativo.
        official_store_id = _extract_official_store_id(data)
        pictures = data.get("pictures") or []
        thumbnail = str(data.get("thumbnail") or "").strip()
        if not thumbnail and pictures and isinstance(pictures[0], dict):
            thumbnail = str(
                pictures[0].get("secure_url") or pictures[0].get("url") or
                pictures[0].get("secure_thumbnail") or pictures[0].get("thumbnail") or ""
            ).strip()

        out.append({
            "id": iid,
            "item_id": iid,
            "product_id": str(data.get("catalog_product_id") or "").strip(),
            "title": title,
            "name": title,
            "permalink": permalink,
            "thumbnail": thumbnail,
            "pictures": pictures,
            "price": price,
            "original_price": data.get("original_price") or data.get("base_price"),
            "seller_id": data.get("seller_id") or seller.get("id"),
            "official_store_id": official_store_id,
            "sold_quantity": data.get("sold_quantity") or 0,
            "shipping": shipping,
            "free_shipping": bool(shipping.get("free_shipping") or data.get("free_shipping")),
            "condition": data.get("condition") or "new",
            "highlight_position": pos,
        })

    with _PUBLIC_PERFUME_CACHE_LOCK:
        _PUBLIC_PERFUME_CACHE[cache_key] = list(out)
    print(f"[PERFUMES FALLBACK PUBLICO] {query} -> {len(out)} ITEMs reais")
    return out[:int(limit or 20)]

def _search_arabic_perfumes(fast=False):
    """Busca uma amostra ampla de perfumes árabes por marca + termos de alta.

    A descoberta usa /products/search, mas a publicação é sempre resolvida
    para um ITEM MLB real antes de entrar no resultado.
    """
    queries = []
    for q in ARABIC_BESTSELLERS_35 + ARABIC_MODEL_QUERIES + ARABIC_BRAND_QUERIES + ARABIC_TREND_QUERIES:
        if q not in queries:
            queries.append(q)

    out = []
    seen = set()
    rank_base = 1
    if fast:
        # Teste rápido: percorre uma lista grande de modelos/marcas, mas
        # limita cada consulta para reduzir 429 e ainda gerar variedade.
        queries = queries[:5]
    for q in queries:
        try:
            rows = _search_arabic_real_listings(q, limit=30 if fast else 80)
        except Exception as exc:
            print("[ARABES BUSCA]", q, repr(exc))
            continue
        for j, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            item_id = str(row.get("id") or "").strip()
            title = str(row.get("title") or "").strip()
            if not item_id or item_id in seen or not title:
                continue
            seen.add(item_id)
            out.append(({
                "id": item_id,
                "name": title,
                "title": title,
                "source_type": "ITEM",
                "highlight_position": rank_base + j,
                "highlight_category_id": BEST_SELLER_CATEGORY_IDS.get("🌸 Perfumes"),
                "permalink": row.get("permalink"),
                "thumbnail": row.get("thumbnail"),
                "pictures": row.get("pictures") or [],
                "price": row.get("price"),
                "original_price": row.get("original_price") or row.get("regular_price"),
                "seller_id": row.get("seller", {}).get("id") if isinstance(row.get("seller"), dict) else row.get("seller_id"),
                "official_store_id": row.get("official_store_id"),
            }, "🌙 Perfumes Árabes"))
        rank_base += max(40, len(rows))

    print(f"[ARABES BUSCA AMPLA] {len(out)} anúncios candidatos")
    return out

_PUBLIC_GENERIC_CACHE = {}
_PUBLIC_GENERIC_CACHE_LOCK = threading.Lock()


def _search_public_real_item_ids(q, limit=30):
    """Descobre IDs MLB reais diretamente na busca pública do Mercado Livre.

    A busca por /products/search retorna muitos IDs de catálogo. Esses IDs
    podem formar um pool grande, mas depois quase todos falham na conversão
    para uma publicação real. Para categorias comuns, a fonte mais útil para
    a etapa final é a página pública, que contém IDs MLB de anúncios.

    Aqui NÃO consultamos /items ainda. Só descobrimos os IDs; o enriquecimento
    existente (_fetch_product_fast) faz a leitura da publicação real uma única
    vez por ID. Isso evita duplicar chamadas e mantém o fluxo atual.
    """
    query = str(q or '').strip()
    if not query:
        return []
    lim = max(1, min(int(limit or 18), 30))
    key = norm(query)
    with _PUBLIC_GENERIC_CACHE_LOCK:
        cached = _PUBLIC_GENERIC_CACHE.get(key)
    if cached is not None:
        return list(cached)[:lim]

    url = 'https://lista.mercadolivre.com.br/' + quote(query.replace(' ', '-'))
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/605.1.15',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'Cache-Control': 'no-cache',
    }
    page = ''
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code == 429:
                time.sleep(min(2.0, max(0.4, float(r.headers.get('Retry-After') or 0.8))))
                continue
            if r.status_code != 200:
                print('[BUSCA PUBLICA REAL] HTTP', r.status_code, query)
                break
            page = r.text or ''
            break
        except Exception as exc:
            print('[BUSCA PUBLICA REAL] erro', query, repr(exc))
            if attempt == 0:
                time.sleep(0.4)

    ids = []
    seen_ids = set()
    if page:
        patterns = (
            r'(?:MLB[-_]?)(\d{6,})',
            r'(?:wid|item_id|itemId|item-id)["\'=:\s]+(?:MLB[-_]?)(\d{6,})',
            r'/MLB[-_]?(\d{6,})(?:[/?#"\'])',
        )
        for pattern in patterns:
            for m in re.finditer(pattern, page, re.I):
                iid = 'MLB' + m.group(1)
                if iid in seen_ids:
                    continue
                seen_ids.add(iid)
                ids.append(iid)
                if len(ids) >= lim:
                    break
            if len(ids) >= lim:
                break

    with _PUBLIC_GENERIC_CACHE_LOCK:
        _PUBLIC_GENERIC_CACHE[key] = list(ids)
    print(f'[BUSCA PUBLICA REAL] {query} -> {len(ids)} ITEMs')
    return ids[:lim]


def _search_shoes_real_listings(q, limit=60):
    """Busca publicações reais de tênis/calçados pela rota catálogo -> ITEM.

    A busca pública por IDs estava concentrando o resultado nos primeiros
    anúncios da consulta (principalmente tênis de corrida). Para tênis,
    precisamos de diversidade de MARCAS, MODELOS e SUBNICHOS. Esta rota usa
    /products/search + /products/{id}/items, que já é a rota que funciona no
    projeto, e retorna os dados da publicação diretamente sem depender de
    GET /items/{id}.
    """
    query = str(q or "").strip()
    if not query:
        return []
    lim = max(1, min(int(limit or 60), 60))
    listings = []
    seen = set()

    # Fonte primária: anúncios reais (MLB...) com título/preço/imagem já no
    # resultado. A rota de catálogo sozinha retornava poucos candidatos.
    try:
        direct_rows = _search_real_item_listings_api(query, limit=lim)
    except Exception as exc:
        print("[TENIS API REAL]", query, repr(exc))
        direct_rows = []
    for row in direct_rows:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("id") or row.get("item_id") or "").strip().upper()
        title = str(row.get("title") or row.get("name") or "").strip()
        try:
            price = float(row.get("price"))
        except (TypeError, ValueError):
            price = 0
        title_norm = norm(title)
        if (not re.fullmatch(r"MLB\d+", iid) or iid in seen or not title
                or price < MIN_PRODUCT_PRICE or price > 100000
                or any(term in title_norm for term in (
                    "chuteira", "trava society", "trava campo", "futsal",
                    "pelucia", "pelúcia", "plush", "boneco", "boneca", "brinquedo",
                    "action figure", "almofada", "chaveiro", "miniatura", "colecionavel", "colecionável"
                ))):
            continue
        row["id"] = iid
        row["item_id"] = iid
        row["title"] = title
        row["name"] = title
        row["source_type"] = "ITEM"
        seen.add(iid)
        listings.append(row)
        if len(listings) >= lim:
            break

    if len(listings) >= lim:
        print(f"[TENIS BUSCA REAL] {query} -> {len(listings)} anúncios API")
        return listings[:lim]

    try:
        products = search_products_direct(query, limit=min(10, max(6, lim)))
    except Exception as exc:
        print("[TENIS CATALOGO]", query, repr(exc))
        products = []

    for product_row in products:
        if len(listings) >= lim:
            break
        if not isinstance(product_row, dict):
            continue
        pid = str(product_row.get("id") or product_row.get("product_id") or "").strip()
        if not pid:
            continue

        candidates = []
        bb = product_row.get("buy_box_winner") or product_row.get("buy_box")
        if isinstance(bb, dict):
            candidates.append(bb)

        try:
            candidates.extend(product_items(pid) or [])
        except Exception as exc:
            print("[TENIS ITEMS]", pid, repr(exc))

        # Se a busca direta não trouxe itens, tenta o detalhe do catálogo
        # apenas para recuperar o buy box/publicação ligada ao produto.
        if not candidates:
            try:
                detail = product(pid)
                if isinstance(detail, dict):
                    dbb = detail.get("buy_box_winner") or detail.get("buy_box")
                    if isinstance(dbb, dict):
                        candidates.append(dbb)
            except Exception as exc:
                print("[TENIS PRODUTO]", pid, repr(exc))

        for item in candidates:
            if len(listings) >= lim:
                break
            if not isinstance(item, dict):
                continue
            iid = str(item.get("id") or item.get("item_id") or "").strip().upper()
            if not re.fullmatch(r"MLB\d+", iid) or iid in seen:
                continue

            price = item.get("price")
            if price is None:
                price = item.get("sale_price")
            try:
                price = float(price) if price is not None else None
            except Exception:
                price = None
            if price is None or price < MIN_PRODUCT_PRICE or price > 100000:
                continue

            title = str(
                item.get("title")
                or product_row.get("title")
                or product_row.get("name")
                or ""
            ).strip()
            if not title:
                continue
            if any(term in norm(title) for term in (
                "chuteira", "trava society", "trava campo", "futsal",
                "pelucia", "pelúcia", "plush", "boneco", "boneca", "brinquedo",
                "action figure", "almofada", "chaveiro", "miniatura", "colecionavel", "colecionável"
            )):
                continue

            shipping = item.get("shipping") if isinstance(item.get("shipping"), dict) else {}
            seller = item.get("seller") if isinstance(item.get("seller"), dict) else {}
            pictures = item.get("pictures") or product_row.get("pictures") or []
            thumbnail = str(item.get("thumbnail") or product_row.get("thumbnail") or "").strip()
            if not thumbnail and isinstance(pictures, list):
                for pic in pictures:
                    if isinstance(pic, dict):
                        thumbnail = str(
                            pic.get("secure_url") or pic.get("url") or
                            pic.get("secure_thumbnail") or pic.get("thumbnail") or ""
                        ).strip()
                        if thumbnail:
                            break

            seen.add(iid)
            listings.append({
                "id": iid,
                "item_id": iid,
                "product_id": pid,
                "title": title,
                "name": title,
                "permalink": item.get("permalink") or "",
                "thumbnail": thumbnail,
                "pictures": pictures,
                "price": price,
                "original_price": item.get("original_price") or item.get("regular_price"),
                "seller_id": item.get("seller_id") or seller.get("id"),
                "official_store_id": _extract_official_store_id(item) or _extract_official_store_id(product_row),
                "sold_quantity": item.get("sold_quantity") or 0,
                "shipping": shipping,
                "free_shipping": bool(shipping.get("free_shipping") or item.get("free_shipping")),
                "logistic_type": shipping.get("logistic_type") or item.get("logistic_type"),
                "condition": item.get("condition") or "new",
                "source_type": "ITEM",
            })

    print(f"[TENIS BUSCA REAL] {query} -> {len(listings)} anúncios")
    return listings[:lim]


def _shoe_diverse_queries(fast=False):
    """Consultas de calçados limitadas aos subnichos definidos no catálogo."""
    groups = [
        [
            "Nike tênis corrida", "Adidas tênis corrida", "Asics tênis corrida",
            "Mizuno tênis corrida", "Olympikus tênis corrida", "New Balance tênis corrida",
            "Fila tênis corrida", "Puma tênis corrida",
        ],
        [
            "Nike tênis casual", "Adidas tênis casual", "Vans tênis casual",
            "Converse tênis casual", "Lacoste tênis casual", "Puma tênis casual", "Skechers tênis casual",
        ],
        [
            "Nike LeBron", "Jordan tênis basquete", "Adidas Harden", "Under Armour Curry",
        ],
        [
            "Vans skate", "Converse skate", "Nike SB", "DC Shoes skate",
        ],
        [
            "Lacoste tênis premium", "Oakley tênis", "New Balance premium", "Converse premium",
        ],
        [
            "Nike slide", "Adidas slide", "Puma slide", "Havaianas", "Rider", "Ipanema",
        ],
        [
            "Nike tênis feminino", "Adidas tênis feminino", "Puma tênis feminino", "Asics tênis feminino",
            "New Balance tênis feminino", "Mizuno tênis feminino", "Olympikus tênis feminino",
            "Fila tênis feminino", "Skechers tênis feminino",
        ],
        [
            "Nike tênis masculino", "Adidas tênis masculino", "Puma tênis masculino", "Asics tênis masculino",
            "New Balance tênis masculino", "Mizuno tênis masculino", "Olympikus tênis masculino",
            "Fila tênis masculino", "Reebok tênis masculino", "Skechers tênis masculino",
        ],
        ["tênis infantil", "tênis infantil masculino", "tênis infantil feminino"],
    ]
    merged = []
    pos = 0
    while True:
        added = False
        for group in groups:
            if pos < len(group):
                q = group[pos]
                if q not in merged:
                    merged.append(q)
                added = True
        if not added:
            break
        pos += 1
    limit = 40 if fast else 60
    return merged[:limit]


def _search_shoes_category(cat, fast=False):
    """Busca tênis/calçados com diversidade real de marcas e modelos."""
    queries = _shoe_diverse_queries(fast=fast)
    # Buscar todas as categorias não pode disparar dezenas de consultas só de
    # calçados. A busca individual continua usando a lista ampla completa.
    if fast:
        queries = queries[:12]
    per_query = 24 if fast else 30
    rows_by_query = []

    # Busca várias consultas em paralelo, mantendo a ordem original para que
    # a montagem round-robin continue priorizando diversidade entre marcas.
    def _run_shoe_query(q):
        try:
            return _search_shoes_real_listings(q, limit=per_query)
        except Exception as exc:
            print("[TENIS BUSCA]", q, repr(exc))
            return []

    with _ThreadPoolExecutor(max_workers=min(5, max(1, len(queries)))) as pool:
        future_by_query = {q: pool.submit(_run_shoe_query, q) for q in queries}
        for q in queries:
            try:
                rows_by_query.append((q, future_by_query[q].result()))
            except Exception as exc:
                print("[TENIS BUSCA RESULTADO]", q, repr(exc))
                rows_by_query.append((q, []))

    # Round-robin entre consultas: primeiro entra 1 produto de cada marca,
    # depois o segundo de cada marca. Assim Nike/Puma/Fila não ocupam toda a
    # primeira página antes de Adidas/Asics/Mizuno/New Balance etc.
    out = []
    seen = set()
    round_index = 0
    target = SEARCH_RAW_POOL_PER_CATEGORY if fast else min(500, SEARCH_CANDIDATES_PER_CATEGORY_SINGLE)
    while len(out) < target:
        progressed = False
        for q, rows in rows_by_query:
            if round_index >= len(rows):
                continue
            row = rows[round_index]
            progressed = True
            if not isinstance(row, dict):
                continue
            iid = str(row.get("id") or row.get("item_id") or "").strip().upper()
            if not re.fullmatch(r"MLB\d+", iid) or iid in seen:
                continue
            seen.add(iid)
            out.append(({
                **row,
                "id": iid,
                "item_id": iid,
                "title": row.get("title") or row.get("name") or iid,
                "name": row.get("title") or row.get("name") or iid,
                "source_type": "ITEM",
                "highlight_position": len(out) + 1,
                "public_query": q,
            }, cat))
            if len(out) >= target:
                break
        if not progressed:
            break
        round_index += 1

    print(f"[TENIS DIVERSIDADE] {cat}: {len(out)} candidatos de {len(queries)} consultas")
    return out

# Circuit breaker: somente 403 bloqueia definitivamente este endpoint.
# 429 é temporário: entra em pausa temporizada e depois pode tentar novamente.
_REAL_ITEM_SEARCH_API_BLOCKED = False
_REAL_ITEM_SEARCH_API_BLOCKED_LOCK = threading.Lock()
_REAL_ITEM_SEARCH_API_BLOCKED_LOGGED = False
_REAL_ITEM_SEARCH_API_RETRY_AT = 0.0
_REAL_ITEM_SEARCH_API_429_LOGGED = False


def _search_real_item_listings_api(q, limit=50, offset=0):
    """Busca anúncios reais sem desativar o endpoint permanentemente por 429."""
    global _REAL_ITEM_SEARCH_API_BLOCKED, _REAL_ITEM_SEARCH_API_BLOCKED_LOGGED
    global _REAL_ITEM_SEARCH_API_RETRY_AT, _REAL_ITEM_SEARCH_API_429_LOGGED
    query = str(q or "").strip()
    if not query:
        return []
    with _REAL_ITEM_SEARCH_API_BLOCKED_LOCK:
        if _REAL_ITEM_SEARCH_API_BLOCKED:
            return []
        if time.monotonic() < _REAL_ITEM_SEARCH_API_RETRY_AT:
            return []
    try:
        lim = max(1, min(int(limit or 50), 50))
        off = max(0, int(offset or 0))
    except Exception:
        lim, off = 50, 0

    data, status, _ = ml_get(f"/sites/{SITE_ID}/search", {
        "q": query,
        "limit": lim,
        "offset": off,
        "sort": "relevance",
    })
    if status != 200 or not isinstance(data, dict):
        if status == 403:
            with _REAL_ITEM_SEARCH_API_BLOCKED_LOCK:
                _REAL_ITEM_SEARCH_API_BLOCKED = True
                should_log = not _REAL_ITEM_SEARCH_API_BLOCKED_LOGGED
                _REAL_ITEM_SEARCH_API_BLOCKED_LOGGED = True
            if should_log:
                print("[BUSCA ITEMS API] HTTP 403: endpoint não autorizado; usando fontes alternativas até reiniciar o processo.")
        elif status == 429:
            with _REAL_ITEM_SEARCH_API_BLOCKED_LOCK:
                _REAL_ITEM_SEARCH_API_RETRY_AT = time.monotonic() + 120.0
                should_log = not _REAL_ITEM_SEARCH_API_429_LOGGED
                _REAL_ITEM_SEARCH_API_429_LOGGED = True
            if should_log:
                print("[BUSCA ITEMS API] HTTP 429: pausa de 120s só neste endpoint; /products/search e cache continuam ativos.")
        else:
            print(f"[BUSCA ITEMS API] {query} -> HTTP {status}")
        return []

    rows = data.get("results") or []
    out = []
    for pos, row in enumerate(rows, start=off + 1):
        if not isinstance(row, dict):
            continue
        iid = str(row.get("id") or "").strip().upper()
        title = str(row.get("title") or "").strip()
        if not re.fullmatch(r"MLB\d+", iid) or not title:
            continue
        out.append({
            "id": iid,
            "item_id": iid,
            "title": title,
            "name": title,
            "source_type": "ITEM",
            "permalink": row.get("permalink"),
            "thumbnail": row.get("thumbnail"),
            "pictures": row.get("pictures") or [],
            "price": row.get("price"),
            "original_price": row.get("original_price"),
            "seller_id": (row.get("seller") or {}).get("id") if isinstance(row.get("seller"), dict) else row.get("seller_id"),
            "seller": row.get("seller"),
            "shipping": row.get("shipping") or {},
            "condition": row.get("condition") or "new",
            "sold_quantity": row.get("sold_quantity") or 0,
            "listing_type_id": row.get("listing_type_id"),
            "highlight_position": pos,
        })
    print(f"[BUSCA ITEMS API] {query} -> {len(out)} anúncios reais")
    return out


def _category_real_item_seed_queries(cat, fast=False):
    """Retorna sementes limpas para a busca de anúncios reais.

    Não usamos todos os 117 termos de uma vez: isso seria lento e aumentaria
    o risco de 429. A lista mantém diversidade por subnicho e usa uma amostra
    grande na busca manual de uma categoria.
    """
    seeds = []
    for seed in CATALOG.get(cat, []):
        clean = str(seed or "").strip()
        if not clean or clean == cat:
            continue
        clean = re.sub(r"^[^A-Za-zÀ-ÿ0-9]+", "", clean).strip()
        if not clean or clean.lower() in {"e", "ou"}:
            continue
        if clean not in seeds:
            seeds.append(clean)

    # Prioriza consultas mais específicas e mantém uma consulta ampla no início.
    priority = []
    for x in seeds:
        n = norm(x)
        if cat == "👟 Tênis & Calçados" and any(k in n for k in (
            "tenis", "corrida", "casual", "futebol", "chuteira", "basquete",
            "trilha", "skate", "feminino", "masculino", "infantil", "nike",
            "adidas", "asics", "mizuno", "olympikus", "fila", "puma",
        )):
            priority.append(x)
        elif cat == "📱 Tecnologia" and any(k in n for k in (
            "iphone", "samsung", "motorola", "xiaomi", "celular", "smartphone",
            "notebook", "tablet", "fone", "smartwatch", "teclado", "mouse",
        )):
            priority.append(x)
        elif cat == "💪 Academia & Fitness" and any(k in n for k in (
            "creatina", "whey", "halter", "anilha", "barra", "esteira", "banco",
            "suplement", "academia", "fitness",
        )):
            priority.append(x)
        elif cat == "🏠 Casa e Organização" and any(k in n for k in (
            "guarda", "armario", "organizador", "cozinha", "air fryer", "cafeteira",
            "aspirador", "estante", "prateleira", "mesa", "cadeira", "cama",
        )):
            priority.append(x)
        elif cat == "👕 Moda" and any(k in n for k in (
            "camiseta", "camisa", "bermuda", "short", "calca", "jeans", "moletom",
            "jaqueta", "polo", "vestido", "nike", "adidas", "puma",
        )):
            priority.append(x)
        elif cat == "💇 Saúde & Beleza" and any(k in n for k in (
            "shampoo", "cabelo", "skincare", "protetor", "hidratante", "barbeador",
            "aparador", "maquiagem", "esmalte", "secador", "chapinha",
        )):
            priority.append(x)

    merged=[]
    for x in priority + seeds:
        if x not in merged:
            merged.append(x)
    return merged[:(SEARCH_REAL_ITEM_QUERIES_ALL if fast else SEARCH_REAL_ITEM_QUERIES_SINGLE)]


def _search_category(cat, fast=False):
    """Monta uma fila ampla de candidatos usando somente as buscas da categoria."""
    if cat == "🌙 Perfumes Árabes":
        return _search_arabic_perfumes(fast=fast)

    # Perfumes precisam de uma rota própria: o ranking Highlights de MLB178938
    # pode trazer poucos/nenhum candidato útil para os filtros finais.
    # Buscamos anúncios reais por vários termos positivos e deixamos o
    # enriquecimento /items resolver preço, vendedor e imagem.
    if cat == "👟 Tênis & Calçados":
        return _search_shoes_category(cat, fast=fast)

    if cat == "🌸 Perfumes":
        out = []
        seen = set()
        # Prioridade da busca: aumentar a presença de perfumes masculinos
        # importados sem retirar os femininos. A lista prioritária entra antes
        # das demais porque o modo rápido usa as primeiras 40 consultas.
        PERFUME_PRIORITY_QUERIES = [
            # 🌎 Consultas amplas: aumentam o universo de publicações antes
            # de entrar nos modelos específicos. Isso é importante porque uma
            # única marca pode ter dezenas de anúncios diferentes com desconto.
            "perfume masculino importado", "perfumes masculinos importados",
            "perfume masculino original importado", "perfumes importados masculinos",
            "perfume feminino importado", "perfumes femininos importados",
            "perfume importado masculino promoção", "perfume importado feminino promoção",
            "perfume masculino desconto", "perfume feminino desconto",
            "perfumes importados promoção", "perfumes importados desconto",
            # 🌎 Masculinos importados — prioridade maior
            "Dior Sauvage", "Dior Homme", "Dior Homme Intense",
            "Chanel Bleu de Chanel", "Chanel Allure Homme Sport",
            "Yves Saint Laurent Y", "Yves Saint Laurent La Nuit de L'Homme",
            "Yves Saint Laurent Y Eau de Parfum",
            "Giorgio Armani Acqua di Gio", "Armani Code",
            "Armani Stronger With You", "Armani Acqua di Gio Profumo",
            "Versace Eros", "Versace Dylan Blue", "Versace Pour Homme",
            "Paco Rabanne 1 Million", "Paco Rabanne Invictus",
            "Paco Rabanne Phantom", "Rabanne 1 Million",
            "Jean Paul Gaultier Le Male", "Jean Paul Gaultier Ultra Male",
            "Jean Paul Gaultier Le Beau", "Jean Paul Gaultier Scandal Pour Homme",
            "Hugo Boss Bottled", "Hugo Boss The Scent",
            "Montblanc Explorer", "Montblanc Legend Spirit",
            "Azzaro Wanted", "Azzaro The Most Wanted",
            "Givenchy Gentleman", "Lacoste L.12.12 Blanc",
            "Bvlgari Man in Black", "Calvin Klein Eternity Men",
            "Issey Miyake L'Eau d'Issey Pour Homme",
            # 🌎 Femininos importados — continuam com boa presença
            "Carolina Herrera Good Girl", "Carolina Herrera 212 VIP",
            "Dior J'adore", "Dior Miss Dior",
            "Chanel Coco Mademoiselle", "Chanel Chance",
            "Yves Saint Laurent Libre", "Yves Saint Laurent Black Opium",
            "Armani My Way", "Versace Bright Crystal",
            "Dolce Gabbana Light Blue", "Dolce Gabbana The One",
            "Paco Rabanne Olympea", "Jean Paul Gaultier La Belle",
            "Givenchy L'Interdit", "Valentino Born in Roma",
            "Prada Paradoxe", "Burberry Her",
            "Narciso Rodriguez For Her", "Lancôme La Vie Est Belle",
        ]

        perfume_queries = []
        for q in (PERFUME_PRIORITY_QUERIES + list(CATALOG["🌸 Perfumes"]) + PERFUME_BRAND_QUERIES + PERFUME_TREND_QUERIES):
            if q not in perfume_queries:
                perfume_queries.append(q)

        rank_base = 1
        if fast:
            # Mantemos 40 consultas no modo rápido, mas agora as primeiras
            # consultas são majoritariamente importadas e masculinas.
            perfume_queries = perfume_queries[:12]
        for q in perfume_queries:
            # Mantém o micro-nicho exatamente como definido e acrescenta apenas
            # a exclusão operacional de decant na consulta.
            search_q = q
            print("[BUSCA PUBLICA EQUIVALENTE]", public_search_url(q))
            try:
                # Usa a mesma rota de ITEM real que já funciona para os
                # perfumes árabes. Isso evita depender somente do catálogo
                # /products/search, que pode retornar produto sem publicação
                # utilizável para o perfume normal.
                rows = _search_arabic_real_listings(
                    search_q, limit=50 if fast else 60
                )

                # IMPORTANTE: para perfumes normais, não podemos usar a busca
                # pública somente quando `rows` vier vazia. O catálogo pode
                # devolver alguns ITEMs válidos, mas eles podem ser títulos que
                # o filtro final rejeita (ex.: marca/modelo sem a palavra
                # "perfume"). Nesse caso a busca pública ainda precisa ser
                # consultada para trazer outras publicações reais.
                #
                # Mesclamos as duas fontes e deixamos a etapa final decidir
                # quais anúncios realmente entram. Isso não altera as outras
                # categorias nem o fluxo de afiliado/WhatsApp.
                public_rows = _search_perfume_public_fallback(
                    search_q, limit=22 if fast else 30
                )
                if public_rows:
                    known_ids = {
                        str(x.get("id") or x.get("item_id") or "").strip()
                        for x in rows
                        if isinstance(x, dict)
                    }
                    rows = list(rows) + [
                        x for x in public_rows
                        if str(x.get("id") or x.get("item_id") or "").strip()
                        not in known_ids
                    ]
            except Exception as exc:
                print("[PERFUMES BUSCA ITEM]", q, repr(exc))
                try:
                    rows = _search_perfume_public_fallback(
                        search_q, limit=22 if fast else 30
                    )
                except Exception as fallback_exc:
                    print("[PERFUMES FALLBACK]", q, repr(fallback_exc))
                    rows = []

            for j, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                item_id = str(row.get("id") or "").strip()
                if not item_id or item_id in seen:
                    continue
                seen.add(item_id)
                out.append(({
                    "id": item_id,
                    "name": row.get("title") or item_id,
                    "title": row.get("title") or item_id,
                    "source_type": "ITEM",
                    "highlight_position": rank_base + j,
                    "highlight_category_id": BEST_SELLER_CATEGORY_IDS.get(cat),
                    "permalink": row.get("permalink"),
                    "thumbnail": row.get("thumbnail"),
                    "pictures": row.get("pictures") or [],
                    "price": row.get("price"),
                    "original_price": row.get("original_price") or row.get("regular_price"),
                    "seller_id": row.get("seller", {}).get("id") if isinstance(row.get("seller"), dict) else row.get("seller_id"),
                    "official_store_id": row.get("official_store_id"),
                }, cat))
            rank_base += max(50, len(rows))

        print(f"[PERFUMES BUSCA REAL] {len(out)} anúncios candidatos")
        return out

    out = []
    seen = set()

    # V44 — FONTE PRINCIPAL: página pública do Mercado Livre.
    # A API /sites/MLB/search frequentemente retorna 403 neste projeto.
    # Quando isso acontece, a versão anterior voltava para /products/search,
    # que entrega IDs de catálogo e fazia o pool 184 -> apenas 4 ofertas.
    # Agora descobrimos primeiro IDs MLB reais na página pública e os enviamos
    # ao mesmo pipeline de /items, preço, imagem e coerência.
    real_target = SEARCH_RAW_POOL_PER_CATEGORY if fast else min(500, SEARCH_CANDIDATES_PER_CATEGORY_SINGLE)
    public_queries = _category_real_item_seed_queries(cat, fast=fast)
    # Na busca individual usamos até 25 consultas públicas; no modo todas
    # usamos 8 para preservar tempo. Cada consulta pode devolver vários IDs.
    if fast:
        public_queries = public_queries[:SEARCH_REAL_ITEM_QUERIES_ALL]
    else:
        public_queries = public_queries[:40]

    def _public_ids_for_query(q):
        try:
            return q, _search_public_real_item_ids(q, limit=30)
        except Exception as exc:
            print("[BUSCA PUBLICA REAL]", cat, q, repr(exc))
            return q, []

    public_added = 0
    # Concorrência moderada: suficiente para não ficar lento, sem bombardear
    # o site público e provocar 429.
    with _ThreadPoolExecutor(max_workers=min(6, max(1, len(public_queries)))) as ex:
        futures = [ex.submit(_public_ids_for_query, q) for q in public_queries]
        for fut in as_completed(futures):
            q, ids = fut.result()
            for iid in ids:
                iid = str(iid or '').strip().upper()
                if not re.fullmatch(r"MLB\d+", iid) or iid in seen:
                    continue
                seen.add(iid)
                out.append(({
                    "id": iid,
                    "item_id": iid,
                    "title": "",
                    "name": "",
                    "source_type": "ITEM",
                    "public_query": q,
                }, cat))
                public_added += 1
                if len(out) >= real_target:
                    break
            if len(out) >= real_target:
                break

    print(f"[V59 BUSCA PUBLICA REAL] {cat}: +{public_added} ITEMs reais | pool inicial={len(out)}")

    # Segunda fonte: API de anúncios reais. Se estiver liberada, complementa
    # a busca pública; se devolver 403, não impede a primeira fonte.
    if len(out) < real_target:
        real_queries = _category_real_item_seed_queries(cat, fast=fast)
        real_added = 0
        for q in real_queries:
            if len(out) >= real_target:
                break
            try:
                rows = _search_real_item_listings_api(q, limit=SEARCH_REAL_ITEM_RESULTS_PER_QUERY)
            except Exception as exc:
                print("[BUSCA ITEMS API]", cat, q, repr(exc))
                rows = []
            for row in rows:
                iid = str(row.get("id") or "").strip().upper()
                if not re.fullmatch(r"MLB\d+", iid) or iid in seen:
                    continue
                seen.add(iid)
                out.append((row, cat))
                real_added += 1
                if len(out) >= real_target:
                    break
        print(f"[V61 API ITEMS] {cat}: +{real_added} | pool={len(out)}")

    category_id = BEST_SELLER_CATEGORY_IDS.get(cat)
    if not category_id:
        try:
            category_id = _category_id_for(cat)
        except Exception as exc:
            print("[TOP 20] categoria", cat, repr(exc))

    if category_id:
        try:
            ranking = highlights(category_id) or []
        except Exception as exc:
            print("[TOP 20] highlights", cat, repr(exc))
            ranking = []
        for position, row in enumerate(ranking[:20], start=1):
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip()
            typ = str(row.get("type") or "").upper().strip()
            if not pid or pid in seen or typ not in {"ITEM", "PRODUCT", "USER_PRODUCT"}:
                continue
            seen.add(pid)
            out.append(({
                "id": pid,
                "name": row.get("title") or row.get("name") or pid,
                "title": row.get("title") or row.get("name") or pid,
                "source_type": typ,
                "highlight_position": row.get("position") or position,
                "highlight_category_id": category_id,
            }, cat))

    # Complementa com consultas específicas. No modo rápido usamos 12 sementes
    # por categoria para combinar subnicho + marcas e ampliar a descoberta.
    # A busca manual continua usando todas as sementes da categoria.
    rank_base = 100
    seed_queries = []
    for seed in CATALOG.get(cat, []):
        # Entradas que são somente cabeçalhos visuais (emoji + nome da seção)
        # não devem consumir uma chamada de busca.
        clean_seed = str(seed or '').strip()
        if not clean_seed:
            continue
        if clean_seed == cat:
            continue
        if re.match(r'^[^A-Za-zÀ-ÿ0-9]+$', clean_seed):
            continue
        # Cabeçalhos como "📱 Celulares" / "🥤 Suplementação" podem ser
        # úteis como contexto visual, mas não são consultas tão boas quanto
        # os termos reais logo abaixo deles.
        if re.match(r'^[^A-Za-zÀ-ÿ0-9]*[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 &/+-]*$', clean_seed) and clean_seed.count(' ') <= 4 and any(ch in clean_seed for ch in '📱🏠💪🏃🏋️🥤💇💅🧴🪒🧖👕🏀🧥🩳👖👚👔🩱🧢👟⚽'):
            clean_seed = re.sub(r'^[^A-Za-zÀ-ÿ0-9]+', '', clean_seed).strip()
        if clean_seed and clean_seed not in seed_queries:
            seed_queries.append(clean_seed)
        if fast and len(seed_queries) >= SEARCH_SEEDS_FAST_PER_CATEGORY:
            break

    for q in seed_queries:
        try:
            rows = search_products_direct(
                q,
                limit=SEARCH_RESULTS_PER_QUERY_FAST if fast else 50,
            )
        except Exception as exc:
            print("[BUSCA ESPECIFICA]", cat, q, repr(exc))
            continue
        for j, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or row.get("product_id") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out.append(({
                "id": pid,
                "name": row.get("title") or row.get("name") or pid,
                "title": row.get("title") or row.get("name") or pid,
                "source_type": str(row.get("type") or "PRODUCT").upper(),
                "highlight_position": rank_base + j,
                "highlight_category_id": category_id,
            }, cat))
        rank_base += SEARCH_RESULTS_PER_QUERY_FAST if fast else 50
        if len(out) >= (SEARCH_RAW_POOL_PER_CATEGORY if fast else 180):
            break

    # COMPLEMENTO: o /products/search forma um pool grande de CATÁLOGOS,
    # mas a etapa de enriquecimento pode converter apenas poucos deles em
    # publicações reais. Isso era exatamente o que fazia 184 candidatos
    # virarem 4 ofertas.
    #
    # Complementamos a descoberta com a busca pública, que entrega IDs MLB
    # de anúncios reais. Esses IDs entram como source_type=ITEM e seguem o
    # mesmo enriquecimento/validação já existente. Não alteramos os filtros
    # de coerência, preço ou imagem.
    public_target = SEARCH_RAW_POOL_PER_CATEGORY if fast else min(500, SEARCH_CANDIDATES_PER_CATEGORY_SINGLE)
    if len(out) < public_target:
        public_queries = seed_queries[:8] if fast else seed_queries[:14]
        public_added = 0
        for q in public_queries:
            try:
                real_ids = _search_public_real_item_ids(q, limit=30 if fast else 30)
            except Exception as exc:
                print('[BUSCA PUBLICA COMPLEMENTAR]', cat, q, repr(exc))
                continue
            for iid in real_ids:
                if any(str(row.get('id') or '') == iid for row, _ in out):
                    continue
                out.append(({
                    'id': iid,
                    'name': iid,
                    'title': iid,
                    'source_type': 'ITEM',
                    'highlight_position': 10000 + public_added,
                    'highlight_category_id': category_id,
                    'public_query': q,
                }, cat))
                public_added += 1
                if len(out) >= public_target:
                    break
            if len(out) >= public_target:
                break
        print(f'[BUSCA PUBLICA COMPLEMENTAR] {cat}: +{public_added} ITEMs reais | total bruto={len(out)}')

    print(f"[TOP 20] {cat}: {len(out)} candidatos amplos")
    return out


def _get_item_quality(item_id):
    """Lê o anúncio real para validar Full + vendas antes de exibir."""
    item_id = str(item_id or "").strip()
    if not item_id:
        return None
    with _ITEM_QUALITY_CACHE_LOCK:
        if item_id in _ITEM_QUALITY_CACHE:
            return _ITEM_QUALITY_CACHE[item_id]

    data, status, _ = ml_get(f"/items/{item_id}")
    if status != 200 or not isinstance(data, dict):
        result = None
    else:
        shipping = data.get("shipping") or {}
        if not isinstance(shipping, dict):
            shipping = {}
        sold = data.get("sold_quantity")
        try:
            sold = int(float(sold or 0))
        except Exception:
            sold = 0
        result = {
            "item_id": data.get("id") or item_id,
            "seller_id": data.get("seller_id"),
            "official_store_id": _extract_official_store_id(data),
            "sold_quantity": sold,
            "logistic_type": shipping.get("logistic_type"),
            "shipping_mode": shipping.get("mode"),
            "free_shipping": bool(shipping.get("free_shipping")),
            "price": data.get("price"),
            "original_price": data.get("original_price"),
            "permalink": data.get("permalink"),
            "condition": data.get("condition"),
            "listing_type_id": data.get("listing_type_id"),
        }

    with _ITEM_QUALITY_CACHE_LOCK:
        _ITEM_QUALITY_CACHE[item_id] = result
    return result


def _get_seller_quality(seller_id):
    """Lê a reputação pública do vendedor e identifica Gold/Platinum."""
    sid = str(seller_id or "").strip()
    if not sid:
        return None
    with _SELLER_QUALITY_CACHE_LOCK:
        if sid in _SELLER_QUALITY_CACHE:
            return _SELLER_QUALITY_CACHE[sid]

    data, status, _ = ml_get(f"/users/{sid}")
    if status != 200 or not isinstance(data, dict):
        result = None
    else:
        rep = data.get("seller_reputation") or {}
        status_name = str(rep.get("power_seller_status") or "").strip().lower()
        # A documentação do Mercado Livre identifica Gold/Platinum em
        # seller_reputation.power_seller_status.
        result = {
            "seller_id": data.get("id") or sid,
            "power_seller_status": status_name or None,
            "level_id": rep.get("level_id"),
            "completed_sales": ((rep.get("transactions") or {}).get("completed") or 0),
            "seller_nickname": data.get("nickname"),
        }

    with _SELLER_QUALITY_CACHE_LOCK:
        _SELLER_QUALITY_CACHE[sid] = result
    return result


def _approve_real_item(item_id, base_item=None):
    """Valida somente que o ITEM é uma publicação real.

    Os filtros antigos de Full, Gold/Platinum e 100 vendas foram removidos.
    Isso evita que a busca fique vazia quando o Mercado Livre não retorna
    esses dados para determinado anúncio/vendedor.
    """
    item_id = str(item_id or "").strip()
    if not item_id:
        return None

    real = _get_item_quality(item_id)
    if not real:
        return None

    approved = dict(base_item or {})
    approved.update({
        "item_id": real.get("item_id") or item_id,
        "seller_id": real.get("seller_id") or approved.get("seller_id"),
        "official_store_id": real.get("official_store_id") or approved.get("official_store_id"),
        "sold_quantity": int(real.get("sold_quantity") or 0),
        "logistic_type": real.get("logistic_type"),
        "shipping_mode": real.get("shipping_mode"),
        "free_shipping": bool(real.get("free_shipping")),
        "price": real.get("price") if real.get("price") is not None else approved.get("price"),
        "original_price": real.get("original_price") if real.get("original_price") is not None else approved.get("original_price"),
        "permalink": real.get("permalink") or approved.get("permalink"),
        "condition": real.get("condition") or approved.get("condition"),
        "listing_type_id": real.get("listing_type_id") or approved.get("listing_type_id"),
        "seller_status": None,
        "seller_level_id": None,
        "seller_completed_sales": None,
        "seller_nickname": None,
        "quality_validated": True,
    })

    # Reputação do vendedor agora é apenas informativa.
    seller_id = approved.get("seller_id")
    if seller_id:
        seller = _get_seller_quality(seller_id)
        if seller:
            approved.update({
                "seller_status": seller.get("power_seller_status"),
                "seller_level_id": seller.get("level_id"),
                "seller_completed_sales": seller.get("completed_sales") or 0,
                "seller_nickname": seller.get("seller_nickname"),
            })

    return approved

def validate_high_turnover_item(item, product_id=None):
    """Validação eliminatória: Full + Gold/Platinum + >=100 vendas.

    Se a publicação escolhida pelo catálogo não passar, procura outras
    publicações do mesmo produto antes de descartar o produto inteiro.
    Isso é importante porque um mesmo produto pode ter vários vendedores.
    """
    if not isinstance(item, dict):
        return None

    approved = _approve_real_item(item.get("item_id"), item)
    if approved:
        return approved

    pid = str(product_id or "").strip()
    if not pid:
        return None

    try:
        candidates = product_items(pid)
    except Exception as exc:
        print("[FILTRO ALTO GIRO] alternativas", pid, repr(exc))
        candidates = []

    checked = {str(item.get("item_id") or "").strip()}
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        cid = str(candidate.get("item_id") or candidate.get("id") or "").strip()
        if not cid or cid in checked:
            continue
        checked.add(cid)
        normalized = normalize_item(candidate)
        if not normalized:
            continue
        approved = _approve_real_item(cid, normalized)
        if approved:
            return approved

    return None



_PUBLIC_ITEM_PAGE_CACHE = {}
_PUBLIC_ITEM_PAGE_CACHE_LOCK = threading.Lock()

def _scrape_public_item_page(item_id):
    """Lê os dados básicos do anúncio diretamente da página pública.

    O app já consegue descobrir IDs MLB reais na busca pública, mas o V44
    descartava esses IDs quando GET /items/{id} retornava 403. Isso era o
    gargalo que transformava centenas de candidatos em 4 ofertas.

    A página pública do anúncio contém metadados públicos (título, imagem,
    preço e URL canônica). Usamos esses dados apenas como fallback quando a
    API de /items não estiver disponível.
    """
    iid = str(item_id or '').strip().upper()
    if not re.fullmatch(r'MLB\d+', iid):
        return None

    cache_key = iid
    with _PUBLIC_ITEM_PAGE_CACHE_LOCK:
        cached = _PUBLIC_ITEM_PAGE_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    urls = [
        f'https://www.mercadolivre.com.br/{iid.lower()}-produto',
        f'https://produto.mercadolivre.com.br/{iid.replace("MLB", "MLB-", 1)}',
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'Cache-Control': 'no-cache',
    }

    html = ''
    final_url = ''
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code == 200 and r.text:
                html = r.text
                final_url = str(r.url or url)
                break
            print('[ITEM PUBLICO] HTTP', r.status_code, iid, url)
        except Exception as exc:
            print('[ITEM PUBLICO] erro', iid, repr(exc))

    if not html:
        return None

    def meta(prop=None, name=None, itemprop=None):
        if prop:
            attr = r'(?:property|name)=[\"\']' + re.escape(prop) + r'[\"\']'
        elif name:
            attr = r'name=[\"\']' + re.escape(name) + r'[\"\']'
        elif itemprop:
            attr = r'itemprop=[\"\']' + re.escape(itemprop) + r'[\"\']'
        else:
            return ''
        patterns = [
            r'<meta\b[^>]*' + attr + r'[^>]*content=[\"\']([^\"\']+)[\"\']',
            r'<meta\b[^>]*content=[\"\']([^\"\']+)[\"\'][^>]*' + attr + r'[^>]*>',
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.I)
            if m:
                return html_lib.unescape(m.group(1)).strip()
        return ''

    title = meta(prop='og:title') or meta(name='twitter:title') or meta(itemprop='name')
    image = meta(prop='og:image') or meta(name='twitter:image') or meta(itemprop='image')
    price_raw = (
        meta(prop='product:price:amount')
        or meta(itemprop='price')
        or meta(name='price')
    )
    currency = meta(prop='product:price:currency') or meta(itemprop='priceCurrency')

    canonical = ''
    m = re.search(r'<link\b[^>]*(?:rel=[\"\']canonical[\"\'])[^>]*href=[\"\']([^\"\']+)', html, re.I)
    if not m:
        m = re.search(r'<link\b[^>]*href=[\"\']([^\"\']+)[\"\'][^>]*(?:rel=[\"\']canonical[\"\'])', html, re.I)
    if m:
        canonical = html_lib.unescape(m.group(1)).strip()

    # Fallback para JSON-LD/Product/Offer.
    if not title or not image or not price_raw:
        for sm in re.finditer(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.I | re.S):
            raw_json = html_lib.unescape(sm.group(1)).strip()
            try:
                obj = json.loads(raw_json)
            except Exception:
                continue
            stack = obj if isinstance(obj, list) else [obj]
            while stack:
                node = stack.pop(0)
                if isinstance(node, list):
                    stack.extend(node)
                    continue
                if not isinstance(node, dict):
                    continue
                if not title and node.get('name'):
                    title = str(node.get('name')).strip()
                if not image and node.get('image'):
                    img = node.get('image')
                    if isinstance(img, list):
                        img = img[0] if img else ''
                    image = str(img or '').strip()
                offers_node = node.get('offers')
                if isinstance(offers_node, dict):
                    if not price_raw and offers_node.get('price') is not None:
                        price_raw = str(offers_node.get('price'))
                    if not currency and offers_node.get('priceCurrency'):
                        currency = str(offers_node.get('priceCurrency'))
                elif isinstance(offers_node, list):
                    stack.extend(offers_node)
                if node.get('@graph'):
                    stack.extend(node.get('@graph') if isinstance(node.get('@graph'), list) else [node.get('@graph')])

    def parse_price(v):
        if v is None:
            return None
        txt = str(v).strip().replace('\xa0', ' ')
        # JSON-LD costuma vir em 1234.56; meta pode vir em 1.234,56.
        if re.fullmatch(r'\d+(?:\.\d+)?', txt):
            try:
                return float(txt)
            except Exception:
                return None
        m = re.search(r'\d[\d.]*,\d{2}', txt)
        if m:
            try:
                return float(m.group(0).replace('.', '').replace(',', '.'))
            except Exception:
                return None
        m = re.search(r'\d+(?:\.\d+)?', txt)
        try:
            return float(m.group(0)) if m else None
        except Exception:
            return None

    price = parse_price(price_raw)
    if not title or price is None or price <= 0:
        print('[ITEM PUBLICO] dados insuficientes', iid, 'title=', bool(title), 'price=', price_raw)
        return None

    if not canonical or '/p/' not in canonical:
        canonical = final_url or f'https://produto.mercadolivre.com.br/{iid.replace("MLB", "MLB-", 1)}'

    row = {
        'id': iid,
        'item_id': iid,
        'title': title,
        'name': title,
        'permalink': canonical,
        'price': price,
        'original_price': None,
        'thumbnail': image,
        'pictures': [{'secure_url': image}] if image else [],
        'seller_id': None,
        'seller': {},
        'shipping': {},
        'condition': 'new',
        'source_type': 'ITEM',
        'public_page': True,
        'currency_id': currency or 'BRL',
    }
    with _PUBLIC_ITEM_PAGE_CACHE_LOCK:
        _PUBLIC_ITEM_PAGE_CACHE[cache_key] = dict(row)
    return row

def _fetch_product_fast(pid, raw=None, base=None):
    """Enriquece o candidato sem eliminar catálogo durante a busca.

    Catálogo é permitido nesta etapa porque o Mercado Livre usa catálogo em
    várias categorias. A conversão para publicação real fica protegida apenas
    no fluxo de geração do afiliado.
    """
    base = base or {"category_id": None, "category_name": None, "query": ""}
    cache_key = str(pid)

    with _PRODUCT_CACHE_LOCK:
        if cache_key in _PRODUCT_CACHE:
            return _PRODUCT_CACHE[cache_key]

    # 0) Busca que já trouxe uma publicação ITEM real.
    # Primeiro aproveitamos os dados já coletados da página pública. Isso é
    # essencial porque /items/{id} pode responder 403 para o token atual.
    source_type = str((raw or {}).get("source_type") or "").upper().strip()
    if source_type == "ITEM":
        raw_item = normalize_item(raw)
        raw_title = str((raw or {}).get("title") or (raw or {}).get("name") or "").strip()
        raw_price = raw_item.get("price") if raw_item else None
        raw_image = _extract_image_url(raw or {})
        if raw_item is not None and raw_title and raw_price is not None and float(raw_price or 0) > 0 and raw_image:
            raw_item["permalink"] = (raw_item.get("permalink") or (raw or {}).get("permalink"))
            p = dict(raw or {})
            p.update({"id": pid, "name": raw_title, "title": raw_title})
            result = (pid, p, raw_item, base)
            with _PRODUCT_CACHE_LOCK:
                _PRODUCT_CACHE[cache_key] = result
            return result

        # Segunda tentativa: API oficial /items.
        item_data, status, _ = ml_get(f"/items/{pid}")
        if status == 200 and isinstance(item_data, dict):
            item = normalize_item(item_data)
            if item is not None:
                item = _hydrate_real_item_permalink(item)
                p = dict(raw or {})
                p.update({
                    "id": pid,
                    "name": item_data.get("title") or p.get("name") or pid,
                    "title": item_data.get("title") or p.get("title") or pid,
                    "pictures": item_data.get("pictures") or [],
                    "permalink": item.get("permalink") or p.get("permalink"),
                })
                result = (pid, p, item, base)
                with _PRODUCT_CACHE_LOCK:
                    _PRODUCT_CACHE[cache_key] = result
                return result

        # Fallback definitivo: página pública do próprio anúncio.
        public_row = _scrape_public_item_page(pid)
        if public_row:
            item = normalize_item(public_row)
            if item is not None:
                p = dict(public_row)
                p.update({"id": pid, "name": public_row.get("title") or pid, "title": public_row.get("title") or pid})
                result = (pid, p, item, base)
                with _PRODUCT_CACHE_LOCK:
                    _PRODUCT_CACHE[cache_key] = result
                return result

    # 1) Aproveita qualquer buy box que já tenha vindo na busca.
    if isinstance(raw, dict):
        bb = raw.get("buy_box_winner") or raw.get("buy_box")
        item = _build_item_from_buy_box(bb)
        if item is not None:
            item = _hydrate_real_item_permalink(item)
            p = dict(raw)
            p.setdefault("name", raw.get("title") or pid)
            result = (pid, p, item, base)
            with _PRODUCT_CACHE_LOCK:
                _PRODUCT_CACHE[cache_key] = result
            return result

    # 2) Detalhe do catálogo.
    p = product(pid)
    if p:
        bb = p.get("buy_box_winner") or p.get("buy_box")
        item = _build_item_from_buy_box(bb)
        if item is not None:
            item = _hydrate_real_item_permalink(item)
            result = (pid, p, item, base)
            with _PRODUCT_CACHE_LOCK:
                _PRODUCT_CACHE[cache_key] = result
            return result

    # 3) Tenta publicações associadas ao produto.
    with _ITEMS_CACHE_LOCK:
        cached_items = _ITEMS_CACHE.get(cache_key)
    items = cached_items if cached_items is not None else product_items(pid)
    if cached_items is None:
        with _ITEMS_CACHE_LOCK:
            _ITEMS_CACHE[cache_key] = items

    best = None
    for candidate in items or []:
        item = normalize_item(candidate)
        if not item:
            continue
        item = _hydrate_real_item_permalink(item)
        item["sold_quantity"] = candidate.get("sold_quantity") or 0
        if best is None or (item.get("free_shipping") and not best.get("free_shipping")):
            best = item

    if best is None:
        return None

    if p is None:
        p = dict(raw or {})
    p.setdefault("name", (raw or {}).get("title") or pid)
    p.setdefault("title", (raw or {}).get("title") or pid)
    p.setdefault("permalink", best.get("permalink"))

    result = (pid, p, best, base)
    with _PRODUCT_CACHE_LOCK:
        _PRODUCT_CACHE[cache_key] = result
    return result

def _resolve_scan_categories(queries):
    """Resolve corretamente uma ou várias categorias sem perder as demais.

    O bug crítico anterior era usar apenas queries[0]. Quando a rotina de
    atualização enviava uma semente de cada categoria, a primeira semente
    (smartphone) fazia o scanner trabalhar somente em Celulares.
    """
    values = [str(x).strip() for x in (queries or []) if str(x).strip()]
    if not values:
        return list(CATALOG.keys())

    # Se vierem os nomes das categorias, respeita exatamente a seleção.
    direct = []
    for value in values:
        if value in CATALOG and value not in direct:
            direct.append(value)
    if direct:
        return direct

    # Se vierem várias sementes, recupera TODAS as categorias representadas.
    mapped = []
    for value in values:
        cat = query_category(value)
        if cat and cat not in mapped:
            mapped.append(cat)
    if mapped:
        return mapped

    # Uma consulta livre ainda tenta identificar a categoria; se não houver
    # correspondência, pesquisa todas as categorias para não retornar vazio.
    if len(values) == 1:
        cat = query_category(values[0])
        return [cat] if cat else list(CATALOG.keys())
    return list(CATALOG.keys())


_IMAGE_CACHE = {}
_IMAGE_CACHE_LOCK = threading.Lock()

def _extract_image_url(obj):
    if not isinstance(obj, dict):
        return ""
    for key in ("secure_url", "url", "secure_thumbnail", "thumbnail", "picture_url", "image"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            return value.strip()
    pics = obj.get("pictures") or []
    if isinstance(pics, list):
        for pic in pics:
            value = _extract_image_url(pic)
            if value:
                return value
    return ""

def _image_url_works(url):
    url = str(url or "").strip()
    if not url:
        return False
    with _IMAGE_CACHE_LOCK:
        if url in _IMAGE_CACHE:
            return _IMAGE_CACHE[url]
    ok = False
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, stream=True, timeout=8, allow_redirects=True)
        ctype = (r.headers.get("content-type") or "").lower()
        ok = r.status_code == 200 and (ctype.startswith("image/") or not ctype)
        r.close()
    except Exception:
        ok = False
    with _IMAGE_CACHE_LOCK:
        _IMAGE_CACHE[url] = ok
    return ok

def _resolve_offer_image(product_data, item_data, base_data=None, item_id=None):
    """Garante uma URL de imagem real do Mercado Livre antes de aceitar a oferta."""
    for obj in (product_data, item_data, base_data):
        url = _extract_image_url(obj)
        if url and _image_url_works(url):
            return url

    iid = str(item_id or "").strip()
    if iid:
        try:
            data, status, _ = ml_get(f"/items/{iid}")
            if status == 200 and isinstance(data, dict):
                url = _extract_image_url(data)
                if url and _image_url_works(url):
                    return url
        except Exception as exc:
            print("[IMAGEM] erro item", iid, repr(exc))
    return ""


def _is_arabic_perfume_for_query(title, query=""):
    """Valida perfume árabe pela marca OU pelo modelo pesquisado.

    Algumas publicações do Mercado Livre trazem somente o nome do modelo
    no título (ex.: "Asad Eau de Parfum"), sem repetir "Lattafa". Por isso
    não podemos exigir todas as palavras da consulta original.
    """
    if not _is_real_perfume(title):
        return False

    title_norm = norm(title)
    query_norm = norm(query)
    brand_match = any(norm(term) in title_norm for term in ARABIC_PERFUME_TERMS)

    generic = {
        "perfume", "perfumes", "arabe", "arabes", "árabe", "árabes",
        "eau", "parfum", "mais", "vendido", "vendidos", "vendida",
        "vendidas", "procurado", "procurados", "procurada", "procuradas",
        "alta", "importado", "nacional",
    }
    brand_words = set()
    for brand in ARABIC_PERFUME_TERMS:
        brand_words.update(norm(brand).split())

    query_terms = [
        x for x in query_norm.split()
        if len(x) >= 3 and x not in generic and x not in brand_words
    ]

    # Para uma consulta específica (Lattafa Asad, Afnan 9PM, Khamrah etc.),
    # basta o modelo aparecer no título. Para consulta genérica de marca, a
    # própria marca já é suficiente.
    model_match = bool(query_terms) and any(x in title_norm for x in query_terms)
    return bool(brand_match or model_match)


def _direct_perfume_offer_from_listing(row, cat, position, query):
    """Transforma diretamente uma publicação ITEM real em oferta.

    Esta rota é usada para Perfumes e Perfumes Árabes. Ela NÃO passa pelo
    fluxo catálogo -> Buy Box -> /items associado, porque esse caminho pode
    devolver catálogo /p/MLB... e depois bloquear a geração do afiliado.
    Aqui já recebemos um anúncio ITEM real com item_id=MLB..., então a oferta
    nasce pronta para o fluxo de afiliado. Mantemos apenas preço >= R$69,90,
    perfume válido, sem kit/decant e com imagem.
    """
    if not isinstance(row, dict):
        return None

    item_id = str(row.get("id") or row.get("item_id") or "").strip()
    title = str(row.get("title") or "").strip()
    if not item_id or not title:
        return None

    if cat == "🌸 Perfumes":
        if not _is_normal_perfume_for_query(title, query):
            return None
    else:
        # Não dependa somente do nome da marca. Algumas publicações do ML
        # trazem apenas o nome do modelo (ex.: Khamrah, Asad, 9PM). Como a
        # busca já foi feita por micro-nicho, a consulta também valida o título.
        if not _is_arabic_perfume_for_query(title, query):
            return None

    # Bloqueio explícito de decant/amostra/miniatura.
    nt = norm(title)
    if any(x in nt for x in ("decant", "amostra", "miniatura")):
        return None

    try:
        price = float(row.get("price")) if row.get("price") is not None else None
    except Exception:
        price = None
    if price is None or price < MIN_PRODUCT_PRICE or price > 100000:
        return None

    try:
        original = float(row.get("original_price")) if row.get("original_price") is not None else None
    except Exception:
        original = None

    # Loja Oficial NÃO é obrigatória. O campo é apenas informativo.
    official_store_id = _extract_official_store_id(row)
    if original is None:
        try:
            data, status, _ = ml_get(f"/items/{item_id}")
            if status == 200 and isinstance(data, dict):
                if not official_store_id:
                    official_store_id = _extract_official_store_id(data)
                original = data.get("original_price") or data.get("base_price")
        except Exception as exc:
            print("[PRECO ORIGINAL] falha ao confirmar", item_id, repr(exc))

    shipping = row.get("shipping") or {}
    if not isinstance(shipping, dict):
        shipping = {}
    free = bool(shipping.get("free_shipping"))
    shipping_cost = 0 if free else shipping.get("cost")

    image = str(
        row.get("thumbnail")
        or row.get("secure_thumbnail")
        or row.get("picture_url")
        or ""
    ).strip()

    # Algumas respostas trazem a imagem somente dentro de pictures.
    if not image:
        pictures = row.get("pictures") or []
        if isinstance(pictures, list):
            for pic in pictures:
                if isinstance(pic, dict):
                    image = str(
                        pic.get("secure_url")
                        or pic.get("url")
                        or pic.get("secure_thumbnail")
                        or pic.get("thumbnail")
                        or ""
                    ).strip()
                    if image:
                        break

    # Se a busca não trouxer imagem nenhuma, tenta uma única consulta ao item real.
    if not image:
        data, status, _ = ml_get(f"/items/{item_id}")
        if status == 200 and isinstance(data, dict):
            pictures = data.get("pictures") or []
            if pictures and isinstance(pictures[0], dict):
                image = str(
                    pictures[0].get("secure_url")
                    or pictures[0].get("url")
                    or pictures[0].get("secure_thumbnail")
                    or pictures[0].get("thumbnail")
                    or ""
                ).strip()

    # Não descartar perfume válido apenas porque a API omitiu a foto.

    seller = row.get("seller") or {}
    seller_id = seller.get("id") if isinstance(seller, dict) else row.get("seller_id")
    sold = row.get("sold_quantity") or 0
    try:
        sold = int(float(sold))
    except Exception:
        sold = 0

    disc = discount(price, original)
    return {
        "product_id": item_id,
        "item_id": item_id,
        "title": title,
        "modelo_nome": model_name(title),
        "especificacoes": specs(title),
        "image": image,
        "category_name": cat,
        "micro_nicho": query,
        # Para perfumes, nunca usa URL de catálogo /p/MLB... como fallback.
        # Se não houver permalink da publicação, monta diretamente a URL do ITEM real.
        "permalink": (
            row.get("permalink")
            if row.get("permalink") and not re.search(
                r"/p/MLB\d+(?:[/?#]|$)", str(row.get("permalink")), re.I
            )
            else f"https://produto.mercadolivre.com.br/{item_id.replace('MLB', 'MLB-', 1)}"
        ),
        "price": price,
        "original_price": original,
        "discount": disc,
        "seller_id": seller_id,
        "condition": row.get("condition") or "new",
        "free_shipping": free,
        "shipping_cost": shipping_cost,
        "shipping_known": shipping_cost is not None,
        "total_price": total(price, shipping_cost) if shipping_cost is not None else price,
        "relevance_score": 1.0,
        "sold_quantity": sold,
        "logistic_type": shipping.get("logistic_type") or "",
        "shipping_mode": shipping.get("mode"),
        "seller_status": None,
        "seller_level_id": None,
        "seller_completed_sales": 0,
        "seller_nickname": None,
        "quality_validated": False,
        "giro_score": min(1000, sold * 2),
        "trend_score": 0,
        "trend_keyword": None,
        "trend_bucket": None,
        "trend_rank": None,
        "best_seller_position": position,
        "best_seller_category": None,
        "appears_both": False,
        "demand_score": max(0, 1000 - position * 10),
        "opportunity_score": max(0, 1000 - position * 10) + (20 if free else 0) + min(20, disc),
        "cupom": None,
        "desconto_cupom": 0,
        "percentual_cupom_efetivo": 0,
        "cupom_match": None,
        "cupom_uso_limite": None,
        "cash_discount": 0,
        "cash_label": None,
        "cash_final": None,
        "melhor_forma": None,
        "maior_desconto": 0,
        "preco_com_cupom": None,
        "preco_final_melhor": None,
        "affiliate_link": "",
        "extra_earnings": 0,
        "micro_nicho": query,
    }

def _arabic_relevance_score(title, ds):
    """Peso extra para perfumes árabes, priorizando marca + alta + best-seller."""
    text = norm(title or "")
    brand_hits = sum(1 for b in ARABIC_PERFUME_TERMS if norm(b) in text)
    brand_bonus = min(180.0, brand_hits * 45.0)
    trend_bonus = min(260.0, float(ds.get("trend_score") or 0) * 7.0)
    bestseller_pos = ds.get("best_seller_position")
    bestseller_bonus = max(0.0, 220.0 - (float(bestseller_pos or 99) - 1) * 4.0) if bestseller_pos else 0.0
    both_bonus = 700.0 if ds.get("appears_both") else 0.0
    return brand_bonus + trend_bonus + bestseller_bonus + both_bonus



def _title_matches_scan_category(category, title):
    """Validação final de coerência: o produto encontrado precisa pertencer ao nicho."""
    cat = str(category or "").strip()
    n = norm(title or "")
    if not n:
        return False

    # Exclusões globais de produtos claramente fora do mix de ofertas.
    # O anúncio do lubrificante Silispeed para esteira apareceu por engano.
    global_excluded = (
        "lubrificante de silicone", "silispeed", "silicone liquido para esteira",
        "silicone líquido para esteira", "lubrificante para esteira",
        "lubrificante de esteira", "oleo de silicone para esteira",
        "óleo de silicone para esteira",
    )
    if any(term in n for term in global_excluded):
        return False

    # Bloqueios universais para evitar cruzamento óbvio entre categorias.
    fragrance = any(x in n for x in (
        "perfume", "parfum", "eau de", "edt", "edp", "fragrance",
        "body splash", "body mist", "colonia", "colônia",
    ))
    shoe = any(x in n for x in (
        "tenis", "tênis", "sneaker", "sneakers", "sapatenis", "sapatênis",
        "calcado", "calçado", "sapato", "sapatos", "mocassim", "mocassins",
        "oxford", "loafer", "sapatilha", "scarpin", "bota masculina",
        "bota feminina", "botina", "ankle boot", "social masculino",
        "sapato social", "chinelo", "slide", "sandalia", "sandália",
        "running shoe", "running shoes",
        # Marcas de calçados reconhecidas no catálogo do projeto; ajudam quando
        # o anúncio omite a palavra "sapato" no título (ex.: Ferracini Blady).
        "ferracini", "pegada", "democrata", "west coast", "freeway",
        "kildare", "sandro moscoloni", "moleca", "modare", "beira rio",
        "via marte", "comfortflex", "piccadilly", " dakota ",
        # modelos/linhas muito característicos de tênis
        "air max", "air force", "air jordan", "jordan", "dunk low", "dunk",
        "ultraboost", "superstar", "adizero", "pegasus", "vomero",
        "novablast", "gel kayano", "gel nimbus", "gel cumulus", "fresh foam",
        "1080", "574", "990", "1080v", "clifton", "bondi", "corre",
    ))

    if cat == "🌸 Perfumes":
        return _is_normal_perfume_for_query(title, "")
    if cat == "🌙 Perfumes Árabes":
        return _is_arabic_perfume_for_query(title, "")

    if cat == "👟 Tênis & Calçados":
        if fragrance or any(x in n for x in ("chuteira", "trava society", "trava campo", "futsal")):
            return False
        return shoe

    if cat == "👕 Moda":
        if fragrance or shoe:
            return False
        return any(x in n for x in (
            "camiseta", "t shirt", "tshirt", "camisa", "jersey", "bermuda",
            "short", "jaqueta", "corta vento", "calca", "calça", "jeans",
            "moletom", "casaco", "polo", "vestido", "blusa", "cropped",
            "legging", "top fitness", "regata", "conjunto", "biquini",
            "biquíni", "sunga", "maio", "maiô", "bone", "boné", "viseira",
            "oculos de sol", "óculos de sol", "carteira", "cinto", "mochila",
        ))

    if cat == "📱 Tecnologia":
        if fragrance or shoe:
            return False
        return any(x in n for x in (
            "iphone", "ipad", "smartphone", "celular", "galaxy", "redmi",
            "poco", "motorola", "realme", "notebook", "macbook", "laptop",
            "monitor", "teclado", "mouse", "ssd", "memoria ram", "impressora",
            "webcam", "tablet", "smartwatch", "fone", "headset", "airpods",
            "caixa de som", "soundbar", "bluetooth", "carregador", "cabo usb",
            "power bank", "pelicula", "película", "capa", "case", "console",
            "videogame", "playstation", "xbox", "nintendo",
        ))

    if cat == "🏠 Casa e Organização":
        if fragrance or shoe:
            return False
        return any(x in n for x in (
            "organizador", "guarda roupa", "armario", "armário", "estante",
            "prateleira", "sapateira", "pote", "cozinha", "utensilio",
            "utensílio", "air fryer", "aspirador", "cafeteira", "liquidificador",
            "panela", "mixer", "lampada", "lâmpada", "fita led", "jogo de cama",
            "toalha", "varal", "escorredor", "mesa", "cadeira", "sofa", "sofá",
            "cama", "colchao", "colchão", "armario", "armário", "móvel", "moveis",
            "móveis", "decoração", "decoracao",
        ))

    if cat == "💪 Academia & Fitness":
        if fragrance or shoe:
            return False
        return any(x in n for x in (
            "creatina", "whey", "proteina", "proteína", "pre treino", "pré treino",
            "hipercalorico", "hipercalórico", "bcaa", "isotonico", "isotônico",
            "halter", "anilha", "barra musculacao", "barra musculação", "rack",
            "estacao de musculacao", "estação de musculação", "aparelho de academia",
            "maquina de musculacao", "máquina de musculação", "esteira",
            "bicicleta ergometrica", "bicicleta ergométrica", "spinning",
            "eliptico", "elíptico", "step", "banco de treino", "academia",
            "fitness", "musculacao", "musculação", "crossfit",
        ))

    if cat == "💇 Saúde & Beleza":
        # Perfumes possuem categorias próprias no catálogo e não devem cair
        # nesta categoria por acidente.
        if fragrance or shoe:
            return False
        return any(x in n for x in (
            "shampoo", "condicionador", "mascara capilar", "máscara capilar",
            "secador", "chapinha", "modelador", "escova secadora", "wella",
            "loreal", "l'oreal", "kerastase", "elseve", "truss", "salon line",
            "manicure", "esmalte", "cabine uv", "cabine led", "unha", "nail art",
            "skincare", "protetor solar", "hidratante facial", "serum", "sérum",
            "niacinamida", "acido hialuronico", "ácido hialurônico", "cerave",
            "la roche", "principia", "neutrogena", "vichy", "barbeador",
            "barbearia", "aparador", "trimmer", "hidratante corporal",
            "creme corporal", "esfoliante", "depilador", "oleo corporal",
            "óleo corporal", "desodorante",
        ))

    return True

def scan_queries(queries, min_discount=0, apply_coupons=False):
    """Busca candidatos das categorias e enriquece as publicações reais.

    Para categorias comuns, a V59 prioriza IDs ITEM reais descobertos na página pública
    e enriquece cada anúncio pela própria página pública quando /items/{id} retorna 403. Perfumes mantêm a rota própria.
    """
    categories = _resolve_scan_categories(queries)
    print(f"[CATEGORIAS RESOLVIDAS] {categories}")


    raw_by_cat = {}
    with _ThreadPoolExecutor(max_workers=min(3, max(1, len(categories)))) as ex:
        fmap = {ex.submit(_search_category, cat, FAST_ALL_CATEGORIES and len(categories) > 1): cat for cat in categories}
        for fut in as_completed(fmap):
            cat = fmap[fut]
            try:
                raw_by_cat[cat] = fut.result() or []
                print(f"[V66 PROGRESSO] categoria concluída: {cat}; candidatos={len(raw_by_cat[cat])}")
            except Exception as e:
                print("[TOP 20 BUSCA]", cat, repr(e))
                raw_by_cat[cat] = []
                print(f"[V66 PROGRESSO] categoria com erro: {cat}; erro={e!r}")

    # PERFUMES: rota direta de publicação real.
    # Tanto Perfumes quanto Perfumes Árabes precisam nascer de ITEM real.
    # Isso evita que o fluxo de enriquecimento transforme a publicação em
    # catálogo /p/MLB..., o que depois pode impedir o link de afiliado.
    direct_perfume_offers = []
    direct_arabic_offers = []

    if "🌸 Perfumes" in categories:
        perfume_raw = raw_by_cat.get("🌸 Perfumes", [])
        print(f"[PERFUMES CANDIDATOS BRUTOS] {len(perfume_raw)}")
        seen_perfume_items = set()
        direct_limit = 80 if FAST_ALL_CATEGORIES and len(categories) > 1 else 180
        for pos, (raw, source_query) in enumerate(perfume_raw[:direct_limit], start=1):
            try:
                item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
                if not item_id or item_id in seen_perfume_items:
                    continue
                seen_perfume_items.add(item_id)
                direct = _direct_perfume_offer_from_listing(
                    raw, "🌸 Perfumes", pos, source_query
                )
                if direct:
                    direct_perfume_offers.append(direct)
            except Exception as exc:
                print("[PERFUMES ROTA DIRETA]", repr(exc))
        print(f"[PERFUMES ROTA DIRETA] {len(direct_perfume_offers)} ofertas ITEM reais")

    if "🌙 Perfumes Árabes" in categories:
        arabic_raw = raw_by_cat.get("🌙 Perfumes Árabes", [])
        print(f"[ARABES CANDIDATOS BRUTOS] {len(arabic_raw)}")
        seen_arabic_items = set()
        direct_limit = 80 if FAST_ALL_CATEGORIES and len(categories) > 1 else 180
        for pos, (raw, source_query) in enumerate(arabic_raw[:direct_limit], start=1):
            try:
                item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
                if not item_id or item_id in seen_arabic_items:
                    continue
                seen_arabic_items.add(item_id)
                direct = _direct_perfume_offer_from_listing(
                    raw, "🌙 Perfumes Árabes", pos, source_query
                )
                if direct:
                    direct_arabic_offers.append(direct)
            except Exception as exc:
                print("[ARABES ROTA DIRETA]", repr(exc))
        print(f"[ARABES ROTA DIRETA] {len(direct_arabic_offers)} ofertas ITEM reais")

    candidates = []
    seen = set()
    for cat in categories:
        # As duas categorias de perfumes já foram convertidas diretamente
        # de ITEM real e não passam pelo enriquecimento normal.
        if cat in {"🌸 Perfumes", "🌙 Perfumes Árabes"}:
            continue
        candidate_limit = (
            SEARCH_CANDIDATES_PER_CATEGORY_ALL
            if FAST_ALL_CATEGORIES and len(categories) > 1
            else SEARCH_CANDIDATES_PER_CATEGORY_SINGLE
        )
        for raw, source_query in raw_by_cat.get(cat, [])[:candidate_limit]:
            pid = str(raw.get("id") or raw.get("product_id") or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            position = int(raw.get("highlight_position") or 99)
            # Para perfumes, além da posição da descoberta, aproveitamos os
            # sinais oficiais de mais vendidos/tendências quando disponíveis.
            if cat in {"🌸 Perfumes", "🌙 Perfumes Árabes"}:
                try:
                    ds = demand_score(
                        raw.get("title") or raw.get("name") or pid,
                        cat,
                        pid,
                        signals,
                        allow_direct=True,
                    )
                except Exception:
                    ds = {
                        "trend_score": 0, "trend_keyword": None,
                        "trend_bucket": None, "trend_rank": None,
                        "best_seller_position": position,
                        "best_seller_category": raw.get("highlight_category_id"),
                        "appears_both": False,
                        "demand_score": max(0, 1000 - position * 10),
                    }
                if ds.get("best_seller_position") is None:
                    ds["best_seller_position"] = position
                if ds.get("best_seller_category") is None:
                    ds["best_seller_category"] = raw.get("highlight_category_id")
            else:
                ds = {
                    "trend_score": 0,
                    "trend_keyword": None,
                    "trend_bucket": None,
                    "trend_rank": None,
                    "best_seller_position": position,
                    "best_seller_category": raw.get("highlight_category_id"),
                    "appears_both": False,
                    "demand_score": max(0, 1000 - position * 10),
                }
            if cat == "🌙 Perfumes Árabes":
                ds["arabic_relevance"] = _arabic_relevance_score(raw.get("title") or raw.get("name") or pid, ds)
            else:
                ds["arabic_relevance"] = 0.0
            candidates.append((1000 - position, pid, raw, cat, ds, source_query))

    # Mantém exatamente o ranking por posição dentro de cada categoria.
    candidates.sort(key=lambda x: (x[3], x[0] * -1, x[1]))

    print(f"[V66 PROGRESSO] descoberta concluída: categorias={len(categories)}; candidatos_para_enriquecer={len(candidates)}; perfumes_diretos={len(direct_perfume_offers)}; arabes_diretos={len(direct_arabic_offers)}")
    fetched = []
    enrichment_workers = 4 if FAST_ALL_CATEGORIES and len(categories) > 1 else 5
    with _ThreadPoolExecutor(max_workers=enrichment_workers) as ex:
        fmap = {
            ex.submit(_fetch_product_fast, pid, raw, {
                "category_name": cat,
                "query": source_query,
                "category_id": raw.get("highlight_category_id"),
            }): (pid, raw, cat, ds, source_query)
            for _, pid, raw, cat, ds, source_query in candidates
        }
        for fut in as_completed(fmap):
            pid, raw, cat, ds, source_query = fmap[fut]
            try:
                result = fut.result()
                if result:
                    fetched.append((result, cat, ds, source_query))
            except Exception as e:
                print("[TOP 20 ENRIQUECIMENTO]", pid, repr(e))

    # Reordena pelo ranking oficial depois do enriquecimento paralelo.
    fetched.sort(key=lambda x: (
        x[1],
        int(x[2].get("best_seller_position") or 99),
    ))

    # Começa com os perfumes já convertidos diretamente de anúncios ITEM reais.
    # As demais categorias seguem o fluxo normal.
    offers = list(direct_perfume_offers) + list(direct_arabic_offers)
    for result, cat, ds, source_query in fetched:
        try:
            pid, p, item, base = result
            title = p.get("name") or p.get("title") or pid

            try:
                price = float(item.get("price")) if item.get("price") is not None else None
            except Exception:
                price = None
            try:
                original = float(item.get("original_price")) if item.get("original_price") is not None else None
            except Exception:
                original = None

            if price is None or price <= 0:
                sale, sale_original = get_current_sale_price(item.get("item_id"))
                if sale is not None:
                    price = sale
                    if sale_original is not None:
                        original = sale_original
            # Se o buy box/catalogo não trouxe preço original, consulta a
            # venda atual do ITEM. O Mercado Livre pode guardar o preço cheio
            # em /items/{id}/sale_price; sem essa recuperação o filtro antigo
            # descartava quase todos os produtos.
            if item.get("item_id") and (original is None or original <= price):
                try:
                    sale_price, sale_original = get_current_sale_price(item.get("item_id"))
                    if sale_price is not None and sale_price > 0:
                        price = sale_price
                    if sale_original is not None and sale_original > price:
                        original = sale_original
                except Exception as exc:
                    print("[SALE PRICE] falha", item.get("item_id"), repr(exc))

            # REGRA PRINCIPAL: o preço mínimo de produto é o valor configurado em MIN_PRODUCT_PRICE.
            # O valor precisa ser filtrado aqui, depois de resolver o preço
            # real da publicação, e não apenas na descoberta do catálogo.
            # Isso impede que produtos de R$ 15,99, R$ 22,99 etc. cheguem
            # ao resultado final quando o catálogo retorna outro valor.
            if not valid_catalog_price(price):
                print(f"[PREÇO MÍNIMO] descartado {pid}: R$ {price:.2f} < R$ {MIN_PRODUCT_PRICE:.2f}")
                continue

            if original is None and isinstance(p.get("buy_box_winner"), dict):
                bb = p["buy_box_winner"]
                try:
                    original = float(bb.get("regular_price")) if bb.get("regular_price") is not None else None
                except Exception:
                    pass

            seller_disc = discount(price, original)
            if seller_disc < float(min_discount or 0):
                continue

            shipping = item.get("shipping_cost")
            known = shipping is not None
            total_price = total(price, shipping) if known else price
            free = bool(item.get("free_shipping"))

            # A busca pode exibir catálogo. O bloqueio de catálogo acontece
            # somente quando o usuário gerar o link afiliado.
            permalink = str(
                item.get("permalink")
                or p.get("permalink")
                or ""
            ).strip()

            # BLOQUEIO DE COERÊNCIA: a busca pode devolver um item fora do
            # nicho. Nunca publicamos só porque ele apareceu na pesquisa.
            if not _title_matches_scan_category(cat, title):
                print(f"[COERÊNCIA] descartado fora do nicho: {cat} -> {title[:120]}")
                continue

            # Imagem opcional: não elimina um subnicho inteiro quando a API omite a foto.
            image = _resolve_offer_image(p, item, base, item.get("item_id"))

            offers.append({
                "product_id": pid,
                "item_id": item.get("item_id"),
                "title": title,
                "modelo_nome": model_name(title),
                "especificacoes": specs(title),
                "image": image,
                "category_name": cat,
                "permalink": permalink,
                "price": price,
                "original_price": original,
                "discount": seller_disc,
                "seller_id": item.get("seller_id"),
                "official_store_id": item.get("official_store_id"),
                "condition": item.get("condition"),
                "free_shipping": free,
                "shipping_cost": shipping,
                "shipping_known": known,
                "total_price": total_price,
                "relevance_score": 1.0,
                "sold_quantity": item.get("sold_quantity") or 0,
                "logistic_type": item.get("logistic_type") or "",
                "shipping_mode": item.get("shipping_mode"),
                "seller_status": item.get("seller_status"),
                "seller_level_id": item.get("seller_level_id"),
                "seller_completed_sales": item.get("seller_completed_sales") or 0,
                "seller_nickname": item.get("seller_nickname"),
                "quality_validated": False,
                "giro_score": min(1000, float(item.get("sold_quantity") or 0) * 2),
                "trend_score": ds.get("trend_score", 0),
                "trend_keyword": ds.get("trend_keyword"),
                "trend_bucket": ds.get("trend_bucket"),
                "trend_rank": ds.get("trend_rank"),
                "best_seller_position": ds.get("best_seller_position"),
                "best_seller_category": ds.get("best_seller_category"),
                "appears_both": bool(ds.get("appears_both")),
                "demand_score": ds["demand_score"],
                "arabic_relevance": float(ds.get("arabic_relevance") or 0),
                "opportunity_score": ds["demand_score"] + (20 if free else 0) + min(20, seller_disc) + float(ds.get("arabic_relevance") or 0),
                "cupom": None,
                "desconto_cupom": 0,
                "percentual_cupom_efetivo": 0,
                "cupom_match": None,
                "cupom_uso_limite": None,
                "cash_discount": 0,
                "cash_label": None,
                "cash_final": None,
                "melhor_forma": None,
                "maior_desconto": 0,
                "preco_com_cupom": None,
                "preco_final_melhor": None,
                "affiliate_link": "",
                "extra_earnings": 0,
            })
        except Exception as e:
            print("[OFERTA TOP 20]", repr(e))

    # BUSCA AMPLA: não eliminamos um produto somente porque o catálogo
    # não informou preço original. Isso era o principal gargalo que fazia
    # centenas de candidatos virarem 1 única oferta.
    # Quando há preço original, o desconto real continua sendo calculado e
    # usado no ranking. Quando não há, a oferta continua válida pelo preço
    # atual, imagem e coerência do nicho. Cupons entram depois.
    before_discount_filter = len(offers)
    for o in offers:
        if _discount_is_real(o.get("price"), o.get("original_price")):
            o["discount"] = discount(o.get("price"), o.get("original_price"))
        else:
            o["discount"] = 0.0
    print(f"[BUSCA AMPLA] {before_discount_filter} ofertas após filtros básicos; desconto real usado no ranking")

    # Perfumes: mostra somente perfumes/fragrâncias individuais,
    # incluindo Body Splash e Body Mist, sem kits/combos.
    if "🌸 Perfumes" in categories:
        offers = [
            o for o in offers
            if o.get("category_name") != "🌸 Perfumes"
            or (
                _is_normal_perfume_for_query(
                    o.get("title"),
                    o.get("micro_nicho") or "",
                )
                and not _is_arabic_perfume(o.get("title"))
            )
        ]

    # Perfumes Árabes: a rota direta já entregou ITEM real; aqui só mantém a
    # confirmação final do título para impedir derivados.
    if "🌙 Perfumes Árabes" in categories:
        offers = [
            o for o in offers
            if o.get("category_name") != "🌙 Perfumes Árabes"
            or _is_arabic_perfume_for_query(o.get("title"), o.get("micro_nicho") or "")
        ]

    # Moda: cueca geriátrica nunca entra. Combos de cuecas normais só entram
    # quando são de 5 ou 10 unidades e têm desconto realmente bom.
    if "👚 Moda Básica e Kits de Vestuário" in categories:
        moda = []
        for o in offers:
            if o.get("category_name") != "👚 Moda Básica e Kits de Vestuário":
                moda.append(o)
                continue
            title_norm = norm(o.get("title") or "")
            if any(term in title_norm for term in (
                "cueca geriatrica", "geriatrica", "escapes de urina",
                "escape de urina", "incontinencia",
            )):
                continue
            # Combos de 5/10 podem entrar, mas precisam representar uma
            # promoção de verdade; anúncios de 2/3/4 unidades não entram.
            if "cueca" in title_norm:
                qty_match = re.search(r"\b(\d+)\s*(?:unidades?|unid|pecas?|pcs?)\b", title_norm)
                if qty_match:
                    qty = int(qty_match.group(1))
                    if qty not in (5, 10):
                        continue
                    if float(o.get("discount") or 0) < 15.0:
                        continue
            moda.append(o)
        offers = moda

    # DEDUPLICAÇÃO ROBUSTA
    # O mesmo produto pode chegar com product_id diferente e com pequenas
    # diferenças no título (ex.: "Escapes Urina G" x "Escapes Urina Gg").
    # O agrupamento apenas por product_id/título não é suficiente.
    # Primeiro usamos o título exato; depois a imagem normalizada, que é um
    # identificador muito mais confiável quando o Mercado Livre devolve a
    # mesma publicação/catálogo por caminhos diferentes.
    def _offer_value(x):
        try:
            return float(x.get("total_price")) if x.get("total_price") is not None else float(x.get("price") or 999999)
        except Exception:
            return 999999.0

    def _image_identity(x):
        raw = str(x.get("image") or "").strip()
        if not raw:
            return ""
        try:
            u = urlparse(raw)
            path = re.sub(r"\s+", "", u.path.lower())
            # Ignora parâmetros de CDN que só alteram tamanho/formato.
            path = re.sub(r"[?&](?:width|height|size|quality|format)=[^&]+", "", path)
            return (u.netloc.lower() + path).strip()
        except Exception:
            return raw.lower().split("?")[0].strip()

    # 1) Título exato.
    unique_offers = {}
    for o in offers:
        title_key = norm(o.get("title") or "")
        item_key = str(o.get("item_id") or o.get("product_id") or "").strip()
        key = title_key or item_key
        if not key:
            continue
        current = unique_offers.get(key)
        if current is None or _offer_value(o) < _offer_value(current):
            unique_offers[key] = o

    # 2) Mesma imagem = mesmo produto visual. Isso captura publicações que
    # possuem IDs/títulos diferentes, mas mostram exatamente o mesmo produto.
    by_image = {}
    no_image = []
    for o in unique_offers.values():
        ikey = _image_identity(o)
        if not ikey:
            no_image.append(o)
            continue
        current = by_image.get(ikey)
        if current is None or _offer_value(o) < _offer_value(current):
            by_image[ikey] = o

    deduped = list(by_image.values()) + no_image

    # 3) Pequenas diferenças de título só são usadas como desempate quando
    # a imagem também coincide. O objetivo é remover duplicata, não juntar
    # variantes legítimas que possuem imagens diferentes.
    final_offers = []
    for o in deduped:
        duplicate_index = None
        title = norm(o.get("title") or "")
        image = _image_identity(o)
        if image and title:
            for i, existing in enumerate(final_offers):
                if image != _image_identity(existing):
                    continue
                other = norm(existing.get("title") or "")
                if title == other or SequenceMatcher(None, title, other).ratio() >= 0.94:
                    duplicate_index = i
                    break
        if duplicate_index is None:
            final_offers.append(o)
        elif _offer_value(o) < _offer_value(final_offers[duplicate_index]):
            final_offers[duplicate_index] = o

    offers = final_offers

    # EXCLUSÃO FINAL DE PRODUTOS SEM INTERESSE / RESULTADOS ENGANOSOS.
    # "Tênis pé" em talco/antisséptico não é tênis; bolas também não fazem
    # parte do objetivo principal do projeto de moda/calçados.
    junk_terms = (
        "lubrificante de silicone", "silispeed", "silicone liquido para esteira",
        "silicone líquido para esteira", "lubrificante para esteira",
        "lubrificante de esteira", "oleo de silicone para esteira", "óleo de silicone para esteira",
        "talco para os pes", "talco para os pés", "tenys pe", "tenys pé",
        "antisseptico para os pes", "antisséptico para os pés", "desodorante para os pes",
        "desodorante para os pés", "creme para os pes", "creme para os pés",
        "palmilha", "cadarco", "cadarço", "kit limpeza tenis", "kit limpeza tênis",
        "bola de futebol", "bolas de futebol", "bola society", "bola futsal",
        "bola de basquete", "bola de volei", "bola de vôlei", "bola de tenis",
        "bola de tênis", "bola de handebol", "bola esportiva", "bomba para bola",
        "agulha para bola", "rede de futebol", "rede para gol",
        "pelucia", "pelúcia", "plush", "bichinho de pelucia", "bicho de pelucia",
        "boneco", "boneca", "brinquedo", "action figure", "figura de acao", "figura de ação",
        "almofada", "pantufa de pelucia", "enfeite decorativo", "miniatura colecionavel",
    )
    offers = [o for o in offers if not any(term in norm(o.get("title") or "") for term in junk_terms)]

    # A categoria de tênis precisa conter um calçado real, não só a palavra
    # "pé" ou uma marca no título. Exclui produtos de higiene/acessórios.
    shoe_categories = {"👟 Tênis & Calçados", "👟 Tênis", "Calçados"}
    shoe_core_terms = (
        "tenis", "tênis", "sapatilha", "sapato", "sapatênis", "sapatenis",
        "bota", "coturno", "chinelo", "sandalia", "sandália", "slide", "mocassim",
    )
    shoe_bad_terms = (
        "talco", "antisseptico", "antisséptico", "desodorante", "lubrificante",
        "creme", "spray para os pes", "spray para os pés", "palmilha", "cadarco", "cadarço",
        "meia", "meias", "limpa tenis", "limpa tênis", "escova para tenis", "escova para tênis",
        "pelucia", "pelúcia", "plush", "boneco", "boneca", "brinquedo", "action figure",
        "almofada", "chaveiro", "miniatura", "colecionavel", "colecionável",
    )
    offers = [o for o in offers if not (
        (o.get("category_name") in shoe_categories)
        and (
            any(term in norm(o.get("title") or "") for term in shoe_bad_terms)
            or not any(term in norm(o.get("title") or "") for term in shoe_core_terms)
        )
    )]

    # Academia: limita bicicletas ergométricas/spinning a no máximo 1 oferta,
    # para não ocupar espaço que deve ser distribuído por outras categorias.
    fitness_bikes = 0
    balanced_offers = []
    for offer in offers:
        title_norm = norm(offer.get("title") or "")
        cat_name = offer.get("category_name") or ""
        is_bike = any(term in title_norm for term in (
            "bicicleta ergometrica", "bicicleta ergométrica", "bike spinning",
            "bicicleta spinning", "bicicleta de spinning", "bicicleta indoor",
        ))
        if cat_name == "💪 Academia & Fitness" and is_bike:
            if fitness_bikes >= 1:
                continue
            fitness_bikes += 1
        balanced_offers.append(offer)
    offers = balanced_offers

    def _display_demand_key(o):
        cat = o.get("category_name") or ""
        # O desconto real da publicação passa a ser um dos principais
        # critérios de escolha. Assim o sistema procura oportunidades de
        # preço realmente boas, sem abandonar os sinais de vendas e tendência.
        discount_score = min(100.0, max(0.0, float(o.get("discount") or 0)))
        if cat == "🌙 Perfumes Árabes":
            return (
                -discount_score,
                -(float(o.get("arabic_relevance") or 0)),
                0 if o.get("appears_both") else 1,
                -(float(o.get("trend_score") or 0)),
                float(o.get("best_seller_position") or 999),
                -(float(o.get("demand_score") or 0)),
                float(o.get("total_price") or 999999),
            )
        if cat == "🌸 Perfumes":
            return (
                -discount_score,
                0 if o.get("appears_both") else 1,
                -(float(o.get("trend_score") or 0)),
                float(o.get("best_seller_position") or 999),
                -(float(o.get("demand_score") or 0)),
                float(o.get("total_price") or 999999),
            )
        return (
            1, 0, float(o.get("best_seller_position") or 999),
            -(float(o.get("demand_score") or 0)),
            float(o.get("total_price") or 999999),
        )

    # CASA/COZINHA: multiprocessadores não podem dominar a categoria.
    # Só mantemos até 2 e somente quando houver desconto real de pelo menos 20%.
    casa = [o for o in offers if o.get("category_name") == "🏡 Achadinhos de Casa e Cozinha"]
    outros = [o for o in offers if o.get("category_name") != "🏡 Achadinhos de Casa e Cozinha"]
    multiprocessadores = []
    casa_sem_multi = []
    for o in casa:
        t = norm(o.get("title") or "")
        if any(x in t for x in ("multiprocessador", "multi processador", "processador de alimentos")):
            disc = float(o.get("discount") or 0)
            if disc >= 20:
                multiprocessadores.append(o)
        else:
            casa_sem_multi.append(o)
    multiprocessadores.sort(key=lambda o: (-float(o.get("discount") or 0), float(o.get("total_price") or 999999)))
    offers = outros + casa_sem_multi + multiprocessadores[:2]

    offers.sort(key=lambda o: ((o.get("category_name") or ""), _display_demand_key(o)))

    # Até 50 por categoria; perfumes recebem o mesmo limite para manter variedade e
    # variedade. Dentro de cada categoria, boas promoções têm prioridade real.
    grouped = {}
    for o in offers:
        grouped.setdefault(o["category_name"], []).append(o)

    flat = []
    major_brands = (
        "nike", "adidas", "asics", "mizuno", "new balance", "puma", "fila",
        "reebok", "skechers", "under armour", "vans", "converse", "jordan",
        "olympikus", "lacoste", "tommy hilfiger", "calvin klein", "levi's",
        "levis", "levi", "reebok", "hering", "reserva", "lululemon",
        "zara", "polo ralph lauren", "ralph lauren", "guess", "columbia",
        "salomon", "timberland", "oakley", "umbro", "new era",
    )
    for cat in categories:
        arr = grouped.get(cat, [])
        if cat in ("👟 Tênis & Calçados", "👟 Tênis", "Calçados", "👕 Moda"):
            def _brand_priority(o):
                title = norm(o.get("title") or "")
                for i, brand in enumerate(major_brands):
                    if norm(brand) in title:
                        return (0, i, _display_demand_key(o))
                # Mantém produto genérico apenas depois das marcas principais;
                # marcas desconhecidas ficam no fim, sem dominar o resultado.
                return (1, len(major_brands), _display_demand_key(o))
            arr.sort(key=_brand_priority)
        else:
            arr.sort(key=_display_demand_key)
        # Em uma categoria isolada, podemos entregar até 250 ofertas.
        # No modo "todas", mantemos 50 por categoria para preservar velocidade
        # e permitir passar de 230 ofertas somando as categorias.
        if len(categories) == 1:
            limit = 250
        else:
            limit = 50
        flat.extend(arr[:limit])

    # A ordem exibida é aleatória; a posição real de mais vendido continua salva em best_seller_position.
    random.shuffle(flat)

    dominant_type = None

    if apply_coupons and flat:
        public_cards = get_public_coupon_cards_cached()
        affiliate_coupon_catalog = get_affiliate_coupon_catalog_cached()
        coupon_count = 0
        coupon_limit_count = 0
        best_coupon_discount = 0.0
        best_coupon_price = None
        coupon_coverage = {}
        offer_coupon_candidates = {}

        # PRIMEIRO PASSO: descobre todos os cupons realmente associados a cada
        # oferta. Assim conseguimos escolher um código que tenha boa cobertura
        # entre os produtos da rodada, em vez de escolher um código diferente
        # para cada produto.
        for o in flat:
            candidates_for_offer = match_public_coupons(
                o.get("title") or "",
                o.get("price") or 0,
                public_cards,
            )
            offer_key = str(o.get("item_id") or o.get("product_id") or id(o))
            offer_coupon_candidates[offer_key] = candidates_for_offer
            for cup in candidates_for_offer:
                code = str(cup.get("code") or cup.get("label") or "").strip().upper()
                if not code:
                    continue
                coupon_coverage.setdefault(code, {
                    "count": 0,
                    "coupon": cup,
                    "products": [],
                })
                coupon_coverage[code]["count"] += 1
                coupon_coverage[code]["products"].append(offer_key)

        # SEGUNDO CAMINHO: cupons amplos divulgados por parceiros de Afiliados.
        broad_coupon_coverage = {}
        broad_coupon_for_offer = {}
        for o in flat:
            offer_key = str(o.get("item_id") or o.get("product_id") or id(o))
            broad = choose_broad_affiliate_coupon_for_offer(o, affiliate_coupon_catalog)
            if not broad:
                continue
            broad_coupon_for_offer[offer_key] = broad
            code = str(broad.get("code") or "").strip().upper()
            if not code:
                continue
            broad_coupon_coverage.setdefault(code, {"count": 0, "coupon": broad, "products": []})
            broad_coupon_coverage[code]["count"] += 1
            broad_coupon_coverage[code]["products"].append(offer_key)

        # O cupom principal é escolhido por COBERTURA real entre as ofertas.
        dominant_code = None
        dominant_info = None
        dominant_type = None
        combined_coverage = {}
        for code, info in coupon_coverage.items():
            combined_coverage[code] = dict(info)
            combined_coverage[code]["kind"] = "produto_publico"
        for code, info in broad_coupon_coverage.items():
            current = combined_coverage.get(code)
            if current is None or int(info.get("count") or 0) > int(current.get("count") or 0):
                combined_coverage[code] = dict(info)
                combined_coverage[code]["kind"] = "cupom_afiliado_amplo"

        if combined_coverage:
            dominant_code, dominant_info = max(
                combined_coverage.items(),
                key=lambda kv: (
                    int(kv[1].get("count") or 0),
                    1 if kv[1].get("kind") == "cupom_afiliado_amplo" else 0,
                    int(kv[1].get("coupon", {}).get("source_count") or 0),
                    float(kv[1].get("coupon", {}).get("desconto_estimado") or 0),
                ),
            )
            if int(dominant_info.get("count") or 0) < 3:
                dominant_code = None
                dominant_info = None
            else:
                dominant_type = dominant_info.get("kind")

        if dominant_code:
            print(
                f"[CUPOM COBERTURA] principal={dominant_code} "
                f"-> {dominant_info['count']}/{len(flat)} ofertas elegíveis"
            )
        else:
            print("[CUPOM COBERTURA] Nenhum código atingiu cobertura mínima de 3 ofertas; mantendo cupons individuais.")

        # SEGUNDO PASSO: se o cupom principal é elegível para aquele produto,
        # ele ganha prioridade. Caso contrário, usamos o melhor cupom daquele
        # produto. Nunca aplicamos o principal onde não existe associação.
        for o in flat:
            offer_key = str(o.get("item_id") or o.get("product_id") or id(o))
            candidates_for_offer = offer_coupon_candidates.get(offer_key) or []
            cup = None

            if dominant_code:
                if dominant_type == "cupom_afiliado_amplo":
                    broad_candidate = broad_coupon_for_offer.get(offer_key)
                    broad_code = str((broad_candidate or {}).get("code") or "").strip().upper()
                    if broad_code == dominant_code:
                        cup = broad_candidate
                else:
                    for candidate in candidates_for_offer:
                        code = str(candidate.get("code") or candidate.get("label") or "").strip().upper()
                        if code == dominant_code:
                            cup = candidate
                            break

            if cup is None:
                cup = max(
                    candidates_for_offer,
                    key=lambda x: (
                        float(x.get("desconto_estimado") or 0),
                        float(x.get("match_score") or 0),
                    ),
                    default=None,
                )

            # Se a associação global não encontrou nada, preserva o fallback
            # específico que já existia para tentar localizar cupom na página
            # pública do próprio anúncio.
            if cup is None:
                cup = choose_best_coupon(
                    o.get("title") or "",
                    o.get("price") or 0,
                    public_cards=public_cards,
                    item_id=o.get("item_id"),
                    permalink=o.get("permalink"),
                    allow_fallback=True,
                )

            if not cup:
                continue

            d = float(cup.get("desconto_estimado") or 0)
            base_price = float(o.get("price") or 0)
            if d <= 0 or base_price <= 0:
                continue
            d = min(d, base_price)
            o["cupom"] = cup
            o["desconto_cupom"] = round(d, 2)
            o["percentual_cupom_efetivo"] = round((d / base_price) * 100, 2)
            o["cupom_match"] = cup.get("match_type") or "produto_publico"
            o["cupom_uso_limite"] = cup.get("usage_limit")
            o["preco_com_cupom"] = round(base_price - d, 2)
            coupon_count += 1
            if cup.get("max_discount"):
                coupon_limit_count += 1
            best_coupon_discount = max(best_coupon_discount, d)
            if best_coupon_price is None or o["preco_com_cupom"] < best_coupon_price:
                best_coupon_price = o["preco_com_cupom"]

        coupon_coverage_count = int((dominant_info or {}).get("count") or 0)
        coupon_primary_code = dominant_code or ""
        print(
            f"[CUPONS AUTO] cards={len(public_cards)} "
            f"cupons_afiliados={len(affiliate_coupon_catalog)} "
            f"produtos_com_cupom={coupon_count} "
            f"cupom_principal={coupon_primary_code or 'nenhum'} "
            f"tipo={dominant_type or 'nenhum'} "
            f"cobertura={coupon_coverage_count}/{len(flat)}"
        )
    else:
        coupon_count = coupon_limit_count = 0
        best_coupon_discount = 0.0
        best_coupon_price = None
        coupon_primary_code = ""
        coupon_coverage_count = 0
        dominant_type = None

    if coupon_primary_code and coupon_coverage_count:
        for o in flat:
            cup = o.get("cupom") or {}
            code = str(cup.get("code") or cup.get("label") or "").strip().upper()
            if code == coupon_primary_code:
                cup["cobertura_rodada"] = coupon_coverage_count

    models = []
    for o in flat:
        models.append({
            "product_id": o["product_id"],
            "title": o["title"],
            "modelo_nome": o["modelo_nome"],
            "especificacoes": o["especificacoes"],
            "image": o["image"],
            "category_name": o["category_name"],
            "ofertas": [o],
        })

    print(f"[V66 PROGRESSO] scan_queries concluído: ofertas_finais={len(flat)}; candidatos={len(candidates)}; enriquecidos={len(fetched)}; cupons={coupon_count}")
    values = [o["price"] for o in flat if o.get("price") is not None]
    totals = [o["total_price"] for o in flat if o.get("shipping_known") and o.get("total_price") is not None]
    stats = {
        "ofertas": len(flat),
        "aparecem nos dois": 0,
        "mais vendidos": len(flat),
        "produtos em alta": 0,
        "validados alto giro": 0,
        "Full": sum(1 for o in flat if str(o.get("logistic_type") or "").lower() == "fulfillment"),
        "Gold/Platinum": 0,
        "100+ vendas": 0,
        "cupom candidato": coupon_count,
        "cupons com limite": coupon_limit_count,
        "maior desconto estimado": brl(best_coupon_discount),
        "menor preço com cupom": brl(best_coupon_price) if best_coupon_price is not None else "—",
        "menor preço do produto": brl(min(values or [0])),
        "menor total com frete": brl(min(totals or [0])),
        "produtos sem cupom": max(0, len(flat) - coupon_count),
        "cupom principal": coupon_primary_code or "—",
        "tipo do cupom principal": dominant_type or "—",
        "ofertas elegíveis para cupom principal": coupon_coverage_count,
        "modo": f"V63: busca ampliada e filtros de frete grátis/produto novo/origem local removidos; imagens opcionais; rodízio de categorias no WhatsApp; até {SEARCH_CANDIDATES_PER_CATEGORY_ALL} candidatos por categoria no modo todas; preço mínimo R$ {MIN_PRODUCT_PRICE:.2f}",
        "meta_ofertas": SEARCH_TARGET_OFFERS,
        "pool_candidatos": len(candidates),
    }
    print(f"[RESULTADO OFERTAS] {len(flat)} produtos | categorias={categories} | candidatos={len(candidates)} | enriquecidos={len(fetched)}")
    return {
        "stats": stats,
        "modelos": models,
        "ofertas": flat,
        "cupom_principal": coupon_primary_code or None,
        "cupom_principal_cobertura": coupon_coverage_count,
    }

def auto_scan(category=None, min_discount=0):
    # A seleção passa pelo nome da categoria; não depende de uma função
    # auxiliar que pode não existir no ambiente de produção.
    if category and category in CATALOG:
        queries = [category]
    else:
        queries = list(CATALOG.keys())
    return scan_queries(queries, min_discount, apply_coupons=True)

# ============================================================
# ANÚNCIO / AFILIADO
# ============================================================

def valid_affiliate_link(link):
    """Aceita links de afiliado oficiais usados pelo Mercado Livre.

    1) Link curto meli.la
    2) Link gerado pelo Compartilhar/Barra de Afiliados do Mercado Livre,
       identificado por sid=share + wid=MLB...

    Não aceita uma URL comum de produto sem os sinais do link de afiliado.
    """
    link = str(link or "").strip()
    if not link:
        return False

    if re.match(r"^https?://(?:www\.)?meli\.la/[A-Za-z0-9]+/?$", link, re.I):
        return True

    try:
        parsed = urlparse(link)
        host = (parsed.netloc or "").lower().split(":", 1)[0]
        if host not in {"mercadolivre.com.br", "www.mercadolivre.com.br"}:
            return False

        # Em links copiados pelo Compartilhar no iPhone, sid/wid podem vir
        # depois do # (fragmento), e não na query string. Ex.:
        # ...#origin=share&sid=share&wid=MLB4871129789
        query_parts = [parsed.query or ""]
        fragment = (parsed.fragment or "").lstrip("?#&")
        if fragment:
            query_parts.append(fragment)

        sid_values = set()
        wid_values = set()
        for part in query_parts:
            qs = parse_qs(part, keep_blank_values=True)
            sid_values.update(str(v).strip().lower() for v in qs.get("sid", []))
            wid_values.update(str(v).strip().upper() for v in qs.get("wid", []))

        return "share" in sid_values and any(re.fullmatch(r"MLB\d+", v) for v in wid_values)
    except Exception:
        return False

def _extract_item_id_from_affiliate_url(link):
    """Extrai o ITEM MLB de um link de afiliado do Mercado Livre.

    Links compartilhados no iPhone podem trazer wid no fragmento (#),
    enquanto links meli.la precisam ser seguidos até a URL de destino.
    Retorna somente o ID de publicação (MLB...), nunca o ID de catálogo.
    """
    link = str(link or "").strip()
    if not link:
        return None

    def from_url(url):
        try:
            u = urlparse(str(url or ""))
            parts = [u.query or "", (u.fragment or "").lstrip("?#&")]
            for part in parts:
                if not part:
                    continue
                qs = parse_qs(part, keep_blank_values=True)
                for key in ("wid", "item_id", "id"):
                    for value in qs.get(key, []):
                        m = re.search(r"\b(MLB\d+)\b", str(value).upper())
                        if m:
                            return m.group(1)
                for value in qs.get("pdp_filters", []):
                    m = re.search(r"item_id(?:%3A|:)\s*(MLB\d+)", str(value), re.I)
                    if m:
                        return m.group(1).upper()
            # Alguns links deixam o item_id percent-encoded no URL inteiro.
            decoded = str(url).replace("%3A", ":").replace("%3a", ":")
            m = re.search(r"item_id[:=]?(MLB\d+)", decoded, re.I)
            if m:
                return m.group(1).upper()
        except Exception:
            pass
        return None

    direct = from_url(link)
    if direct:
        return direct

    try:
        u = urlparse(link)
        host = (u.netloc or "").lower().split(":", 1)[0]
        if host not in {"meli.la", "www.meli.la"}:
            return None

        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        response = requests.get(link, headers=headers, timeout=12, allow_redirects=True)

        # Primeiro tenta todas as URLs do redirecionamento, da última para a primeira.
        urls = [getattr(response, "url", "")]
        for hist in reversed(getattr(response, "history", []) or []):
            loc = hist.headers.get("Location") or hist.headers.get("location") or ""
            if loc:
                urls.append(loc)
            urls.append(getattr(hist, "url", ""))
        for url in urls:
            item_id = from_url(url)
            if item_id:
                return item_id

        # Último recurso: procura o item_id no HTML da página de destino.
        html = response.text or ""
        patterns = [
            r"[\"']item_id[\"']\s*[:=]\s*[\"'](MLB\d+)",
            r"item_id(?:%3A|:)\s*(MLB\d+)",
            r"(?:wid|itemId|item_id)[=\"':]+(MLB\d+)",
        ]
        for pattern in patterns:
            m = re.search(pattern, html, re.I)
            if m:
                return m.group(1).upper()
    except Exception as exc:
        print("[AFILIADO RESOLVE]", repr(exc))

    return None


def ad_text(o, affiliate=""):
    """Monta o anúncio no formato visual pedido para o WhatsApp.

    Formato:
    - frase de destaque
    - nome do produto
    - preço antigo riscado com ~ ~
    - cupom, quando existir
    - preço atual em destaque
    - uma linha em branco antes da chamada
    - PEGAR PROMOÇÃO + link na MESMA linha

    O ~texto~ é o recurso nativo de tachado do WhatsApp.
    """
    title = str(o.get("title") or "Produto").strip()
    marketing = _marketing_phrase(title, o)

    lines = [
        f"*{marketing.upper()}*",
        "",
        f"*{title}*",
        "",
    ]

    # Em queda de preço, o preço antigo deve ser o último preço publicado
    # anteriormente no grupo. Nas ofertas normais, usamos o preço original
    # do anúncio, quando disponível.
    previous_price = o.get("_price_drop_from")
    try:
        previous_price = float(previous_price) if previous_price not in (None, "") else None
        current_price = float(o.get("price") or 0)
    except (TypeError, ValueError):
        previous_price = None
        current_price = 0.0
    is_price_drop = previous_price is not None and current_price > 0 and current_price < previous_price - 0.01

    original = o.get("original_price")
    try:
        original_value = float(original) if original not in (None, "") else 0.0
    except (TypeError, ValueError):
        original_value = 0.0

    # Evita mostrar dois preços antigos diferentes no anúncio de queda.
    if not is_price_drop and original_value > 0:
        lines.append(f"~De {brl(original_value)}~")

    if o.get("cupom"):
        c = o["cupom"] or {}
        label = c.get("code") or c.get("label") or "Cupom disponível"
        lines.append(f"🎟️ Cupom: *{label}*")

    # Na queda, exibe explicitamente o último preço enviado e o preço atual.
    if is_price_drop:
        lines[0] = "🚨 *VOLTOU MAIS BARATO! PREÇO REDUZIDO* 🚨"
        lines.append(f"~Antes: {brl(previous_price)}~")
    lines.append(f"Por *{brl(o['price'])}*")

    link = str(affiliate or "").strip()
    if not valid_affiliate_link(link):
        raise ValueError("Informe um link de afiliado válido do Mercado Livre antes de gerar o anúncio.")

    # Link fica na frente, continuando a mesma linha de PEGAR PROMOÇÃO.
    lines.append(f"*PEGAR PROMOÇÃO 🔥:* {link}")

    return "\n".join(lines)


def _marketing_phrase(title, offer=None):
    """Cria uma chamada curta e uma microdescrição de venda baseada no produto.

    A ideia é não usar uma frase genérica por categoria. A função aproveita
    marca, modelo, linha, características que aparecem no título e, quando
    disponíveis, preço/desconto do próprio anúncio. Não inventa especificações
    que não estejam no título ou em regras conhecidas do produto.
    """
    t = str(title or "Produto").strip()
    n = norm(t)
    o = offer or {}

    def money(v):
        try:
            return brl(float(v))
        except Exception:
            return ""

    def product_name(max_words=8):
        # Remove ruídos comuns de título sem apagar marca/modelo.
        clean = re.sub(r"\b(mercado livre|original|novo|lacrado|envio gratis|frete gratis)\b", "", t, flags=re.I)
        clean = re.sub(r"\s+", " ", clean).strip(" -|/")
        parts = clean.split()
        return " ".join(parts[:max_words]) if parts else "este produto"

    pn = product_name()
    price = money(o.get("price"))
    old = money(o.get("original_price"))

    # Desconto: usa somente valores presentes no próprio anúncio.
    discount_text = ""
    try:
        if o.get("original_price") and o.get("price"):
            op = float(o["original_price"])
            cp = float(o["price"])
            if op > cp > 0:
                pct = round((1 - cp / op) * 100)
                if pct >= 1:
                    discount_text = f"{pct}% abaixo do preço anterior"
    except Exception:
        pass

    brands = ["Apple", "Samsung", "Xiaomi", "Motorola", "Realme", "Nike", "Adidas", "Puma", "Asics", "New Balance", "Mizuno", "Olympikus", "Fila", "Reebok", "Vans", "Converse", "Under Armour", "Skechers", "Oakley", "Lacoste", "JBL", "Sony", "Lenovo", "Dell", "Acer", "Boticário", "Natura", "Lattafa", "Wella", "L'Oréal", "L'Oreal", "CeraVe", "Principia"]
    brand = next((b for b in brands if norm(b) in n), "")
    bprefix = f"{brand.upper()} • " if brand else ""

    def result(headline, emojis, detail):
        return {"headline": headline, "emojis": emojis, "detail": detail}

    # ============================================================
    # PERFUMES — mantém o estilo específico por modelo.
    # ============================================================
    perfume_profiles = [
        (["fakhar rose", "fakhar women", "fakhar feminino"],
         "FLORAL, FEMININO E ELEGANTE — O FAKHAR ROSE É UM DESTAQUE DA LATTAFA", "🌸✨",
         "Tuberosa e jasmim no coração, com uma base de baunilha, almíscar branco e sândalo."),
        (["fakhar black", "fakhar men", "fakhar masculino"],
         "FRESCO, MASCULINO E COM PRESENÇA — FAKHAR BLACK EM DESTAQUE", "🖤🔥",
         "Uma opção da Lattafa para quem procura uma fragrância masculina com perfil moderno."),
        (["fakhar extrait"],
         "FRESCO NA SAÍDA, ESPECIADO NO CORAÇÃO E MARCANTE NA BASE", "🔥✨",
         "Fakhar Extrait combina grapefruit, pimenta-rosa e cardamomo com tuberosa, âmbar e couro."),
        (["asad"],
         "INTENSO, ESPECIADO E MARCANTE — ASAD EM DESTAQUE", "🖤🔥",
         "Uma opção da Lattafa para quem prefere fragrâncias mais intensas e de personalidade."),
        (["khamrah qahwa"],
         "DOCE, ESPECIADO E COM UM TOQUE DE CAFÉ — KHAMRAH QAHWA", "☕🔥",
         "Uma combinação para quem gosta de perfumes quentes e envolventes."),
        (["khamrah"],
         "DOCE, QUENTE E ENVOLVENTE — KHAMRAH EM DESTAQUE", "🍂🔥",
         "Uma opção para quem curte um perfil mais gourmand e cheio de presença."),
        (["oud for glory"],
         "OUD INTENSO, ELEGANTE E MARCANTE — PARA QUEM GOSTA DE PRESENÇA", "🖤🔥",
         "Uma escolha para quem procura um perfume árabe com personalidade forte."),
        (["club de nuit intense"],
         "MARCANTE E ELEGANTE — CLUB DE NUIT INTENSE EM DESTAQUE", "🔥🖤",
         "Uma fragrância para quem gosta de um perfil marcante e sofisticado."),
        (["club de nuit woman", "club de nuit women"],
         "FEMININO, ELEGANTE E MARCANTE — CLUB DE NUIT WOMAN", "🌹✨",
         "Uma opção para quem gosta de fragrâncias femininas com presença."),
        (["9pm"],
         "DOCE, SEDUTOR E MARCANTE — 9PM EM DESTAQUE", "🌙🔥",
         "Uma opção para quem prefere um perfume mais adocicado e envolvente."),
        (["qaed al fursan"],
         "FRUTADO, MARCANTE E CHEIO DE PERSONALIDADE — QAED AL FURSAN", "🍍🔥",
         "Uma escolha para quem gosta de fragrâncias árabes com perfil frutado."),
        (["yara moi"],
         "CREMOSO, FEMININO E DELICADAMENTE ADOCICADO — YARA MOI", "🤍✨",
         "Uma opção da linha Yara para quem prefere um perfil feminino mais cremoso."),
        (["yara tous"],
         "FRUTADO, FEMININO E VIBRANTE — YARA TOUS EM DESTAQUE", "🥭✨",
         "Uma escolha para quem gosta de fragrâncias femininas com uma pegada mais alegre."),
        (["yara candy"],
         "DOCE, JOVEM E DIVERTIDO — YARA CANDY", "🍬💗",
         "Uma opção para quem procura um perfume feminino com proposta mais doce."),
        (["yara"],
         "DELICADO, FEMININO E ADOCICADO — YARA EM DESTAQUE", "🎀✨",
         "Uma escolha para quem gosta de fragrâncias femininas mais doces e delicadas."),
        (["delilah"],
         "FLORAL, FEMININO E ELEGANTE — DELILAH EM DESTAQUE", "🌸✨",
         "Uma opção para quem procura uma fragrância feminina com perfil floral."),
        (["khair pistachio"],
         "PISTACHE, DOÇURA E CREMOSIDADE — KHAIR PISTACHIO", "💚✨",
         "Uma escolha para quem ama perfumes gourmand e um perfil mais cremoso."),
        (["nebras", "neb ras"],
         "DOCE, CREMOSO E ENVOLVENTE — NEBRAS EM DESTAQUE", "🍫✨",
         "Uma opção para quem prefere fragrâncias doces e aconchegantes."),
        (["liam grey"],
         "ELEGANTE, ESPECIADO E SOFISTICADO — LIAM GREY", "🩶🔥",
         "Uma opção para quem gosta de perfumes com personalidade e perfil refinado."),
        (["liquid brun"],
         "QUENTE, MARCANTE E SOFISTICADO — LIQUID BRUN", "🤎🔥",
         "Uma escolha para quem procura uma fragrância masculina com presença."),
        (["maahir black"],
         "ESCURO, INTENSO E MARCANTE — MAAHIR BLACK", "🖤🔥",
         "Uma opção para quem prefere perfumes árabes com personalidade forte."),
        (["najdia"],
         "FRESCO, VIBRANTE E MASCULINO — NAJDIA EM DESTAQUE", "💙🔥",
         "Uma escolha para quem procura uma fragrância masculina com proposta fresca."),
        (["teriaq", "tériaq"],
         "DOCE, MARCANTE E ENVOLVENTE — TERIAQ EM DESTAQUE", "🍯🔥",
         "Uma opção para quem gosta de perfumes com presença e lado adocicado."),
    ]
    for terms, headline, emojis, detail in perfume_profiles:
        if any(term in n for term in terms):
            return result(headline, emojis, detail)

    if any(x in n for x in ["perfume", "parfum", "eau de", "fragrance", "colonia", "colônia", "body splash", "body mist"]):
        if "body splash" in n:
            return result(f"BODY SPLASH EM OFERTA — {pn.upper()}", "🌸🔥", f"{pn} aparece com preço promocional — uma opção leve para quem gosta de manter a fragrância por perto.")
        if "body mist" in n:
            return result(f"BODY MIST EM DESTAQUE — {pn.upper()}", "✨🌸", f"{pn} entra no radar com preço promocional, uma opção prática para a rotina.")
        if "feminino" in n or "women" in n:
            return result(f"PERFUME FEMININO EM DESTAQUE — {pn.upper()}", "🌸✨", f"{pn} aparece com condição promocional para quem procura uma fragrância feminina para a coleção.")
        if "masculino" in n or "men" in n:
            return result(f"PERFUME MASCULINO EM DESTAQUE — {pn.upper()}", "🖤🔥", f"{pn} aparece com condição promocional para quem procura uma fragrância masculina com presença.")
        return result(f"PERFUME EM DESTAQUE — {pn.upper()}", "✨🔥", f"{pn} apareceu com preço promocional e merece entrar no radar de quem gosta de perfumaria.")

    # ============================================================
    # TECNOLOGIA — descrição baseada no item/modelo.
    # ============================================================
    if any(x in n for x in ["iphone", "smartphone", "celular", "galaxy", "redmi", "poco", "moto g", "motorola"]):
        extra = ""
        if re.search(r"\b(5g)\b", n): extra = " com 5G"
        if re.search(r"\b(128gb|256gb|512gb|1tb)\b", n): extra += " e armazenamento destacado no anúncio"
        return result(f"{bprefix}{pn.upper()} — PREÇO PARA FICAR DE OLHO", "📱🔥", f"O {pn}{extra} aparece com condição promocional — uma oportunidade para quem quer atualizar o celular.")
    if any(x in n for x in ["capa", "case", "pelicula", "película", "carregador", "cabo usb", "power bank", "suporte para celular"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "📱⚡", f"{pn} é aquele acessório útil para complementar o celular e apareceu com preço promocional.")
    if any(x in n for x in ["airpods", "fone", "headset", "caixa de som", "jbl", "soundbar", "bluetooth"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "🎧🔥", f"{pn} aparece com preço promocional — uma boa hora para melhorar o áudio sem deixar a oferta passar.")
    if any(x in n for x in ["notebook", "macbook", "monitor", "teclado", "mouse", "ssd", "memoria ram", "memória ram", "impressora", "webcam"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "💻🔥", f"{pn} aparece com condição promocional — ótimo para quem está montando, atualizando ou completando o setup.")
    if any(x in n for x in ["tablet", "smartwatch", "tv smart", "console", "videogame", "controle gamer"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "⚡🔥", f"{pn} apareceu com preço promocional e pode ser uma boa oportunidade para quem já estava procurando esse tipo de produto.")

    # ============================================================
    # CASA / FERRAMENTAS
    # ============================================================
    if any(x in n for x in ["air fryer", "cafeteira", "liquidificador", "aspirador", "panela elétrica", "mixer"]):
        return result(f"{pn.upper()} EM OFERTA — ACHADO PARA CASA", "🏠🔥", f"{pn} combina praticidade para a rotina com uma condição promocional que vale conferir.")
    if any(x in n for x in ["pote", "organizador", "estante", "sapateira", "varal", "organização"]):
        return result(f"{pn.upper()} EM DESTAQUE — CASA MAIS ORGANIZADA", "🏠✨", f"{pn} é uma solução prática para organização e apareceu com preço promocional.")
    if any(x in n for x in ["furadeira", "parafusadeira", "esmerilhadeira", "broca", "ferramenta", "serra", "martelete"]):
        extra = ""
        m = re.search(r"\b(\d+\s?v|\d+\s?volts?)\b", n)
        if m: extra = f" com {m.group(1)}"
        return result(f"{pn.upper()} EM OFERTA — OLHA ESSA CONDIÇÃO", "🔧🔥", f"{pn}{extra} aparece com preço promocional — uma opção para oficina, manutenção ou projetos em casa.")

    # ============================================================
    # ACADEMIA & FITNESS
    # ============================================================
    if any(x in n for x in ["esteira", "bicicleta ergométrica", "bike spinning", "elíptico", "step", "stepper"]):
        return result(f"{pn.upper()} EM OFERTA — TREINO EM CASA", "🏃🔥", f"{pn} apareceu com preço promocional para quem quer montar ou melhorar o espaço de treino em casa.")
    if any(x in n for x in ["faixa elástica", "faixa elastica", "elástico de resistência", "elastico de resistencia", "caneleira", "luva de academia", "strap", "wrist wrap", "cinturão", "corda de pular", "barra de porta"]):
        return result(f"{pn.upper()} EM DESTAQUE — ACESSÓRIO DE TREINO", "💪🔥", f"{pn} é um acessório para complementar a rotina de treino e apareceu com uma condição promocional.")
    if any(x in n for x in ["garrafa fitness", "coqueteleira", "shaker", "mochila academia", "bolsa academia", "toalha academia"]):
        return result(f"{pn.upper()} EM OFERTA — PARA A ROTINA FITNESS", "🥤💪", f"{pn} é aquele item prático para acompanhar os treinos e apareceu com preço promocional.")
    if any(x in n for x in ["yoga", "pilates", "alongamento", "tapete"]):
        return result(f"{pn.upper()} EM DESTAQUE — YOGA/PILATES", "🧘🔥", f"{pn} apareceu com preço promocional para treinos, alongamentos e exercícios em casa.")

    # ============================================================
    # SAÚDE & BELEZA
    # ============================================================
    if any(x in n for x in ["shampoo", "condicionador", "máscara capilar", "mascara capilar", "secador", "chapinha", "modelador", "escova secadora"]):
        return result(f"{pn.upper()} EM DESTAQUE — CUIDADOS COM O CABELO", "💇🔥", f"{pn} aparece com preço promocional para quem quer cuidar ou renovar a rotina de cabelos.")
    if any(x in n for x in ["unha", "manicure", "esmalte", "cabine uv", "cabine led", "nail art", "lixa elétrica", "gel para unhas"]):
        return result(f"{pn.upper()} EM OFERTA — UNHAS E MANICURE", "💅✨", f"{pn} apareceu com condição promocional para montar, renovar ou completar o kit de manicure.")
    if any(x in n for x in ["skincare", "protetor solar facial", "hidratante facial", "serum facial", "sérum facial", "vitamina c", "niacinamida", "ácido hialurônico", "acido hialuronico"]):
        return result(f"{pn.upper()} EM DESTAQUE — SKINCARE", "🧴✨", f"{pn} apareceu com preço promocional para quem quer manter ou completar a rotina de cuidados faciais.")
    if any(x in n for x in ["barbeador", "barbearia", "máquina de cortar cabelo", "maquina de cortar cabelo", "aparador", "trimmer"]):
        return result(f"{pn.upper()} EM OFERTA — CUIDADOS MASCULINOS", "🪒🔥", f"{pn} é uma opção prática para barba e cabelo e apareceu com condição promocional.")
    if any(x in n for x in ["hidratante corporal", "creme corporal", "body cream", "óleo corporal", "oleo corporal"]):
        return result(f"{pn.upper()} EM DESTAQUE — CUIDADOS CORPORAIS", "🧖✨", f"{pn} apareceu com preço promocional para completar a rotina de cuidados corporais.")

    # ============================================================
    # MODA
    # ============================================================
    if any(x in n for x in ["camiseta", "t-shirt", "tee"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "👕🔥", f"{pn} traz uma proposta casual e apareceu com preço promocional — boa hora para renovar o guarda-roupa.")
    if any(x in n for x in ["camisa de futebol", "camisa esportiva", "camisa futebol", "jersey"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "⚽🔥", f"{pn} apareceu com condição promocional para quem curte futebol e quer garantir uma peça esportiva.")
    if any(x in n for x in ["jaqueta", "corta vento", "corta-vento"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "🧥🔥", f"{pn} é uma peça versátil para completar o visual e apareceu com preço promocional.")
    if any(x in n for x in ["bermuda", "shorts"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "🩳🔥", f"{pn} é uma peça prática para dia a dia, lazer ou treino e apareceu com condição promocional.")
    if any(x in n for x in ["calça", "calca", "jeans"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "👖🔥", f"{pn} é uma peça versátil para o guarda-roupa e apareceu com preço promocional.")
    if any(x in n for x in ["fitness", "legging", "top esportivo", "short esportivo", "roupa esportiva"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "🏃🔥", f"{pn} aparece com condição promocional para quem procura roupa para treinar ou praticar esportes.")
    if any(x in n for x in ["moletom", "casaco"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "🧥🔥", f"{pn} combina conforto para o dia a dia e apareceu com preço promocional.")
    if any(x in n for x in ["vestido", "blusa feminina", "saia", "conjunto feminino"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "👚✨", f"{pn} apareceu com preço promocional para quem quer renovar o visual.")
    if "polo" in n:
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "👔🔥", f"{pn} é uma peça versátil para produções casuais e apareceu com condição promocional.")
    if any(x in n for x in ["biquini", "biquíni", "maiô", "maio", "sunga"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "🏖️🔥", f"{pn} apareceu com preço promocional para quem já está de olho na próxima praia ou piscina.")
    if any(x in n for x in ["boné", "bone", "bucket", "viseira"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "🧢🔥", f"{pn} é um detalhe fácil para completar o visual e apareceu com condição promocional.")

    # ============================================================
    # TÊNIS & CALÇADOS
    # ============================================================
    if any(x in n for x in ["corrida", "running"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA — CORRIDA", "🏃👟", f"{pn} aparece com preço promocional para quem procura um tênis voltado para corrida e treinos.")
    if any(x in n for x in ["academia", "training", "treino"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE — ACADEMIA", "🏋️👟", f"{pn} apareceu com condição promocional para acompanhar a rotina de treino.")
    if any(x in n for x in ["chuteira", "futebol"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA — FUTEBOL", "⚽🔥", f"{pn} apareceu com preço promocional para quem joga ou curte futebol.")
    if any(x in n for x in ["basquete", "basketball"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE — BASQUETE", "🏀🔥", f"{pn} aparece com condição promocional para quadra ou para quem curte o estilo do basquete.")
    if any(x in n for x in ["trilha", "adventure", "trail"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA — TRILHA/ADVENTURE", "🥾🔥", f"{pn} apareceu com preço promocional para quem procura um calçado para atividades ao ar livre.")
    if "skate" in n:
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE — SKATE", "🛹🔥", f"{pn} apareceu com condição promocional para quem anda de skate ou curte a pegada casual.")
    if any(x in n for x in ["chinelo", "slide", "sandália"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "🏖️🔥", f"{pn} aposta em praticidade para o dia a dia e apareceu com preço promocional.")
    if any(x in n for x in ["infantil", "kids"]):
        return result(f"{bprefix}{pn.upper()} EM DESTAQUE", "👟🧒", f"{pn} apareceu com condição promocional para os pequenos.")
    if any(x in n for x in ["tênis", "tenis", "sapatênis", "sapatenis", "calçado", "calcado"]):
        return result(f"{bprefix}{pn.upper()} EM OFERTA", "👟🔥", f"{pn} apareceu com preço promocional — uma opção para quem já estava procurando esse tipo de calçado.")

    # ============================================================
    # FALLBACK: ainda usa o nome real do produto, sem descrição falsa.
    # ============================================================
    if brand:
        return result(f"{brand.upper()} • {pn.upper()} EM DESTAQUE", "🔥👀", f"{pn} apareceu com condição promocional e merece entrar no radar de quem já procurava esse produto.")

    detail = f"{pn} apareceu com preço promocional — confira a condição enquanto a oferta estiver disponível."
    if discount_text:
        detail = f"{pn} está {discount_text}; uma boa oportunidade para quem já estava de olho nesse produto."
    return result(f"{pn.upper()} EM OFERTA", "🔥👀", detail)

def ad_text(o, affiliate=""):
    """Monta o anúncio no formato visual pedido para o WhatsApp.

    Formato:
    - frase de destaque
    - nome do produto
    - preço antigo riscado com ~ ~
    - cupom, quando existir
    - preço atual em destaque
    - uma linha em branco antes da chamada
    - PEGAR PROMOÇÃO + link na MESMA linha

    O ~texto~ é o recurso nativo de tachado do WhatsApp.
    """
    title = str(o.get("title") or "Produto").strip()
    marketing = _marketing_phrase(title, o)

    lines = [
        f"*{marketing.upper()}*",
        "",
        f"*{title}*",
        "",
    ]

    # Preço antigo no estilo do anúncio de referência: ~De R$222,83~
    # Só mostramos o valor riscado quando ele realmente existe.
    original = o.get("original_price")
    try:
        original_value = float(original) if original not in (None, "") else 0.0
    except (TypeError, ValueError):
        original_value = 0.0

    if original_value > 0:
        lines.append(f"~De {brl(original_value)}~")

    if o.get("cupom"):
        c = o["cupom"] or {}
        label = c.get("code") or c.get("label") or "Cupom disponível"
        lines.append(f"🎟️ Cupom: *{label}*")

    # Preço atual separado do preço antigo para ficar visualmente limpo.
    lines.append(f"Por *{brl(o['price'])}*")

    link = str(affiliate or "").strip()
    if not valid_affiliate_link(link):
        raise ValueError("Informe um link de afiliado válido do Mercado Livre antes de gerar o anúncio.")

    # Link fica na frente, continuando a mesma linha de PEGAR PROMOÇÃO.
    lines.append(f"*PEGAR PROMOÇÃO 🔥:* {link}")

    return "\n".join(lines)


def _marketing_phrase(title, offer=None):
    """Gera uma chamada comercial específica para qualquer categoria.

    A frase usa o próprio título do produto para identificar o tipo de item,
    marca e/ou modelo quando possível. Evita deixar a comunicação restrita
    a perfumes e evita o fallback genérico sempre que houver informação útil.
    """
    t = str(title or "Produto").strip()
    n = norm(t)

    # ============================================================
    # PERFUMES — mantém as chamadas específicas já construídas.
    # ============================================================
    perfume_profiles = [
        (["fakhar rose", "fakhar women", "fakhar feminino"],
         "FLORAL, FEMININO E ELEGANTE — O FAKHAR ROSE É UM DESTAQUE DA LATTAFA", "🌸✨",
         "Tuberosa e jasmim no coração, com uma base de baunilha, almíscar branco e sândalo."),
        (["fakhar black", "fakhar men", "fakhar masculino"],
         "FRESCO, MASCULINO E COM PRESENÇA — FAKHAR BLACK EM DESTAQUE", "🖤🔥",
         "Uma opção da Lattafa para quem procura uma fragrância masculina com perfil moderno."),
        (["fakhar extrait"],
         "FRESCO NA SAÍDA, ESPECIADO NO CORAÇÃO E MARCANTE NA BASE", "🔥✨",
         "Fakhar Extrait combina grapefruit, pimenta-rosa e cardamomo com tuberosa, âmbar e couro."),
        (["asad"],
         "INTENSO, ESPECIADO E MARCANTE — ASAD EM DESTAQUE", "🖤🔥",
         "Uma opção da Lattafa para quem prefere fragrâncias mais intensas e de personalidade."),
        (["khamrah qahwa"],
         "DOCE, ESPECIADO E COM UM TOQUE DE CAFÉ — KHAMRAH QAHWA", "☕🔥",
         "Uma combinação para quem gosta de perfumes quentes e envolventes."),
        (["khamrah"],
         "DOCE, QUENTE E ENVOLVENTE — KHAMRAH EM DESTAQUE", "🍂🔥",
         "Uma opção para quem curte um perfil mais gourmand e cheio de presença."),
        (["oud for glory"],
         "OUD INTENSO, ELEGANTE E MARCANTE — PARA QUEM GOSTA DE PRESENÇA", "🖤🔥",
         "Uma escolha para quem procura um perfume árabe com personalidade forte."),
        (["club de nuit intense"],
         "MARCANTE E ELEGANTE — CLUB DE NUIT INTENSE EM DESTAQUE", "🔥🖤",
         "Uma fragrância para quem gosta de um perfil marcante e sofisticado."),
        (["club de nuit woman", "club de nuit women"],
         "FEMININO, ELEGANTE E MARCANTE — CLUB DE NUIT WOMAN", "🌹✨",
         "Uma opção para quem gosta de fragrâncias femininas com presença."),
        (["9pm"],
         "DOCE, SEDUTOR E MARCANTE — 9PM EM DESTAQUE", "🌙🔥",
         "Uma opção para quem prefere um perfume mais adocicado e envolvente."),
        (["qaed al fursan"],
         "FRUTADO, MARCANTE E CHEIO DE PERSONALIDADE — QAED AL FURSAN", "🍍🔥",
         "Uma escolha para quem gosta de fragrâncias árabes com perfil frutado."),
        (["yara moi"],
         "CREMOSO, FEMININO E DELICADAMENTE ADOCICADO — YARA MOI", "🤍✨",
         "Uma opção da linha Yara para quem prefere um perfil feminino mais cremoso."),
        (["yara tous"],
         "FRUTADO, FEMININO E VIBRANTE — YARA TOUS EM DESTAQUE", "🥭✨",
         "Uma escolha para quem gosta de fragrâncias femininas com uma pegada mais alegre."),
        (["yara candy"],
         "DOCE, JOVEM E DIVERTIDO — YARA CANDY", "🍬💗",
         "Uma opção para quem procura um perfume feminino com proposta mais doce."),
        (["yara"],
         "DELICADO, FEMININO E ADOCICADO — YARA EM DESTAQUE", "🎀✨",
         "Uma escolha para quem gosta de fragrâncias femininas mais doces e delicadas."),
        (["delilah"],
         "FLORAL, FEMININO E ELEGANTE — DELILAH EM DESTAQUE", "🌸✨",
         "Uma opção para quem procura uma fragrância feminina com perfil floral."),
        (["khair pistachio"],
         "PISTACHE, DOÇURA E CREMOSIDADE — KHAIR PISTACHIO", "💚✨",
         "Uma escolha para quem ama perfumes gourmand e um perfil mais cremoso."),
        (["nebras", "neb ras"],
         "DOCE, CREMOSO E ENVOLVENTE — NEBRAS EM DESTAQUE", "🍫✨",
         "Uma opção para quem prefere fragrâncias doces e aconchegantes."),
        (["liam grey"],
         "ELEGANTE, ESPECIADO E SOFISTICADO — LIAM GREY", "🩶🔥",
         "Uma opção para quem gosta de perfumes com personalidade e perfil refinado."),
        (["liquid brun"],
         "QUENTE, MARCANTE E SOFISTICADO — LIQUID BRUN", "🤎🔥",
         "Uma escolha para quem procura uma fragrância masculina com presença."),
        (["maahir black"],
         "ESCURO, INTENSO E MARCANTE — MAAHIR BLACK", "🖤🔥",
         "Uma opção para quem prefere perfumes árabes com personalidade forte."),
        (["najdia"],
         "FRESCO, VIBRANTE E MASCULINO — NAJDIA EM DESTAQUE", "💙🔥",
         "Uma escolha para quem procura uma fragrância masculina com proposta fresca."),
        (["teriaq", "tériaq"],
         "DOCE, MARCANTE E ENVOLVENTE — TERIAQ EM DESTAQUE", "🍯🔥",
         "Uma opção para quem gosta de perfumes com presença e lado adocicado."),
    ]
    for terms, headline, emojis, detail in perfume_profiles:
        if any(term in n for term in terms):
            return {"headline": headline, "emojis": emojis, "detail": detail}

    if any(x in n for x in [
        "perfume", "parfum", "eau de", "fragrance",
        "colonia", "colônia", "body splash", "body mist",
        "edt", "edp", "eau de toilette", "eau de parfum",
        "eau de cologne", "deo colonia", "deo colônia",
        "desodorante colonia", "desodorante colônia",
    ]):
        if "body splash" in n:
            return {"headline": "BODY SPLASH EM OFERTA — PERFUME LEVE PARA O DIA A DIA", "emojis": "🌸🔥", "detail": "Uma opção prática para quem prefere uma fragrância leve e fácil de usar."}
        if "body mist" in n:
            return {"headline": "BODY MIST EM DESTAQUE — LEVE E PRÁTICO PARA REAPLICAR", "emojis": "✨🌸", "detail": "Uma opção para deixar na rotina e reaplicar ao longo do dia."}
        if "feminino" in n or "women" in n:
            return {"headline": "PERFUME FEMININO EM DESTAQUE — OLHA ESSA OFERTA", "emojis": "🌸✨", "detail": "Uma opção para quem gosta de fragrâncias femininas e encontrou um bom preço."}
        if "masculino" in n or "men" in n:
            return {"headline": "PERFUME MASCULINO EM DESTAQUE — PREÇO PARA FICAR DE OLHO", "emojis": "🖤🔥", "detail": "Uma alternativa para quem procura uma fragrância masculina em promoção."}
        return {"headline": "PERFUME EM DESTAQUE — OLHA O PREÇO DESSE ACHADO", "emojis": "✨🔥", "detail": "Uma fragrância para colocar no radar quando aparece com preço promocional."}

    # ============================================================
    # TECNOLOGIA
    # ============================================================
    brands = ["Apple", "Samsung", "Xiaomi", "Motorola", "Realme", "Nike", "Adidas", "Puma", "Asics", "New Balance", "Mizuno", "Olympikus", "Fila", "Reebok", "Vans", "Converse", "Under Armour", "Skechers", "Oakley", "Lacoste", "JBL", "Sony", "Lenovo", "Dell", "Acer"]
    brand = next((b for b in brands if norm(b) in n), "")
    bprefix = f"{brand.upper()} • " if brand else ""

    if any(x in n for x in ["iphone", "smartphone", "celular", "galaxy", "redmi", "poco", "moto g", "motorola"]):
        return {"headline": f"{bprefix}CELULAR EM DESTAQUE — OLHA ESSE PREÇO", "emojis": "📱🔥", "detail": "Uma oferta para quem já estava de olho em trocar ou atualizar o celular."}
    if any(x in n for x in ["capa", "case", "pelicula", "película", "carregador", "cabo usb", "power bank", "suporte para celular"]):
        return {"headline": "ACESSÓRIO PARA CELULAR EM OFERTA — PREÇO BAIXOU", "emojis": "📱⚡", "detail": "Itens úteis para proteger, carregar ou complementar o celular."}
    if any(x in n for x in ["airpods", "fone", "headset", "caixa de som", "jbl", "soundbar", "bluetooth"]):
        return {"headline": f"{bprefix}ÁUDIO EM DESTAQUE — OFERTA PARA FICAR DE OLHO", "emojis": "🎧🔥", "detail": "Uma opção para quem quer melhorar o áudio sem deixar passar uma boa oferta."}
    if any(x in n for x in ["notebook", "macbook", "monitor", "teclado", "mouse", "ssd", "memoria ram", "memória ram", "impressora", "webcam"]):
        return {"headline": f"{bprefix}INFORMÁTICA EM OFERTA — OLHA O PREÇO", "emojis": "💻🔥", "detail": "Uma oportunidade para quem está montando ou atualizando o setup."}
    if any(x in n for x in ["tablet", "smartwatch", "tv smart", "console", "videogame", "controle gamer"]):
        return {"headline": "ELETRÔNICO EM DESTAQUE — PREÇO PARA CONFERIR", "emojis": "⚡🔥", "detail": "Um produto que pode valer a pena quando aparece com essa condição."}

    # ============================================================
    # CASA E FERRAMENTAS
    # ============================================================
    if any(x in n for x in ["air fryer", "cafeteira", "liquidificador", "aspirador", "panela elétrica", "mixer", "cozinha", "pote", "organizador", "estante", "sapateira", "varal"]):
        return {"headline": "ACHADO PARA CASA — PREÇO BOM PARA DEIXAR NO RADAR", "emojis": "🏠🔥", "detail": "Produto útil para a rotina e que merece uma olhada quando entra em promoção."}
    if any(x in n for x in ["furadeira", "parafusadeira", "esmerilhadeira", "chave", "broca", "ferramenta", "serra", "martelete"]):
        return {"headline": "FERRAMENTA EM OFERTA — BOA HORA PARA QUEM ESTÁ PRECISANDO", "emojis": "🔧🔥", "detail": "Uma opção prática para oficina, manutenção ou projetos em casa."}

    # ============================================================
    # ACADEMIA & FITNESS
    # ============================================================
    if any(x in n for x in ["esteira", "bicicleta ergométrica", "bike spinning", "elíptico", "step", "stepper", "cardio"]):
        return {"headline": "TREINO EM CASA — EQUIPAMENTO EM OFERTA", "emojis": "🏃🔥", "detail": "Uma opção para montar ou melhorar o espaço de treino em casa."}
    if any(x in n for x in ["faixa elástica", "faixa elastica", "elástico de resistência", "elastico de resistencia", "caneleira", "luva de academia", "strap", "wrist wrap", "cinturão", "corda de pular", "barra de porta"]):
        return {"headline": "ACESSÓRIO DE TREINO EM DESTAQUE — OLHA ESSA OFERTA", "emojis": "💪🔥", "detail": "Acessório para complementar o treino sem complicar a rotina."}
    if any(x in n for x in ["garrafa fitness", "coqueteleira", "shaker", "mochila academia", "bolsa academia", "toalha academia"]):
        return {"headline": "FITNESS EM OFERTA — ACESSÓRIO PARA O DIA A DIA", "emojis": "🥤💪", "detail": "Um item útil para acompanhar a rotina de treino."}
    if any(x in n for x in ["yoga", "pilates", "alongamento", "tapete"]):
        return {"headline": "YOGA E PILATES — ITEM EM OFERTA", "emojis": "🧘🔥", "detail": "Uma opção para treinar, alongar e montar seu espaço em casa."}

    # ============================================================
    # SAÚDE & BELEZA
    # ============================================================
    if any(x in n for x in ["shampoo", "condicionador", "máscara capilar", "mascara capilar", "secador", "chapinha", "modelador", "escova secadora", "wella", "l'oreal", "loreal", "kerastase", "elseve", "truss", "salon line"]):
        return {"headline": f"{bprefix}CUIDADOS COM O CABELO — OFERTA EM DESTAQUE", "emojis": "💇🔥", "detail": "Uma opção para cuidar dos cabelos aproveitando uma condição promocional."}
    if any(x in n for x in ["unha", "manicure", "esmalte", "cabine uv", "cabine led", "nail art", "lixa elétrica", "gel para unhas"]):
        return {"headline": "UNHAS E MANICURE — KIT OU PRODUTO EM OFERTA", "emojis": "💅✨", "detail": "Uma boa opção para montar ou renovar o kit de manicure."}
    if any(x in n for x in ["skincare", "protetor solar facial", "hidratante facial", "serum facial", "sérum facial", "vitamina c", "niacinamida", "ácido hialurônico", "acido hialuronico", "cerave", "la roche", "principia", "neutrogena", "vichy"]):
        return {"headline": "SKINCARE EM DESTAQUE — OLHA ESSA CONDIÇÃO", "emojis": "🧴✨", "detail": "Produto para cuidados faciais que apareceu com preço promocional."}
    if any(x in n for x in ["barbeador", "barbearia", "máquina de cortar cabelo", "maquina de cortar cabelo", "aparador", "trimmer"]):
        return {"headline": "CUIDADOS MASCULINOS — EQUIPAMENTO EM OFERTA", "emojis": "🪒🔥", "detail": "Uma opção prática para barba, cabelo e rotina de cuidados."}
    if any(x in n for x in ["hidratante corporal", "creme corporal", "body cream", "óleo corporal", "oleo corporal", "cuidados corporais"]):
        return {"headline": "CUIDADOS CORPORAIS — PRODUTO EM PROMOÇÃO", "emojis": "🧖✨", "detail": "Uma opção para cuidados diários com uma condição promocional."}

    # ============================================================
    # MODA — exclui social tradicional/manga longa no catálogo, mas
    # a chamada também evita incentivar esse tipo de peça.
    # ============================================================
    if any(x in n for x in ["camiseta", "t-shirt", "tee"]):
        return {"headline": f"{bprefix}CAMISETA EM DESTAQUE — OLHA ESSE PREÇO", "emojis": "👕🔥", "detail": "Peça casual para o dia a dia com condição promocional."}
    if any(x in n for x in ["camisa de futebol", "camisa esportiva", "camisa futebol", "jersey", "futebol"]):
        return {"headline": f"{bprefix}CAMISA ESPORTIVA EM OFERTA — PREÇO PARA CONFERIR", "emojis": "⚽🔥", "detail": "Boa opção para quem curte futebol e quer aproveitar uma promoção."}
    if any(x in n for x in ["jaqueta", "corta vento", "corta-vento", "corta vento"]):
        return {"headline": f"{bprefix}JAQUETA EM OFERTA — PEÇA PARA FICAR DE OLHO", "emojis": "🧥🔥", "detail": "Uma peça versátil para completar o visual em dias mais frios ou de vento."}
    if any(x in n for x in ["bermuda", "shorts"]):
        return {"headline": f"{bprefix}BERMUDA EM DESTAQUE — PREÇO BOM PARA APROVEITAR", "emojis": "🩳🔥", "detail": "Peça prática para o dia a dia, treino ou momentos de lazer."}
    if any(x in n for x in ["calça", "calca", "jeans"]):
        return {"headline": f"{bprefix}CALÇA/JEANS EM OFERTA — OLHA O PREÇO", "emojis": "👖🔥", "detail": "Uma peça versátil para renovar o guarda-roupa."}
    if any(x in n for x in ["fitness", "legging", "top esportivo", "short esportivo", "roupa esportiva"]):
        return {"headline": f"{bprefix}ROUPA FITNESS EM DESTAQUE — OFERTA PARA O TREINO", "emojis": "🏃🔥", "detail": "Peça esportiva para treinar com uma condição promocional."}
    if any(x in n for x in ["moletom", "casaco"]):
        return {"headline": f"{bprefix}MOLETOM/CASACO EM OFERTA — OLHA ESSA CONDIÇÃO", "emojis": "🧥🔥", "detail": "Peça confortável para o dia a dia com preço promocional."}
    if any(x in n for x in ["moda feminina", "vestido", "blusa feminina", "saia", "conjunto feminino"]):
        return {"headline": f"{bprefix}MODA FEMININA EM DESTAQUE — PREÇO PARA CONFERIR", "emojis": "👚✨", "detail": "Uma peça para renovar o visual sem perder a oportunidade de promoção."}
    if any(x in n for x in ["polo"]):
        return {"headline": f"{bprefix}POLO EM OFERTA — ESTILO CASUAL COM PREÇO ESPECIAL", "emojis": "👔🔥", "detail": "Uma peça versátil para looks casuais e do dia a dia."}
    if any(x in n for x in ["biquini", "biquíni", "maiô", "maio", "sunga", "moda praia"]):
        return {"headline": f"{bprefix}MODA PRAIA EM DESTAQUE — OLHA ESSA OFERTA", "emojis": "🏖️🔥", "detail": "Uma opção para curtir praia ou piscina aproveitando o preço."}
    if any(x in n for x in ["boné", "bone", "bucket", "viseira", "acessório de moda"]):
        return {"headline": f"{bprefix}ACESSÓRIO EM OFERTA — DETALHE QUE FAZ DIFERENÇA", "emojis": "🧢🔥", "detail": "Um complemento fácil para o visual do dia a dia."}

    # ============================================================
    # TÊNIS & CALÇADOS
    # ============================================================
    shoe_brand = f"{brand.upper()} • " if brand else ""
    if any(x in n for x in ["corrida", "running"]):
        return {"headline": f"{shoe_brand}TÊNIS DE CORRIDA EM OFERTA — OLHA ESSE PREÇO", "emojis": "🏃👟", "detail": "Uma opção para corrida e treinos, com preço promocional."}
    if any(x in n for x in ["academia", "training", "treino"]):
        return {"headline": f"{shoe_brand}TÊNIS PARA ACADEMIA — OFERTA EM DESTAQUE", "emojis": "🏋️👟", "detail": "Uma opção para complementar o treino com uma condição promocional."}
    if any(x in n for x in ["chuteira", "futebol"]):
        return {"headline": f"{shoe_brand}FUTEBOL EM OFERTA — CHUTEIRA PARA FICAR DE OLHO", "emojis": "⚽🔥", "detail": "Uma opção para quem joga e quer aproveitar um preço promocional."}
    if any(x in n for x in ["basquete", "basketball"]):
        return {"headline": f"{shoe_brand}TÊNIS DE BASQUETE EM DESTAQUE", "emojis": "🏀🔥", "detail": "Uma opção para quadra ou para quem curte o estilo do basquete."}
    if any(x in n for x in ["trilha", "adventure", "trail"]):
        return {"headline": f"{shoe_brand}TRILHA/ADVENTURE — CALÇADO EM OFERTA", "emojis": "🥾🔥", "detail": "Uma opção para atividades ao ar livre e terrenos mais exigentes."}
    if any(x in n for x in ["skate"]):
        return {"headline": f"{shoe_brand}SKATE EM OFERTA — TÊNIS PARA FICAR DE OLHO", "emojis": "🛹🔥", "detail": "Uma opção casual e esportiva para quem anda de skate."}
    if any(x in n for x in ["chinelo", "slide", "sandália"]):
        return {"headline": f"{shoe_brand}CHINELO/SLIDE EM OFERTA — PREÇO PARA APROVEITAR", "emojis": "🏖️🔥", "detail": "Conforto para o dia a dia com uma condição promocional."}
    if any(x in n for x in ["infantil", "kids"]):
        return {"headline": f"{shoe_brand}TÊNIS INFANTIL EM DESTAQUE — OLHA O PREÇO", "emojis": "👟🧒", "detail": "Uma opção para os pequenos aproveitando uma condição promocional."}
    # "masculino/feminino/men/women" sozinhos NÃO identificam calçado.
    # Só usamos gênero depois de confirmar que o título realmente contém
    # algum marcador de tênis/calçado.
    shoe_markers = (
        "tenis", "tênis", "sapatenis", "sapatênis", "calcado", "calçado",
        "sneaker", "sneakers", "chuteira", "chinelo", "slide", "sandalia",
        "sandália", "running shoe", "running shoes",
        "air max", "air force", "air jordan", "jordan", "dunk low", "dunk",
        "ultraboost", "superstar", "adizero", "pegasus", "vomero",
        "novablast", "gel kayano", "gel nimbus", "gel cumulus", "fresh foam",
        "1080", "574", "990", "clifton", "bondi", "corre",
    )
    has_shoe_marker = any(x in n for x in shoe_markers)
    if has_shoe_marker and any(x in n for x in ["feminino", "feminina", "women"]):
        return {"headline": f"{shoe_brand}TÊNIS FEMININO EM OFERTA — PREÇO PARA CONFERIR", "emojis": "👟✨", "detail": "Uma opção para completar o visual ou a rotina de treino."}
    if has_shoe_marker and any(x in n for x in ["masculino", "masculina", "men"]):
        return {"headline": f"{shoe_brand}TÊNIS MASCULINO EM DESTAQUE — OLHA ESSA OFERTA", "emojis": "👟🔥", "detail": "Uma opção versátil para o dia a dia ou treino."}
    if has_shoe_marker:
        return {"headline": f"{shoe_brand}TÊNIS EM DESTAQUE — PREÇO PARA FICAR DE OLHO", "emojis": "👟🔥", "detail": "Uma opção para uso casual ou rotina, dependendo do modelo."}

    # ============================================================
    # FALLBACK — ainda é específico o suficiente para qualquer item.
    # ============================================================
    if brand:
        return {"headline": f"{brand.upper()} EM DESTAQUE — OLHA ESSA OFERTA", "emojis": "🔥👀", "detail": "Produto de marca em condição promocional para ficar no radar."}

    words = [w for w in re.split(r"\s+", t) if len(w) > 2]
    short_name = " ".join(words[:4]) if words else "produto"
    return {"headline": f"{short_name.upper()} EM OFERTA — OLHA ESSA CONDIÇÃO", "emojis": "🔥👀", "detail": "Oferta encontrada pelo Caçador de Ofertas; confira preço, condições e disponibilidade."}

def ad_text(o, affiliate=""):
    """Monta o anúncio no estilo visual solicitado para o WhatsApp."""
    title = str(o.get("title") or "Produto").strip()
    marketing = _marketing_phrase(title, o)

    # Formato aprovado pelo usuário: headline comercial específico + emojis,
    # depois o produto. Não exibe uma segunda descrição em itálico.
    lines = [
        f"*{marketing['headline']}*",
        marketing["emojis"],
        "",
        f"*{title}*",
        "",
    ]

    if o.get("original_price"):
        # Tachado real do WhatsApp.
        lines.append(f"~De {brl(o['original_price'])}~")

    if o.get("cupom"):
        c = o["cupom"] or {}
        label = c.get("code") or c.get("label") or "Cupom disponível"
        lines.append(f"🎟️ Cupom: *{label}*")

    lines.append(f"Por *{brl(o['price'])}*")

    link = str(affiliate or "").strip()
    if not valid_affiliate_link(link):
        raise ValueError("Informe um link de afiliado válido do Mercado Livre antes de gerar o anúncio.")

    # A chama fica DEPOIS de PROMOÇÃO.
    lines += ["", "*PEGAR PROMOÇÃO 🔥:* " + link]

    return "\n".join(lines)

# ============================================================
# IMAGEM NATURAL PARA WHATSAPP
# ============================================================

def _image_mime_from_response(response):
    content_type = (response.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
    if content_type in {"image/jpeg", "image/png", "image/webp"}:
        return content_type
    return "image/jpeg"


def _cleanup_whatsapp_images(max_age_seconds=86400):
    try:
        now = time.time()
        for name in os.listdir(WHATSAPP_IMAGE_DIR):
            path = os.path.join(WHATSAPP_IMAGE_DIR, name)
            try:
                if os.path.isfile(path) and now - os.path.getmtime(path) > max_age_seconds:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


def _save_generated_whatsapp_image(image_bytes, extension="png"):
    extension = extension if extension in {"png", "jpg", "jpeg", "webp"} else "png"
    filename = f"{uuid.uuid4().hex}.{extension}"
    path = os.path.join(WHATSAPP_IMAGE_DIR, filename)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return f"{PUBLIC_BASE_URL}/whatsapp/image/{filename}"


def _try_higher_resolution_ml_image(source):
    """Tenta trocar uma miniatura do Mercado Livre pela variante original."""
    candidates = [source]
    replacements = [
        ("-I.jpg", "-O.jpg"), ("-I.png", "-O.png"), ("-I.webp", "-O.webp"),
        ("-I.jpeg", "-O.jpeg"), ("-F.jpg", "-O.jpg"), ("-F.png", "-O.png"),
        ("-F.webp", "-O.webp"), ("-F.jpeg", "-O.jpeg"),
        ("-V.jpg", "-O.jpg"), ("-V.webp", "-O.webp"),
    ]
    for old, new in replacements:
        if old in source:
            candidates.insert(0, source.replace(old, new))
    # Algumas URLs usam parâmetros de thumbnail; remover apenas parâmetros
    # conhecidos de tamanho para tentar obter o arquivo maior.
    candidates.append(re.sub(r"([?&](?:width|height|w|h)=)\d+", "", source))

    seen = set()
    for url in candidates:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*"},
                stream=True,
                timeout=15,
                allow_redirects=True,
            )
            ctype = (r.headers.get("content-type") or "").lower()
            if r.status_code == 200 and ctype.startswith("image/"):
                data = r.content
                r.close()
                if data:
                    return url, data
            r.close()
        except Exception:
            pass
    return source, None


def gerar_imagem_natural_whatsapp(image_url, offer_text=""):
    """Prepara a imagem para o WhatsApp no estilo visual aprovado pelo usuário.

    V26:
      - cria uma área quadrada branca 1080x1080;
      - preserva 100% da foto original, sem crop/deformação;
      - deixa margem confortável ao redor do produto;
      - tenta obter a versão original/maior da imagem do Mercado Livre;
      - aplica melhoria leve de nitidez e contraste;
      - mantém a foto limpa, sem texto, sem moldura colorida e sem arte.

    O resultado fica semelhante ao exemplo enviado: produto centralizado,
    inteiro e com respiro nas bordas, em vez de ocupar a tela inteira.
    """
    source = str(image_url or "").strip()
    if not source:
        return ""

    if Image is None:
        print("[IMAGEM ESTILO WHATSAPP] Pillow não instalado; usando original.")
        return source

    cache_key = "v34-whatsapp-smart-fit:" + source
    cached = _WHATSAPP_IMAGE_ENHANCE_CACHE.get(cache_key)
    if cached:
        return cached

    try:
        used_source, raw = _try_higher_resolution_ml_image(source)
        if not raw:
            response = requests.get(
                source,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "image/avif,image/webp,image/jpeg,image/png,*/*"},
                timeout=20,
                allow_redirects=True,
            )
            response.raise_for_status()
            raw = response.content
            used_source = source

        if not raw:
            return source

        from io import BytesIO
        with Image.open(BytesIO(raw)) as original:
            img = ImageOps.exif_transpose(original)
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.getchannel("A")
                bg.paste(img.convert("RGB"), mask=alpha)
                img = bg
            else:
                img = img.convert("RGB")

            original_width, original_height = img.size

            # Qualidade final suficiente para o WhatsApp sem criar arquivos
            # gigantes. Primeiro garantimos uma resolução boa da foto.
            MAX_SOURCE = 2200
            MIN_SOURCE = 1400
            longest = max(img.width, img.height)
            if longest < MIN_SOURCE:
                scale = MIN_SOURCE / max(1, longest)
                img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
            elif longest > MAX_SOURCE:
                scale = MAX_SOURCE / longest
                img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)

            img = ImageEnhance.Contrast(img).enhance(1.035)
            img = ImageEnhance.Color(img).enhance(1.02)
            img = ImageEnhance.Sharpness(img).enhance(1.10)
            img = img.filter(ImageFilter.UnsharpMask(radius=0.7, percent=70, threshold=2))

            # V34: encaixe inteligente. O problema anterior era a margem de 90px
            # somada ao fundo inteiro da foto, fazendo o produto parecer pequeno.
            # Em fotos com fundo branco, removemos apenas o excesso de borda branca
            # antes de encaixar; em fotos reais/lifestyle não fazemos crop agressivo.
            try:
                from PIL import ImageChops
                bg = Image.new("RGB", img.size, img.getpixel((0, 0)))
                diff = ImageChops.difference(img, bg)
                diff = ImageChops.autocontrast(diff)
                bbox = diff.getbbox()
                corner_samples = [
                    img.getpixel((0, 0)),
                    img.getpixel((img.width - 1, 0)),
                    img.getpixel((0, img.height - 1)),
                    img.getpixel((img.width - 1, img.height - 1)),
                ]
                near_white = sum(1 for px in corner_samples if min(px) >= 235 and max(px) >= 245) >= 3
                if near_white and bbox:
                    left, top, right, bottom = bbox
                    pad = max(18, int(min(img.width, img.height) * 0.025))
                    left = max(0, left - pad)
                    top = max(0, top - pad)
                    right = min(img.width, right + pad)
                    bottom = min(img.height, bottom + pad)
                    if right - left >= img.width * 0.45 and bottom - top >= img.height * 0.45:
                        img = img.crop((left, top, right, bottom))
                        print("[IMAGEM V34] excesso de fundo branco removido:", (left, top, right, bottom))
            except Exception:
                pass

            CANVAS = 1080
            MARGIN = 28
            max_w = CANVAS - (MARGIN * 2)
            max_h = CANVAS - (MARGIN * 2)
            scale = min(max_w / img.width, max_h / img.height)
            fit = img.resize(
                (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                Image.Resampling.LANCZOS,
            )

            canvas = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
            x = (CANVAS - fit.width) // 2
            y = (CANVAS - fit.height) // 2
            canvas.paste(fit, (x, y))

            output = BytesIO()
            canvas.save(
                output,
                format="JPEG",
                quality=97,
                optimize=True,
                progressive=True,
                subsampling=0,
            )
            prepared_url = _save_generated_whatsapp_image(output.getvalue(), extension="jpg")

        _WHATSAPP_IMAGE_ENHANCE_CACHE[cache_key] = prepared_url
        print(
            "[IMAGEM ESTILO WHATSAPP] OK:",
            f"{original_width}x{original_height} -> {CANVAS}x{CANVAS}",
            "encaixe inteligente, margem 28px, fonte:", used_source[:120],
        )
        return prepared_url

    except Exception as exc:
        print("[IMAGEM ESTILO WHATSAPP] falhou; usando original:", repr(exc))
        return source


def whatsapp_image(filename):
    """Entrega temporariamente as imagens geradas para o WhatsApp Bot."""
    if not re.fullmatch(r"[a-f0-9]{32}\.(?:png|jpg|jpeg|webp)", filename or "", re.I):
        return "Imagem inválida.", 400

    path = os.path.join(WHATSAPP_IMAGE_DIR, filename)
    if not os.path.isfile(path):
        return "Imagem não encontrada.", 404

    from flask import send_file
    return send_file(path, max_age=3600)


# ============================================================
# PUBLICAÇÃO AUTOMÁTICA NO WHATSAPP
# ============================================================

AUTO_WHATSAPP_ENABLED = os.getenv("AUTO_WHATSAPP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
AUTO_WHATSAPP_INTERVAL = max(60, int(os.getenv("AUTO_WHATSAPP_INTERVAL", "900")))  # padrão: 15 minutos
AUTO_WHATSAPP_LIMIT = 3  # no máximo 3 ofertas por rodada
AUTO_WHATSAPP_ALWAYS_ON = True  # sem horário de início ou parada
AUTO_WHATSAPP_TZ = os.getenv("AUTO_WHATSAPP_TZ", "America/Sao_Paulo").strip() or "America/Sao_Paulo"
AUTO_WHATSAPP_LOCK = threading.Lock()
AUTO_WHATSAPP_THREAD = None

def _auto_whatsapp_horario_atual():
    """Compatibilidade com chamadas antigas: a automação fica liberada 24/7."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(AUTO_WHATSAPP_TZ))
    except Exception:
        now = datetime.now()
    return True, now, 0, 24 * 60


def _whatsapp_send_text(text, image_url=""):
    """Envia texto ou foto com legenda ao grupo selecionado pelo WhatsApp Bot."""
    if not WHATSAPP_BOT_URL:
        return False, "WHATSAPP_BOT_URL não configurada."
    if not WHATSAPP_BOT_KEY:
        return False, "WHATSAPP_BOT_KEY não configurada."
    prepared_image = gerar_imagem_natural_whatsapp(
        image_url,
        text,
    )

    try:
        response = requests.post(
            f"{WHATSAPP_BOT_URL}/api/send-offer",
            headers={
                "Content-Type": "application/json",
                "x-bot-key": WHATSAPP_BOT_KEY,
            },
            json={"text": text, "image": prepared_image},
            timeout=150,
        )
    except requests.RequestException as exc:
        return False, f"Falha de comunicação: {exc}"

    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "error": response.text[:500] or "Resposta inválida."}

    if response.ok and payload.get("ok"):
        return True, payload.get("message") or "Enviado."
    return False, payload.get("error") or payload.get("erro") or f"HTTP {response.status_code}"


def _whatsapp_previous_price(product_id):
    """Retorna o último preço publicado para o anúncio, se já existir."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT last_price FROM whatsapp_publicacoes WHERE product_id=?",
            (str(product_id),),
        ).fetchone()
        return float(row["last_price"]) if row and row["last_price"] is not None else None
    except (TypeError, ValueError, sqlite3.Error):
        return None
    finally:
        conn.close()


def _whatsapp_should_publish(product_id, price):
    """Novo produto publica; repetição só é permitida quando o preço caiu."""
    old_price = _whatsapp_previous_price(product_id)
    if old_price is None:
        return True
    try:
        new_price = float(price)
    except (TypeError, ValueError):
        return False
    return new_price < old_price - 0.01


def _whatsapp_mark_published(offer):
    conn = get_db()
    conn.execute("""
        INSERT INTO whatsapp_publicacoes
            (product_id, last_price, last_permalink, last_title, published_count, last_published_at)
        VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        ON CONFLICT(product_id) DO UPDATE SET
            last_price=excluded.last_price,
            last_permalink=excluded.last_permalink,
            last_title=excluded.last_title,
            published_count=whatsapp_publicacoes.published_count + 1,
            last_published_at=CURRENT_TIMESTAMP
    """, (
        str(offer.get("product_id") or ""),
        float(offer.get("price") or 0),
        offer.get("permalink") or "",
        offer.get("title") or "Produto",
    ))
    conn.commit()
    conn.close()


def _whatsapp_publish_scan(result):
    """Publica no máximo AUTO_WHATSAPP_LIMIT ofertas elegíveis desta rodada.

    Se o WhatsApp falhar, a rodada é interrompida imediatamente e os produtos
    que ainda não foram enviados permanecem disponíveis para a próxima rodada.
    """
    offers = list((result or {}).get("ofertas") or [])
    # Rodízio entre categorias: evita que as 3 vagas da rodada sejam ocupadas
    # por produtos do mesmo nicho quando há ofertas de outras categorias.
    by_category = {}
    for offer in offers:
        category = str(offer.get("category_name") or "Outros")
        by_category.setdefault(category, []).append(offer)
    diversified = []
    category_order = list(by_category)
    random.shuffle(category_order)
    while category_order:
        remaining_categories = []
        for category in category_order:
            bucket = by_category.get(category) or []
            if bucket:
                diversified.append(bucket.pop(0))
            if bucket:
                remaining_categories.append(category)
        category_order = remaining_categories
    offers = diversified
    sent = 0
    skipped = 0

    for offer in offers:
        if sent >= AUTO_WHATSAPP_LIMIT:
            break

        product_id = str(offer.get("product_id") or "").strip()
        if not product_id:
            continue

        price = offer.get("price")
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        previous_price = _whatsapp_previous_price(product_id)
        if previous_price is not None and price >= previous_price - 0.01:
            # Não repete a mesma oferta no mesmo preço nem se o preço subir.
            skipped += 1
            continue
        if previous_price is not None:
            # O gerador de anúncio usa estes campos para avisar que voltou
            # mais barato e mostrar a comparação com o preço anteriormente enviado.
            offer["_price_drop_from"] = previous_price
            offer["_price_drop_amount"] = round(previous_price - price, 2)

        # ========================================================
        # LINK AFILIADO AUTOMÁTICO
        # ========================================================
        # A rotina manual já gera o meli.la pelo servidor. A automação
        # precisa executar exatamente a mesma etapa antes de montar o
        # anúncio; nunca usamos o permalink normal como link de afiliado.
        affiliate_link = str(offer.get("affiliate_link") or "").strip()

        if not valid_affiliate_link(affiliate_link):
            product_url = str(offer.get("permalink") or "").strip()

            # Se por algum motivo a oferta vier sem permalink, monta a
            # publicação real a partir do item_id MLBxxxxxxxx.
            if not product_url:
                item_id = str(offer.get("item_id") or product_id).strip().upper()
                m = re.fullmatch(r"MLB(\d+)", item_id)
                if m:
                    product_url = f"https://produto.mercadolivre.com.br/MLB-{m.group(1)}"

            if not product_url:
                skipped += 1
                print(f"[AUTO WHATSAPP] {product_id}: sem URL de publicação válida.")
                continue

            try:
                affiliate_link = _affiliate_csrf_and_link(product_url)
                offer["affiliate_link"] = affiliate_link
                print(f"[AUTO AFILIADO] {product_id}: {affiliate_link}")
            except Exception as exc:
                # Não marca a oferta como publicada. Ela ficará disponível
                # para uma próxima rodada, caso a sessão/cookie seja
                # renovada ou o Mercado Livre volte a responder.
                print(f"[AUTO AFILIADO] Falha em {product_id}: {exc}")
                skipped += 1
                continue

        # Gera o mesmo anúncio usado pelo fluxo manual, agora com o
        # meli.la recém-criado, e só então envia ao WhatsApp.
        try:
            text = ad_text(offer, affiliate_link)
        except Exception as exc:
            print(f"[AUTO WHATSAPP] Falha ao gerar anúncio de {product_id}: {exc}")
            skipped += 1
            continue

        # Não existe bloqueio por horário: publica sempre que houver oferta elegível.
        ok, detail = _whatsapp_send_text(text, offer.get("image") or "")
        if not ok:
            print("[AUTO WHATSAPP] Envio interrompido:", detail)
            return {"ok": False, "enviadas": sent, "ignoradas": skipped, "erro": detail}

        _whatsapp_mark_published(offer)
        sent += 1
        print(f"[AUTO WHATSAPP] Oferta {product_id} enviada ({sent}/{AUTO_WHATSAPP_LIMIT}).")

    return {"ok": True, "enviadas": sent, "ignoradas": skipped, "erro": None}


def executar_caca_automatica():
    """Executa uma rodada completa sem restrição de horário (24 horas por dia)."""
    if not AUTO_WHATSAPP_ENABLED:
        return {"ok": True, "desativado": True, "enviadas": 0}
    active, now, start_min, end_min = _auto_whatsapp_horario_atual()

    if not AUTO_WHATSAPP_LOCK.acquire(blocking=False):
        print("[AUTO WHATSAPP] Já existe uma rodada em andamento; ignorando esta execução.")
        return {"ok": True, "ocupado": True, "enviadas": 0}

    try:
        print(
            f"[AUTO WHATSAPP] Iniciando nova caça automática às {now:%H:%M:%S} "
            f"({AUTO_WHATSAPP_TZ}); categorias={len(CATALOG)}."
        )
        result = scan_queries(list(CATALOG.keys()), apply_coupons=True)
        offers = list((result or {}).get("ofertas") or [])
        if not offers:
            print(
                "[AUTO WHATSAPP] ALERTA: busca terminou com ZERO ofertas válidas; "
                "nenhuma mensagem será enviada. Verifique os logs [BUSCA PUBLICA REAL], "
                "[BUSCA ITEMS API], [V61 API ITEMS] e os retornos de product_items."
            )
        publish = _whatsapp_publish_scan(result)
        print(
            f"[AUTO WHATSAPP] Rodada finalizada: "
            f"ofertas_validas={len(offers)}, "
            f"enviadas={publish.get('enviadas', 0)}, "
            f"erro={publish.get('erro') or 'nenhum'}"
        )
        return publish
    except Exception as exc:
        print("[AUTO WHATSAPP] Erro na rodada:", repr(exc))
        return {"ok": False, "enviadas": 0, "erro": str(exc)}
    finally:
        AUTO_WHATSAPP_LOCK.release()


def iniciar_automacao_whatsapp():
    """Inicia uma única thread de publicação automática por processo Gunicorn."""
    global AUTO_WHATSAPP_THREAD
    if not AUTO_WHATSAPP_ENABLED or AUTO_WHATSAPP_THREAD is not None:
        return

    def worker():
        print(
            f"[AUTO WHATSAPP] Automação contínua ativa 24/7; "
            f"intervalo-alvo={AUTO_WHATSAPP_INTERVAL}s ({AUTO_WHATSAPP_INTERVAL // 60} min), "
            f"até {AUTO_WHATSAPP_LIMIT} ofertas por rodada; "
            f"fuso de referência={AUTO_WHATSAPP_TZ}. Sem horário de início/parada."
        )
        while True:
            rodada_inicio = time.monotonic()
            try:
                resultado = executar_caca_automatica() or {}
                duracao = max(0.0, time.monotonic() - rodada_inicio)
                # O intervalo é medido entre INÍCIOS das rodadas, não após
                # terminar a busca. Assim uma rodada de 7 minutos não vira
                # um ciclo de 22 minutos. Rodadas longas não se sobrepõem.
                espera = max(0.0, AUTO_WHATSAPP_INTERVAL - duracao)
                print(
                    f"[AUTO WHATSAPP] Ciclo: duração={duracao:.1f}s; "
                    f"próxima rodada em {espera:.1f}s; "
                    f"enviadas nesta rodada={resultado.get('enviadas', 0)}."
                )
                if espera > 0:
                    time.sleep(espera)
                else:
                    print(
                        "[AUTO WHATSAPP] A rodada demorou mais que o intervalo; "
                        "a próxima começa agora, sem sobrepor a anterior."
                    )
            except Exception as exc:
                print("[AUTO WHATSAPP] Erro no agendador:", repr(exc))
                time.sleep(30)

    AUTO_WHATSAPP_THREAD = threading.Thread(
        target=worker,
        name="whatsapp-auto-publisher",
        daemon=True,
    )
    AUTO_WHATSAPP_THREAD.start()


@app.route("/api/whatsapp/automacao")
def api_whatsapp_automacao():
    conn = get_db()
    rows = conn.execute("""
        SELECT product_id, last_price, last_title, published_count, last_published_at
        FROM whatsapp_publicacoes
        ORDER BY last_published_at DESC
        LIMIT 100
    """).fetchall()
    conn.close()
    return jsonify({
        "ok": True,
        "ativo": AUTO_WHATSAPP_ENABLED,
        "intervalo_segundos": AUTO_WHATSAPP_INTERVAL,
        "limite_por_rodada": AUTO_WHATSAPP_LIMIT,
        "sempre_ligado": True,
        "restricao_horario": False,
        "fuso_horario_referencia": AUTO_WHATSAPP_TZ,
        "hora_local_agora": _auto_whatsapp_horario_atual()[1].isoformat(),
        "publicadas": [dict(row) for row in rows],
    })


# A thread começa depois que o módulo terminou de carregar as rotas e o banco.
iniciar_automacao_whatsapp()

# ============================================================
# JOBS DE CAÇA EM SEGUNDO PLANO
# ============================================================

JOBS = {}
JOBS_LOCK = threading.Lock()

def create_job():
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "queued",
            "progress": 0,
            "message": "Aguardando início...",
            "result": None,
            "error": None,
        }
    return job_id

def update_job(job_id, **kwargs):
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(kwargs)

def get_job(job_id):
    with JOBS_LOCK:
        return dict(JOBS.get(job_id, {
            "status": "not_found",
            "progress": 0,
            "message": "Caça não encontrada.",
            "result": None,
            "error": "not_found",
        }))

def run_caca_job(job_id, category=None):
    try:
        update_job(job_id, status="running", progress=5, message="🔎 Iniciando busca completa dos 20 mais vendidos...")

        if category:
            # Uma categoria específica: busca todas as sementes dela.
            queries = [category]
        else:
            # TODAS as categorias selecionadas no catálogo. O scanner resolve
            # cada uma separadamente; não usar somente a primeira semente.
            queries = list(CATALOG.keys())

        update_job(job_id, progress=12, message=f"🛒 Consultando Mercado Livre ({len(queries)} buscas)...")
        update_job(job_id, progress=55, message="📦 Carregando os 20 mais vendidos de cada categoria...")
        result = scan_queries(queries, apply_coupons=True)
        update_job(job_id, progress=96, message="📊 Finalizando ranking...")
        update_job(job_id, status="done", progress=100, message=f"✅ Produtos atualizados: {result.get('stats', {}).get('ofertas', 0)} ofertas. Cupons verificados e associados aos produtos quando houver correspondência pública.", result=json_safe(result))
    except Exception as e:
        print("[ERRO JOB CAÇA]", repr(e))
        update_job(job_id, status="error", progress=100, message="❌ Erro durante a atualização.", error=str(e))



def run_manual_search_job(job_id, q):
    """Executa busca manual em segundo plano e devolve erro explícito à interface."""
    try:
        update_job(job_id, status="running", progress=5, message="🔎 Preparando busca manual...")
        category, category_queries = _manual_queries_for_category(q)
        queries = category_queries if category and category_queries else [q]
        update_job(
            job_id, progress=15,
            message=f"🛒 Consultando {category or q} ({len(queries)} termos)..."
        )
        result = scan_queries(queries, apply_coupons=True)
        result.setdefault("stats", {})["busca_manual"] = category or q
        result["stats"]["nichos_pesquisados"] = len(queries)
        update_job(job_id, progress=96, message="📊 Organizando ofertas...")
        offers_count = result.get("stats", {}).get("ofertas", 0)
        update_job(
            job_id, status="done", progress=100,
            message=f"✅ Busca concluída: {offers_count} ofertas.",
            result=json_safe(result)
        )
    except Exception as exc:
        print("[ERRO BUSCA MANUAL]", repr(exc))
        update_job(
            job_id, status="error", progress=100,
            message="❌ A busca manual falhou.",
            error=str(exc)
        )

# ============================================================
# RESOLUÇÃO DA BUSCA MANUAL POR CATEGORIA
# ============================================================

def _manual_queries_for_category(q):
    """Resolve a busca manual para a categoria inteira.

    A busca manual não pode tratar variações como "Tênis e calçados" como
    uma consulta literal. Elas precisam apontar para "👟 Tênis & Calçados"
    e então disparar TODOS os micro-nichos da categoria.
    """
    nq = norm(q)

    # Correspondência exata pelo nome cadastrado.
    for category, seeds in CATALOG.items():
        if nq == norm(category):
            return category, list(seeds)

    # Também aceita o nome sem emoji.
    for category, seeds in CATALOG.items():
        plain = re.sub(r"^[^A-Za-zÀ-ÿ0-9]+", "", category).strip()
        if nq == norm(plain):
            return category, list(seeds)

    # ALIASES IMPORTANTES: o usuário normalmente digita a categoria de
    # forma natural, sem copiar exatamente o texto do botão.
    aliases = {
        "tecnologia": "📱 Tecnologia",
        "celulares": "📱 Tecnologia",
        "celular": "📱 Tecnologia",
        "casa": "🏠 Casa e Organização",
        "casa e organizacao": "🏠 Casa e Organização",
        "casa e cozinha": "🏠 Casa e Organização",
        "organizacao": "🏠 Casa e Organização",
        "academia": "💪 Academia & Fitness",
        "academia e fitness": "💪 Academia & Fitness",
        "fitness": "💪 Academia & Fitness",
        "saude e beleza": "💇 Saúde & Beleza",
        "saude beleza": "💇 Saúde & Beleza",
        "beleza": "💇 Saúde & Beleza",
        "moda": "👕 Moda",
        "roupas": "👕 Moda",
        "roupa": "👕 Moda",
        "tenis": "👟 Tênis & Calçados",
        "tenis e calcados": "👟 Tênis & Calçados",
        "tenis e calcados": "👟 Tênis & Calçados",
        "calcados": "👟 Tênis & Calçados",
        "calcado": "👟 Tênis & Calçados",
        "sapatos": "👟 Tênis & Calçados",
        "perfumes": "🌸 Perfumes",
        "perfume": "🌸 Perfumes",
        "perfumes arabes": "🌙 Perfumes Árabes",
        "perfumes arabes": "🌙 Perfumes Árabes",
        "perfume arabe": "🌙 Perfumes Árabes",
    }
    category = aliases.get(nq)
    if category in CATALOG:
        return category, list(CATALOG[category])

    return None, None


# ============================================================
# API
# ============================================================

@app.route("/api/buscar")
def api_buscar():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"erro": "Informe uma busca."}), 400

    # Quando a busca manual recebe uma categoria, NÃO pesquisa a palavra
    # "Eletrônicos"/"Casa e Cozinha"/etc. como uma consulta genérica.
    # Pesquisa cada nicho cadastrado naquela categoria separadamente.
    category, category_queries = _manual_queries_for_category(q)

    if category and category_queries:
        print(f"[BUSCA MANUAL CATEGORIA] {category}: {len(category_queries)} nichos")
        # Categoria manual = TODOS os micro-nichos, nunca apenas a frase
        # digitada pelo usuário. Isso é o que permite encontrar dezenas de
        # modelos diferentes em Tênis, Moda, Tecnologia etc.
        queries = category_queries
    else:
        # Busca livre continua funcionando normalmente.
        queries = [q]

    resultado = scan_queries(
        queries,
        request.args.get("desconto", 0),
        apply_coupons=True
    )

    resultado["stats"]["busca_manual"] = category or q
    resultado["stats"]["nichos_pesquisados"] = len(queries)

    return jsonify(json_safe(resultado))

@app.route("/api/buscar/job")
def api_buscar_job():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"erro": "Informe uma busca."}), 400
    job_id = create_job()
    thread = threading.Thread(
        target=run_manual_search_job, args=(job_id, q),
        name="manual-search-" + job_id[:8], daemon=True
    )
    thread.start()
    return jsonify({"ok": True, "job_id": job_id, "status": "queued"})


@app.route("/api/cacar")
def api_cacar():
    categoria=request.args.get("categoria","").strip() or None
    job_id=create_job()
    thread=threading.Thread(target=run_caca_job, args=(job_id, categoria), daemon=True)
    thread.start()
    return jsonify({"ok":True,"job_id":job_id,"status":"queued"})

@app.route("/api/cacar/status/<job_id>")
def api_cacar_status(job_id):
    return jsonify(json_safe(get_job(job_id)))

@app.route("/api/cupons")
def api_cupons():
    sync = None
    if request.args.get("atualizar")=="1":
        sync = sync_coupons()
    return jsonify({"cupons":json_safe(coupons()),"fontes":COUPON_SOURCE_URLS,"sincronizacao":json_safe(sync)})

@app.route("/api/preco-atual")
def api_preco_atual():
    """Revalida o preço da publicação imediatamente antes do anúncio.

    A busca pode ter sido carregada minutos antes e o Mercado Livre pode
    alterar o preço nesse intervalo. Por isso esta rota consulta o ITEM real
    novamente e usa o valor atual, sem alterar ranking, categorias ou cupons.
    """
    item_id = str(request.args.get("item_id") or "").strip()
    affiliate_link = str(request.args.get("affiliate_link") or "").strip()

    # Se o usuário colou um link meli.la, resolve o redirecionamento para
    # descobrir o ITEM exato que o link de afiliado aponta. Isso é essencial
    # quando o produto do catálogo possui vários vendedores/publicações.
    resolved_item_id = _extract_item_id_from_affiliate_url(affiliate_link) if affiliate_link else None
    if resolved_item_id:
        item_id = resolved_item_id

    if not item_id:
        return jsonify({"ok": False, "erro": "Não foi possível identificar o ITEM desta oferta."}), 400

    data, status, _ = ml_get(f"/items/{item_id}")
    if status != 200 or not isinstance(data, dict):
        return jsonify({"ok": False, "erro": f"Não foi possível atualizar o preço do item ({status})."}), 502

    try:
        price = float(data.get("price")) if data.get("price") is not None else None
    except Exception:
        price = None
    try:
        original = float(data.get("original_price")) if data.get("original_price") is not None else None
    except Exception:
        original = None

    # Quando a publicação informa preço de venda separado, prioriza o valor
    # retornado pela própria publicação e usa sale_price apenas como fallback.
    if price is None or price <= 0:
        sale, sale_original = get_current_sale_price(item_id)
        if sale is not None:
            price = sale
            if sale_original is not None:
                original = sale_original

    if price is None or price <= 0 or price > 100000:
        return jsonify({"ok": False, "erro": "O Mercado Livre não retornou um preço válido para este item."}), 502

    shipping = data.get("shipping") or {}
    free = bool(shipping.get("free_shipping"))
    try:
        shipping_cost = 0.0 if free else (float(shipping.get("cost")) if shipping.get("cost") is not None else None)
    except Exception:
        shipping_cost = None

    pictures = data.get("pictures") or []
    image = None
    if isinstance(pictures, list):
        for picture in pictures:
            if isinstance(picture, dict):
                image = picture.get("secure_url") or picture.get("url") or image
                if image:
                    break

    return jsonify({
        "ok": True,
        "item_id": item_id,
        "title": data.get("title"),
        "price": round(price, 2),
        "original_price": round(original, 2) if original is not None and original > 0 else None,
        "free_shipping": free,
        "shipping_cost": shipping_cost,
        "permalink": data.get("permalink"),
        "image": image or data.get("thumbnail"),
    })


@app.route("/api/gerar-anuncio")
def api_anuncio():
    o = {
        "title":request.args.get("title","Produto"),
        "price":request.args.get("price",0),
        "original_price":request.args.get("original_price"),
        "discount":float(request.args.get("discount",0) or 0),
        "free_shipping":request.args.get("shipping_free")=="1",
        "cupom":None,"preco_com_cupom":None
    }
    code=request.args.get("cupom","").strip()
    if code:
        # Cupons de cards públicos podem ser "Cupom R$ 15 OFF" ou "Cupom 10% OFF"
        # e não possuem necessariamente um código digitável. Aceita ambos.
        public = detect_public_coupon(code)
        if public:
            cup = dict(public)
            d = calculate_public_coupon(cup, float(o["price"]))
            cup["desconto_estimado"] = d
            cup["label"] = cup.get("label") or code
            o["cupom"] = cup
            o["desconto_cupom"] = d
            o["preco_com_cupom"] = max(0, float(o["price"]) - d)
        else:
            c=get_db()
            row=c.execute("SELECT * FROM cupons WHERE code=? AND active=1",(code.upper(),)).fetchone()
            c.close()
            if row:
                cup=dict(row); cup["desconto_estimado"]=coupon_discount(cup,float(o["price"]))
                o["cupom"]=cup
                o["desconto_cupom"]=cup["desconto_estimado"]
                o["preco_com_cupom"]=max(0,float(o["price"])-cup["desconto_estimado"])
    affiliate_link = request.args.get("affiliate_link", "").strip()
    if not valid_affiliate_link(affiliate_link):
        return jsonify({
            "ok": False,
            "erro": "Informe o link de afiliado desta oferta antes de gerar o anúncio.",
            "affiliate_required": True,
        }), 400
    try:
        anuncio = ad_text(o, affiliate_link)
    except ValueError as exc:
        return jsonify({"ok": False, "erro": str(exc)}), 400
    return jsonify({"ok": True, "anuncio": anuncio, "affiliate_link": affiliate_link})


@app.route("/api/enviar-whatsapp", methods=["POST"])
def api_enviar_whatsapp():
    """Envia para o grupo selecionado no WhatsApp Bot."""
    if not WHATSAPP_BOT_URL:
        return jsonify({
            "ok": False,
            "erro": "WHATSAPP_BOT_URL não configurada no Railway."
        }), 500

    if not WHATSAPP_BOT_KEY:
        return jsonify({
            "ok": False,
            "erro": "WHATSAPP_BOT_KEY não configurada no Railway."
        }), 500

    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    image = str(data.get("image") or "").strip()
    affiliate_link = str(data.get("affiliate_link") or "").strip()

    if not valid_affiliate_link(affiliate_link):
        return jsonify({"ok": False, "erro": "Envio bloqueado: esta oferta não possui um link de afiliado válido."}), 400
    if affiliate_link not in text:
        return jsonify({"ok": False, "erro": "Envio bloqueado: o anúncio não contém o mesmo link de afiliado informado."}), 400

    if not text:
        return jsonify({
            "ok": False,
            "erro": "O anúncio está vazio."
        }), 400

    prepared_image = gerar_imagem_natural_whatsapp(
        image,
        text,
    )

    try:
        response = requests.post(
            f"{WHATSAPP_BOT_URL}/api/send-offer",
            headers={
                "Content-Type": "application/json",
                "x-bot-key": WHATSAPP_BOT_KEY,
            },
            json={"text": text, "image": prepared_image},
            timeout=150,
        )
    except requests.RequestException as exc:
        print("[WHATSAPP] Falha de comunicação:", repr(exc))
        return jsonify({
            "ok": False,
            "erro": "Não foi possível conectar ao WhatsApp Bot.",
            "detalhes": str(exc),
        }), 502

    try:
        payload = response.json()
    except ValueError:
        payload = {
            "ok": False,
            "erro": response.text[:1000] or "Resposta inválida do WhatsApp Bot.",
        }

    if response.ok and payload.get("ok"):
        print("[WHATSAPP] Oferta enviada com sucesso.")
        return jsonify({
            "ok": True,
            "mensagem": payload.get("message") or "Oferta enviada para o WhatsApp.",
        })

    print(
        "[WHATSAPP] Bot recusou envio:",
        response.status_code,
        payload,
    )
    return jsonify({
        "ok": False,
        "erro": payload.get("error") or payload.get("erro") or "O WhatsApp Bot recusou o envio.",
        "status_http": response.status_code,
    }), 502

# ============================================================
# TESTES / DIAGNÓSTICO
# ============================================================

@app.route("/mercadolivre/teste-produto-itens")
def teste_items():
    pid=request.args.get("product_id","MLB58793248")
    data,status,_=ml_get(f"/products/{pid}/items")
    return jsonify({"product_id":pid,"status_http":status,"resposta":data}),status

@app.route("/mercadolivre/teste-produto")
def teste_product():
    pid=request.args.get("product_id","MLB58793248")
    data,status,_=ml_get(f"/products/{pid}")
    return jsonify({"product_id":pid,"status_http":status,"resposta":data}),status

@app.route("/mercadolivre/diagnostico")
def diagnostico():
    t=tokens()
    result={"configurado":bool(ML_CLIENT_ID),"conectado":bool(access_token())}
    if access_token():
        me,status,_=ml_get("/users/me")
        result["users_me"]={"status_http":status,"resposta":me}
    if t:
        result["token_local"]={"user_id":t.get("user_id"),"nickname":t.get("nickname"),"expires_at":t.get("expires_at")}
    return jsonify(result)

# ============================================================
# AFILIADOS - FLUXO OFICIAL
# ============================================================

AFFILIATE_GENERATOR_URL = "https://www.mercadolivre.com.br/l/afiliados-gere-seus-links"


def _parse_affiliate_cookies(raw):
    """Converte ML_AFFILIATE_COOKIES em um dicionário simples para requests."""
    raw = str(raw or "").strip()
    if not raw:
        return {}

    # Formato recomendado: JSON exportado pelo navegador:
    # [{"name":"_csrf","value":"..."}, ...]
    try:
        parsed = __import__("json").loads(raw)
        if isinstance(parsed, list):
            out = {}
            for item in parsed:
                if isinstance(item, dict) and item.get("name"):
                    out[str(item["name"])] = str(item.get("value", ""))
            if out:
                return out
        elif isinstance(parsed, dict):
            # Também aceita {"nome":"valor", ...}
            return {str(k): str(v) for k, v in parsed.items() if k}
    except Exception:
        pass

    # Formato alternativo: Cookie: nome=valor; nome2=valor2
    out = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


def _affiliate_csrf_and_link(product_url, item_id=None, product_title="", seller_id=None, expected_price=None):
    """Gera meli.la usando a sessão salva no Railway.

    REGRA CRÍTICA:
    O endpoint de afiliados do Mercado Livre NÃO aceita URL de catálogo
    /p/MLB.... Portanto, antes do POST, resolvemos qualquer catálogo para
    uma PUBLICAÇÃO/ITEM real.

    A resolução tenta nesta ordem:
      1) /items/{item_id}, quando o ID já é uma publicação real;
      2) /products/{item_id}/items, quando o ID recebido é um produto/catálogo;
      3) /products/{catalog_id}/items, quando a própria URL é /p/MLB....
    """
    cookies = _parse_affiliate_cookies(ML_AFFILIATE_COOKIES)
    if not cookies:
        raise RuntimeError("ML_AFFILIATE_COOKIES não configurado")
    if not ML_AFFILIATE_TAG:
        raise RuntimeError("ML_AFFILIATE_TAG não configurado")

    product_url = str(product_url or "").strip()
    item_id = str(item_id or "").strip().upper()

    def is_catalog_url(url):
        return bool(re.search(r"/p/MLB\d+(?:[/?#]|$)", str(url or ""), re.I))

    def extract_catalog_id(url):
        m = re.search(r"/p/(MLB\d+)(?:[/?#]|$)", str(url or ""), re.I)
        return m.group(1).upper() if m else ""

    def pick_real_permalink(items):
        if not isinstance(items, list):
            return ""
        # Prefere publicação ativa com permalink real.
        for candidate in items:
            if not isinstance(candidate, dict):
                continue
            iid = str(candidate.get("id") or candidate.get("item_id") or "").strip().upper()
            permalink = str(candidate.get("permalink") or "").strip()
            if iid and re.fullmatch(r"MLB\d+", iid) and permalink and not is_catalog_url(permalink):
                return permalink
        return ""

    def resolve_by_search():
        """Último fallback: encontra a publicação REAL via busca de anúncios.

        Alguns produtos de catálogo não expõem buy_box_winner nem
        /products/{id}/items para o token OAuth usado pelo app. Nesse caso,
        usamos os dados da própria oferta (título, vendedor e preço) para
        localizar a publicação real em /sites/MLB/search e confirmamos o ITEM
        novamente em /items/{id} antes de gerar o afiliado.
        """
        title = str(product_title or "").strip()
        if not title:
            return ""

        try:
            wanted_price = float(expected_price) if expected_price is not None else None
        except Exception:
            wanted_price = None

        wanted_seller = str(seller_id or "").strip()
        simplified = re.sub(r"[^\w\sÀ-ÿ]", " ", title, flags=re.UNICODE)
        simplified = re.sub(r"\s+", " ", simplified).strip()
        queries = [title]
        if simplified and simplified.lower() != title.lower():
            queries.append(simplified)

        best = None
        best_score = -10**9

        qwords = {
            w.lower()
            for w in re.findall(r"[\wÀ-ÿ]{3,}", simplified or title)
            if w.lower() not in {"para", "com", "sem", "uma", "uns", "dos", "das"}
        }

        for q in queries:
            try:
                data, status, _ = ml_get(
                    "/sites/MLB/search",
                    params={"q": q, "limit": 50},
                )
            except Exception as exc:
                print("[AFILIADO BUSCA] erro:", repr(exc))
                continue

            if status != 200 or not isinstance(data, dict):
                print("[AFILIADO BUSCA] HTTP", status, "para", q[:100])
                continue

            results = data.get("results") or []
            if not isinstance(results, list):
                continue

            for row in results:
                if not isinstance(row, dict):
                    continue

                rid = str(row.get("id") or "").strip().upper()
                permalink = str(row.get("permalink") or "").strip()
                if not re.fullmatch(r"MLB\d+", rid):
                    continue
                if rid == item_id or not permalink or is_catalog_url(permalink):
                    continue

                seller = row.get("seller") or {}
                row_seller_id = (
                    seller.get("id")
                    if isinstance(seller, dict)
                    else row.get("seller_id")
                )
                row_seller_id = str(row_seller_id or "").strip()

                try:
                    row_price = float(row.get("price")) if row.get("price") is not None else None
                except Exception:
                    row_price = None

                row_title = str(row.get("title") or "").strip()
                score = 0.0

                if wanted_seller:
                    score += 1000 if row_seller_id == wanted_seller else -300

                if wanted_price is not None and row_price is not None:
                    diff = abs(row_price - wanted_price)
                    if diff < 0.01:
                        score += 600
                    elif diff <= max(2.0, wanted_price * 0.02):
                        score += 250
                    else:
                        score -= min(400, diff * 2)

                if qwords and row_title:
                    rwords = {
                        w.lower()
                        for w in re.findall(r"[\wÀ-ÿ]{3,}", row_title)
                    }
                    score += len(qwords & rwords) * 10

                if score > best_score:
                    best_score = score
                    best = (rid, permalink, row_seller_id, row_price, row_title)

            if best and wanted_seller and best[2] == wanted_seller:
                if wanted_price is None or (
                    best[3] is not None and abs(best[3] - wanted_price) <= 0.01
                ):
                    break

        if not best:
            return ""

        rid, permalink, _, _, _ = best

        try:
            real_data, real_status, _ = ml_get(f"/items/{rid}")
        except Exception as exc:
            print("[AFILIADO BUSCA ITEM] erro:", rid, repr(exc))
            real_data, real_status = None, 0

        if real_status != 200 or not isinstance(real_data, dict):
            return ""

        real_permalink = str(real_data.get("permalink") or "").strip()
        if not real_permalink or is_catalog_url(real_permalink):
            return ""

        real_seller = real_data.get("seller") or {}
        real_seller_id = (
            real_seller.get("id")
            if isinstance(real_seller, dict)
            else real_data.get("seller_id")
        )
        real_seller_id = str(real_seller_id or "").strip()

        try:
            real_price = float(real_data.get("price")) if real_data.get("price") is not None else None
        except Exception:
            real_price = None

        if wanted_seller and real_seller_id and real_seller_id != wanted_seller:
            return ""

        if (
            wanted_price is not None
            and real_price is not None
            and abs(real_price - wanted_price) > max(2.0, wanted_price * 0.02)
        ):
            return ""

        print(
            "[AFILIADO BUSCA] publicação real encontrada:",
            rid,
            "seller=", real_seller_id,
            "price=", real_price,
        )
        return real_permalink

    # Se a oferta já trouxe uma URL de publicação real, NÃO consulte /items/{id}
    # novamente. Em 2026 esse endpoint pode retornar 403 para aplicações não
    # liberadas, mesmo quando a URL pública do anúncio é perfeitamente válida.
    # Isso é especialmente importante para os perfumes, cuja publicação já
    # veio de /products/{id}/items.
    product_host_ok = bool(re.search(
        r"https?://(?:www\.|produto\.)?mercadolivre\.com\.br/",
        product_url,
        re.I,
    ))
    if product_url and product_host_ok and not is_catalog_url(product_url):
        print("[AFILIADO] URL de publicação já confirmada; pulando /items:", product_url[:120])
    elif re.fullmatch(r"MLB\d+", item_id):
        try:
            item_data, item_status, _ = ml_get(f"/items/{item_id}")
        except Exception as exc:
            item_data, item_status = None, 0
            print("[AFILIADO ITEM] erro ao consultar", item_id, repr(exc))

        if item_status == 200 and isinstance(item_data, dict):
            real_permalink = str(item_data.get("permalink") or "").strip()
            if real_permalink and not is_catalog_url(real_permalink):
                product_url = real_permalink
            else:
                # IMPORTANTE: um item de catálogo pode responder normalmente em
                # /items/{id}, mas continuar apontando para /p/MLB.... Nesse
                # caso, o próprio item costuma informar catalog_product_id.
                # O catalog_product_id é o ID correto para consultar /products/
                # e descobrir o buy_box_winner (a publicação real).
                catalog_product_id = str(
                    item_data.get("catalog_product_id") or ""
                ).strip().upper()

                if not re.fullmatch(r"MLB\d+", catalog_product_id):
                    cp = item_data.get("catalog_product")
                    if isinstance(cp, dict):
                        catalog_product_id = str(
                            cp.get("id")
                            or cp.get("product_id")
                            or ""
                        ).strip().upper()

                if re.fullmatch(r"MLB\d+", catalog_product_id) and catalog_product_id != item_id:
                    try:
                        catalog_data, catalog_product_status, _ = ml_get(
                            f"/products/{catalog_product_id}"
                        )
                    except Exception as exc:
                        catalog_data, catalog_product_status = None, 0
                        print(
                            "[AFILIADO CATALOG_PRODUCT] erro ao consultar",
                            catalog_product_id,
                            repr(exc),
                        )

                    if catalog_product_status == 200 and isinstance(catalog_data, dict):
                        bb = catalog_data.get("buy_box_winner") or catalog_data.get("buy_box")
                        candidates_bb = []
                        if isinstance(bb, dict):
                            candidates_bb.append(bb)
                            if isinstance(bb.get("item"), dict):
                                candidates_bb.append(bb.get("item"))
                            if isinstance(bb.get("winner"), dict):
                                candidates_bb.append(bb.get("winner"))

                        for candidate in candidates_bb:
                            if not isinstance(candidate, dict):
                                continue
                            cid = str(
                                candidate.get("item_id")
                                or candidate.get("id")
                                or ""
                            ).strip().upper()
                            cp = str(candidate.get("permalink") or "").strip()

                            if re.fullmatch(r"MLB\d+", cid) and cid != item_id:
                                if cp and not is_catalog_url(cp):
                                    product_url = cp
                                    break

                                try:
                                    real_data, real_status, _ = ml_get(f"/items/{cid}")
                                except Exception:
                                    real_data, real_status = None, 0

                                if real_status == 200 and isinstance(real_data, dict):
                                    rp = str(real_data.get("permalink") or "").strip()
                                    if rp and not is_catalog_url(rp):
                                        product_url = rp
                                        break

                # Mantém a tentativa antiga como fallback, caso o ID recebido
                # seja realmente uma publicação diferente do catálogo.
                if is_catalog_url(product_url):
                    real_item_id = str(
                        item_data.get("item_id")
                        or item_data.get("id")
                        or ""
                    ).strip().upper()

                    if re.fullmatch(r"MLB\d+", real_item_id) and real_item_id != item_id:
                        try:
                            real_data, real_status, _ = ml_get(f"/items/{real_item_id}")
                        except Exception:
                            real_data, real_status = None, 0

                        real_permalink = (
                            str(real_data.get("permalink") or "").strip()
                            if real_status == 200 and isinstance(real_data, dict)
                            else ""
                        )

                        if real_permalink and not is_catalog_url(real_permalink):
                            product_url = real_permalink

        else:
            # O ID pode ser um PRODUTO/CATÁLOGO. Primeiro tentamos o detalhe
            # do produto, porque o buy_box_winner costuma trazer o ITEM real
            # mesmo quando /products/{id}/items não está disponível para o token.
            real_item_id = ""
            real_permalink = ""

            try:
                product_data, product_status, _ = ml_get(f"/products/{item_id}")
            except Exception as exc:
                product_data, product_status = None, 0
                print("[AFILIADO PRODUTO DETALHE] erro ao consultar", item_id, repr(exc))

            if product_status == 200 and isinstance(product_data, dict):
                bb = product_data.get("buy_box_winner") or product_data.get("buy_box")
                candidates_bb = []
                if isinstance(bb, dict):
                    candidates_bb.append(bb)
                    if isinstance(bb.get("item"), dict):
                        candidates_bb.append(bb.get("item"))
                    if isinstance(bb.get("winner"), dict):
                        candidates_bb.append(bb.get("winner"))

                for candidate in candidates_bb:
                    if not isinstance(candidate, dict):
                        continue
                    cid = str(
                        candidate.get("item_id")
                        or candidate.get("id")
                        or ""
                    ).strip().upper()
                    cp = str(candidate.get("permalink") or "").strip()
                    if re.fullmatch(r"MLB\d+", cid) and cid != item_id:
                        real_item_id = cid
                        if cp and not is_catalog_url(cp):
                            real_permalink = cp
                            break

                if real_item_id and not real_permalink:
                    try:
                        real_data, real_status, _ = ml_get(f"/items/{real_item_id}")
                    except Exception as exc:
                        real_data, real_status = None, 0
                        print("[AFILIADO ITEM REAL] erro ao consultar", real_item_id, repr(exc))
                    if real_status == 200 and isinstance(real_data, dict):
                        rp = str(real_data.get("permalink") or "").strip()
                        if rp and not is_catalog_url(rp):
                            real_permalink = rp

            # Segunda tentativa: publicações associadas ao catálogo.
            products_status = 0
            if not real_permalink:
                try:
                    product_items_data, products_status, _ = ml_get(f"/products/{item_id}/items")
                except Exception as exc:
                    product_items_data, products_status = None, 0
                    print("[AFILIADO PRODUTO] erro ao consultar", item_id, repr(exc))

                candidates = []
                if isinstance(product_items_data, list):
                    candidates = product_items_data
                elif isinstance(product_items_data, dict):
                    candidates = product_items_data.get("results") or []

                real_permalink = pick_real_permalink(candidates)

                # Se encontrou o ITEM mas não o permalink, consulta /items/{id}.
                if not real_permalink:
                    for candidate in candidates:
                        if not isinstance(candidate, dict):
                            continue
                        cid = str(
                            candidate.get("item_id")
                            or candidate.get("id")
                            or ""
                        ).strip().upper()
                        if not re.fullmatch(r"MLB\d+", cid) or cid == item_id:
                            continue
                        try:
                            real_data, real_status, _ = ml_get(f"/items/{cid}")
                        except Exception:
                            real_data, real_status = None, 0
                        if real_status == 200 and isinstance(real_data, dict):
                            rp = str(real_data.get("permalink") or "").strip()
                            if rp and not is_catalog_url(rp):
                                real_permalink = rp
                                break

            if real_permalink:
                product_url = real_permalink
            elif is_catalog_url(product_url):
                searched_permalink = resolve_by_search()
                if searched_permalink:
                    product_url = searched_permalink
                else:
                    raise RuntimeError(
                        f"O catálogo {item_id} foi identificado, mas o Mercado Livre "
                        f"não forneceu uma publicação real para ele "
                        f"(items HTTP {item_status}, product HTTP {product_status}, "
                        f"product items HTTP {products_status}) e a busca também "
                        f"não encontrou uma publicação compatível."
                    )

    # 2) Se a URL recebida ainda é /p/MLB..., resolve o catálogo antes do POST.
    if is_catalog_url(product_url):
        catalog_id = extract_catalog_id(product_url)
        if catalog_id:
            try:
                catalog_items_data, catalog_status, _ = ml_get(f"/products/{catalog_id}/items")
            except Exception as exc:
                catalog_items_data, catalog_status = None, 0
                print("[AFILIADO CATALOGO] erro ao consultar", catalog_id, repr(exc))

            candidates = []
            if isinstance(catalog_items_data, list):
                candidates = catalog_items_data
            elif isinstance(catalog_items_data, dict):
                candidates = catalog_items_data.get("results") or []

            real_permalink = pick_real_permalink(candidates)

            # Alguns catálogos não expõem /products/{id}/items para o token,
            # mas o detalhe /products/{id} ainda informa o buy_box_winner.
            if not real_permalink:
                try:
                    catalog_product, catalog_product_status, _ = ml_get(f"/products/{catalog_id}")
                except Exception as exc:
                    catalog_product, catalog_product_status = None, 0
                    print("[AFILIADO CATALOGO DETALHE] erro ao consultar", catalog_id, repr(exc))

                if catalog_product_status == 200 and isinstance(catalog_product, dict):
                    bb = catalog_product.get("buy_box_winner") or catalog_product.get("buy_box")
                    bb_candidates = []
                    if isinstance(bb, dict):
                        bb_candidates.append(bb)
                        if isinstance(bb.get("item"), dict):
                            bb_candidates.append(bb.get("item"))
                        if isinstance(bb.get("winner"), dict):
                            bb_candidates.append(bb.get("winner"))

                    for candidate in bb_candidates:
                        if not isinstance(candidate, dict):
                            continue
                        cid = str(
                            candidate.get("item_id")
                            or candidate.get("id")
                            or ""
                        ).strip().upper()
                        cp = str(candidate.get("permalink") or "").strip()
                        if re.fullmatch(r"MLB\d+", cid) and cid != catalog_id:
                            if cp and not is_catalog_url(cp):
                                real_permalink = cp
                                break
                            try:
                                real_data, real_status, _ = ml_get(f"/items/{cid}")
                            except Exception:
                                real_data, real_status = None, 0
                            if real_status == 200 and isinstance(real_data, dict):
                                rp = str(real_data.get("permalink") or "").strip()
                                if rp and not is_catalog_url(rp):
                                    real_permalink = rp
                                    break

            if real_permalink:
                product_url = real_permalink
            else:
                searched_permalink = resolve_by_search()
                if searched_permalink:
                    product_url = searched_permalink
                else:
                    raise RuntimeError(
                        f"O Mercado Livre retornou o catálogo {catalog_id}, "
                        f"mas não existe uma publicação real compatível disponível para gerar o afiliado."
                    )

    if not product_url:
        raise RuntimeError("URL do produto vazia")

    # Segurança final: NUNCA envia /p/MLB... ao endpoint de afiliados.
    if is_catalog_url(product_url):
        raise RuntimeError("URL de catálogo /p/MLB... bloqueada antes da geração do afiliado.")

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    csrf = cookies.get("_csrf", "")

    # O token da página pode ser mais atual que o cookie.
    try:
        page = requests.get(
            product_url,
            headers={"Cookie": cookie_header, "User-Agent": ua},
            timeout=ML_AFFILIATE_TIMEOUT,
            allow_redirects=True,
        )
        if page.ok:
            m = re.search(r'csrfToken[^\"]*"([^"]+)"', page.text)
            if not m:
                m = re.search(r'name="csrf-token"\s+content="([^"]+)"', page.text)
            if m:
                csrf = m.group(1)
    except Exception:
        pass

    try:
        parsed_product = urlparse(product_url)
        product_host = (parsed_product.netloc or "").lower()
    except Exception:
        product_host = ""

    origin = (
        "https://www.mercadolivre.com.br"
        if "mercadolivre.com.br" in product_host and not product_host.startswith("produto.")
        else "https://produto.mercadolivre.com.br"
    )

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-CSRF-Token": csrf,
        "Cookie": cookie_header,
        "Referer": product_url,
        "Origin": origin,
        "User-Agent": ua,
    }

    r = requests.post(
        ML_AFFILIATE_URL,
        headers=headers,
        json={"url": product_url.rstrip("/"), "tag": ML_AFFILIATE_TAG},
        timeout=ML_AFFILIATE_TIMEOUT,
        allow_redirects=True,
    )

    if not r.ok:
        detail = r.text[:800].replace("\n", " ").strip()
        is_url_not_allowed = (
            r.status_code == 400
            and ("URL not allowed in affiliates program" in detail
                 or '"error_code":111' in detail
                 or '"error_code": 111' in detail)
        )
        if is_url_not_allowed:
            print("[AFILIADO FALLBACK] URL recusada; procurando publicação alternativa compatível.")
            alternative_url = resolve_by_search()
            if alternative_url and alternative_url.rstrip("/") != product_url.rstrip("/"):
                try:
                    alt_page = requests.get(
                        alternative_url,
                        headers={"Cookie": cookie_header, "User-Agent": ua},
                        timeout=ML_AFFILIATE_TIMEOUT,
                        allow_redirects=True,
                    )
                    alt_csrf = csrf
                    if alt_page.ok:
                        m = re.search(r'csrfToken[^\"]*"([^\"]+)"', alt_page.text)
                        if not m:
                            m = re.search(r'name="csrf-token"\s+content="([^\"]+)"', alt_page.text)
                        if m:
                            alt_csrf = m.group(1)
                    alt_host = (urlparse(alternative_url).netloc or "").lower()
                    alt_origin = ("https://www.mercadolivre.com.br"
                                  if "mercadolivre.com.br" in alt_host and not alt_host.startswith("produto.")
                                  else "https://produto.mercadolivre.com.br")
                    alt_headers = dict(headers)
                    alt_headers.update({"X-CSRF-Token": alt_csrf, "Referer": alternative_url, "Origin": alt_origin})
                    alt_response = requests.post(
                        ML_AFFILIATE_URL,
                        headers=alt_headers,
                        json={"url": alternative_url.rstrip("/"), "tag": ML_AFFILIATE_TAG},
                        timeout=ML_AFFILIATE_TIMEOUT,
                        allow_redirects=True,
                    )
                    if alt_response.ok:
                        alt_data = alt_response.json()
                        alt_short_url = str(alt_data.get("short_url") or "").strip()
                        if alt_short_url:
                            print("[AFILIADO FALLBACK] publicação alternativa aceita.")
                            return alt_short_url
                        print("[AFILIADO FALLBACK] resposta alternativa sem short_url.")
                    else:
                        print("[AFILIADO FALLBACK] alternativa recusada: HTTP", alt_response.status_code,
                              alt_response.text[:400].replace("\n", " "))
                except Exception as alt_exc:
                    print("[AFILIADO FALLBACK] erro na alternativa:", repr(alt_exc))
            else:
                print("[AFILIADO FALLBACK] nenhuma publicação alternativa compatível encontrada.")
        raise RuntimeError(f"Mercado Livre respondeu HTTP {r.status_code}: {detail}")

    try:
        data = r.json()
    except Exception as exc:
        raise RuntimeError("Resposta do Mercado Livre não veio em JSON") from exc

    short_url = str(data.get("short_url") or "").strip()
    if not short_url:
        raise RuntimeError("Mercado Livre não retornou short_url")

    return short_url


@app.route("/api/afiliado/gerar", methods=["POST"])
def api_afiliado_gerar():
    """Gera o link afiliado no servidor; sem cookies, retorna 503 para o fallback Safari."""
    payload = request.get_json(silent=True) or {}
    product_url = str(payload.get("url") or "").strip()
    item_id = str(payload.get("item_id") or "").strip().upper()
    product_title = str(payload.get("title") or payload.get("product_title") or "").strip()
    seller_id = str(payload.get("seller_id") or "").strip()
    expected_price = payload.get("price")
    if not product_url and not re.fullmatch(r"MLB\d+", item_id):
        return jsonify({"ok": False, "erro": "Publicação/ITEM do produto não informado."}), 400

    if not ML_AFFILIATE_COOKIES:
        return jsonify({
            "ok": False,
            "configurado": False,
            "erro": "Gerador automático ainda não configurado no Railway.",
        }), 503

    try:
        link = _affiliate_csrf_and_link(
            product_url,
            item_id=item_id,
            product_title=product_title,
            seller_id=seller_id,
            expected_price=expected_price,
        )
        return jsonify({"ok": True, "link": link, "modo": "servidor", "item_id": item_id})
    except Exception as exc:
        # Não expõe cookies/token nos logs nem na resposta.
        return jsonify({
            "ok": False,
            "configurado": True,
            "erro": str(exc)[:1000],
        }), 502


AFFILIATE_PORTAL_URL = "https://www.mercadolivre.com.br/l/visite-o-portal-de-afiliados"

@app.route("/afiliado/gerador")
def afiliado_gerador():
    # Mantido apenas para compatibilidade com versões antigas.
    # No iPhone, o fluxo principal usa a página do produto no app.
    return redirect(AFFILIATE_PORTAL_URL)

@app.route("/afiliado/portal")
def afiliado_portal():
    return redirect(AFFILIATE_PORTAL_URL)

@app.route("/afiliado/retorno")
def afiliado_retorno():
    # Retorno simples e robusto do bookmarklet do Safari.
    # Não usa url_for() aqui para evitar erro 500 caso o endpoint raiz
    # seja alterado/registrado de forma diferente no deploy.
    state=request.args.get("state","").strip()
    link=request.args.get("link","").strip()
    if not state or not link:
        return redirect("/")
    return redirect("/?" + urlencode({
        "afiliado_state": state,
        "afiliado_link": link
    }))

@app.route("/afiliado/bookmarklet")
def afiliado_bookmarklet():
    js=(
        "javascript:(async()=>{try{"
        "const h=(location.hash||'').replace(/^#/,''),hp=new URLSearchParams(h);"
        "const state=hp.get('cacador_state')||'';"
        "const ret=hp.get('cacador_return')||'';"
        "const u=new URL(location.href);u.hash='';"
        "const tr=await fetch('/affiliate-program/api/v2/stripe/user/tags',{headers:{Accept:'application/json'}});"
        "const tj=await tr.json();"
        "const tag=(tj.tags||[]).find(x=>x.in_use)?.tag;"
        "if(!tag)throw Error('Tag de afiliado não encontrada');"
        "const lr=await fetch('/affiliate-program/api/v2/stripe/user/links',{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({url:u.toString(),tag})});"
        "const j=await lr.json();"
        "if(!j.short_url)throw Error(j.error?.message||'O Mercado Livre não gerou o link');"
        "if(!ret)throw Error('Retorno do Caçador não encontrado');"
        "location.href=ret+'?state='+encodeURIComponent(state)+'&link='+encodeURIComponent(j.short_url);"
        "}catch(e){alert('❌ '+(e.message||e))}})()"
    )
    return render_template_string('''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Caçador — Bookmarklet</title><style>body{font-family:Arial;background:#f4f5f7;padding:18px}.card{max-width:700px;margin:auto;background:#fff;padding:20px;border-radius:16px;box-shadow:0 5px 20px #0001}textarea{width:100%;min-height:190px;font-size:12px;box-sizing:border-box}button{width:100%;padding:14px;border:0;border-radius:10px;background:#3483fa;color:#fff;margin-top:8px;font-size:15px}.ok{background:#eef8f0;padding:12px;border-radius:10px}</style></head><body><div class="card"><h2>🔗 Bookmarklet do Caçador</h2><div class="ok">Use esta versão no Safari. Ela pega a tag ativa da sua conta Mercado Livre, gera o link afiliado e volta automaticamente para o Caçador.</div><h3>1. Código</h3><textarea id="code" readonly>{{js}}</textarea><button onclick="copyCode()">📋 Copiar código</button><h3>2. Instalar no Safari</h3><p>Crie/edite um favorito no Safari, dê o nome <b>Caçador Afiliado</b> e substitua o endereço do favorito pelo código acima.</p><p>Depois, quando o Caçador abrir um produto, toque no favorito <b>Caçador Afiliado</b>. O link será gerado e você voltará automaticamente para o Caçador.</p></div><script>async function copyCode(){try{await navigator.clipboard.writeText(document.getElementById('code').value);alert('✅ Código copiado. Agora cole no endereço do favorito do Safari.')}catch(e){const t=document.getElementById('code');t.focus();t.select();alert('Selecione o código e copie manualmente.')}}</script></body></html>''', js=js)

# ============================================================
# HTML
# ============================================================

HTML = r"""
<!doctype html><html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Caçador de Ofertas</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f5f7;font-family:Arial;color:#222}
.container{max-width:1050px;margin:auto;padding:18px}.card{background:#fff;border-radius:16px;padding:18px;margin-bottom:18px;box-shadow:0 5px 20px #0000000c}
button,input,select{width:100%;padding:13px;border-radius:10px;border:1px solid #ddd;font-size:15px}button{border:0;background:#3483fa;color:#fff;cursor:pointer;margin-top:7px}
.login{background:#ffe600;color:#222}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:9px}.cat{background:#fff;border:1px solid #ddd;color:#222;text-align:left}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:9px}.stat{background:#f3f4f6;padding:13px;border-radius:11px}.stat b{display:block;font-size:23px;margin-top:4px}
.modelo{border:2px solid #eee;border-radius:15px;padding:14px;margin:13px 0}.mh{display:flex;gap:12px;align-items:center}.mh img{width:85px;height:85px;object-fit:contain;background:#fafafa;border-radius:10px}.title{font-size:18px;font-weight:bold}.tag{display:inline-block;background:#eef4ff;color:#3483fa;border-radius:7px;padding:5px 8px;font-size:11px;margin:3px}.seller{background:#fafafa;border:1px solid #eee;border-radius:12px;padding:12px;margin-top:10px}.price{font-size:22px;font-weight:bold}.green{color:#00a650;font-weight:bold}.old{text-decoration:line-through;color:#777}.coupon{background:#fff8d6;border:1px dashed #d7ad00;border-radius:10px;padding:10px;margin-top:9px}.final{background:#eaf8ef;color:#008a3e;font-weight:bold;padding:9px;border-radius:8px;margin-top:7px}.ad{display:none;white-space:pre-wrap;background:#f7f7f7;padding:10px;border-radius:9px;margin-top:8px;font-size:13px}.small{font-size:12px;color:#666}.status{background:#eef8f0;padding:10px;border-radius:9px}
</style>
<script>
let cacarTimer=null;
async function cacar(cat){
 const status=document.getElementById('status');
 status.textContent='🔄 Carregando busca completa... Aguarde até terminar.';
 document.getElementById('results').innerHTML='<p>🔎 Buscando os 20 mais vendidos da categoria. Aguarde a busca completa...</p>';
 if(cacarTimer){clearTimeout(cacarTimer);cacarTimer=null;}
 try{
  const url='/api/cacar'+(cat?'?categoria='+encodeURIComponent(cat):'');
  const r=await fetch(url,{cache:'no-store'});
  const start=await r.json();
  if(!start.job_id){throw new Error(start.erro||'Não foi possível iniciar a atualização.');}
  acompanharCaca(start.job_id);
 }catch(e){
  status.textContent='❌ '+e.message;
 }
}
async function acompanharCaca(jobId){
 const status=document.getElementById('status');
 try{
  const r=await fetch('/api/cacar/status/'+encodeURIComponent(jobId),{cache:'no-store'});
  const job=await r.json();
  status.textContent=(job.message||'🔄 Atualizando...')+' '+(job.progress||0)+'%';
  if(job.status==='done'){
   if(job.result) render(job.result);
   status.textContent='✅ '+(job.message||'Produtos atualizados.');
   cacarTimer=null;
   return;
  }
  if(job.status==='error'){
   status.textContent='❌ '+(job.error||job.message||'Erro durante a atualização.');
   cacarTimer=null;
   return;
  }
  cacarTimer=setTimeout(()=>acompanharCaca(jobId),1200);
 }catch(e){
  status.textContent='⚠️ Aguardando resposta do servidor...';
  cacarTimer=setTimeout(()=>acompanharCaca(jobId),1800);
 }
}
async function buscar(){
 const q=document.getElementById('q').value.trim();
 if(!q)return;
 const status=document.getElementById('status');
 status.textContent='🔄 Busca iniciada. Consultando o Mercado Livre...';
 document.getElementById('results').innerHTML='<p>🔎 Buscando ofertas em segundo plano. O resultado aparecerá aqui quando terminar.</p>';
 if(cacarTimer){clearTimeout(cacarTimer);cacarTimer=null;}
 try{
  const r=await fetch('/api/buscar/job?q='+encodeURIComponent(q),{cache:'no-store'});
  const start=await r.json();
  if(!r.ok || !start.job_id) throw new Error(start.erro||'Não foi possível iniciar a busca.');
  acompanharCaca(start.job_id);
 }catch(e){
  status.textContent='❌ Erro ao iniciar a busca: '+e.message;
 }
}
function render(data){
 document.getElementById('stats').innerHTML=Object.entries(data.stats||{}).map(([k,v])=>`<div class="stat">${k}<b>${v}</b></div>`).join('');
 document.getElementById('results').innerHTML=(data.modelos||[]).map((m,mi)=>`
 <div class="modelo">
  <div class="mh">${m.image?`<img src="${m.image}">`:''}<div>
   <span class="tag">🔥 OPORTUNIDADE ${mi+1}</span><div class="title">${esc(m.modelo_nome)}</div>
   ${(m.especificacoes||[]).map(s=>`<span class="tag">${esc(s)}</span>`).join('')}
   <div class="small">${m.ofertas.length} vendedor(es)</div>
  </div></div>
  ${m.ofertas.map((o,oi)=>seller(o,mi,oi)).join('')}
 </div>`).join('') || '<p>Nenhuma oportunidade encontrada.</p>';
}
function seller(o,mi,oi){
 const id='a'+mi+'_'+oi;
 const cup=o.cupom;
 return `<div class="seller">
 ${o.menor_preco_modelo?'<span class="tag" style="background:#00a650;color:white">🏆 MELHOR CUSTO TOTAL</span>':''}
 <div class="price">${brl(o.price)}</div>
 ${o.original_price?`<div class="old">De: ${brl(o.original_price)}</div>`:''}
 ${o.appears_both?'<div class="tag" style="background:#e8fff0;color:#008a3e;font-weight:bold">🔥 APARECE NOS DOIS: PROCURADO + MAIS VENDIDO</div>':''}
 ${o.trend_bucket?`<div class="tag">📈 ${esc(o.trend_bucket)}${o.trend_rank?` #${o.trend_rank}`:''}</div>`:''}
 ${o.best_seller_position?`<div class="tag" style="background:#fff1d6;color:#8a5700">🏆 MAIS VENDIDO #${o.best_seller_position}</div>`:''}
 ${o.discount>0?`<div class="green">🔥 ${o.discount}% OFF</div>`:''}
 ${o.free_shipping?'<div class="green">🚚 Frete grátis</div>':''}
 ${o.shipping_known ? (Number(o.shipping_cost||0)>0 ? `<div>🚚 Frete: ${brl(o.shipping_cost)}</div><div class="green"><b>💰 Total pago estimado: ${brl(o.total_price)}</b></div>` : `<div class="green"><b>💰 Total pago: ${brl(o.total_price)}</b></div>`) : '<div class="small">🚚 Frete não informado pelo Mercado Livre</div>'}
 ${cup?`<div class="coupon"><b>🎟️ CUPOM: ${esc(cup.code || cup.label || 'Cupom disponível')}</b>
 ${cup.discount_percent?`<div>🔥 Até ${cup.discount_percent}% OFF</div>`:''}
 ${cup.fixed_discount?`<div>💰 ${brl(cup.fixed_discount)} OFF</div>`:''}
 ${cup.min_purchase?`<div class="small">Compra mínima: ${brl(cup.min_purchase)}</div>`:''}
 ${cup.max_discount?`<div class="small">Desconto máximo: ${brl(cup.max_discount)}</div>`:''}
 ${cup.usage_limit?`<div class="small">👥 Limite informado: ${Number(cup.usage_limit).toLocaleString('pt-BR')} usos</div>`:''}
 <div>💵 Desconto estimado: <b>${brl(o.desconto_cupom)}</b> (${Number(o.percentual_cupom_efetivo||0).toFixed(2)}%)</div>
 <div class="final">💥 Estimado com cupom: ${brl(o.preco_com_cupom)}</div>
 <div class="small">⚠️ ${o.cupom_match==='produto_publico'?'Cupom encontrado associado ao produto em fonte pública do Mercado Livre.':'Cupom encontrado em fonte pública para este produto.'} Confirme no checkout.</div></div>`:''}
 ${o.cash_discount>0?`<div class="coupon" style="background:#eefaf2;border-color:#78c995"><b>💳 ${esc(o.cash_label||'Pagamento à vista')}</b><div>Desconto informado: ${brl(o.cash_discount)}</div><div class="final">💥 Final estimado: ${brl(o.cash_final)}</div><div class="small">⚠️ Não somado ao cupom automaticamente.</div></div>`:''}
 <div class="small">👤 Vendedor: ${o.seller_id||'N/A'}</div><br>
 <a href="${o.permalink}" target="_blank">🛒 Ver produto</a>
 <button onclick="copiarUrl('${id}',decodeURIComponent('${encodeURIComponent(String(o.permalink||""))}'))" style="background:#555">🔗 Copiar URL do produto</button>
 <button onclick="iniciarAfiliado('${id}',decodeURIComponent('${encodeURIComponent(JSON.stringify(o))}'))" style="background:#ffe600;color:#222;font-weight:bold">🔗 Gerar meu link afiliado</button>
 <div class="small" style="margin-top:8px">⚡ O Caçador tenta gerar o <b>meli.la</b> automaticamente. Se a sessão do servidor não estiver configurada, ele mantém o fallback pelo Safari.</div>
 <input id="aff_${id}" type="url" inputmode="url" placeholder="Ou cole aqui um link de afiliado do Mercado Livre" autocomplete="off">
 <button onclick="anuncio('${id}',decodeURIComponent('${encodeURIComponent(JSON.stringify(o))}'))">📢 Gerar anúncio com meu link afiliado</button>
 <button id="copy_${id}" style="display:none;background:#ff8a00" onclick="copyAd('${id}')">📋 Copiar oferta</button>
 <button id="wa_${id}" style="display:none;background:#25D366;color:#fff" onclick="enviarWhatsApp('${id}')">📲 Enviar para WhatsApp</button>
 <div id="ad_${id}" class="ad"></div>
 </div>`;
}


async function copiarUrl(id,url){
 const value=String(url||'').trim();
 if(!value){alert('❌ Este produto não possui uma URL válida.');return;}
 try{
  if(navigator.clipboard && typeof navigator.clipboard.writeText==='function'){
   await navigator.clipboard.writeText(value);
  }else{
   const ta=document.createElement('textarea');
   ta.value=value;
   ta.setAttribute('readonly','');
   ta.style.position='fixed';
   ta.style.left='-9999px';
   ta.style.top='0';
   ta.style.opacity='0';
   document.body.appendChild(ta);
   ta.focus();
   ta.select();
   ta.setSelectionRange(0,ta.value.length);
   const ok=document.execCommand('copy');
   ta.remove();
   if(!ok) throw new Error('copy_failed');
  }
  alert('✅ URL copiada! Agora cole no Gerador oficial de afiliado do Mercado Livre.');
 }catch(e){
  // No iPhone, alguns navegadores bloqueiam a área de transferência.
  // Mostramos a URL em um campo selecionável para o usuário copiar manualmente.
  const box=document.createElement('div');
  box.style.cssText='position:fixed;z-index:99999;left:12px;right:12px;top:18%;background:#fff;border:2px solid #3483fa;border-radius:14px;padding:16px;box-shadow:0 8px 30px rgba(0,0,0,.25);';
  box.innerHTML='<b style="font-size:17px">🔗 URL do produto</b><div style="margin:10px 0 6px;font-size:12px;color:#666">Toque no campo, selecione e copie:</div>'+
   '<textarea id="urlFallbackCopy" readonly style="width:100%;min-height:110px;font-size:13px;padding:10px;box-sizing:border-box;border:1px solid #ccc;border-radius:8px;">'+esc(value)+'</textarea>'+
   '<button id="urlFallbackClose" style="width:100%;margin-top:10px;background:#3483fa;color:#fff">Fechar</button>';
  document.body.appendChild(box);
  const ta=box.querySelector('#urlFallbackCopy');
  ta.focus();
  ta.select();
  ta.setSelectionRange(0,ta.value.length);
  box.querySelector('#urlFallbackClose').onclick=()=>box.remove();
 }
}
async function iniciarAfiliado(id,o){
 try{
  if(typeof o==='string'){o=JSON.parse(o);}

  // PRIMEIRO: tenta gerar automaticamente no próprio servidor.
  // Isso elimina Safari/favorito quando ML_AFFILIATE_COOKIES estiver configurado.
  const itemId=String(o.item_id||o.id||'').trim().toUpperCase();
  let target='';
  const m=itemId.match(/^MLB(\d+)$/);
  if(m){
   target='https://produto.mercadolivre.com.br/MLB-'+m[1];
  }else{
   target=String(o.permalink||'').trim();
  }
  if(!target){throw new Error('A oferta não possui uma publicação válida do Mercado Livre.');}

  try{
   const r=await fetch('/api/afiliado/gerar',{
    method:'POST',
    headers:{'Content-Type':'application/json','Accept':'application/json'},
    body:JSON.stringify({
     url:target,
     item_id:itemId,
     title:String(o.title||o.product_title||'').trim(),
     seller_id:String(o.seller_id||'').trim(),
     price:o.price
    })
   });
   const data=await r.json().catch(()=>({}));
   if(data.ok && data.link){
    const field=document.getElementById('aff_'+id);
    if(field) field.value=data.link;
    await anuncio(id,JSON.stringify(o));
    return;
   }
   // Se o servidor está configurado mas recusou esta publicação, NÃO
   // abrimos uma URL de fallback no Safari. Isso era exatamente o que
   // fazia o iPhone cair na página “Parece que esta página não existe”.
   if(data.configurado){
    throw new Error(data.erro || 'O Mercado Livre não aceitou esta publicação para gerar o link afiliado.');
   }
  }catch(e){
   // Só usa o Safari quando o gerador do servidor realmente não está
   // configurado. Com cookies configurados, mostramos o erro real.
   if(String(e && e.message || '').trim()){
    alert('❌ Não foi possível gerar o link afiliado no servidor. '+e.message);
    return;
   }
  }

  // FALLBACK: fluxo Safari somente quando o servidor não estiver configurado.
  const state='af'+Date.now().toString(36)+Math.random().toString(36).slice(2,8);
  localStorage.setItem('cacador_aff_pending_'+state,JSON.stringify({id:id,offer:o,createdAt:Date.now()}));
  const u=new URL(target);
  u.hash='cacador_state='+state+'&cacador_return='+encodeURIComponent(location.origin+'/afiliado/retorno');
  window.location.href=u.toString();
 }catch(e){
  alert('❌ Não foi possível gerar o link afiliado. '+e.message);
 }
}

async function processarRetornoAfiliado(){
 const p=new URLSearchParams(location.search);
 const state=p.get('afiliado_state');
 const link=p.get('afiliado_link');
 if(!state || !link) return;
 const key='cacador_aff_pending_'+state;
 let pending=null;
 try{pending=JSON.parse(localStorage.getItem(key)||'null');}catch(e){}
 localStorage.removeItem(key);
 history.replaceState({},document.title,location.pathname);
 if(!pending || !pending.offer){
  alert('⚠️ O link afiliado foi gerado, mas a oferta anterior não foi encontrada nesta sessão.');
  return;
 }
 const o=pending.offer;
 render({stats:{'Link afiliado':'Gerado'},modelos:[{modelo_nome:o.title||o.product_title||'Oferta encontrada',image:o.image||'',especificacoes:[],ofertas:[o]}]});
 const id='a0_0';
 const field=document.getElementById('aff_'+id);
 if(field) field.value=link;
 const box=document.getElementById('results');
 if(box) box.scrollIntoView({behavior:'smooth',block:'start'});
 await anuncio(id,JSON.stringify(o));
}

async function anuncio(id,o){
 try{
  if(typeof o==='string'){o=JSON.parse(o);}
  const affiliate=document.getElementById('aff_'+id).value.trim();
  let affiliateOk=false;
  try{
   const u=new URL(affiliate);
   const host=(u.hostname||'').toLowerCase();
   if(/^(www\.)?meli\.la$/i.test(host) && /^\/[A-Za-z0-9]+\/?$/.test(u.pathname)){
    affiliateOk=true;
   }else if(/^(www\.)?mercadolivre\.com\.br$/i.test(host)){
    // No iPhone, o Compartilhar pode colocar sid/wid no fragmento (#),
    // por exemplo: #origin=share&sid=share&wid=MLB4871129789
    const sidQuery=(u.searchParams.get('sid')||'').toLowerCase();
    const widQuery=(u.searchParams.get('wid')||'').toUpperCase();
    let sid=sidQuery;
    let wid=widQuery;
    if(!sid || !wid){
      const hash=(u.hash||'').replace(/^#/, '');
      const hp=new URLSearchParams(hash);
      sid=(hp.get('sid')||sid).toLowerCase();
      wid=(hp.get('wid')||wid).toUpperCase();
    }
    affiliateOk=(sid==='share' && /^MLB\d+$/.test(wid));
   }
  }catch(e){}
  if(!affiliateOk){throw new Error('Cole um link de afiliado válido do Mercado Livre antes de gerar o anúncio.');}
  // Revalida a publicação EXATA apontada pelo link de afiliado.
  // Se for meli.la, o servidor resolve o redirecionamento e descobre o wid/ITEM.
  // Isso evita misturar preço de outro vendedor/publicação do mesmo produto.
  try{
   const pr=await fetch('/api/preco-atual?item_id='+encodeURIComponent(o.item_id||'')+'&affiliate_link='+encodeURIComponent(affiliate));
   const pd=await pr.json();
   if(pr.ok && pd.ok && Number(pd.price)>0){
    o.item_id=pd.item_id || o.item_id;
    o.price=Number(pd.price);
    if(pd.original_price) o.original_price=Number(pd.original_price);
    o.free_shipping=!!pd.free_shipping;
    if(pd.permalink) o.permalink=pd.permalink;
    if(pd.title) o.title=pd.title;
    if(pd.image) o.image=pd.image;
   }
  }catch(e){
   console.warn('Preço/item exato não pôde ser revalidado:',e);
  }
  window._offerData=window._offerData||{};
  window._offerData[id]=o;
  const p=new URLSearchParams({title:o.title,price:o.price,discount:o.discount,shipping_free:o.free_shipping?'1':'0',cupom:o.cupom?(o.cupom.code || o.cupom.label || ''):'',affiliate_link:affiliate});
  if(o.original_price)p.set('original_price',o.original_price);
  const r=await fetch('/api/gerar-anuncio?'+p);
  const d=await r.json();
  if(!r.ok || !d.anuncio) throw new Error(d.erro||'Não foi possível gerar o anúncio.');
  document.getElementById('ad_'+id).style.display='block';
  document.getElementById('ad_'+id).textContent=d.anuncio;
  document.getElementById('copy_'+id).style.display='block';
  document.getElementById('wa_'+id).style.display='block';
 }catch(e){
  alert('❌ '+e.message);
 }
}
async function enviarWhatsApp(id){
 const el=document.getElementById('ad_'+id);
 const text=el.textContent.trim();
 if(!text){alert('Gere o anúncio primeiro.');return;}
 const b=document.getElementById('wa_'+id);
 const old=b.textContent;
 b.disabled=true;
 b.textContent='⏳ Enviando...';
 try{
  const offer=(window._offerData&&window._offerData[id])||{};
  const image=String(offer.image||'');
  const affiliate=document.getElementById('aff_'+id).value.trim();
  const r=await fetch('/api/enviar-whatsapp',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,image,affiliate_link:affiliate})});
  const d=await r.json();
  if(!r.ok || !d.ok) throw new Error(d.erro||'O WhatsApp recusou o envio.');
  b.textContent='✅ Enviado para WhatsApp';
  setTimeout(()=>{b.textContent=old;b.disabled=false;},2500);
 }catch(e){
  b.disabled=false;
  b.textContent=old;
  alert('❌ '+e.message);
 }
}
async function copyAd(id){
 const el=document.getElementById('ad_'+id); const text=el.textContent.trim();
 if(!text){alert('Gere o anúncio primeiro.');return;}
 try{
  if(navigator.clipboard && window.isSecureContext){await navigator.clipboard.writeText(text);}
  else{
   const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';document.body.appendChild(ta);ta.focus();ta.select();document.execCommand('copy');ta.remove();
  }
  const b=document.getElementById('copy_'+id); const old=b.textContent; b.textContent='✅ Copiado!'; setTimeout(()=>b.textContent=old,1500);
 }catch(e){alert('Não foi possível copiar automaticamente. Selecione o texto do anúncio e copie.');}
}
function brl(v){return 'R$ '+Number(v||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}
function esc(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
</script></head><body><div class="container">
<div class="card"><h1>🛒 Caçador de Ofertas</h1>
<p>Encontra produtos de alto giro a partir de R$ 29,90. A conta do Mercado Livre permanece conectada automaticamente.</p>
{% if conectado %}<div class="status">🟢 Mercado Livre conectado{% if nickname %}<br><b>{{nickname}}</b>{% endif %}</div><a href="/mercadolivre/logout"><button>Desconectar</button></a>
{% else %}<a href="/mercadolivre/login"><button class="login">🔗 Conectar Mercado Livre</button></a>{% endif %}
</div>
<div class="card"><h2>🔥 Encontrar melhores produtos</h2><p class="small">Selecione uma categoria ou procure <b>todas de uma vez</b>. O sistema combina <b>categoria → subnicho → marca → produto</b> e só mostra o resultado quando a consulta terminar.</p><button class="cat" style="background:#3483fa;color:#fff;border:0;font-weight:bold" onclick="cacar('')">🔎 BUSCAR TODAS AS CATEGORIAS</button><div class="grid" style="margin-top:10px">{% for c in categorias %}<button class="cat" onclick="cacar({{c|tojson}})">{{c}}</button>{% endfor %}</div><p id="status" class="small">Escolha uma categoria ou use o botão acima para buscar todas.</p></div>
<div class="card"><h2>🔎 Busca manual</h2><input id="q" placeholder="Digite uma categoria ou produto: Tecnologia, Moda, Tênis, Academia, Perfumes..."><button onclick="buscar()">Procurar</button></div>
<div class="card"><h2>📊 Resultado</h2><div id="stats" class="stats"></div></div>
<div class="card"><h2>🏆 Melhores oportunidades</h2><p class="small">A busca principal combina vários micro-nichos da categoria e usa a API do Mercado Livre. Os cupons ficam em um módulo separado para não deixar a atualização dos produtos lenta nem aplicar descontos que não foram confirmados.</p><div id="results"><p>Faça uma busca para começar.</p></div></div>
<div class="card"><a href="/afiliado/portal">📲 Central de Afiliados</a><br><br><a href="/afiliado/gerador">🔗 Ferramentas oficiais de afiliado</a><br><br><a href="/api/cupons?atualizar=1" target="_blank">🎟️ Atualizar/consultar cupons</a><br><br><a href="/mercadolivre/diagnostico" target="_blank">🧪 Diagnóstico Mercado Livre</a></div>
</div><script>window.addEventListener('load',()=>{processarRetornoAfiliado();});</script></body></html>
"""

@app.route("/")
def index():
    t=tokens()
    return render_template_string(HTML, conectado=bool(access_token()), nickname=t.get("nickname") if t else None, categorias=list(CATALOG.keys()))

@app.route("/buscar")
def buscar_page():
    q = request.args.get("q", "").strip()
    if not q:
        return redirect("/")

    category, category_queries = _manual_queries_for_category(q)
    queries = category_queries if category and category_queries else [q]

    resultado = scan_queries(
        queries,
        request.args.get("desconto", 0),
        apply_coupons=True
    )

    t = tokens()
    return render_template_string(
        HTML,
        conectado=bool(access_token()),
        nickname=t.get("nickname") if t else None,
        categorias=list(CATALOG.keys()),
        resultado=resultado
    )

@app.route("/cupons")
def coupons_page():
    return jsonify({"cupons":json_safe(coupons()),"fontes":COUPON_SOURCE_URLS})

@app.route("/health")
def health():
    return jsonify({
        "status":"ok","app":"Cacador de Ofertas",
        "mercado_livre_conectado":bool(access_token()),
        "catalogo_categorias":len(CATALOG),
        "fluxo":"products/{product_id}/items",
        "cupons":"separado","cupom_por_produto":"separado","cupom_primeiro":"não aplicado na busca rápida","produto_minimo":MIN_PRODUCT_PRICE,"gerador_anuncio":"ativo",
        "produtos_alto_giro":"ativo","link_afiliado":"gerador_oficial",
        "meta_busca_ofertas": SEARCH_TARGET_OFFERS,
        "candidatos_por_categoria": SEARCH_CANDIDATES_PER_CATEGORY_ALL,
    })

@app.errorhandler(404)
def e404(e): return jsonify({"erro":"Rota não encontrada.","rota":request.path}),404

@app.errorhandler(500)
def e500(e): return jsonify({"erro":"Erro interno no servidor.","detalhes":str(e)}),500


# ============================================================
# TESTE EXTRA — /users/{USER_ID}/items/search
# ============================================================

@app.route("/mercadolivre/teste-user-items")
def teste_user_items():
    """
    Testa o endpoint de itens do vendedor usando o token já existente.
    Não altera a lógica normal do aplicativo.
    """
    try:
        token = access_token()
    except Exception as exc:
        return jsonify({
            "ok": False,
            "erro_token": repr(exc),
        }), 500

    if not token:
        return jsonify({
            "ok": False,
            "erro": "Nenhum access token disponível. Conecte o Mercado Livre primeiro.",
        }), 401

    user_id = "204115657"
    url = f"{ML_API}/users/{user_id}/items/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        r = requests.get(
            url,
            headers=headers,
            params={"limit": 10},
            timeout=20,
        )

        try:
            data = r.json()
        except Exception:
            data = {"texto": r.text[:2000]}

        return jsonify({
            "teste": f"GET /users/{user_id}/items/search",
            "user_id": user_id,
            "status_http": r.status_code,
            "ok": r.ok,
            "resultado": data,
        }), 200

    except Exception as exc:
        return jsonify({
            "teste": f"GET /users/{user_id}/items/search",
            "ok": False,
            "erro": repr(exc),
        }), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")), debug=False)
