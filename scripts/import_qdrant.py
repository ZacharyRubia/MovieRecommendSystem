"""
将 MySQL 中的电影数据向量化并导入 Qdrant（Content-Based 向量库）

数据流向:
  MySQL (movies + genres) --> sentence-transformers 向量化 --> Qdrant (movies 集合)

表关系:
  - movies 表: id, title, release_year
  - movies_genres 表: movie_id, genre_id   (关联)
  - genres 表: id, code, name              (题材字典)

环境依赖:
  pip install pymysql qdrant-client sentence-transformers pandas
"""
import sys
import io
import time
import pymysql
from qdrant_client import QdrantClient
from qdrant_client.http import models
from sentence_transformers import SentenceTransformer

# Windows 控制台 UTF-8 编码处理
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ============================================
# 配置区
# ============================================
# MySQL 配置（与后端一致，指向 Ubuntu VM）
DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}

# Qdrant 配置（指向 Ubuntu VM 上的 Qdrant 服务）
QDRANT_HOST = "192.168.1.38"
QDRANT_PORT = 6333
COLLECTION_NAME = "movies"

# 本地模型路径（已通过 scripts/download_model.py 下载到 D:\Code\models\all-MiniLM-L6-v2）
MODEL_PATH = r"D:\Code\models\all-MiniLM-L6-v2"


def get_movies_with_genres(cursor):
    """
    从 MySQL 获取电影及其题材列表，返回 list[dict]
    每个 dict: { id, title, release_year, genres_str }

    SQL 说明:
      - movies 表存放电影基础信息
      - movies_genres 是电影-题材关联表
      - genres 表存放题材字典 (code 字段为题材英文名)
    """
    query = """
    SELECT
        m.id,
        m.title,
        m.release_year,
        GROUP_CONCAT(g.code SEPARATOR ', ') AS genres_str
    FROM movies m
    LEFT JOIN movies_genres mg ON m.id = mg.movie_id
    LEFT JOIN genres g ON mg.genre_id = g.id
    GROUP BY m.id
    ORDER BY m.id
    """
    cursor.execute(query)
    rows = cursor.fetchall()

    # PyMySQL 返回的是元组列表，需要手动映射列名
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
    """
    将电影信息拼接成一段富含语义的自然语言文本，供向量化模型使用
    """
    title = movie['title']
    year = movie['release_year']
    genres = movie['genres_str'] if movie['genres_str'] else "Unknown"

    # 构建英文语义文本
    text = f"Title: {title}. "
    if year:
        text += f"Release Year: {year}. "
    text += f"Genres: {genres}."
    return text


def main():
    print("=" * 60)
    print("🎬 电影数据向量化并导入 Qdrant")
    print("=" * 60)

    # --------------------------------------------------
    # Step 1: 初始化向量模型
    # --------------------------------------------------
    print("\n1. 正在加载本地向量模型...")
    print(f"   模型路径: {MODEL_PATH}")
    sys.stdout.flush()

    model = SentenceTransformer(MODEL_PATH)
    vector_size = model.get_sentence_embedding_dimension()
    print(f"   ✅ 模型加载完成，输出向量维度: {vector_size}")
    sys.stdout.flush()

    # --------------------------------------------------
    # Step 2: 连接 Qdrant 并准备集合
    # --------------------------------------------------
    print("\n2. 正在连接 Qdrant...")
    sys.stdout.flush()
    q_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    print(f"   ✅ Qdrant 连接成功: {QDRANT_HOST}:{QDRANT_PORT}")
    sys.stdout.flush()

    # 如果集合已存在，清空重建以确保数据和向量维度一致
    if q_client.collection_exists(COLLECTION_NAME):
        print(f"   ℹ️  集合 '{COLLECTION_NAME}' 已存在，正在删除重建...")
        q_client.delete_collection(COLLECTION_NAME)

    q_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=models.VectorParams(
            size=vector_size,
            distance=models.Distance.COSINE
        ),
    )
    print(f"   ✅ 集合 '{COLLECTION_NAME}' 创建成功（Cosine 相似度，{vector_size} 维）")
    sys.stdout.flush()

    # --------------------------------------------------
    # Step 3: 连接 MySQL 并读取数据
    # --------------------------------------------------
    print("\n3. 正在连接 MySQL 并读取电影数据...")
    sys.stdout.flush()
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    try:
        movies = get_movies_with_genres(cursor)
        total_movies = len(movies)
        print(f"   ✅ 成功从 MySQL 读取 {total_movies} 部电影数据")
        sys.stdout.flush()

        # 统计信息
        with_genre = sum(1 for m in movies if m['genres_str'])
        print(f"   - 有题材信息的电影: {with_genre}")
        print(f"   - 无题材信息的电影: {total_movies - with_genre}")
        sys.stdout.flush()

        # 显示前 3 条示例
        print("\n   数据示例 (前 3 条):")
        for m in movies[:3]:
            embed_text = build_text_for_embedding(m)
            print(f"     ID={m['id']} | 拼接文本: {embed_text}")
        sys.stdout.flush()

        # --------------------------------------------------
        # Step 4: 向量化并批量写入 Qdrant
        # --------------------------------------------------
        print(f"\n4. 开始向量化并批量写入 Qdrant...")
        sys.stdout.flush()
        batch_size = 500  # 每批处理 500 条

        for i in range(0, total_movies, batch_size):
            batch = movies[i: i + batch_size]

            # 4a. 构建文本列表和对应的 ID
            texts_to_encode = []
            synced_ids = []
            for m in batch:
                texts_to_encode.append(build_text_for_embedding(m))
                synced_ids.append(m['id'])

            # 4b. 批量向量化（模型内部自动并行，效率远高于逐条编码）
            t0 = time.time()
            embeddings = model.encode(texts_to_encode)
            t1 = time.time()
            print(f"   ⏱️  向量化耗时: {t1-t0:.1f}s", end="")

            # 4c. 组装 Qdrant PointStruct
            points = []
            for idx, m in enumerate(batch):
                # genres 字段存为字符串列表，便于 Qdrant 的前置过滤
                genres_list = [g.strip() for g in m['genres_str'].split(',') if g.strip()] if m['genres_str'] else []

                point = models.PointStruct(
                    id=m['id'],                             # 必须与 MySQL movie.id 严格一致
                    vector=embeddings[idx].tolist(),        # numpy -> Python list
                    payload={
                        "title": m['title'],
                        "release_year": m['release_year'],
                        "genres": genres_list
                    }
                )
                points.append(point)

            # 4d. 写入 Qdrant
            t2 = time.time()
            q_client.upsert(
                collection_name=COLLECTION_NAME,
                points=points
            )
            t3 = time.time()
            print(f" | 写入 Qdrant 耗时: {t3-t2:.1f}s", end="")

            # 4e. 更新 MySQL 的 vector_synced_at 字段，标记已同步
            format_strings = ','.join(['%s'] * len(synced_ids))
            update_query = f"""
                UPDATE movies
                SET vector_synced_at = CURRENT_TIMESTAMP
                WHERE id IN ({format_strings})
            """
            cursor.execute(update_query, tuple(synced_ids))
            conn.commit()

            progress = min(i + batch_size, total_movies)
            print(f" | 进度: {progress} / {total_movies} ({(progress / total_movies * 100):.1f}%)")
            sys.stdout.flush()

        # --------------------------------------------------
        # Step 5: 最终汇总统计
        # --------------------------------------------------
        print(f"\n{'=' * 60}")
        print("🎉 全部电影特征向量已成功写入 Qdrant！")
        print(f"{'=' * 60}")
        print(f"📊 统计汇总:")
        print(f"   - 集合名称:        {COLLECTION_NAME}")
        print(f"   - 向量维度:        {vector_size}")
        print(f"   - 写入向量总数:    {total_movies}")
        print(f"{'=' * 60}")
        sys.stdout.flush()

        # 验证 Qdrant 中的数据量
        collection_info = q_client.get_collection(COLLECTION_NAME)
        qdrant_count = collection_info.points_count
        print(f"\n🔍 Qdrant 集合状态验证: 约 {qdrant_count} 个向量点")

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