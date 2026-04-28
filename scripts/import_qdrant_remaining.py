"""
补录脚本：将 MySQL 中尚未同步到 Qdrant 的剩余电影数据补全。
基于 import_qdrant.py 首次运行时写入的 vector_synced_at 字段来判断哪些电影尚未同步。

使用方法:
  python scripts/import_qdrant_remaining.py
"""
import sys
import io
import time
import pymysql
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ============================================
# 配置区（与 import_qdrant.py 保持一致）
# ============================================
DB_CONFIG = {
    'host': '192.168.200.128',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}

QDRANT_HOST = "192.168.200.128"
QDRANT_PORT = 6333
COLLECTION_NAME = "movies"
MODEL_PATH = r"D:\Code\models\all-MiniLM-L6-v2"


def get_unsynced_movies(cursor):
    """查询 vector_synced_at 为 NULL 的未同步电影"""
    query = """
    SELECT
        m.id,
        m.title,
        m.release_year,
        GROUP_CONCAT(g.code SEPARATOR ', ') AS genres_str
    FROM movies m
    LEFT JOIN movies_genres mg ON m.id = mg.movie_id
    LEFT JOIN genres g ON mg.genre_id = g.id
    WHERE m.vector_synced_at IS NULL
    GROUP BY m.id
    ORDER BY m.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()
    movies = []
    for row in rows:
        movies.append({
            'id': row[0],
            'title': row[1],
            'release_year': row[2],
            'genres_str': row[3] if row[3] else ""
        })
    return movies


def build_text_for_embedding(movie):
    """将电影信息拼接成一段富含语义的自然语言文本"""
    title = movie['title']
    year = movie['release_year']
    genres = movie['genres_str'] if movie['genres_str'] else "Unknown"
    text = f"Title: {title}. "
    if year:
        text += f"Release Year: {year}. "
    text += f"Genres: {genres}."
    return text


def main():
    print("=" * 60)
    print("🎬 补录：将未同步的剩余电影导入 Qdrant")
    print("=" * 60)

    # --------------------------------------------------
    # Step 1: 加载向量模型
    # --------------------------------------------------
    print("\n1. 正在加载本地向量模型...")
    print(f"   模型路径: {MODEL_PATH}")
    sys.stdout.flush()
    model = SentenceTransformer(MODEL_PATH)
    vector_size = model.get_sentence_embedding_dimension()
    print(f"   ✅ 模型加载完成，输出向量维度: {vector_size}")
    sys.stdout.flush()

    # --------------------------------------------------
    # Step 2: 连接 Qdrant（不重建集合）
    # --------------------------------------------------
    print("\n2. 正在连接 Qdrant...")
    sys.stdout.flush()
    q_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print(f"   ✅ Qdrant 连接成功: {QDRANT_HOST}:{QDRANT_PORT}")
    sys.stdout.flush()

    if not q_client.collection_exists(COLLECTION_NAME):
        print(f"   ❌ 集合 '{COLLECTION_NAME}' 不存在！请先运行 import_qdrant.py")
        return

    # 获取当前 Qdrant 中的点数
    collection_info = q_client.get_collection(COLLECTION_NAME)
    current_count = collection_info.points_count
    print(f"   ℹ️  Qdrant 当前数据量: 约 {current_count} 条")
    sys.stdout.flush()

    # --------------------------------------------------
    # Step 3: 连接 MySQL 读取未同步数据
    # --------------------------------------------------
    print("\n3. 正在连接 MySQL 并查询未同步电影...")
    sys.stdout.flush()
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        movies = get_unsynced_movies(cursor)
        total = len(movies)

        if total == 0:
            print(f"\n   ✅ 所有电影均已同步至 Qdrant，无需补录！")
            return

        print(f"   ✅ 找到 {total} 部未同步的电影（vector_synced_at IS NULL）")
        sys.stdout.flush()

        with_genre = sum(1 for m in movies if m['genres_str'])
        print(f"   - 有题材信息的电影: {with_genre}")
        print(f"   - 无题材信息的电影: {total - with_genre}")
        sys.stdout.flush()

        if total > 0:
            print("\n   数据示例 (前 3 条):")
            for m in movies[:3]:
                embed_text = build_text_for_embedding(m)
                print(f"     ID={m['id']} | 拼接文本: {embed_text}")
            sys.stdout.flush()

        # --------------------------------------------------
        # Step 4: 向量化并批量写入 Qdrant
        # --------------------------------------------------
        print(f"\n4. 开始补录（批量大小 500）...")
        sys.stdout.flush()
        batch_size = 500

        for i in range(0, total, batch_size):
            batch = movies[i: i + batch_size]

            texts_to_encode = []
            synced_ids = []
            for m in batch:
                texts_to_encode.append(build_text_for_embedding(m))
                synced_ids.append(m['id'])

            t0 = time.time()
            embeddings = model.encode(texts_to_encode)
            t1 = time.time()
            print(f"   ⏱️  向量化耗时: {t1-t0:.1f}s", end="")

            points = []
            for idx, m in enumerate(batch):
                genres_list = [g.strip() for g in m['genres_str'].split(',') if g.strip()] if m['genres_str'] else []
                point = models.PointStruct(
                    id=m['id'],
                    vector=embeddings[idx].tolist(),
                    payload={
                        "title": m['title'],
                        "release_year": m['release_year'],
                        "genres": genres_list
                    }
                )
                points.append(point)

            t2 = time.time()
            q_client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
            t3 = time.time()
            print(f" | 写入 Qdrant 耗时: {t3-t2:.1f}s", end="")

            # 更新 MySQL 的 vector_synced_at 字段
            format_strings = ','.join(['%s'] * len(synced_ids))
            update_query = f"""
                UPDATE movies
                SET vector_synced_at = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """
            cursor.execute(update_query, tuple(synced_ids))
            conn.commit()

            progress = min(i + batch_size, total)
            print(f" | 进度: {progress} / {total} ({(progress / total * 100):.1f}%)")
            sys.stdout.flush()

        # --------------------------------------------------
        # Step 5: 最终验证
        # --------------------------------------------------
        print(f"\n{'=' * 60}")
        print("🎉 补录完成！")
        print(f"{'=' * 60}")
        print(f"📊 统计:")
        print(f"   - 本次补录: {total} 条")
        sys.stdout.flush()

        # 验证最终 Qdrant 数据量
        collection_info = q_client.get_collection(COLLECTION_NAME)
        qdrant_count = collection_info.points_count
        print(f"   - Qdrant 总数据量: 约 {qdrant_count} 条")
        print(f"{'=' * 60}")
        sys.stdout.flush()

    except Exception as e:
        print(f"\n❌ 执行过程中发生错误: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
        print("\n   ✅ MySQL 数据库连接已关闭")


if __name__ == "__main__":
    main()