# 电影视频文件设置指南

## 架构说明

### 媒体文件存储位置
视频文件存储在 **后端** `backend/media/` 目录，而不是前端。

**原因：**
- 后端可以提供流媒体服务（Range 请求）
- 支持大文件和 4K 视频的分段传输
- 便于统一管理和权限控制
- 符合标准流媒体服务器架构

### 支持的视频格式
| 格式 | 视频编码 | 音频编码 | 浏览器兼容性 |
|------|----------|----------|-------------|
| **MP4 (推荐)** | H.264 | AAC | ✅ 所有浏览器 |
| MKV | H.264 | AAC | ⚠️ Chrome/Edge 可能无声音 |
| MKV | H.264 | DTS/AC3 | ❌ 浏览器不支持音频 |
| WebM | VP9 | Opus | ✅ Chrome/Firefox |
| AVI | 多种 | 多种 | ❌ 不推荐 |

**最佳实践：MP4 (H.264 + AAC) 是兼容性最好的格式**

## 完整设置步骤

### 步骤 1：准备视频文件
1. 将视频文件放入 `backend/media/` 目录
2. 建议使用 MP4 格式以获得最佳兼容性
3. 文件名建议使用英文和数字，避免空格

### 步骤 2：更新数据库
在 `movie` 表中设置 `video_url` 字段：

```sql
-- 使用后端媒体文件（推荐）
UPDATE movie 
SET video_url = 'video-file-name.mp4' 
WHERE id = 1;

-- 或者带完整路径
UPDATE movie 
SET video_url = '/api/media/video-file-name.mkv' 
WHERE id = 2;

-- 也可以使用外部 URL
UPDATE movie 
SET video_url = 'https://example.com/videos/movie.mp4' 
WHERE id = 3;
```

### 步骤 3：验证设置
1. 重启后端服务
2. 登录系统
3. 进入电影详情页
4. 点击"播放电影"按钮

## MKV 无声音问题解决方案

### 问题原因
- MKV 容器内的音频编码可能是 DTS、AC3、E-AC3 等
- Chrome、Edge 等浏览器仅原生支持 AAC、MP3、Opus
- 即使视频能播放，音频也可能无声

### 解决方案 1：转换为 MP4（推荐）

```bash
# 使用 FFmpeg 转换，保留视频，转换音频为 AAC
ffmpeg -i input.mkv -c:v copy -c:a aac -b:a 192k output.mp4

# 批量转换 MKV 文件
for %i in (*.mkv) do ffmpeg -i "%i" -c:v copy -c:a aac -b:a 192k "%~ni.mp4"
```

### 解决方案 2：使用支持的浏览器
- **Firefox** 对 MKV/DTS 的支持比 Chrome 更好
- 安装 K-Lite Codec Pack 可能改善 Windows 上的播放

### 解决方案 3：使用外部播放器
- 下载视频后使用 VLC、MPC-HC 等专业播放器播放

## 4K 视频播放说明

### 系统要求
- **CPU**: 4 核心以上，建议 8 核心
- **内存**: 8GB 以上，建议 16GB
- **网络**: 建议 50Mbps 以上带宽
- **浏览器**: Chrome 70+ / Firefox 65+ / Edge 79+

### 优化建议
1. **使用 H.265 (HEVC) 编码**: 相同质量体积减小 50%
2. **启用硬件加速**: 浏览器设置中启用
3. **关闭其他程序**: 释放系统资源
4. **使用有线网络**: 比 WiFi 更稳定

## 后端 API 说明

### 获取媒体文件
```
GET /api/media/{filename}
```
- 支持 Range 请求（分段下载）
- 支持流式播放
- 返回原始文件流

### 获取媒体列表
```
GET /api/media
```
返回 `backend/media` 目录下的所有视频文件列表。

## 示例 SQL 脚本

```sql
-- 查看所有电影及其视频地址
SELECT id, title, video_url FROM movie;

-- 批量设置示例视频
UPDATE movie SET video_url = 'avatar.mp4' WHERE title LIKE '%阿凡达%';
UPDATE movie SET video_url = 'inception.mp4' WHERE title LIKE '%盗梦空间%';
UPDATE movie SET video_url = 'interstellar.mp4' WHERE title LIKE '%星际穿越%';

-- 清除视频地址
UPDATE movie SET video_url = NULL WHERE id = 99;
```

## 常见问题

### Q: 视频播放卡顿怎么办？
A: 
1. 检查网络带宽，建议使用有线连接
2. 降低视频分辨率或码率
3. 关闭浏览器其他标签页释放内存
4. 确保视频使用 H.264 编码

### Q: MKV 转 MP4 后画质下降？
A: 使用 `-c:v copy` 参数直接复制视频流，只重新编码音频：
```bash
ffmpeg -i input.mkv -c:v copy -c:a aac -b:a 192k output.mp4
```

### Q: 大文件上传失败？
A: 直接将文件复制到 `backend/media/` 目录，然后更新数据库即可。

### Q: 如何支持字幕？
A: 目前需要在转换视频时烧录字幕到视频中：
```bash
ffmpeg -i input.mkv -vf subtitles=input.mkv -c:a aac output.mp4
```

## 目录结构

```
MovieRecommendSystem/
├── backend/
│   ├── server.js
│   ├── media/                    # 视频文件存储目录
│   │   ├── avatar.mp4
│   │   ├── inception.mkv
│   │   └── ...
│   └── ...
├── frontend/
│   └── public/
│       ├── movie-player.html     # 播放页面
│       ├── movie-detail.html     # 详情页面
│       └── ...
└── ...