"""
TMDB 电影数据爬虫 — 爬取电影封面、演员、导演信息并写入数据库

使用 TMDB (The Movie Database) 免费 API 获取电影元数据。
首次运行通过标题搜索匹配 -> 缓存 tmdb_id 到 movies 表 -> 后续直查详情。

功能：
1. 从 TMDB 热门电影列表拉取前 2 页（约 40 部）
2. 对每部电影获取：封面图、演员（前 8 名）、导演
3. 通过标题 + 年份模糊匹配数据库中的电影
4. 匹配成功后将 tmdb_id 缓存到 movies 表，后续运行免搜索
5. 补充推荐列表中缺少封面/演员/导演信息的电影

前置条件：
  pip install requests pymysql
  TMDB API Key: https://www.themoviedb.org/settings/api

用法:
  python scripts/crawl/crawl_imdb.py --api-key YOUR_KEY [--proxy http://127.0.0.1:7890]
  python scripts/crawl/crawl_imdb.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --chart-only
  python scripts/crawl/crawl_imdb.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --all
  python scripts/crawl/crawl_imdb.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --dry-run
"""

import os
import sys
import re
import time

try:
    import pymysql
except ImportError:
    print("请安装 pymysql: pip install pymysql"); sys.exit(1)
try:
    import requests
except ImportError:
    print("请安装 requests: pip install requests"); sys.exit(1)

# ============================================================
# 配置
# ============================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', '192.168.1.38'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'user': os.environ.get('DB_USER', 'newuser'),
    'password': os.environ.get('DB_PASSWORD', 'yourpassword'),
    'database': os.environ.get('DB_NAME', 'MovieRecommendSystem'),
    'charset': 'utf8mb4',
}

TMDB_API_KEY = os.environ.get('TMDB_API_KEY', '')
TMDB_BASE = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/w500'

TMDB_CHART_PAGES = 2
MAX_ACTORS = 8
REQUEST_DELAY = 0.25
SEARCH_TIMEOUT = 15

HTTP_PROXY = None

# ============================================================
# CLI & config
# ============================================================

def check_api_key(api_key=None):
    global TMDB_API_KEY
    if api_key:
        TMDB_API_KEY = api_key
    if not TMDB_API_KEY:
        print("=" * 60)
        print("错误: 未设置 TMDB_API_KEY")
        print()
        print("获取免费 API Key:")
        print("  1. 注册: https://www.themoviedb.org/signup")
        print("  2. 获取: https://www.themoviedb.org/settings/api")
        print("  3. 使用: python scripts/crawl/crawl_imdb.py --api-key YOUR_KEY")
        print("=" * 60)
        sys.exit(1)

# ============================================================
# 数据库工具
# ============================================================

class DB:
    def __init__(self, config):
        self.conn = pymysql.connect(**config)
        self.cursor = self.conn.cursor()

    def query(self, sql, params=None):
        self.cursor.execute(sql, params or ())
        return self.cursor.fetchall()

    def execute(self, sql, params=None):
        self.cursor.execute(sql, params or ())
        self.conn.commit()
        return self.cursor.lastrowid

    def close(self):
        self.cursor.close()
        self.conn.close()


def get_db_movies(db, limit=2000):
    return db.query(
        'SELECT id, title, release_year, cover_url, tmdb_id FROM movies '
        'ORDER BY id ASC LIMIT %s', (limit,)
    )


def get_movie_by_id(db, movie_id):
    rows = db.query(
        'SELECT id, title, release_year, cover_url, tmdb_id FROM movies WHERE id = %s',
        (movie_id,)
    )
    return rows[0] if rows else None


def get_movie_tmdb_id(db, movie_id):
    rows = db.query('SELECT tmdb_id FROM movies WHERE id = %s', (movie_id,))
    return rows[0][0] if rows and rows[0][0] else None


def set_movie_tmdb_id(db, movie_id, tmdb_id):
    db.execute('UPDATE movies SET tmdb_id = %s WHERE id = %s AND tmdb_id IS NULL',
               (tmdb_id, movie_id))


def get_all_movies_needing_data(db, limit=None):
    """用单条 SQL 查出所有缺少封面/演员/导演的电影"""
    rows = db.query(
        'SELECT m.id, m.title, m.release_year, m.cover_url, m.tmdb_id '
        'FROM movies m '
        'LEFT JOIN movies_actors ma ON m.id = ma.movie_id '
        'LEFT JOIN movies_directors md ON m.id = md.movie_id '
        'WHERE m.cover_url IS NULL OR m.cover_url = "" '
        '   OR ma.movie_id IS NULL '
        '   OR md.movie_id IS NULL '
        'GROUP BY m.id '
        'ORDER BY m.id ASC'
        + (' LIMIT %s' % limit if limit else '')
    )
    return rows


def _has_actors(db, movie_id):
    rows = db.query('SELECT 1 FROM movies_actors WHERE movie_id = %s LIMIT 1', (movie_id,))
    return len(rows) > 0


def _has_directors(db, movie_id):
    rows = db.query('SELECT 1 FROM movies_directors WHERE movie_id = %s LIMIT 1', (movie_id,))
    return len(rows) > 0


def upsert_actor(db, name, avatar_url=''):
    db.execute(
        'INSERT INTO actors (name, avatar_url) VALUES (%s, %s) '
        'ON DUPLICATE KEY UPDATE avatar_url = IF(VALUES(avatar_url) != "", VALUES(avatar_url), avatar_url)',
        (name, avatar_url)
    )
    rows = db.query('SELECT id FROM actors WHERE name = %s', (name,))
    return rows[0][0] if rows else None


def upsert_director(db, name, avatar_url=''):
    db.execute(
        'INSERT INTO directors (name, avatar_url) VALUES (%s, %s) '
        'ON DUPLICATE KEY UPDATE avatar_url = IF(VALUES(avatar_url) != "", VALUES(avatar_url), avatar_url)',
        (name, avatar_url)
    )
    rows = db.query('SELECT id FROM directors WHERE name = %s', (name,))
    return rows[0][0] if rows else None


def link_movie_actor(db, movie_id, actor_id):
    db.execute('INSERT IGNORE INTO movies_actors (movie_id, actor_id) VALUES (%s, %s)',
               (movie_id, actor_id))


def link_movie_director(db, movie_id, director_id):
    db.execute('INSERT IGNORE INTO movies_directors (movie_id, director_id) VALUES (%s, %s)',
               (movie_id, director_id))


def update_movie_cover(db, movie_id, cover_url):
    if not cover_url:
        return
    db.execute(
        'UPDATE movies SET cover_url = %s WHERE id = %s AND (cover_url IS NULL OR cover_url = "")',
        (cover_url, movie_id)
    )


# ============================================================
# TMDB API (with proxy support)
# ============================================================

def _make_session():
    s = requests.Session()
    if HTTP_PROXY:
        s.proxies = {'http': HTTP_PROXY, 'https': HTTP_PROXY}
    return s


def tmdb_request(endpoint, params=None):
    url = f'{TMDB_BASE}{endpoint}'
    p = {'api_key': TMDB_API_KEY, 'language': 'zh-CN'}
    if params:
        p.update(params)

    time.sleep(REQUEST_DELAY)
    s = _make_session()
    try:
        resp = s.get(url, params=p, timeout=SEARCH_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            print('  [TMDB] 频率限制，等待 5 秒...')
            time.sleep(5)
            resp = s.get(url, params=p, timeout=SEARCH_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
        print(f'  [TMDB] HTTP {resp.status_code}: {resp.text[:150]}')
    except requests.RequestException as e:
        print(f'  [TMDB] 网络错误: {e}')
    return None


def fetch_popular_movies(page=1):
    data = tmdb_request('/movie/popular', {'page': page})
    return data.get('results', []) if data else []


def search_movie(title, year=None):
    params = {'query': title}
    if year:
        params['year'] = year
    data = tmdb_request('/search/movie', params)
    return data['results'][0] if data and data.get('results') else None


def fetch_movie_details(tmdb_id):
    data = tmdb_request(f'/movie/{tmdb_id}', {'append_to_response': 'credits'})
    if not data:
        return None

    result = {
        'tmdb_id': tmdb_id,
        'title': data.get('title', ''),
        'original_title': data.get('original_title', ''),
        'year': int(data['release_date'][:4]) if data.get('release_date') else None,
        'cover_url': '',
        'directors': [],
        'actors': [],
    }

    poster = data.get('poster_path')
    if poster:
        result['cover_url'] = f'{TMDB_IMAGE_BASE}{poster}'

    credits = data.get('credits', {})
    for person in credits.get('crew', []):
        if person.get('job') == 'Director':
            name = person.get('name', '').strip()
            if name:
                result['directors'].append(name)

    for person in credits.get('cast', [])[:MAX_ACTORS]:
        name = person.get('name', '').strip()
        if name:
            result['actors'].append(name)

    return result


def fetch_movie_details_en(tmdb_id):
    """获取英文版演员导演（用于数据库是英文标题时匹配）"""
    data = tmdb_request(f'/movie/{tmdb_id}',
                        {'append_to_response': 'credits', 'language': 'en-US'})
    if not data:
        return {'directors': [], 'actors': []}
    result = {'directors': [], 'actors': []}
    credits = data.get('credits', {})
    for person in credits.get('crew', []):
        if person.get('job') == 'Director':
            name = person.get('name', '').strip()
            if name:
                result['directors'].append(name)
    for person in credits.get('cast', [])[:MAX_ACTORS]:
        name = person.get('name', '').strip()
        if name:
            result['actors'].append(name)
    return result


# ============================================================
# 标题匹配
# ============================================================

def normalize_title(title):
    if not title:
        return ''
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    t = t.replace('&', 'and')
    for art in ('the ', 'a ', 'an '):
        if t.startswith(art):
            t = t[len(art):]
    return t


def match_movie(db_movie, tmdb_detail):
    db_id, db_title, db_year, _, _ = db_movie
    db_norm = normalize_title(db_title)

    titles = [tmdb_detail['title']]
    if tmdb_detail.get('original_title') and tmdb_detail['original_title'] != tmdb_detail['title']:
        titles.append(tmdb_detail['original_title'])

    for t in titles:
        t_norm = normalize_title(t)
        if not t_norm or not db_norm:
            continue
        if not (db_norm == t_norm or db_norm in t_norm or t_norm in db_norm):
            continue
        if db_year and tmdb_detail['year']:
            if abs(int(db_year) - int(tmdb_detail['year'])) <= 1:
                return True
        elif not db_year or not tmdb_detail['year']:
            return True
    return False


def find_db_match(db_movies, tmdb_detail):
    for m in db_movies:
        if match_movie(m, tmdb_detail):
            return m
    return None


# ============================================================
# 数据写入
# ============================================================

def save_movie_data(db, movie_id, detail, tmdb_id, dry_run=False):
    stats = {'cover': False, 'actors': 0, 'directors': 0}
    if dry_run:
        if detail['cover_url']: stats['cover'] = True
        stats['actors'] = len(detail['actors'])
        stats['directors'] = len(detail['directors'])
        return stats

    if detail['cover_url']:
        update_movie_cover(db, movie_id, detail['cover_url'])
        stats['cover'] = True

    for name in detail['directors']:
        did = upsert_director(db, name)
        if did:
            link_movie_director(db, movie_id, did)
            stats['directors'] += 1

    for name in detail['actors']:
        aid = upsert_actor(db, name)
        if aid:
            link_movie_actor(db, movie_id, aid)
            stats['actors'] += 1

    if tmdb_id:
        set_movie_tmdb_id(db, movie_id, tmdb_id)

    return stats


# ============================================================
# 核心流程: 查找或解析 TMDB 详情
# ============================================================

def resolve_tmdb_detail(db, movie_id, title, year):
    """
    获取电影的 TMDB 详情。优先策略：
    1. 如果 movies.tmdb_id 已有值 -> 直接用 tmdb_id 获取详情
    2. 否则通过标题+年份搜索 -> 找到后缓存 tmdb_id
    """
    cached_tmdb_id = get_movie_tmdb_id(db, movie_id)

    if cached_tmdb_id:
        print(f'  [缓存] tmdb_id={cached_tmdb_id}，直查详情')
        detail = fetch_movie_details(cached_tmdb_id)
        if detail:
            return detail, cached_tmdb_id
        print(f'  [缓存] tmdb_id 已失效，重新搜索...')

    # 搜索
    result = search_movie(title, year)
    if not result:
        result = search_movie(title)  # 不带年份再试

    if not result:
        return None, None

    found_tmdb_id = result['id']
    detail = fetch_movie_details(found_tmdb_id)
    return detail, found_tmdb_id


# ============================================================
# 阶段 1: 热门电影
# ============================================================

def run_chart_scrape(db, dry_run=False):
    print('=' * 60)
    print('阶段 1: 拉取 TMDB 热门电影（前 {} 页）'.format(TMDB_CHART_PAGES))
    print('=' * 60)

    all_movies = []
    for p in range(1, TMDB_CHART_PAGES + 1):
        print(f'  [TMDB] 拉取第 {p} 页热门...')
        movies = fetch_popular_movies(p)
        all_movies.extend(movies)
        print(f'  [TMDB] 第 {p} 页获取 {len(movies)} 部')

    print(f'\n共 {len(all_movies)} 部热门电影，开始匹配...\n')

    db_movies = get_db_movies(db, limit=5000)
    print(f'数据库 {len(db_movies)} 部电影用于匹配\n')

    total_cover = total_actors = total_directors = matched = skipped = 0

    for i, movie in enumerate(all_movies, 1):
        tmdb_id = movie['id']
        title = movie.get('title', 'N/A')
        print(f'[{i}/{len(all_movies)}] {title} (TMDB: {tmdb_id})')

        detail = fetch_movie_details(tmdb_id)
        if not detail:
            skipped += 1
            continue

        db_match = find_db_match(db_movies, detail)
        if not db_match and detail['original_title'] != detail['title']:
            en = fetch_movie_details_en(tmdb_id)
            detail_en = detail.copy()
            detail_en['title'] = detail['original_title']
            detail_en['directors'] = en.get('directors', detail.get('directors', []))
            detail_en['actors'] = en.get('actors', detail.get('actors', []))
            db_match = find_db_match(db_movies, detail_en)
            if db_match:
                detail = detail_en

        if not db_match:
            print(f'  [匹配] 数据库无匹配: "{detail["title"]}" ({detail.get("year")})')
            skipped += 1
            continue

        db_id, db_title, db_year, db_cover, db_tmdb = db_match
        has_cover = bool(db_cover)
        has_a = _has_actors(db, db_id)
        has_d = _has_directors(db, db_id)

        if has_cover and has_a and has_d:
            set_movie_tmdb_id(db, db_id, tmdb_id)
            print(f'  [匹配] {db_title} (id={db_id}) 数据已完整，仅缓存 tmdb_id')
            skipped += 1
            continue

        print(f'  [匹配] {db_title} (id={db_id}, year={db_year})')
        stats = save_movie_data(db, db_id, detail, tmdb_id, dry_run)
        matched += 1
        total_cover += (1 if stats['cover'] else 0)
        total_actors += stats['actors']
        total_directors += stats['directors']
        print(f'  [写入] 封面={stats["cover"]} 导演+{stats["directors"]} 演员+{stats["actors"]}')

    print('\n' + '=' * 60)
    print(f'阶段 1 完成: 匹配 {matched} / 跳过 {skipped}')
    print(f'  封面 {total_cover}  演员 {total_actors}  导演 {total_directors}')
    print('=' * 60)


# ============================================================
# 阶段 2: 补充推荐电影数据
# ============================================================

def run_recommend_scrape(db, dry_run=False):
    print('\n' + '=' * 60)
    print('阶段 2: 补充推荐电影中缺少的封面/演员/导演')
    print('=' * 60)

    needing = get_all_movies_needing_data(db)
    print(f'共需补充: {len(needing)} 部')

    if not needing:
        print('无需补充')
        return

    total_cover = total_actors = total_directors = matched = skipped = 0

    for i, (mid, title, year, cover, tmdb_id_cached) in enumerate(needing, 1):
        print(f'[{i}/{len(needing)}] {title} ({year}) [id={mid}]')

        detail, tmdb_id = resolve_tmdb_detail(db, mid, title, year)
        if not detail:
            print(f'  [Search] TMDB 无结果，跳过')
            skipped += 1
            continue

        print(f'  [Search] tmdb_id={tmdb_id} 标题="{detail.get("title", "?")}"')

        needs_cover = not cover
        needs_a = not _has_actors(db, mid)
        needs_d = not _has_directors(db, mid)

        if not needs_cover and not needs_a and not needs_d:
            set_movie_tmdb_id(db, mid, tmdb_id)
            print(f'  [数据] 已完整，仅缓存 tmdb_id')
            skipped += 1
            continue

        stats = save_movie_data(db, mid, detail, tmdb_id, dry_run)
        matched += 1
        total_cover += (1 if stats['cover'] else 0)
        total_actors += stats['actors']
        total_directors += stats['directors']
        print(f'  [写入] 封面={stats["cover"]} 导演+{stats["directors"]} 演员+{stats["actors"]}')

    print('\n' + '=' * 60)
    print(f'阶段 2 完成: 匹配 {matched} / 跳过 {skipped}')
    print(f'  封面 {total_cover}  演员 {total_actors}  导演 {total_directors}')
    print('=' * 60)


# ============================================================
# Entry
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description='TMDB 电影数据爬虫')
    parser.add_argument('--api-key', type=str, default='',
                        help='TMDB API Key（也可设环境变量 TMDB_API_KEY）')
    parser.add_argument('--proxy', type=str, default='',
                        help='HTTP 代理地址，如 http://127.0.0.1:7890')
    parser.add_argument('--chart-only', action='store_true', help='仅热门电影')
    parser.add_argument('--all', action='store_true', help='补充全部缺数据的电影（默认仅热门）')
    parser.add_argument('--dry-run', action='store_true', help='预览不写入')
    args = parser.parse_args()

    global HTTP_PROXY
    if args.proxy:
        HTTP_PROXY = args.proxy
        print(f'使用代理: {HTTP_PROXY}')

    check_api_key(args.api_key)

    if args.dry_run:
        print('*** DRY RUN — 不会写入 ***\n')

    db = DB(DB_CONFIG)
    try:
        db.query('SELECT 1')
        print(f'数据库连接成功: {DB_CONFIG["host"]}:{DB_CONFIG["port"]}/{DB_CONFIG["database"]}\n')
    except Exception as e:
        print(f'数据库连接失败: {e}'); sys.exit(1)

    try:
        # --chart-only: 只跑阶段1(热门)；--all: 只跑阶段2(全量补充)；默认：两阶段都跑
        if not args.all:
            run_chart_scrape(db, args.dry_run)
        if not args.chart_only:
            run_recommend_scrape(db, args.dry_run)
    finally:
        db.close()

    print('\n全部完成!')


if __name__ == '__main__':
    main()
