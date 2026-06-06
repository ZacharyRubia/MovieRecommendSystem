#!/usr/bin/env python3
"""
将项目中所有代码文件去除注释和空行后，导出到一个 Markdown 文件中。
支持：.py .js .html .sql .bat .ps1 .sh
"""

import re
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 需要导出的文件扩展名
CODE_EXTENSIONS = {'.py', '.js', '.html', '.sql', '.bat', '.ps1', '.sh'}

# 需要排除的目录
EXCLUDE_DIRS = {
    'node_modules', '.git', '__pycache__', 'scripts.zip',
    'evaluation_cache', 'coldstart_results',  # 大数据/缓存目录
}

OUTPUT_FILE = PROJECT_ROOT / 'exported_code.md'


# ── 注释移除函数 ──────────────────────────────────────────────

def remove_py_comments(text: str) -> str:
    """移除 Python 注释(# 和 多行字符串)"""
    # 移除多行字符串 ("""...""" 和 '''...''')
    text = re.sub(r'("""[\s\S]*?""")', _py_str_replacer, text)
    text = re.sub(r"('''[\s\S]*?''')", _py_str_replacer, text)
    # 移除 # 注释 (保留字符串内的 #)
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        # 简单处理：找到不在引号内的 #
        line = re.sub(r'(?<!")(?<!\w)#.*$', '', line)
        # 也处理行尾 ## 注释
        line = re.sub(r'#.*$', '', line)
        # 如果移除注释后整行只剩空格，跳过
        if line.strip():
            lines.append(line.rstrip())
    return '\n'.join(lines)


def _py_str_replacer(match):
    """将多行字符串替换为等长空行，保持行号"""
    s = match.group(0)
    return '\n' * s.count('\n')


def remove_js_comments(text: str) -> str:
    """移除 JS 注释 (// 和 块注释)"""
    # 先移除块注释
    text = re.sub(r'/\*[\s\S]*?\*/', lambda m: '\n' * m.group(0).count('\n'), text)
    lines = []
    for line in text.split('\n'):
        # 移除 //
        line = re.sub(r'//.*$', '', line)
        if line.strip():
            lines.append(line.rstrip())
    return '\n'.join(lines)


def remove_html_comments(text: str) -> str:
    """移除 HTML 注释"""
    text = re.sub(r'<!--[\s\S]*?-->', lambda m: '\n' * m.group(0).count('\n'), text)
    lines = [line.rstrip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)


def remove_sql_comments(text: str) -> str:
    """移除 SQL 注释 (-- 和 块注释)"""
    text = re.sub(r'/\*[\s\S]*?\*/', lambda m: '\n' * m.group(0).count('\n'), text)
    lines = []
    for line in text.split('\n'):
        line = re.sub(r'--.*$', '', line)
        if line.strip():
            lines.append(line.rstrip())
    return '\n'.join(lines)


def remove_bat_comments(text: str) -> str:
    """移除 .bat 注释 (REM, ::)"""
    lines = []
    for line in text.split('\n'):
        stripped = line.strip().upper()
        if stripped.startswith('REM ') or stripped.startswith('REM\t') or stripped == 'REM':
            continue
        if stripped.startswith('::'):
            continue
        if line.strip():
            lines.append(line.rstrip())
    return '\n'.join(lines)


def remove_hash_comments(text: str) -> str:
    """移除 # 注释 (用于 .ps1, .sh)"""
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        line = re.sub(r'#.*$', '', line)
        if line.strip():
            lines.append(line.rstrip())
    return '\n'.join(lines)


def remove_ps1_multiline_comments(text: str) -> str:
    """移除 PowerShell 多行注释 <#...#>"""
    text = re.sub(r'<#[\s\S]*?#>', lambda m: '\n' * m.group(0).count('\n'), text)
    return text


# ── 文件类型到处理函数的映射 ──────────────────────────────

COMMENT_REMOVERS = {
    '.py':   remove_py_comments,
    '.js':   remove_js_comments,
    '.html': remove_html_comments,
    '.sql':  remove_sql_comments,
    '.bat':  remove_bat_comments,
    '.ps1':  lambda t: remove_hash_comments(remove_ps1_multiline_comments(t)),
    '.sh':   remove_hash_comments,
}

# ── Markdown 代码块语言标记 ──────────────────────────────

LANG_MAP = {
    '.py':   'python',
    '.js':   'javascript',
    '.html': 'html',
    '.sql':  'sql',
    '.bat':  'batch',
    '.ps1':  'powershell',
    '.sh':   'bash',
}


def collect_files() -> list:
    """收集所有需要导出的代码文件"""
    files = []
    for ext in CODE_EXTENSIONS:
        for fpath in PROJECT_ROOT.rglob(f'*{ext}'):
            # 排除指定目录
            parts = set(fpath.relative_to(PROJECT_ROOT).parts)
            if parts & EXCLUDE_DIRS:
                continue
            # 确保父目录未被排除
            skip = False
            for p in fpath.relative_to(PROJECT_ROOT).parts:
                if p in EXCLUDE_DIRS:
                    skip = True
                    break
            if skip:
                continue
            files.append(fpath)
    # 按相对路径排序
    files.sort(key=lambda p: str(p.relative_to(PROJECT_ROOT)))
    return files


def process_file(fpath: Path) -> str:
    """读取文件，移除注释和空行"""
    ext = fpath.suffix.lower()
    try:
        with open(fpath, 'r', encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError:
        try:
            with open(fpath, 'r', encoding='gbk') as f:
                text = f.read()
        except Exception:
            print(f'  [跳过] 编码错误: {fpath.relative_to(PROJECT_ROOT)}')
            return None

    remover = COMMENT_REMOVERS.get(ext)
    if remover:
        text = remover(text)

    # 额外去空行
    lines = [l.rstrip() for l in text.split('\n') if l.strip()]
    text = '\n'.join(lines)

    if not text.strip():
        print(f'  [跳过] 去除后为空: {fpath.relative_to(PROJECT_ROOT)}')
        return None

    return text


def generate_markdown(files: list) -> str:
    """生成 Markdown 内容"""
    # 目录
    toc_lines = ['# 项目代码导出\n']
    toc_lines.append('## 目录\n')
    for fpath in files:
        rel = str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/')
        toc_lines.append(f'- [{rel}](#{_anchor(rel)})')
    toc_lines.append('')

    # 正文
    body_lines = []
    for fpath in files:
        rel = str(fpath.relative_to(PROJECT_ROOT)).replace('\\', '/')
        lang = LANG_MAP.get(fpath.suffix.lower(), '')
        print(f'处理: {rel}')
        code = process_file(fpath)
        if code is None:
            # 从目录中也没法移除，先保留但标记为跳过
            continue

        body_lines.append(f'\n## {rel}\n')
        body_lines.append(f'```{lang}')
        body_lines.append(code)
        body_lines.append('```\n')

    return '\n'.join(toc_lines) + '\n' + '\n'.join(body_lines)


def _anchor(path: str) -> str:
    """生成 GitHub 风格的锚点"""
    # 简化：直接用路径作为锚点文本，GitHub 会自动处理
    return path.replace('/', '').replace('.', '').replace('-', '').lower()


def main():
    print('收集代码文件...')
    files = collect_files()
    print(f'找到 {len(files)} 个文件\n')

    print('生成 Markdown...')
    md_content = generate_markdown(files)

    # 计算大小
    size_kb = len(md_content.encode('utf-8')) / 1024
    OUTPUT_FILE.write_text(md_content, encoding='utf-8')
    print(f'\n已导出到: {OUTPUT_FILE}')
    print(f'文件大小: {size_kb:.1f} KB')
    print(f'代码行数(去除注释和空行后): {md_content.count(chr(10)) - md_content[:md_content.index("```")].count(chr(10))}')


if __name__ == '__main__':
    main()
