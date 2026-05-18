/**
 * abTestMiddleware.js - A/B 测试流量分发中间件
 * 
 * 功能：
 * 1. 对每个推荐请求，计算用户分桶
 * 2. 解析命中的实验和策略
 * 3. 将实验标识挂载到 req.experiment 上供后续使用
 * 4. 响应头附加实验信息用于前端埋点校验
 * 
 * 接入点：
 * - 应在认证中间件之后、推荐路由处理函数之前插入
 * - 只影响有 user 上下文的推荐请求
 */

const abTestService = require('../services/abTestService');

/**
 * A/B 测试中间件工厂
 * 
 * @param {Object} options
 * @param {string[]} options.routes - 需要应用中间件的路由路径列表
 * @param {boolean} options.requireUser - 是否要求用户已登录
 * @returns {Function} Express 中间件
 */
function createABTestMiddleware(options = {}) {
  const { routes = [], requireUser = true } = options;

  return async function abTestMiddleware(req, res, next) {
    // 如果指定了 routes 且当前路径不匹配，跳过
    if (routes.length > 0) {
      const matches = routes.some(route => req.path.startsWith(route));
      if (!matches) {
        return next();
      }
    }

    // 提取用户ID（优先从登录用户取，其次设备指纹）
    let userId = null;

    if (req.user && req.user.id) {
      userId = req.user.id;
    } else if (req.params && req.params.userId) {
      userId = parseInt(req.params.userId);
    } else if (req.query && req.query.userId) {
      userId = parseInt(req.query.userId);
    } else if (req.headers['x-device-id']) {
      // 未登录用户使用设备指纹（MD5后取整）
      userId = req.headers['x-device-id'];
    }

    // 未获取到有效标识
    if (!userId) {
      if (requireUser) {
        // 需要登录但未登录：不清除已有实验信息，继续请求
        req.experiment = null;
      }
      return next();
    }

    try {
      // 解析该用户的实验命中策略
      const experiments = await abTestService.resolveStrategy(userId);

      // 挂载到请求上
      req.experiment = experiments.length > 0 ? experiments : null;

      // 响应时附加实验信息（便于前端埋点校验）
      if (req.experiment && req.experiment.length > 0) {
        const expInfo = req.experiment.map(e => ({
          expId: e.experimentId,
          expName: e.experimentName,
          stratId: e.strategyId,
          stratName: e.strategyName
        }));
        res.setHeader('X-Experiment-Info', JSON.stringify(expInfo));
      }
    } catch (err) {
      // 中间件容错：AB 测试不应阻塞正常推荐
      console.error('[ABTest] Middleware 解析实验失败:', err.message);
      req.experiment = null;
    }

    next();
  };
}

module.exports = { createABTestMiddleware };