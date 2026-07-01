# ==============================================================================
# ИМПОРТ НЕОБХОДИМЫХ БИБЛИОТЕК
# ==============================================================================
import asyncio          # Для асинхронного выполнения, управления очередями и пулом воркеров
import os               # Для работы с файловой системой (создание папок, сохранение файлов)
import re               # Для регулярных выражений (поиск URL в CSS, фильтрация, паттерны Tilda)
import hashlib          # Для генерации коротких хешей, если имя файла слишком длинное
import urllib.parse     # Для декодирования URL (из %20 в пробел и т.д.) и работы с query-параметрами
from urllib.parse import urljoin, urlparse # Для склеивания относительных ссылок и разбора URL на части
from playwright.async_api import async_playwright # Асинхронный браузер для рендеринга JS и скачивания
from bs4 import BeautifulSoup                 # Парсер HTML для поиска тегов, атрибутов и модификации DOM

# ==============================================================================
# --- БЛОК НАСТРОЕК (КОНФИГУРАЦИЯ) ---
# ==============================================================================

# Стартовый URL, с которого начинается парсинг
START_URL = "https://www.your.site/page1"

_parsed_start_url = urlparse(START_URL)

# Нормализация домена: убираем www. и приводим к нижнему регистру.
# Это критически важно, чтобы ссылки вида https://your.site и https://www.your.site
# считались одним сайтом, сохранялись в одну папку и корректно переписывались в локальные.
_start_netloc = _parsed_start_url.netloc.lower()
if _start_netloc.startswith('www.'):
    _start_netloc = _start_netloc[4:]

AUTO_BASE_URL = f"{_parsed_start_url.scheme}://{_start_netloc}"

# Белые списки URL. Парсер будет скачивать только те страницы, которые начинаются с этих путей.
BASE_URLS = [
     AUTO_BASE_URL, # Раскомментируйте, чтобы скачивать весь домен
#    START_URL,       # Скачивать только указанный раздел и его подразделы
]

# Черные списки URL. Парсер проигнорирует эти страницы, даже если найдет ссылки на них.
BLOCK_URLS = [
#    "https://www.your.site/page3",
]

# Формирование имени папки, в которую будет сохранен сайт.
# Пример: your.site_page1 (без www, чтобы избежать путаницы)
_path_part = urllib.parse.unquote(_parsed_start_url.path).strip('/').replace('/', '_')
OUTPUT_DIR = f"{_start_netloc}_{_path_part}" if _path_part else _start_netloc

# Ограничения и настройки поведения парсера
MAX_PAGES = 500                 # Максимальное количество HTML-страниц для скачивания
CONCURRENT_TABS = 5             # Количество одновременно открытых вкладок в браузере (потоков)
DELAY_BETWEEN_REQUESTS = 0.2    # Задержка в секундах между запросами (имитация живого человека)

# Домены, которые нужно игнорировать (аналитика, соцсети, трекеры, рекламные сети).
# ВНИМАНИЕ: Сюда НЕ добавлены функциональные CDN (шрифты, карты, Tilda CDN), 
# иначе сайт останется без стилей, картинок и интерактивных элементов.
EXCLUDE_DOMAINS = [
    # --- Аналитика и Трекеры ---
    'mc.yandex.ru', 'yandex.ru/metrika', 
    'google-analytics.com', 'googletagmanager.com',
    'hotjar.com', 'mixpanel.com', 'amplitude.com', 'segment.com', 'clarity.ms', 'bing.com/bat',
    'api.vigo.tech', # Пуш-уведомления и аналитика Vigo
    
    # --- Реклама и RTB-сети ---
    'doubleclick.net', 'adsrvr.org', 'adform.net', 'criteo.com', 'outbrain.com', 'taboola.com',
    'ep2.adtrafficquality.google', # Google Ad Traffic Quality
    'pic.rtbcdn.ru', # CDN рекламных сетей (RTB)
    
    # --- Соцсети и виджеты (с трекерами) ---
    'facebook.com', 'facebook.net', 'vk.com', 'instagram.com', 'telegram.org', 't.me',
    'mail.ru', 'ok.ru', 'twitter.com', 'x.com', 'linkedin.com', 'pinterest.com', 'tiktok.com',
    'usocial.pro', # Виджеты соцсетей и SMM
    
    # --- Служебные системы Tilda (аналитика, спам-защита, формы) ---
    'tilda.cc', 'api.tildacdn.com', 'forms.tildacdn.com', 'rec.tildacdn.com', 'stat.tildacdn.com',
]

# Расширения файлов, которые считаются HTML-страницами
PAGE_EXTENSIONS = ['.html', '.htm', '.php', '.asp', '.aspx']

# Расширения файлов, которые считаются статическими ассетами (картинки, документы, стили, скрипты)
ASSET_EXTENSIONS = [
    '.pdf', '.zip', '.rar', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
    '.mp3', '.wav', '.mp4', '.webm', '.jpg', '.jpeg', '.png', '.gif', '.svg', 
    '.webp', '.css', '.js', '.woff', '.woff2', '.ttf', '.eot'
]

# Словарь для определения расширения файла по его MIME-типу (если в URL нет расширения)
MIME_TO_EXT_MAP = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 
    'image/svg+xml': '.svg', 'image/gif': '.gif',
    'text/css': '.css', 'application/javascript': '.js', 
    'font/woff2': '.woff2', 'font/woff': '.woff', 'application/x-font-woff': '.woff',
    'font/ttf': '.ttf', 'application/octet-stream': '.bin',
    'video/mp4': '.mp4', 'video/webm': '.webm', 'audio/mpeg': '.mp3',
    'application/pdf': '.pdf', 'application/zip': '.zip'
}

# Протоколы, которые не нужно скачивать (якоря, почта, телефоны, base64-картинки)
IGNORED_PROTOCOLS = ('mailto:', 'tel:', 'javascript:', 'data:', 'blob:')
IGNORED_HREF_PROTOCOLS = ('mailto:', 'tel:', 'javascript:', 'data:', 'blob:', '#')

# Атрибуты HTML-тегов, в которых могут скрываться ссылки на ассеты.
# Включены специфичные атрибуты для Tilda и плагинов зума (data-img-zoom-url и т.д.)
URL_ATTRIBUTES = [
    'src', 'href', 'poster', 'data-original', 'data-src', 'data-bg', 
    'data-image', 'data-lazy-src', 'data-img-zoom-url', 'data-zoom-image', 
    'data-large-image', 'data-full', 'action', 'cite', 'background', 
    'longdesc', 'usemap', 'dynsrc'
]

# Регулярное выражение для поиска url('...') внутри CSS-файлов и inline-стилей
CSS_URL_REGEX = re.compile(r'url\(["\']?(.*?)["\']?\)')

# Регулярные выражения для поиска специфичных блоков Tilda (карточки товаров, постов)
TILDA_CARD_REGEX = re.compile(r"t-feed__[a-zA-Z0-9\-_]+post-wrapper|t-card__wrapper|t-product__wrapper")
TILDA_IMG_REGEX = re.compile(r"t-feed__post-imgwrapper|t-bgimg|t-card__imgwrapper|t-product__imgwrapper")

# Домены Tilda CDN. Нужны для фикса "сломанных" относительных путей, которые генерирует Tilda
TILDA_CDN_DOMAINS = ['static.tildacdn.com', 'optim.tildacdn.com', 'static3.tildacdn.com', 'neo.tildacdn.com', 'tildacdn.com']

# ==============================================================================
# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
# ==============================================================================

def fix_tilda_relative_url(url, base_url):
    """
    Исправляет специфичные для Tilda "сломанные" относительные пути.
    Tilda иногда генерирует пути вида `static.tildacdn.com/...` без протокола.
    """
    try:
        parsed = urlparse(url)
        base_netloc = urlparse(base_url).netloc
        if parsed.netloc and parsed.netloc == base_netloc:
            path_parts = parsed.path.strip('/').split('/')
            if path_parts and any(tilda_domain in path_parts[0] for tilda_domain in TILDA_CDN_DOMAINS):
                new_domain = path_parts[0]
                new_path = '/'.join(path_parts[1:])
                return f"https://{new_domain}/{new_path}"
    except Exception:
        pass
    return url

def safe_urljoin(base, url):
    """Безопасное склеивание относительных и абсолютных URL с последующим фиксом багов Tilda."""
    abs_url = urljoin(base, url)
    return fix_tilda_relative_url(abs_url, base)

def is_excluded(url):
    """Проверяет, находится ли URL в черном списке доменов или использует игнорируемый протокол."""
    if not url: return True
    if url.startswith(IGNORED_PROTOCOLS): return True
    return any(excl in url for excl in EXCLUDE_DOMAINS)

def is_base_url(url):
    """
    Проверяет, принадлежит ли URL к разрешенным базовым путям (белый список).
    Игнорирует наличие www. и регистр, чтобы корректно определять внутренние ссылки.
    """
    if not url.startswith('http'): return False
    parsed_url = urlparse(url)
    
    url_netloc = parsed_url.netloc.lower()
    if url_netloc.startswith('www.'):
        url_netloc = url_netloc[4:]
        
    for base in BASE_URLS:
        parsed_base = urlparse(base)
        base_netloc = parsed_base.netloc.lower()
        if base_netloc.startswith('www.'):
            base_netloc = base_netloc[4:]
            
        if url_netloc == base_netloc:
            url_path = parsed_url.path.rstrip('/')
            base_path = parsed_base.path.rstrip('/')
            if url_path.startswith(base_path):
                return True
    return False

def is_blocked_url(url):
    """Проверяет, находится ли URL в жестком черном списке (BLOCK_URLS)."""
    if not url.startswith('http'): return False
    return any(url.startswith(block) for block in BLOCK_URLS)

def should_download_html(url):
    """Комплексная проверка: нужно ли скачивать эту страницу как HTML."""
    if not url.startswith('http'): return False
    if is_excluded(url): return False
    if is_blocked_url(url): return False
    if not is_base_url(url): return False
    return True

def sanitize_filename(name, is_query=False):
    """
    Очищает строку, чтобы её можно было использовать как имя файла или папки.
    Удаляет недопустимые символы ОС, ограничивает длину и добавляет хеш.
    """
    if is_query:
        name = urllib.parse.unquote_plus(name)
        name = re.sub(r'[<>:"/\\|?*=&\x00-\x1F]', "_", name)
    else:
        name = urllib.parse.unquote(name)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
        
    name = name.rstrip('. ')
    if len(name) > 100:
        name = name[:100] + "_" + hashlib.md5(name.encode('utf-8')).hexdigest()[:8]
    return name

def _parse_url_parts(url):
    """
    Разбирает URL на домен и путь. 
    ВАЖНО: Нормализует домен (убирает www.), чтобы все файлы сайта лежали в одной структуре папок.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    if domain.startswith('www.'):
        domain = domain[4:]
        
    try:
        domain = domain.encode('utf-8').decode('idna') # Поддержка кириллических доменов
    except Exception:
        pass
    domain = sanitize_filename(domain)
    path = urllib.parse.unquote(parsed.path).strip('/')
    return parsed, domain, path

def get_local_path_for_html(url):
    """Генерирует локальный путь для сохранения HTML-страницы с учетом query-параметров."""
    parsed, domain, path = _parse_url_parts(url)
    
    if not path:
        local_path = "index.html"
        if parsed.query: local_path = f"index_{sanitize_filename(parsed.query, is_query=True)}.html"
        return os.path.join(domain, local_path)
        
    parts = path.split('/')
    safe_parts = [sanitize_filename(p) or "_" for p in parts]
    
    filename = safe_parts[-1]
    name, ext = os.path.splitext(filename)
    
    if ext.lower() in PAGE_EXTENSIONS:
        if parsed.query:
            filename = f"{name}_{sanitize_filename(parsed.query, is_query=True)}{ext}"
            safe_parts[-1] = filename
    else:
        safe_parts.append("index.html")
        if parsed.query:
            safe_parts[-1] = f"index_{sanitize_filename(parsed.query, is_query=True)}.html"
            
    return os.path.join(domain, *safe_parts)

def get_local_path_for_asset(url):
    """Генерирует локальный путь для сохранения ассета (картинки, CSS, PDF и т.д.)."""
    parsed, domain, path = _parse_url_parts(url)
    
    if not path:
        return os.path.join(domain, "index.bin")
        
    parts = path.split('/')
    safe_parts = [sanitize_filename(p) or "_" for p in parts]
    
    return os.path.join(domain, *safe_parts)

def extract_asset_urls(soup, base_url):
    """
    Извлекает ВСЕ возможные ссылки на ассеты из HTML-кода страницы.
    Проверяет стандартные атрибуты, srcset, inline-стили и ссылки <a> на файлы.
    """
    urls = set()
    
    for tag in soup.find_all():
        for attr in URL_ATTRIBUTES:
            if tag.has_attr(attr):
                if tag.name == 'a' and attr == 'href':
                    continue 
                val = tag[attr]
                if isinstance(val, list): continue
                if val.startswith(IGNORED_PROTOCOLS): continue
                urls.add(safe_urljoin(base_url, val))
                
    for tag in soup.find_all(srcset=True):
        for part in tag['srcset'].split(','):
            url = part.strip().split(' ')[0]
            if url and not url.startswith(IGNORED_PROTOCOLS): 
                urls.add(safe_urljoin(base_url, url))
                
    for tag in soup.find_all(style=True):
        style_val = tag['style']
        urls_in_style = CSS_URL_REGEX.findall(style_val)
        for bg_url in urls_in_style:
            if not bg_url or bg_url.startswith('data:') or bg_url.startswith('blob:'): continue
            urls.add(safe_urljoin(base_url, bg_url))

    for a in soup.find_all('a', href=True):
        ext = os.path.splitext(urlparse(a['href']).path)[1].lower()
        if ext in ASSET_EXTENSIONS:
            urls.add(safe_urljoin(base_url, a['href']))
            
    return urls

async def download_asset(url, page, visited_assets, queue, visited_pages, queued_pages, visiting_assets=None):
    """
    Асинхронная функция скачивания ассетов.
    - Рекурсивно скачивает CSS и парсит их на наличие шрифтов/картинок.
    - Использует заголовки Referer для обхода защиты Tilda CDN от хотлинкинга.
    """
    if not url or url.startswith(IGNORED_PROTOCOLS):
        return url
        
    if visiting_assets is None:
        visiting_assets = set()
        
    if url in visited_assets:
        return visited_assets[url]
        
    if url in visiting_assets:
        return url 
        
    visiting_assets.add(url)
    
    clean_url = url if url.startswith('http') else 'https:' + url
    if is_excluded(clean_url):
        visiting_assets.discard(url)
        return url
        
    local_rel_path = get_local_path_for_asset(clean_url)
    full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)
    
    try:
        # Добавляем Referer для обхода 403 Forbidden на CDN
        headers = {
            "Referer": START_URL, 
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = await page.request.get(clean_url, headers=headers)
        if not resp.ok: 
            visiting_assets.discard(url)
            return url
        content_bytes = await resp.body()
        content_type = resp.headers.get('content-type', '').split(';')[0].strip().lower()
    except Exception:
        visiting_assets.discard(url)
        return url
        
    if 'html' in content_type:
        html_path = get_local_path_for_html(clean_url)
        visited_assets[url] = html_path
        visited_assets[clean_url] = html_path
        
        if clean_url not in visited_pages and clean_url not in queued_pages:
            if len(visited_pages) + len(queued_pages) < MAX_PAGES:
                queued_pages.add(clean_url)
                await queue.put(clean_url)
                
        visiting_assets.discard(url)
        return html_path

    name, ext = os.path.splitext(local_rel_path)
    if not ext:
        new_ext = MIME_TO_EXT_MAP.get(content_type, '.bin')
        local_rel_path = name + new_ext
        full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)

    # Глубокий парсинг CSS-файлов
    if 'css' in content_type or local_rel_path.endswith('.css'):
        css_text = content_bytes.decode('utf-8', errors='ignore')
        css_urls = set(CSS_URL_REGEX.findall(css_text))
        css_dir = os.path.dirname(local_rel_path)
        if not css_dir: css_dir = "."
        
        for css_url in css_urls:
            if not css_url or css_url.startswith('data:') or css_url.startswith('blob:'): continue
            
            abs_css_url = safe_urljoin(clean_url, css_url)
            if is_excluded(abs_css_url): continue
            
            inner_asset_path = await download_asset(abs_css_url, page, visited_assets, queue, visited_pages, queued_pages, visiting_assets)
            if inner_asset_path and inner_asset_path != abs_css_url:
                rel_to_css = os.path.relpath(inner_asset_path, css_dir).replace('\\', '/')
                pattern = re.compile(r'url\(["\']?' + re.escape(css_url) + r'["\']?\)')
                css_text = pattern.sub(f"url('{rel_to_css}')", css_text)
        content_bytes = css_text.encode('utf-8')

    dir_to_create = os.path.dirname(full_local_path)
    if dir_to_create: os.makedirs(dir_to_create, exist_ok=True)
        
    with open(full_local_path, 'wb') as f:
        f.write(content_bytes)
        
    visited_assets[url] = local_rel_path
    visited_assets[clean_url] = local_rel_path
    visiting_assets.discard(url)
    return local_rel_path

async def process_page(page, url, visited_pages, queue, queued_pages, visited_assets):
    """
    Главная функция обработки HTML-страницы: рендеринг, скролл, парсинг ссылок, 
    скачивание ассетов, замена URL и инъекция JS.
    """
    print(f"🔍 Обработка: {url}")
    try:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                break 
            except Exception as e:
                if "Timeout" in str(e) and attempt < max_retries - 1:
                    print(f"⏳ Таймаут. Попытка {attempt + 2}/{max_retries}...")
                    await asyncio.sleep(3)
                else:
                    raise e 
        
        # Эмуляция скролла для срабатывания Lazy Load
        await page.evaluate("""
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 300;
                    const timer = setInterval(() => {
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if(totalHeight >= document.body.scrollHeight){
                            clearInterval(timer); resolve();
                        }
                    }, 100);
                });
            }
        """)
        await page.wait_for_timeout(1500) 
        
        original_html = await page.content()
        soup = BeautifulSoup(original_html, 'html.parser')
        
        # Поиск ссылок на другие страницы
        for a in soup.find_all('a', href=True):
            href = a['href']
            abs_url = safe_urljoin(url, href).split('#')[0] 
            ext = os.path.splitext(urlparse(abs_url).path)[1].lower()
            
            if ext not in ASSET_EXTENSIONS:
                if should_download_html(abs_url):
                    if abs_url not in visited_pages and abs_url not in queued_pages:
                        if len(visited_pages) + len(queued_pages) < MAX_PAGES:
                            queued_pages.add(abs_url)
                            await queue.put(abs_url)

        current_local_path = get_local_path_for_html(url)
        current_dir = os.path.dirname(current_local_path)
        if not current_dir: current_dir = "."

        asset_urls = extract_asset_urls(soup, url)
        replacements = {}
        
        for asset_url in asset_urls:
            if is_excluded(asset_url): continue
            
            local_path_root_rel = await download_asset(asset_url, page, visited_assets, queue, visited_pages, queued_pages)
            if local_path_root_rel and local_path_root_rel != asset_url:
                rel_to_html = os.path.relpath(local_path_root_rel, current_dir).replace('\\', '/')
                replacements[asset_url] = rel_to_html
                
                clean_asset_url = asset_url if asset_url.startswith('http') else 'https:' + asset_url
                if asset_url.startswith('http'):
                    no_proto = asset_url.replace('http:', '').replace('https:', '')
                    replacements[no_proto] = rel_to_html
                replacements[clean_asset_url] = rel_to_html
                
        soup = BeautifulSoup(original_html, 'html.parser')
        
        def process_url_value(val, base_url, replacements, visited_assets):
            if not val or val.startswith(IGNORED_PROTOCOLS) or val.startswith('#'):
                return val
                
            abs_val = safe_urljoin(base_url, val)
            
            if abs_val in replacements: return replacements[abs_val]
            if abs_val in visited_assets: return visited_assets[abs_val]
                
            if val.startswith('//'):
                abs_val_https = 'https:' + val
                if abs_val_https in replacements: return replacements[abs_val_https]
                if abs_val_https in visited_assets: return visited_assets[abs_val_https]
                
            no_proto = abs_val.replace('http:', '').replace('https:', '')
            if no_proto in replacements: return replacements[no_proto]
            
            return val

        for tag in soup.find_all():
            for attr in URL_ATTRIBUTES:
                if tag.has_attr(attr):
                    if tag.name == 'a' and attr == 'href':
                        continue 
                        
                    val = tag[attr]
                    if isinstance(val, list): continue
                        
                    tag[attr] = process_url_value(val, url, replacements, visited_assets)
                        
            if tag.has_attr('srcset'):
                srcset = tag['srcset']
                new_srcset = []
                for part in srcset.split(','):
                    part = part.strip()
                    if not part: continue
                    tokens = part.split()
                    if not tokens: continue
                    u = tokens[0]
                    rest = ' '.join(tokens[1:])
                    
                    new_u = process_url_value(u, url, replacements, visited_assets)
                        
                    if rest: new_srcset.append(f"{new_u} {rest}")
                    else: new_srcset.append(new_u)
                tag['srcset'] = ', '.join(new_srcset)

            if tag.has_attr('style'):
                style_val = tag['style']
                def replace_url_in_style(match):
                    full_match = match.group(0)
                    bg_url = match.group(1)
                    if not bg_url: return full_match
                    
                    new_bg = process_url_value(bg_url, url, replacements, visited_assets)
                    return f"url('{new_bg}')"
                    
                style_val = re.sub(CSS_URL_REGEX, replace_url_in_style, style_val)
                tag['style'] = style_val

        # Фикс кликабельности картинок в карточках Tilda
        for card in soup.find_all(class_=TILDA_CARD_REGEX):
            link = card.find('a', href=True)
            if link and link.get('href'):
                href = link['href']
                img_blocks = card.find_all(class_=TILDA_IMG_REGEX)
                for img_block in img_blocks:
                    if not img_block.find_parent('a'):
                        new_a = soup.new_tag('a', href=href, style="display:block; text-decoration:none; width:100%; height:100%;")
                        img_block.wrap(new_a)

        # Перенос lazy-load картинок в src
        for img in soup.find_all('img'):
            data_orig = img.get('data-original') or img.get('data-src') or img.get('data-lazy-src')
            if data_orig:
                img['src'] = data_orig

        # Обработка ссылок <a href>
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith(IGNORED_HREF_PROTOCOLS):
                continue
            
            abs_href = safe_urljoin(url, href)
            anchor = ""
            if "#" in abs_href:
                abs_href, anchor = abs_href.split("#", 1)
                anchor = "#" + anchor
                
            if abs_href in visited_assets:
                target_path = visited_assets[abs_href]
                rel_to_html = os.path.relpath(target_path, current_dir).replace('\\', '/')
                a['href'] = rel_to_html + anchor
                continue
                
            if should_download_html(abs_href):
                target_path = get_local_path_for_html(abs_href)
                rel_to_html = os.path.relpath(target_path, current_dir).replace('\\', '/')
                a['href'] = rel_to_html + anchor
                continue
                
            a['href'] = abs_href + anchor

        # Инъекция JS для починки зума картинок локально
        zoom_fix_js = """
<script>
(function() {
    document.addEventListener('click', function(e) {
        const img = e.target.closest('.t-zoomable, img[data-img-zoom-url], .t760__img');
        if (img && img.tagName === 'IMG') {
            e.preventDefault();
            e.stopPropagation();
            let zoomUrl = img.getAttribute('data-img-zoom-url') || img.getAttribute('src');
            if (!zoomUrl) return;
            const overlay = document.createElement('div');
            overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.95);z-index:999999;display:flex;justify-content:center;align-items:center;cursor:zoom-out;animation:fadeInZoom 0.3s ease-out;";
            const fullImg = document.createElement('img');
            fullImg.src = zoomUrl;
            fullImg.style.cssText = "max-width:90%;max-height:90%;object-fit:contain;box-shadow:0 10px 30px rgba(0,0,0,0.5);border-radius:4px;";
            overlay.appendChild(fullImg);
            overlay.onclick = function() { overlay.remove(); };
            document.body.appendChild(overlay);
        }
    }, true);
    const style = document.createElement('style');
    style.innerHTML = "@keyframes fadeInZoom { from { opacity: 0; } to { opacity: 1; } }";
    document.head.appendChild(style);
})();
</script>
"""
        if soup.find('body'):
            soup.find('body').append(BeautifulSoup(zoom_fix_js, 'html.parser'))

        final_html = str(soup)

        filepath = os.path.join(OUTPUT_DIR, current_local_path)
        dir_to_create = os.path.dirname(filepath)
        if dir_to_create: os.makedirs(dir_to_create, exist_ok=True)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f"✅ Сохранено: {filepath}")

    except Exception as e:
        print(f"❌ Ошибка на {url}: {e}")

async def worker(worker_id, page, queue, context, visited_pages, queued_pages, visited_assets):
    """Функция-воркер с сетевым перехватом для блокировки мусорного трафика."""
    await page.route("**/*", lambda route: route.abort() if is_excluded(route.request.url) else route.continue_())
    
    while True:
        url = await queue.get()
        if url is None: 
            queue.task_done()
            break
            
        if url in visited_pages:
            queue.task_done()
            continue
            
        visited_pages.add(url)
        
        try:
            await process_page(page, url, visited_pages, queue, queued_pages, visited_assets)
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)
        except Exception as e:
            print(f"❌ Критическая ошибка в потоке {worker_id}: {e}")
        finally:
            queue.task_done()

async def main():
    """Инициализация браузера, запуск воркеров и генерация лаунчера."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        pages = [await context.new_page() for _ in range(CONCURRENT_TABS)]
        
        queue = asyncio.Queue()
        await queue.put(START_URL)
        
        visited_pages = set()
        queued_pages = set([START_URL])
        visited_assets = {} 
        
        print(f"🚀 Запускаем {CONCURRENT_TABS} параллельных потоков...")
        workers = [
            asyncio.create_task(
                worker(i, pages[i], queue, context, visited_pages, queued_pages, visited_assets)
            ) for i in range(CONCURRENT_TABS)
        ]
        
        await queue.join()
        
        for _ in range(CONCURRENT_TABS):
            await queue.put(None)
        await asyncio.gather(*workers)
        
        await browser.close()
        print(f"\n🎉 Готово! Скачано страниц: {len(visited_pages)}. Папка: {OUTPUT_DIR}")

    start_filename = get_local_path_for_html(START_URL).replace('\\', '/')
    
    start_path_str = urllib.parse.unquote(_parsed_start_url.path).strip('/')
    start_path_str = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", start_path_str).replace('/', '_')
    if _parsed_start_url.query:
        start_path_str += "_" + sanitize_filename(_parsed_start_url.query, is_query=True)
    if not start_path_str:
        start_path_str = "index"
    
    launcher_filename = f"!Запуск_{_start_netloc}_{start_path_str}.html"

    launcher_html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Локальная копия сайта</title>
    <meta http-equiv="refresh" content="5;url={start_filename}">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            color: #333;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
            text-align: center;
        }}
        .container {{
            background: white;
            padding: 50px 60px;
            border-radius: 24px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.1);
            max-width: 650px;
            animation: fadeIn 0.8s ease-out;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(20px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
        h1 {{ color: #1a202c; margin-bottom: 15px; font-size: 32px; font-weight: 700; }}
        p {{ color: #4a5568; line-height: 1.7; font-size: 17px; margin-bottom: 30px; }}
        .btn {{
            display: inline-block;
            margin-top: 10px;
            padding: 18px 40px;
            background: linear-gradient(90deg, #4facfe 0%, #00f2fe 100%);
            color: white;
            text-decoration: none;
            border-radius: 12px;
            font-weight: 600;
            font-size: 18px;
            transition: all 0.3s ease;
            box-shadow: 0 8px 20px rgba(79, 172, 254, 0.3);
        }}
        .btn:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 25px rgba(79, 172, 254, 0.4);
        }}
        .countdown-wrapper {{
            margin-top: 35px;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 12px;
            color: #718096;
            font-size: 15px;
        }}
        .countdown-circle {{
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: #edf2f7;
            display: flex;
            justify-content: center;
            align-items: center;
            font-size: 24px;
            font-weight: bold;
            color: #2d3748;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.06);
            animation: pulse 1s infinite;
        }}
        @keyframes pulse {{
            0% {{ box-shadow: inset 0 2px 4px rgba(0,0,0,0.06), 0 0 0 0 rgba(79, 172, 254, 0.4); }}
            70% {{ box-shadow: inset 0 2px 4px rgba(0,0,0,0.06), 0 0 0 10px rgba(79, 172, 254, 0); }}
            100% {{ box-shadow: inset 0 2px 4px rgba(0,0,0,0.06), 0 0 0 0 rgba(79, 172, 254, 0); }}
        }}
        .footer {{
            margin-top: 45px;
            font-size: 13px;
            color: #a0aec0;
            border-top: 1px solid #e2e8f0;
            padding-top: 25px;
            line-height: 1.6;
        }}
        .footer a {{
            color: #4facfe;
            text-decoration: none;
            font-weight: 500;
        }}
        .footer a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📂 Локальная копия сайта</h1>
        <p>Сайт успешно сохранен на ваше устройство.<br>Все страницы, изображения и стили доступны полностью офлайн.</p>
        
        <a href="{start_filename}" class="btn">Открыть сайт ➔</a>
        
        <div class="countdown-wrapper">
            <span>Автоматический переход через</span>
            <div class="countdown-circle" id="countdown">5</div>
            <span>сек.</span>
        </div>
        
        <div class="footer">
            Сайт упакован для локального просмотра с помощью Python-скрипта<br>
            <a href="http://www.obana.info" target="_blank">www.obana.info</a>
        </div>
    </div>

    <script>
        let seconds = 5;
        const countdownEl = document.getElementById('countdown');
        const timer = setInterval(() => {{
            seconds--;
            if (countdownEl) countdownEl.textContent = seconds;
            if (seconds <= 0) {{
                clearInterval(timer);
                window.location.href = "{start_filename}";
            }}
        }}, 1000);
    </script>
</body>
</html>"""

    launcher_path = os.path.join(OUTPUT_DIR, launcher_filename)
    with open(launcher_path, 'w', encoding='utf-8') as f:
        f.write(launcher_html)
    print(f"🌐 Создана стартовая страница: {launcher_path}")

if __name__ == "__main__":
    asyncio.run(main())
