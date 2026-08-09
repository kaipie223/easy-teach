"""文档加载器 — 遍历 knowledge-base/ 加载所有支持的文档"""

import os
import sys
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _project_root)
sys.path.insert(0, os.path.join(_project_root, "backend"))


def _extract_pdf(path):
    from gen.parse.pdf import parse_smart_pdf
    result = parse_smart_pdf(path)
    if result.get("status") == "success":
        return result.get("data", "")
    print(f"[loader] PDF 解析失败: {result.get('message')}")
    return ""


def _extract_docx(path):
    from gen.parse.doc import parse_comprehensive_word
    result = parse_comprehensive_word(path)
    if result.get("status") == "success":
        return result.get("data", "")
    print(f"[loader] DOCX 解析失败: {result.get('message')}")
    return ""


def _extract_txt(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()


_HANDLERS = {'.pdf': _extract_pdf, '.docx': _extract_docx, '.txt': _extract_txt}

def load_documents(kb_dir):
    docs = []
    if not os.path.isdir(kb_dir):
        print(f'[loader] dir not found: {kb_dir}')
        return docs
    for root, dirs, files in os.walk(kb_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in {'.pdf', '.txt', '.docx'}:
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, kb_dir)
            try:
                content = _HANDLERS[ext](fpath)
                if content.strip():
                    docs.append({'content': content, 'source': rel_path, 'type': ext})
                    print(f'[loader] loaded: {rel_path}')
            except Exception as e:
                print(f'[loader] skip {rel_path}: {e}')
    print(f'[loader] total: {len(docs)} documents')
    return docs
