"""RAGRetriever - hybrid search: vector (0.7) + keyword (0.3)"""

import os
import sys
_p = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _p)
sys.path.insert(0, os.path.join(_p, 'backend'))

import chromadb
from schemas import RAGDocument


def _is_chinese(ch):
    return chr(0x4e00) <= ch <= chr(0x9fff)


def _extract_terms(text):
    terms = []
    buf = ''
    for ch in text.lower():
        if _is_chinese(ch):
            buf += ch
        elif ch.isascii() and ch.isalpha():
            buf += ch
        else:
            if buf:
                terms.append(buf)
                buf = ''
    if buf:
        terms.append(buf)
    return terms


def _keyword_score(query, content):
    terms = _extract_terms(query)
    if not terms:
        return 0.0
    cl = content.lower()
    score = sum(len(t) for t in terms if t in cl)
    total = sum(len(t) for t in terms)
    return min(score / total, 1.0)


class RAGRetriever:

    def __init__(self, chroma_persist_dir, collection_name="knowledge_base", vector_weight=0.7):
        if not os.path.isdir(chroma_persist_dir):
            raise FileNotFoundError("ChromaDB dir not found: " + chroma_persist_dir)
        self.vector_weight = vector_weight
        self.kw_weight = 1.0 - vector_weight
        self.client = chromadb.PersistentClient(path=chroma_persist_dir)
        self.collection = self.client.get_collection(collection_name)
        self._all_docs = self._load_all_docs()

    def _load_all_docs(self):
        results = self.collection.get()
        if not results.get('ids'):
            return []
        out = []
        for i in range(len(results['ids'])):
            meta = results['metadatas'][i] if results.get('metadatas') else {}
            out.append({
                'id': results['ids'][i],
                'content': results['documents'][i] or '',
                'source': meta.get('source', 'unknown')
            })
        return out

    def search(self, query, top_k=5):
        candidates = {}
        n_fetch = min(top_k * 5, len(self._all_docs))
        vec_raw = self.collection.query(query_texts=[query], n_results=n_fetch)
        if vec_raw.get('documents') and vec_raw['documents'][0]:
            for i in range(len(vec_raw['documents'][0])):
                did = vec_raw['ids'][0][i]
                vd = vec_raw['distances'][0][i] if vec_raw.get('distances') else 0.0
                vs = 1.0 - min(vd, 1.0)
                candidates[did] = {
                    'content': vec_raw['documents'][0][i],
                    'source': (vec_raw['metadatas'][0][i] or {}).get('source', 'unknown'),
                    'vec_score': vs,
                    'kw_score': 0.0,
                }

        vw = self.vector_weight
        kw = self.kw_weight
        for did in list(candidates.keys()):
            ks = _keyword_score(query, candidates[did]['content'])
            candidates[did]['kw_score'] = ks
            candidates[did]['combined'] = candidates[did]['vec_score'] * vw + ks * kw

        kw_candidates = []
        for doc in self._all_docs:
            if doc['id'] not in candidates:
                ks = _keyword_score(query, doc['content'])
                if ks > 0.1:
                    kw_candidates.append((doc, ks))
        kw_candidates.sort(key=lambda x: x[1], reverse=True)
        for doc, ks in kw_candidates[:top_k * 2]:
            candidates[doc['id']] = {
                'content': doc['content'],
                'source': doc['source'],
                'vec_score': 0.0,
                'kw_score': ks,
                'combined': ks * kw,
            }

        ranked = sorted(candidates.values(), key=lambda x: x['combined'], reverse=True)
        docs = []
        for info in ranked[:top_k]:
            docs.append(RAGDocument(
                content=info['content'],
                source=info['source'],
                score=round(info['combined'], 4),
            ))
        return docs

    def search_raw(self, query, top_k=5):
        return [d.model_dump() for d in self.search(query, top_k)]
