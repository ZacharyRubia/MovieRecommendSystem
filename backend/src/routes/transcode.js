const express = require('express');
const router = express.Router();
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const MEDIA_DIR = path.join(__dirname, '../../media');

// 检查 FFmpeg 是否可用
function checkFFmpeg() {
  return new Promise((resolve) => {
    const ffmpeg = spawn('ffmpeg', ['-version']);
    ffmpeg.on('close', (code) => {
      resolve(code === 0);
    });
    ffmpeg.on('error', () => {
      resolve(false);
    });
  });
}

// 实时转码流 - MKV 转 MP4 (H.264 + AAC)
router.get('/:filename', async (req, res) => {
  const filename = req.params.filename;
  const filePath = path.join(MEDIA_DIR, filename);
  
  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ success: false, message: '文件不存在' });
  }

  const ffmpegAvailable = await checkFFmpeg();
  if (!ffmpegAvailable) {
    return res.status(500).json({ 
      success: false, 
      message: 'FFmpeg 未安装，请安装 FFmpeg 后使用转码功能' 
    });
  }

  // 设置响应头
  res.writeHead(200, {
    'Content-Type': 'video/mp4',
    'Transfer-Encoding': 'chunked',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive'
  });

  // FFmpeg 转码参数
  // - 使用硬件加速（如果可用）
  // - 快速编码预设
  // - 流输出格式
  const ffmpegArgs = [
    '-i', filePath,
    '-c:v', 'libx264',
    '-preset', 'ultrafast',
    '-tune', 'zerolatency',
    '-crf', '28',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-movflags', 'frag_keyframe+empty_moov+faststart',
    '-f', 'mp4',
    '-'
  ];

  const ffmpeg = spawn('ffmpeg', ffmpegArgs);

  // 将转码输出流式传输到响应
  ffmpeg.stdout.pipe(res);

  // 处理错误
  ffmpeg.stderr.on('data', (data) => {
    // FFmpeg 的进度输出在 stderr，这里可以记录日志
    // console.log(`FFmpeg: ${data}`);
  });

  ffmpeg.on('error', (error) => {
    console.error('转码错误:', error);
    if (!res.headersSent) {
      res.status(500).json({ success: false, message: '转码失败' });
    }
  });

  ffmpeg.on('close', (code) => {
    if (code !== 0 && code !== null) {
      console.log(`FFmpeg 进程退出，代码: ${code}`);
    }
  });

  // 客户端断开时清理
  req.on('close', () => {
    ffmpeg.kill('SIGTERM');
  });
});

// 检查转码状态
router.get('/status/ffmpeg', async (req, res) => {
  const available = await checkFFmpeg();
  res.json({
    success: true,
    ffmpegAvailable: available,
    message: available ? 'FFmpeg 已就绪' : 'FFmpeg 未安装'
  });
});

module.exports = router;