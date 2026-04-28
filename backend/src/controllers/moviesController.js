const Movie = require('../models/Movie');
const Comment = require('../models/Comment');
const crypto = require('crypto');

// 生成唯一请求ID
function generateRequestId() {
  return crypto.randomUUID();
}

// 获取所有电影列表
const getAllMovies = async (req, res) => {
  console.log('\n========== [DEBUG] 获取电影列表请求开始 ==========');
  console.log('[DEBUG] 请求URL:', req.originalUrl);
  console.log('[DEBUG] 查询参数 page:', req.query.page, '类型:', typeof req.query.page);
  console.log('[DEBUG] 查询参数 limit:', req.query.limit, '类型:', typeof req.query.limit);
  
  try {
    // 解析并安全处理参数
    const page = Math.max(1, parseInt(req.query.page, 10) || 1);
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit, 10) || 20));
    
    console.log('[DEBUG] 解析后的 page:', page);
    console.log('[DEBUG] 解析后的 limit:', limit);
    console.log('[DEBUG] 计算 offset:', (page - 1) * limit);

    console.log('[DEBUG] 开始查询电影数据...');
    const movies = await Movie.findAll(page, limit);
    console.log('[DEBUG] 查询到的电影数量:', movies.length);
    console.log('[DEBUG] 电影数据:', JSON.stringify(movies, null, 2));

    console.log('[DEBUG] 开始查询电影总数...');
    const total = await Movie.count();
    console.log('[DEBUG] 电影总数:', total);

    const totalPages = Math.max(1, Math.ceil(total / limit));
    console.log('[DEBUG] 计算总页数:', totalPages);

    const response = {
      success: true,
      data: {
        movies,
        pagination: {
          page,
          limit,
          total,
          totalPages
        }
      }
    };
    console.log('[DEBUG] 返回响应:', JSON.stringify(response, null, 2));
    console.log('========== [DEBUG] 获取电影列表请求结束 ==========\n');

    res.json(response);
  } catch (error) {
    console.error('\n========== [ERROR] 获取电影列表失败 ==========');
    console.error('[ERROR] 错误名称:', error.name);
    console.error('[ERROR] 错误消息:', error.message);
    console.error('[ERROR] 错误堆栈:', error.stack);
    console.error('[ERROR] SQL错误代码:', error.code);
    console.error('[ERROR] SQL错误编号:', error.errno);
    console.error('==================================================\n');
    
    res.status(500).json({ 
      success: false, 
      message: '获取电影列表失败',
      debug: {
        error: error.message,
        code: error.code
      }
    });
  }
};

// 获取电影详情
const getMovieById = async (req, res) => {
  try {
    const { id } = req.params;
    const movie = await Movie.findById(id);
    
    if (!movie) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }

    res.json({ success: true, data: movie });
  } catch (error) {
    console.error('获取电影详情失败:', error);
    res.status(500).json({ success: false, message: '获取电影详情失败' });
  }
};

// 用户评分
const rateMovie = async (req, res) => {
  try {
    const { movieId } = req.params;
    const { userId, rating } = req.body;

    if (!userId || !rating) {
      return res.status(400).json({ success: false, message: '用户ID和评分不能为空' });
    }

    if (rating < 1 || rating > 5) {
      return res.status(400).json({ success: false, message: '评分必须在1-5之间' });
    }

    // 检查电影是否存在
    const movie = await Movie.findById(movieId);
    if (!movie) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }

    const requestId = generateRequestId();
    await Movie.addRating(userId, movieId, rating, requestId);

    // 获取更新后的电影信息
    const updatedMovie = await Movie.findById(movieId);
    
    res.json({
      success: true,
      message: '评分成功',
      data: {
        avgRating: updatedMovie.avg_rating,
        userRating: rating
      }
    });
  } catch (error) {
    console.error('评分失败:', error);
    res.status(500).json({ success: false, message: '评分失败' });
  }
};

// 获取电影评分列表
const getMovieComments = async (req, res) => {
  try {
    const { movieId } = req.params;
    const page = parseInt(req.query.page) || 1;
    const limit = parseInt(req.query.limit) || 20;

    const comments = await Movie.getComments(movieId, page, limit);
    const total = await Movie.countComments(movieId);

    res.json({
      success: true,
      data: {
        comments,
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit)
        }
      }
    });
  } catch (error) {
    console.error('获取评论失败:', error);
    res.status(500).json({ success: false, message: '获取评论失败' });
  }
};

// 获取用户对电影的评分
const getUserRating = async (req, res) => {
  try {
    const { userId, movieId } = req.params;
    
    if (!userId || !movieId) {
      return res.status(400).json({ success: false, message: '用户ID和电影ID不能为空' });
    }

    const rating = await Movie.getUserRating(userId, movieId);
    
    res.json({
      success: true,
      data: { rating }
    });
  } catch (error) {
    console.error('获取用户评分失败:', error);
    res.status(500).json({ success: false, message: '获取用户评分失败' });
  }
};

// ==================== 文本评论管理 ====================

// 获取电影文本评论列表（顶级评论，含回复数）
const getMovieTextComments = async (req, res) => {
  try {
    const { movieId } = req.params;
    const page = parseInt(req.query.page) || 1;
    const limit = parseInt(req.query.limit) || 20;

    const comments = await Comment.findByMovieId(movieId, page, limit);
    const total = await Comment.countByMovieId(movieId);

    res.json({
      success: true,
      data: {
        comments,
        pagination: {
          page,
          limit,
          total,
          totalPages: Math.ceil(total / limit)
        }
      }
    });
  } catch (error) {
    console.error('获取文本评论失败:', error);
    res.status(500).json({ success: false, message: '获取评论失败' });
  }
};

// 获取评论的回复列表
const getCommentReplies = async (req, res) => {
  try {
    const { commentId } = req.params;

    const exists = await Comment.exists(commentId);
    if (!exists) {
      return res.status(404).json({ success: false, message: '评论不存在' });
    }

    const replies = await Comment.findReplies(commentId);

    res.json({
      success: true,
      data: { replies }
    });
  } catch (error) {
    console.error('获取回复失败:', error);
    res.status(500).json({ success: false, message: '获取回复失败' });
  }
};

// 发表文本评论（或回复）
const addComment = async (req, res) => {
  try {
    const { movieId } = req.params;
    const { userId, content, parentId } = req.body;

    if (!userId || !content) {
      return res.status(400).json({ success: false, message: '用户ID和评论内容不能为空' });
    }

    if (content.trim().length === 0) {
      return res.status(400).json({ success: false, message: '评论内容不能为空' });
    }

    if (content.length > 500) {
      return res.status(400).json({ success: false, message: '评论内容不能超过500字' });
    }

    // 检查电影是否存在
    const movie = await Movie.findById(movieId);
    if (!movie) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }

    // 如果是回复，检查父评论是否存在
    if (parentId) {
      const parentExists = await Comment.exists(parentId);
      if (!parentExists) {
        return res.status(404).json({ success: false, message: '要回复的评论不存在' });
      }
      // 确保父评论属于同一电影
      const parentMovieId = await Comment.getMovieId(parentId);
      if (parentMovieId !== parseInt(movieId)) {
        return res.status(400).json({ success: false, message: '回复的评论不属于该电影' });
      }
    }

    const requestId = generateRequestId();
    const commentId = await Comment.create(userId, movieId, content, requestId, parentId || null);

    res.json({
      success: true,
      message: parentId ? '回复成功' : '评论成功',
      data: { id: commentId }
    });
  } catch (error) {
    console.error('评论失败:', error);
    res.status(500).json({ success: false, message: '评论失败' });
  }
};

// 删除文本评论（用户删除自己的，管理员可删除任意）
const deleteComment = async (req, res) => {
  try {
    const { commentId } = req.params;
    const { userId, isAdmin } = req.body;

    if (!userId) {
      return res.status(400).json({ success: false, message: '用户ID不能为空' });
    }

    const exists = await Comment.exists(commentId);
    if (!exists) {
      return res.status(404).json({ success: false, message: '评论不存在' });
    }

    // 检查权限并删除
    const adminFlag = isAdmin === true || isAdmin === 'true' || isAdmin === 1 || isAdmin === '1';
    const deleted = await Comment.delete(commentId, userId, adminFlag);

    if (!deleted) {
      return res.status(403).json({ success: false, message: '没有权限删除此评论' });
    }

    res.json({
      success: true,
      message: '评论删除成功'
    });
  } catch (error) {
    console.error('删除评论失败:', error);
    res.status(500).json({ success: false, message: '删除评论失败' });
  }
};

// 记录用户观看行为
const recordView = async (req, res) => {
  try {
    const { movieId } = req.params;
    const { userId, progressSeconds, durationSeconds } = req.body;

    if (!userId) {
      return res.status(400).json({ success: false, message: '用户ID不能为空' });
    }

    // 检查电影是否存在
    const movie = await Movie.findById(movieId);
    if (!movie) {
      return res.status(404).json({ success: false, message: '电影不存在' });
    }

    const requestId = generateRequestId();
    
    // 这里可以添加记录观看行为的逻辑
    // 暂时只返回成功，后续可以完善观看进度记录
    
    res.json({
      success: true,
      message: '观看记录已更新'
    });
  } catch (error) {
    console.error('记录观看失败:', error);
    res.status(500).json({ success: false, message: '记录观看失败' });
  }
};

module.exports = {
  getAllMovies,
  getMovieById,
  rateMovie,
  getMovieComments,
  getUserRating,
  getMovieTextComments,
  getCommentReplies,
  addComment,
  deleteComment,
  recordView
};