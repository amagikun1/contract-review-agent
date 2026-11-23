"""小型中文知识库：重叠切分 → TF-IDF 稀疏向量 → 余弦检索。"""
import json
from pathlib import Path
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

class KnowledgeBase:
    def __init__(self):
        self.documents = json.loads(Path(__file__).with_name('knowledge.json').read_text(encoding='utf-8'))
        self.chunks = []
        for doc in self.documents:
            for offset in range(0, len(doc['text']), 240):
                self.chunks.append({**doc, 'chunk_id': f"{doc['id']}:{offset}", 'text': doc['text'][offset:offset+300]})
        self.vectorizer = TfidfVectorizer(analyzer='char', ngram_range=(1, 3), sublinear_tf=True)
        self.vectors = self.vectorizer.fit_transform([d['title']+' '+d['text'] for d in self.chunks])

    def search(self, query: str, limit: int = 4) -> list[dict]:
        scores = cosine_similarity(self.vectorizer.transform([query]), self.vectors)[0]
        return [{**self.chunks[i], 'score': round(float(scores[i]), 4)} for i in scores.argsort()[::-1][:max(1,min(limit,8))] if scores[i] > 0]

knowledge = KnowledgeBase()
