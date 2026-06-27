import asyncio
import os
import re
import hashlib
from urllib.parse import urljoin, urlparse
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# --- НАСТРОЙКИ ---
BASE_URL = "https://www.site.ru"
BASE_DOMAIN = "site.ru" 
START_URL = f"{BASE_URL}/main" 

# 1. Папка сохранения
parsed_base = urlparse(BASE_URL)
OUTPUT_DIR = parsed_base.netloc

MAX_PAGES = 500 
CONCURRENT_TABS = 5  
# -----------------

# Экранируем домен для безопасного использования в регулярном выражении
escaped_domain = re.escape(BASE_DOMAIN)

URL_REGEX = re.compile(
    rf'((?:https?:)?//(?:[a-z0-9-]+\.)*tildacdn\.com/[^\s"\'<>\)]+|'
    rf'(?:https?:)?//(?:[a-z0-9-]+\.)*{escaped_domain}/[^\s"\'<>\)]+)'
)

EXT_MAP = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 
    'image/svg+xml': '.svg', 'image/gif': '.gif',
    'text/css': '.css', 'application/javascript': '.js', 
    'font/woff2': '.woff2', 'font/woff': '.woff', 'application/x-font-woff': '.woff'
}

def is_api_or_external(url):
    exclude = ['api.tildacdn', 'forms.tildacdn', 'rec.tildacdn', 'tilda.cc', 
               'vk.com', 'facebook', 'instagram', 'telegram', 'mailto:', 'tel:', 
               'yandex.ru/metrika', 'mc.yandex.ru', 'google-analytics']
    return any(x in url for x in exclude)

def sanitize_filename(name):
    name = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name)
    name = name.rstrip('. ')
    if len(name) > 100:
        name = name[:100] + "_" + hashlib.md5(name.encode()).hexdigest()[:8]
    return name

def url_to_local_path(url):
    parsed = urlparse(url)
    path = parsed.path
    
    if not path or path == '/':
        filename = "index.html"
        if parsed.query: filename = f"index_{sanitize_filename(parsed.query)}.html"
        return filename
    
    is_dir = path.endswith('/')
    path = path.strip('/')
    parts = path.split('/')
    safe_parts = [sanitize_filename(p) or "_" for p in parts]
        
    if is_dir:
        safe_parts.append("index.html")
    else:
        filename = safe_parts[-1]
        name, ext = os.path.splitext(filename)
        if not ext: filename = name + ".html"
        if parsed.query: filename = f"{name}_{sanitize_filename(parsed.query)}.html"
        safe_parts[-1] = filename
        
    return os.path.join(*safe_parts)

async def download_asset(url, page, visited_assets):
    if url in visited_assets:
        return visited_assets[url]
    
    clean_url = url if url.startswith('http') else 'https:' + url
    parsed = urlparse(clean_url)
    
    domain = parsed.netloc
    path = parsed.path.lstrip('/')
    if not path: path = "index"
    
    parts = path.split('/')
    safe_parts = [sanitize_filename(p) or "_" for p in parts]
    
    filename = safe_parts[-1]
    name, ext = os.path.splitext(filename)
    
    safe_path = os.path.join(*safe_parts) if len(safe_parts) > 1 else safe_parts[0]
    local_rel_path = os.path.join("assets", domain, safe_path)
    full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)
    
    try:
        resp = await page.request.get(clean_url)
        if not resp.ok: return url
        content_bytes = await resp.body()
        content_type = resp.headers.get('content-type', '').split(';')[0].strip()
    except Exception:
        return url
        
    if not ext:
        ext = EXT_MAP.get(content_type, '')
        filename = name + ext
        safe_parts[-1] = filename
        safe_path = os.path.join(*safe_parts) if len(safe_parts) > 1 else safe_parts[0]
        local_rel_path = os.path.join("assets", domain, safe_path)
        full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)
        
    if len(filename) > 100:
        filename = hashlib.md5(name.encode()).hexdigest() + ext
        safe_parts[-1] = filename
        safe_path = os.path.join(*safe_parts) if len(safe_parts) > 1 else safe_parts[0]
        local_rel_path = os.path.join("assets", domain, safe_path)
        full_local_path = os.path.join(OUTPUT_DIR, local_rel_path)

    if 'css' in content_type or '.css' in local_rel_path:
        css_text = content_bytes.decode('utf-8', errors='ignore')
        css_urls = set(URL_REGEX.findall(css_text))
        css_dir = os.path.dirname(local_rel_path)
        if not css_dir: css_dir = "."
        
        for css_url in css_urls:
            if is_api_or_external(css_url): continue
            inner_asset_path = await download_asset(css_url, page, visited_assets)
            if inner_asset_path and inner_asset_path != css_url:
                rel_to_css = os.path.relpath(inner_asset_path, css_dir).replace('\\', '/')
                css_text = css_text.replace(css_url, rel_to_css)
        content_bytes = css_text.encode('utf-8')

    dir_to_create = os.path.dirname(full_local_path)
    if dir_to_create: os.makedirs(dir_to_create, exist_ok=True)
        
    with open(full_local_path, 'wb') as f:
        f.write(content_bytes)
        
    visited_assets[url] = local_rel_path
    visited_assets[clean_url] = local_rel_path
    return local_rel_path

async def process_page(page, url, visited_pages, queue, queued_pages, visited_assets):
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
        
        for a in soup.find_all('a', href=True):
            href = a['href']
            abs_url = urljoin(url, href).split('#')[0] 
            if BASE_DOMAIN in abs_url and abs_url not in visited_pages and abs_url not in queued_pages and not is_api_or_external(abs_url):
                queued_pages.add(abs_url)
                await queue.put(abs_url)

        current_local_path = url_to_local_path(url)
        current_dir = os.path.dirname(current_local_path)
        if not current_dir: current_dir = "."

        asset_urls = set(URL_REGEX.findall(original_html))
        replacements = {}
        for asset_url in asset_urls:
            if is_api_or_external(asset_url): continue
            
            clean_asset_url = asset_url if asset_url.startswith('http') else 'https:' + asset_url
            parsed_asset = urlparse(clean_asset_url)
            if BASE_DOMAIN in parsed_asset.netloc:
                ext = os.path.splitext(parsed_asset.path)[1].lower()
                allowed_extensions = ['.css', '.js', '.jpg', '.jpeg', '.png', '.webp', '.svg', '.gif', '.woff', '.woff2', '.ttf', '.eot', '.mp4', '.webm', '.pdf', '.ico']
                if ext not in allowed_extensions:
                    continue

            local_path_root_rel = await download_asset(asset_url, page, visited_assets)
            if local_path_root_rel and local_path_root_rel != asset_url:
                rel_to_html = os.path.relpath(local_path_root_rel, current_dir).replace('\\', '/')
                replacements[asset_url] = rel_to_html
                
        final_html = original_html
        for orig, local in replacements.items():
            final_html = final_html.replace(orig, local)
            
        soup = BeautifulSoup(final_html, 'html.parser')
        
        # --- ИСПРАВЛЕНИЕ INLINE-СТИЛЕЙ (background-image) ---
        for tag in soup.find_all(style=True):
            style_val = tag['style']
            urls_in_style = re.findall(r'url\(["\']?(.*?)["\']?\)', style_val)
            for bg_url in urls_in_style:
                if not bg_url or 'data:' in bg_url: continue
                abs_bg_url = urljoin(url, bg_url).split('#')[0]
                if BASE_DOMAIN in abs_bg_url or 'tildacdn.com' in abs_bg_url:
                    if not is_api_or_external(abs_bg_url):
                        local_path_root_rel = await download_asset(abs_bg_url, page, visited_assets)
                        if local_path_root_rel and local_path_root_rel != abs_bg_url:
                            rel_to_html = os.path.relpath(local_path_root_rel, current_dir).replace('\\', '/')
                            style_val = style_val.replace(bg_url, rel_to_html)
            tag['style'] = style_val

        # --- МАГИЯ ДЛЯ TILDA: Чиним кликабельные карточки ---
        card_regex = re.compile(r"t-feed__[a-zA-Z0-9\-_]+post-wrapper|t-card__wrapper|t-product__wrapper")
        img_regex = re.compile(r"t-feed__post-imgwrapper|t-bgimg|t-card__imgwrapper|t-product__imgwrapper")
        
        for card in soup.find_all(class_=card_regex):
            link = card.find('a', href=True)
            if link and link.get('href'):
                href = link['href']
                img_blocks = card.find_all(class_=img_regex)
                for img_block in img_blocks:
                    if not img_block.find_parent('a'):
                        new_a = soup.new_tag('a', href=href, style="display:block; text-decoration:none; width:100%; height:100%;")
                        img_block.wrap(new_a)

        # --- ИСПРАВЛЕНИЕ ВНУТРЕННИХ ССЫЛОК (<a>) ---
        for a in soup.find_all('a', href=True):
            href = a['href']
            if href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                continue
            abs_href = urljoin(url, href).split('#')[0] 
            if BASE_DOMAIN in abs_href and not is_api_or_external(abs_href):
                target_path = url_to_local_path(abs_href)
                rel_to_html = os.path.relpath(target_path, current_dir).replace('\\', '/')
                anchor = "#" + href.split("#", 1)[1] if "#" in href else ""
                a['href'] = rel_to_html + anchor

        # --- LAZY LOAD (data-original) ---
        for el in soup.find_all(attrs={"data-original": True}):
            local_url = el['data-original']
            if local_url.startswith('http') or local_url.startswith('//'):
                if not is_api_or_external(local_url):
                    local_path_root_rel = await download_asset(local_url, page, visited_assets)
                    if local_path_root_rel and local_path_root_rel != local_url:
                        rel_to_html = os.path.relpath(local_path_root_rel, current_dir).replace('\\', '/')
                        local_url = rel_to_html
            
            if el.name == 'img': 
                el['src'] = local_url 
            else: 
                current_style = el.get('style', '')
                if 'background-image' not in current_style:
                    el['style'] = f"background-image: url('{local_url}'); {current_style}"
                
        final_html = str(soup)

        filepath = os.path.join(OUTPUT_DIR, current_local_path)
        dir_to_create = os.path.dirname(filepath)
        if dir_to_create: os.makedirs(dir_to_create, exist_ok=True)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(final_html)
        print(f"✅ Сохранено: {filepath}")

    except Exception as e:
        print(f"❌ Ошибка на {url}: {e}")

async def worker(worker_id, queue, context, visited_pages, queued_pages, visited_assets):
    while True:
        url = await queue.get()
        if url is None: break
            
        if url in visited_pages:
            queue.task_done()
            continue
            
        visited_pages.add(url)
        page = await context.new_page()
        
        await page.route("**/*", lambda route: route.abort() if any(x in route.request.url for x in [
            "mc.yandex.ru", "google-analytics", "googletagmanager", "facebook", "vk.com", "tilda.cc", "stat.tildacdn"
        ]) else route.continue_())
        
        try:
            if len(visited_pages) <= MAX_PAGES:
                await process_page(page, url, visited_pages, queue, queued_pages, visited_assets)
        except Exception as e:
            print(f"❌ Критическая ошибка в потоке {worker_id}: {e}")
        finally:
            await page.close()
            queue.task_done()

async def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        queue = asyncio.Queue()
        await queue.put(START_URL)
        
        visited_pages = set()
        queued_pages = set([START_URL])
        visited_assets = {} 
        
        print(f"🚀 Запускаем {CONCURRENT_TABS} параллельных потоков...")
        workers = [asyncio.create_task(worker(i, queue, context, visited_pages, queued_pages, visited_assets)) for i in range(CONCURRENT_TABS)]
        
        await queue.join()
        
        for _ in range(CONCURRENT_TABS):
            await queue.put(None)
        await asyncio.gather(*workers)
        
        await browser.close()
        print(f"\n🎉 Готово! Скачано страниц: {len(visited_pages)}. Папка: {OUTPUT_DIR}")

    # --- ГЕНЕРАЦИЯ HTML-ЗАСТАВКИ ---
    start_filename = url_to_local_path(START_URL).replace('\\', '/')
    
    # 2. Формируем динамическое имя для Лаунчера на основе START_URL
    parsed_start = urlparse(START_URL)
    start_path_str = parsed_start.path.strip('/').replace('/', '_')
    if parsed_start.query:
        start_path_str += "_" + sanitize_filename(parsed_start.query)
    
    launcher_filename = f"!Запуск_{parsed_start.netloc}_{start_path_str}.html"

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
