"""
TMDB 电影全量数据爬虫 — 抓取系统所需的全部电影元数据并写入数据库

抓取本系统所需的全部信息：
1. 电影基本信息：描述、片长、上映年份、封面图
2. 电影题材分类（genres）→ genres + movies_genres 表
3. 电影关键词/标签（keywords）→ tags + movies_tags 表
4. 导演信息：姓名、头像 → directors + movies_directors 表
5. 演员信息：姓名、头像、饰演角色 → actors + movies_actors 表
6. 预告片/视频 URL → movies.video_url

工作模式：
- 阶段 1：从 TMDB 热门电影列表拉取前 N 页，全量入库
- 阶段 2：补充数据库中已有但信息不全的电影
- 支持 --movie-id 单独处理、--start-from-id 断点续传、--limit 分批

前置条件：
  pip install requests pymysql
  TMDB API Key: https://www.themoviedb.org/settings/api

用法:
  python scripts/crawl/crawl_movie_full.py --api-key YOUR_REAL_KEY --proxy http://127.0.0.1:7890
  python scripts/crawl/crawl_movie_full.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --chart-only
  python scripts/crawl/crawl_movie_full.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --all --start-from-id 5000 --limit 500
  python scripts/crawl/crawl_movie_full.py --api-key YOUR_KEY --proxy http://127.0.0.1:7890 --movie-id 1
  python scripts/crawl/crawl_movie_full.py --api-key YOUR_KEY --dry-run
"""

import os
import sys
import re
import time
import argparse

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
    'host': os.environ.get('DB_HOST', '192.168.43.38'),
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
MAX_ACTORS = 15
MAX_KEYWORDS = 20
REQUEST_DELAY = 0.25
SEARCH_TIMEOUT = 15

COMMIT_INTERVAL = 20

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
        print("  3. 使用: python scripts/crawl/crawl_movie_full.py --api-key YOUR_KEY")
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

    def commit(self):
        self.conn.commit()

    def lastrowid(self):
        return self.cursor.lastrowid

    def close(self):
        self.cursor.close()
        self.conn.close()


def get_db_movies(db, limit=5000):
    return db.query(
        'SELECT id, title, release_year, cover_url, description, duration, tmdb_id, video_url '
        'FROM movies ORDER BY id ASC LIMIT %s', (limit,)
    )


def get_movie_by_id(db, movie_id):
    rows = db.query(
        'SELECT id, title, release_year, cover_url, description, duration, tmdb_id, video_url '
        'FROM movies WHERE id = %s', (movie_id,)
    )
    return rows[0] if rows else None


def get_movie_tmdb_id(db, movie_id):
    rows = db.query('SELECT tmdb_id FROM movies WHERE id = %s', (movie_id,))
    return rows[0][0] if rows and rows[0][0] else None


def set_movie_tmdb_id(db, movie_id, tmdb_id):
    db.execute('UPDATE movies SET tmdb_id = %s WHERE id = %s AND tmdb_id IS NULL',
               (tmdb_id, movie_id))


def clear_movie_tmdb_id(db, movie_id):
    """清除失效的 tmdb_id 缓存"""
    db.execute('UPDATE movies SET tmdb_id = NULL WHERE id = %s', (movie_id,))


def get_all_movies_needing_data(db, start_from_id=1, limit=None):
    rows = db.query(
        'SELECT m.id, m.title, m.release_year, m.cover_url, m.description, m.duration, m.tmdb_id, m.video_url '
        'FROM movies m '
        'LEFT JOIN movies_actors ma ON m.id = ma.movie_id '
        'LEFT JOIN movies_directors md ON m.id = md.movie_id '
        'LEFT JOIN movies_genres mg ON m.id = mg.movie_id '
        'WHERE m.id >= %s '
        '  AND (m.cover_url IS NULL OR m.cover_url = "" '
        '   OR m.description IS NULL OR m.description = "" '
        '   OR m.duration IS NULL OR m.duration = 0 '
        '   OR ma.movie_id IS NULL '
        '   OR md.movie_id IS NULL '
        '   OR mg.movie_id IS NULL) '
        'GROUP BY m.id '
        'ORDER BY m.avg_rating DESC'
        + (' LIMIT %s' % limit if limit else ''),
        (start_from_id,)
    )
    return rows


def _has_actors(db, movie_id):
    rows = db.query('SELECT 1 FROM movies_actors WHERE movie_id = %s LIMIT 1', (movie_id,))
    return len(rows) > 0


def _has_directors(db, movie_id):
    rows = db.query('SELECT 1 FROM movies_directors WHERE movie_id = %s LIMIT 1', (movie_id,))
    return len(rows) > 0


# ---- genres ----

def upsert_genre(db, name, code):
    db.execute(
        'INSERT INTO genres (name, code) VALUES (%s, %s) '
        'ON DUPLICATE KEY UPDATE name = VALUES(name)',
        (name, code)
    )
    rows = db.query('SELECT id FROM genres WHERE code = %s', (code,))
    return rows[0][0] if rows else None


def link_movie_genre(db, movie_id, genre_id):
    db.execute('INSERT IGNORE INTO movies_genres (movie_id, genre_id) VALUES (%s, %s)',
               (movie_id, genre_id))


# ---- tags ----

def upsert_tag(db, name):
    db.execute('INSERT IGNORE INTO tags (name) VALUES (%s)', (name,))
    rows = db.query('SELECT id FROM tags WHERE name = %s', (name,))
    return rows[0][0] if rows else None


def link_movie_tag(db, movie_id, tag_id):
    db.execute('INSERT IGNORE INTO movies_tags (movie_id, tag_id) VALUES (%s, %s)',
               (movie_id, tag_id))


# ---- directors ----

def upsert_director(db, name, avatar_url=''):
    db.execute(
        'INSERT INTO directors (name, avatar_url) VALUES (%s, %s) '
        'ON DUPLICATE KEY UPDATE '
        'avatar_url = IF(VALUES(avatar_url) != "", VALUES(avatar_url), avatar_url)',
        (name, avatar_url)
    )
    rows = db.query('SELECT id FROM directors WHERE name = %s', (name,))
    return rows[0][0] if rows else None


def link_movie_director(db, movie_id, director_id):
    db.execute('INSERT IGNORE INTO movies_directors (movie_id, director_id) VALUES (%s, %s)',
               (movie_id, director_id))


# ---- actors ----

def upsert_actor(db, name, avatar_url=''):
    db.execute(
        'INSERT INTO actors (name, avatar_url) VALUES (%s, %s) '
        'ON DUPLICATE KEY UPDATE '
        'avatar_url = IF(VALUES(avatar_url) != "", VALUES(avatar_url), avatar_url)',
        (name, avatar_url)
    )
    rows = db.query('SELECT id FROM actors WHERE name = %s', (name,))
    return rows[0][0] if rows else None


def link_movie_actor(db, movie_id, actor_id, role=''):
    db.execute(
        'INSERT IGNORE INTO movies_actors (movie_id, actor_id, role) VALUES (%s, %s, %s)',
        (movie_id, actor_id, role)
    )


# ---- movie fields ----

def update_movie_cover(db, movie_id, cover_url):
    if not cover_url:
        return False
    db.execute(
        'UPDATE movies SET cover_url = %s WHERE id = %s AND (cover_url IS NULL OR cover_url = "")',
        (cover_url, movie_id)
    )
    return True


def update_movie_info(db, movie_id, description='', duration=None):
    updates = []
    params = []
    if description:
        updates.append('description = %s')
        params.append(description)
    if duration and duration > 0:
        updates.append('duration = %s')
        params.append(duration)
    if updates:
        params.append(movie_id)
        db.execute(f'UPDATE movies SET {", ".join(updates)} WHERE id = %s', tuple(params))
        return True
    return False


def update_movie_video(db, movie_id, video_url):
    if not video_url:
        return False
    db.execute(
        'UPDATE movies SET video_url = %s WHERE id = %s AND (video_url IS NULL OR video_url = "")',
        (video_url, movie_id)
    )
    return True


def update_movie_release_year(db, movie_id, year):
    if not year:
        return False
    db.execute(
        'UPDATE movies SET release_year = %s WHERE id = %s AND release_year IS NULL',
        (year, movie_id)
    )
    return True


# ============================================================
# TMDB API (with proxy support)
# ============================================================

_auth_failed = False

def _make_session():
    s = requests.Session()
    if HTTP_PROXY:
        s.proxies = {'http': HTTP_PROXY, 'https': HTTP_PROXY}
    return s


def tmdb_request(endpoint, params=None, language='zh-CN'):
    global _auth_failed
    if _auth_failed:
        return None

    url = f'{TMDB_BASE}{endpoint}'
    p = {'api_key': TMDB_API_KEY, 'language': language}
    if params:
        p.update(params)

    time.sleep(REQUEST_DELAY)
    s = _make_session()
    try:
        resp = s.get(url, params=p, timeout=SEARCH_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            print(f'  [TMDB] API Key 无效 (401)，程序终止。请检查 --api-key 参数')
            _auth_failed = True
            return None
        elif resp.status_code == 404:
            return None
        elif resp.status_code == 429:
            print('  [TMDB] 频率限制，等待 10 秒...')
            time.sleep(10)
            resp = s.get(url, params=p, timeout=SEARCH_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 401:
                print(f'  [TMDB] API Key 无效 (401)，程序终止')
                _auth_failed = True
                return None
            elif resp.status_code == 404:
                return None
        if resp.status_code not in (401, 404, 429):
            print(f'  [TMDB] HTTP {resp.status_code}: {resp.text[:120]}')
    except requests.RequestException as e:
        print(f'  [TMDB] 网络错误: {e}')
    return None


def fetch_popular_movies(page=1):
    data = tmdb_request('/movie/popular', {'page': page, 'region': 'US'})
    return data.get('results', []) if data else []


def search_movie(title, year=None):
    params = {'query': title}
    if year:
        params['year'] = year
    data = tmdb_request('/search/movie', params)
    return data['results'][0] if data and data.get('results') else None


def fetch_movie_details(tmdb_id):
    data = tmdb_request(
        f'/movie/{tmdb_id}',
        {'append_to_response': 'credits,keywords,videos'}
    )
    if not data:
        return None

    result = {
        'tmdb_id': tmdb_id,
        'title': data.get('title', ''),
        'original_title': data.get('original_title', ''),
        'year': int(data['release_date'][:4]) if data.get('release_date') else None,
        'description': data.get('overview', '') or '',
        'duration': data.get('runtime') or 0,
        'cover_url': '',
        'video_url': '',
        'directors': [],
        'actors': [],
        'genres': [],
        'keywords': [],
    }

    poster = data.get('poster_path')
    if poster:
        result['cover_url'] = f'{TMDB_IMAGE_BASE}{poster}'

    for g in data.get('genres', []):
        name = g.get('name', '').strip()
        if name:
            result['genres'].append(name)

    credits = data.get('credits', {})
    for person in credits.get('crew', []):
        if person.get('job') == 'Director':
            name = person.get('name', '').strip()
            if name:
                pp = person.get('profile_path')
                result['directors'].append({
                    'name': name,
                    'avatar_url': f'{TMDB_IMAGE_BASE}{pp}' if pp else '',
                })

    for person in credits.get('cast', [])[:MAX_ACTORS]:
        name = person.get('name', '').strip()
        if name:
            pp = person.get('profile_path')
            result['actors'].append({
                'name': name,
                'avatar_url': f'{TMDB_IMAGE_BASE}{pp}' if pp else '',
                'role': person.get('character', '') or '',
            })

    kw_data = data.get('keywords', {})
    for kw in kw_data.get('keywords', [])[:MAX_KEYWORDS]:
        name = kw.get('name', '').strip()
        if name:
            result['keywords'].append(name)

    videos = data.get('videos', {})
    trailer_url = ''
    for v in videos.get('results', []):
        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer':
            trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
            break
    if not trailer_url:
        for v in videos.get('results', []):
            if v.get('site') == 'YouTube':
                trailer_url = f"https://www.youtube.com/watch?v={v.get('key')}"
                break
    result['video_url'] = trailer_url

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
    db_id, db_title, db_year = db_movie[0], db_movie[1], db_movie[2]
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
# 数据写入 — 全量
# ============================================================

def save_all_movie_data(db, movie_id, detail, tmdb_id, dry_run=False):
    stats = {
        'cover': False, 'description': False, 'duration': False,
        'video': False, 'year': False,
        'actors': 0, 'directors': 0, 'genres': 0, 'keywords': 0,
    }

    if dry_run:
        if detail['cover_url']: stats['cover'] = True
        if detail['description']: stats['description'] = True
        if detail['duration']: stats['duration'] = True
        if detail['video_url']: stats['video'] = True
        if detail['year']: stats['year'] = True
        stats['actors'] = len(detail['actors'])
        stats['directors'] = len(detail['directors'])
        stats['genres'] = len(detail['genres'])
        stats['keywords'] = len(detail['keywords'])
        return stats

    if detail['cover_url'] and update_movie_cover(db, movie_id, detail['cover_url']):
        stats['cover'] = True

    if detail['description']:
        update_movie_info(db, movie_id, description=detail['description'])
        stats['description'] = True

    if detail['duration']:
        update_movie_info(db, movie_id, duration=detail['duration'])
        stats['duration'] = True

    if detail['video_url'] and update_movie_video(db, movie_id, detail['video_url']):
        stats['video'] = True

    if detail['year'] and update_movie_release_year(db, movie_id, detail['year']):
        stats['year'] = True

    for genre_name in detail['genres']:
        code = genre_name.lower().replace(' ', '_').replace('&', 'and')
        gid = upsert_genre(db, genre_name, code)
        if gid:
            link_movie_genre(db, movie_id, gid)
            stats['genres'] += 1

    for kw in detail['keywords']:
        tid = upsert_tag(db, kw)
        if tid:
            link_movie_tag(db, movie_id, tid)
            stats['keywords'] += 1

    for d in detail['directors']:
        did = upsert_director(db, d['name'], d.get('avatar_url', ''))
        if did:
            link_movie_director(db, movie_id, did)
            stats['directors'] += 1

    for a in detail['actors']:
        aid = upsert_actor(db, a['name'], a.get('avatar_url', ''))
        if aid:
            link_movie_actor(db, movie_id, aid, a.get('role', ''))
            stats['actors'] += 1

    if tmdb_id:
        set_movie_tmdb_id(db, movie_id, tmdb_id)

    return stats


def print_stats(stats):
    parts = []
    if stats.get('cover'): parts.append('封面')
    if stats.get('description'): parts.append('简介')
    if stats.get('duration'): parts.append('片长')
    if stats.get('video'): parts.append('视频')
    if stats.get('year'): parts.append('年份')
    if stats.get('genres', 0): parts.append(f'题材+{stats["genres"]}')
    if stats.get('keywords', 0): parts.append(f'标签+{stats["keywords"]}')
    if stats.get('directors', 0): parts.append(f'导演+{stats["directors"]}')
    if stats.get('actors', 0): parts.append(f'演员+{stats["actors"]}')
    return ' | '.join(parts) if parts else '无新增'


# ============================================================
# 核心流程: 解析 TMDB 详情（优化版，减少无效 API 调用）
# ============================================================

def resolve_tmdb_detail(db, movie_id, title, year):
    """
    获取电影的 TMDB 详情。优化策略：
    1. 如果有缓存 tmdb_id → 直接用 ID 查详情
       - 成功 → 返回
       - 404（tmdb_id 已失效）→ 清除缓存，走搜索
       - 其他失败 → 返回 None
    2. 搜索 → 找到后查详情并缓存 tmdb_id
    """
    global _auth_failed

    cached_tmdb_id = get_movie_tmdb_id(db, movie_id)

    if cached_tmdb_id:
        print(f'  [缓存] tmdb_id={cached_tmdb_id}，直查详情')
        detail = fetch_movie_details(cached_tmdb_id)
        if _auth_failed:
            return None, None
        if detail:
            return detail, cached_tmdb_id
        # 缓存失效，清除
        print(f'  [缓存] tmdb_id 已失效，清除缓存')
        clear_movie_tmdb_id(db, movie_id)

    # 搜索
    result = search_movie(title, year)
    if _auth_failed:
        return None, None
    if not result:
        result = search_movie(title)
        if _auth_failed:
            return None, None

    if not result:
        return None, None

    found_tmdb_id = result['id']
    detail = fetch_movie_details(found_tmdb_id)
    if _auth_failed:
        return None, None
    return detail, found_tmdb_id


# ============================================================
# 阶段 1: 热门电影全量抓取
# ============================================================

def run_chart_scrape(db, pages=TMDB_CHART_PAGES, dry_run=False):
    global _auth_failed

    print('=' * 60)
    print(f'阶段 1: 拉取 TMDB 热门电影全量数据（前 {pages} 页）')
    print('=' * 60)

    all_movies = []
    for p in range(1, pages + 1):
        print(f'  [TMDB] 拉取第 {p} 页热门...')
        movies = fetch_popular_movies(p)
        if _auth_failed:
            break
        all_movies.extend(movies)
        print(f'  [TMDB] 第 {p} 页获取 {len(movies)} 部')

    if _auth_failed:
        print('API Key 无效，终止阶段 1')
        return

    print(f'\n共 {len(all_movies)} 部热门电影，开始匹配...\n')

    db_movies = get_db_movies(db, limit=5000)
    print(f'数据库 {len(db_movies)} 部电影用于匹配\n')

    totals = {'cover': 0, 'description': 0, 'duration': 0, 'video': 0, 'year': 0,
              'actors': 0, 'directors': 0, 'genres': 0, 'keywords': 0}
    matched = skipped = 0

    for i, movie in enumerate(all_movies, 1):
        if _auth_failed:
            break

        tmdb_id = movie['id']
        title = movie.get('title', 'N/A')
        print(f'[{i}/{len(all_movies)}] {title} (TMDB: {tmdb_id})')

        detail = fetch_movie_details(tmdb_id)
        if _auth_failed:
            break
        if not detail:
            skipped += 1
            continue

        db_match = find_db_match(db_movies, detail)
        if not db_match and detail['original_title'] != detail['title']:
            detail_en_title = detail['title']
            detail['title'] = detail['original_title']
            db_match = find_db_match(db_movies, detail)
            if not db_match:
                detail['title'] = detail_en_title

        if not db_match:
            print(f'  [匹配] 数据库无匹配: "{detail["title"]}" ({detail.get("year")})')
            skipped += 1
            continue

        db_id, db_title, db_year = db_match[0], db_match[1], db_match[2]
        print(f'  [匹配] {db_title} (id={db_id}, year={db_year})')

        stats = save_all_movie_data(db, db_id, detail, tmdb_id, dry_run)
        if i % COMMIT_INTERVAL == 0:
            db.commit()
        matched += 1
        for k in totals:
            totals[k] += (1 if stats[k] is True else stats[k] if isinstance(stats[k], int) else 0)
        print(f'  [写入] {print_stats(stats)}')

    db.commit()

    print('\n' + '=' * 60)
    print(f'阶段 1 完成: 匹配 {matched} / 跳过 {skipped}')
    print(f'  封面 {totals["cover"]}  简介 {totals["description"]}  片长 {totals["duration"]}  视频 {totals["video"]}')
    print(f'  题材 {totals["genres"]}  标签 {totals["keywords"]}')
    print(f'  演员 {totals["actors"]}  导演 {totals["directors"]}')
    print('=' * 60)


# ============================================================
# 阶段 2: 补充已有电影缺失数据
# ============================================================

def run_full_scrape(db, dry_run=False, start_from_id=1, limit=None):
    global _auth_failed

    print('\n' + '=' * 60)
    print('阶段 2: 补充数据库中电影的全部缺失信息')
    if start_from_id > 1:
        print(f'  从 ID={start_from_id} 开始')
    if limit:
        print(f'  本次最多处理 {limit} 部')
    print('=' * 60)

    needing = get_all_movies_needing_data(db, start_from_id=start_from_id, limit=limit)
    print(f'共需补充: {len(needing)} 部\n')

    if not needing:
        print('无需补充')
        return

    totals = {'cover': 0, 'description': 0, 'duration': 0, 'video': 0, 'year': 0,
              'actors': 0, 'directors': 0, 'genres': 0, 'keywords': 0}
    matched = skipped = fail_count = 0
    start_time = time.time()
    last_report_time = start_time

    for i, row in enumerate(needing, 1):
        if _auth_failed:
            print('\n检测到 API Key 无效，提前终止。已处理的数据已提交。')
            break

        mid, title, year, cover, desc, dur, tmdb_cached, video = row

        # 进度提示
        now = time.time()
        if now - last_report_time >= 30 or i == 1:
            elapsed = now - start_time
            rate = (matched + skipped) / elapsed if elapsed > 0 else 0
            eta = (len(needing) - i + 1) / rate if rate > 0 else 0
            print(f'  [进度] {i}/{len(needing)} | 成功 {matched} 跳过 {skipped} | '
                  f'速率 {rate:.1f}/s | 预计剩余 {eta/60:.0f} 分钟')
            last_report_time = now

        print(f'[{i}/{len(needing)}] {title} ({year}) [id={mid}]')

        detail, tmdb_id = resolve_tmdb_detail(db, mid, title, year)
        if _auth_failed:
            break
        if not detail:
            print(f'  [Search] TMDB 无结果，跳过')
            skipped += 1
            continue

        print(f'  [Search] tmdb_id={tmdb_id} 标题="{detail.get("title", "?")}"')

        stats = save_all_movie_data(db, mid, detail, tmdb_id, dry_run)
        if i % COMMIT_INTERVAL == 0:
            db.commit()

        matched += 1
        for k in totals:
            totals[k] += (1 if stats[k] is True else stats[k] if isinstance(stats[k], int) else 0)
        print(f'  [写入] {print_stats(stats)}')

    db.commit()

    total_elapsed = time.time() - start_time
    print('\n' + '=' * 60)
    print(f'阶段 2 完成: 匹配 {matched} / 跳过 {skipped} / 耗时 {total_elapsed/60:.1f} 分钟')
    print(f'  封面 {totals["cover"]}  简介 {totals["description"]}  片长 {totals["duration"]}  视频 {totals["video"]}')
    print(f'  题材 {totals["genres"]}  标签 {totals["keywords"]}')
    print(f'  演员 {totals["actors"]}  导演 {totals["directors"]}')
    print('=' * 60)


# ============================================================
# 单独处理指定电影
# ============================================================

def run_single_movie(db, movie_id, dry_run=False):
    global _auth_failed

    print('=' * 60)
    print(f'单独处理电影 ID: {movie_id}')
    print('=' * 60)

    row = get_movie_by_id(db, movie_id)
    if not row:
        print(f'电影 ID={movie_id} 不存在')
        return

    mid, title, year, cover, desc, dur, tmdb_cached, video = row
    print(f'标题: {title} ({year})')

    detail, tmdb_id = resolve_tmdb_detail(db, mid, title, year)
    if _auth_failed:
        return
    if not detail:
        print('TMDB 无结果')
        return

    print(f'tmdb_id={tmdb_id} 标题="{detail.get("title", "?")}"')
    print(f'简介: {(detail.get("description") or "")[:100]}...')
    print(f'片长: {detail.get("duration")} 分钟')
    print(f'题材: {detail["genres"]}')
    print(f'关键词: {detail["keywords"][:5]}...')
    print(f'导演: {[d["name"] for d in detail["directors"]]}')
    print(f'演员: {[a["name"] for a in detail["actors"][:5]]}...')

    stats = save_all_movie_data(db, mid, detail, tmdb_id, dry_run)
    db.commit()
    print(f'\n写入结果: {print_stats(stats)}')


# ============================================================
# Entry
# ============================================================

def main():
    global HTTP_PROXY, TMDB_CHART_PAGES

    default_pages = TMDB_CHART_PAGES

    parser = argparse.ArgumentParser(description='TMDB 电影全量数据爬虫')
    parser.add_argument('--api-key', type=str, default='',
                        help='TMDB API Key（也可设环境变量 TMDB_API_KEY）')
    parser.add_argument('--proxy', type=str, default='',
                        help='HTTP 代理地址，如 http://127.0.0.1:7890')
    parser.add_argument('--pages', type=int, default=default_pages,
                        help=f'热门电影拉取页数（默认 {default_pages}）')
    parser.add_argument('--chart-only', action='store_true', help='仅热门电影')
    parser.add_argument('--all', action='store_true', help='补充全部缺数据的电影（默认仅热门）')
    parser.add_argument('--start-from-id', type=int, default=1,
                        help='从指定电影 ID 开始处理（断点续传，默认 1）')
    parser.add_argument('--limit', type=int, default=None,
                        help='阶段 2 本次最多处理数量（建议 500~2000 分批跑）')
    parser.add_argument('--movie-id', type=int, default=None,
                        help='单独处理指定电影 ID')
    parser.add_argument('--dry-run', action='store_true', help='预览不写入')
    args = parser.parse_args()

    if args.proxy:
        HTTP_PROXY = args.proxy
        print(f'使用代理: {HTTP_PROXY}')
    if args.pages:
        TMDB_CHART_PAGES = args.pages

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
        if args.movie_id:
            run_single_movie(db, args.movie_id, args.dry_run)
        else:
            if not args.all:
                run_chart_scrape(db, pages=TMDB_CHART_PAGES, dry_run=args.dry_run)
            if not args.chart_only:
                run_full_scrape(db, dry_run=args.dry_run,
                                start_from_id=args.start_from_id, limit=args.limit)
    finally:
        db.close()

    print('\n全部完成!')


if __name__ == '__main__':
    main()
