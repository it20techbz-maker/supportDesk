"""Approved feature documentation. Feature answers come from here and nowhere else.

Each Markdown file in the knowledge folder is split at its "## " headings.
Every heading plus the text under it is one answer.
"""
import glob
import os
import re

_STOP = {
    "this", "that", "with", "from", "have", "does", "what", "where", "which", "when", "there",
    "could", "would", "please", "possible", "system", "about", "your", "into", "want", "need",
    "like", "know", "them", "they", "will", "been", "also", "some", "tell",
}


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _STOP}


class Knowledge:
    def __init__(self, folder):
        self.entries = []
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            if os.path.basename(path).lower() == "readme.md":
                continue
            with open(path, encoding="utf-8") as f:
                parts = re.split(r"^## +", f.read(), flags=re.MULTILINE)[1:]
            for part in parts:
                title, _, body = part.partition("\n")
                if body.strip():
                    self.entries.append({"title": title.strip(), "body": body.strip(), "source": os.path.basename(path)})

    def search(self, question):
        """Best matching entry, or None when nothing matches well enough."""
        q = _words(question)
        best, best_score = None, 0
        for e in self.entries:
            score = 2 * len(q & _words(e["title"])) + len(q & _words(e["body"]))
            if score > best_score:
                best, best_score = e, score
        return best if best_score >= 3 else None
