#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
train_logger.py - 训练脚本通用日志工具

提供 verbose 模式下的详细日志输出：
  - verbose_init(name, enabled)    初始化日志文件目录
  - verbose_step(phase, details, enabled)  输出一步详细日志
  - verbose_close()                关闭日志文件
  - log_output(name)               上下文管理器，将 stdout/stderr 写入终端和日志文件

使用方式:
    from train_logger import log_output, verbose_init, verbose_step, verbose_close
    
    with log_output('train_xxx'):
        verbose_init('train_xxx', args.verbose)
        verbose_step("数据加载", f"读取文件: {path}", args.verbose)
        ...
        verbose_close()
"""

import os
import sys
import time
from datetime import datetime

# ─── 日志目录 ───
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, 'logs', 'verbose')
os.makedirs(LOG_DIR, exist_ok=True)

# 全局日志文件句柄
_log_file = None
_log_filepath = None
_step_count = 0
_start_time = None


def verbose_init(name, enabled=True):
    """
    初始化 verbose 日志系统。
    
    参数:
        name:    脚本名称（用于日志文件名）
        enabled: 是否启用 verbose 模式
    """
    global _log_file, _log_filepath, _step_count, _start_time
    
    if not enabled:
        _log_file = None
        return
    
    _step_count = 0
    _start_time = time.time()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_filepath = os.path.join(LOG_DIR, f'{name}_{timestamp}.log')
    _log_file = open(_log_filepath, 'w', encoding='utf-8')
    
    _write_log(f"[{name}] verbose 日志初始化")
    _write_log(f"日志文件: {_log_filepath}")
    _write_log(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _write_log("=" * 70)


def verbose_step(phase, details="", enabled=True):
    """
    输出一步详细日志。
    
    参数:
        phase:   当前阶段名称（如 "数据加载", "相似度计算"）
        details: 详细描述信息
        enabled: 是否启用 verbose 模式
    """
    global _step_count, _start_time
    
    if not enabled:
        return
    
    _step_count += 1
    elapsed = time.time() - _start_time if _start_time else 0.0
    
    timestamp = datetime.now().strftime('%H:%M:%S')
    header = f"[{_step_count}] [{timestamp}] [{phase}]"
    
    # 终端输出
    if details:
        print(f"  [verbose] {phase} | {details}")
    else:
        print(f"  [verbose] {phase}")
    
    # 文件输出（含所有细节）
    _write_log(f"{header}")
    if details:
        # 按行分割，保持格式化
        for line in details.split('\n'):
            _write_log(f"    {line}")
    _write_log(f"    耗时: {elapsed:.2f}s")
    _write_log("")


def verbose_close():
    """关闭 verbose 日志文件。"""
    global _log_file, _log_filepath, _start_time
    
    if _log_file is not None:
        elapsed = time.time() - _start_time if _start_time else 0.0
        _write_log("=" * 70)
        _write_log(f"日志结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        _write_log(f"总步数: {_step_count}")
        _write_log(f"总耗时: {elapsed:.2f}s")
        _write_log(f"日志文件: {_log_filepath}")
        _log_file.close()
        _log_file = None
        print(f"  [verbose] 日志已保存: {_log_filepath}")


def _write_log(text):
    """写入日志文件（内部方法）。"""
    global _log_file
    if _log_file is not None and not _log_file.closed:
        _log_file.write(text + '\n')
        _log_file.flush()


class _LogCapture:
    """
    同时捕获 stdout 并写入日志文件及终端。
    退出上下文时自动恢复 sys.stdout。
    """
    
    def __init__(self, name, filepath):
        self.name = name
        self.filepath = filepath
        self._original_stdout = sys.stdout
        self._log_fh = None
    
    def __enter__(self):
        self._log_fh = open(self.filepath, 'a', encoding='utf-8')
        # 记开头分隔线
        self._log_fh.write(f"\n{'=' * 70}\n")
        self._log_fh.write(f"[{self.name}] 标准输出日志\n")
        self._log_fh.write(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        self._log_fh.write(f"{'=' * 70}\n")
        self._log_fh.flush()
        return self
    
    def write(self, text):
        self._original_stdout.write(text)
        if self._log_fh and not self._log_fh.closed:
            self._log_fh.write(text)
            self._log_fh.flush()
    
    def flush(self):
        self._original_stdout.flush()
        if self._log_fh and not self._log_fh.closed:
            self._log_fh.flush()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._log_fh and not self._log_fh.closed:
            self._log_fh.write(f"{'=' * 70}\n")
            self._log_fh.write(f"[{self.name}] 日志结束\n")
            self._log_fh.write(f"结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            elapsed = time.time() - _start_time if _start_time else 0.0
            self._log_fh.write(f"总耗时: {elapsed:.2f}s\n")
            self._log_fh.close()
        # 恢复原始 stdout
        sys.stdout = self._original_stdout


def log_output(name):
    """
    返回一个上下文管理器，在此上下文内的 print 输出
    同时写入终端和日志文件 logs/{name}_stdout.log。
    
    用法:
        with log_output('train_itemcf_improved'):
            main()
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_path = os.path.join(LOG_DIR, f'{name}_stdout_{timestamp}.log')
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    capture = _LogCapture(name, log_path)
    sys.stdout = capture
    return capture