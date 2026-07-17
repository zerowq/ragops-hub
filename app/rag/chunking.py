from __future__ import annotations

import re


class StructureAwareChunker:
    def __init__(self, chunk_size: int = 500, overlap: int = 80) -> None:
        if chunk_size <= overlap:
            raise ValueError("chunk_size must be greater than overlap")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> list[str]:
        text = self._clean(text)
        if not text:
            return []
        sections = re.split(r"(?=^#{1,6}\s+|^第[一二三四五六七八九十百]+[章节条]\s*)", text, flags=re.M)
        chunks: list[str] = []
        for section in sections:
            section = section.strip()
            if not section:
                continue
            chunks.extend(self._split_section(section))
        return chunks

    def _split_section(self, section: str) -> list[str]:
        if len(section) <= self.chunk_size:
            return [section]
        paragraphs = [item.strip() for item in re.split(r"\n{2,}", section) if item.strip()]
        if len(paragraphs) == 1:
            paragraphs = [
                item.strip()
                for item in re.split(r"(?<=[。！？.!?])", section)
                if item.strip()
            ]
        result: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > self.chunk_size:
                prefix = current[-self.overlap :] if current else ""
                if current:
                    result.append(current)
                oversized = f"{prefix}\n{paragraph}".strip()
                result.extend(self._split_window(oversized))
                current = ""
                continue
            candidate = f"{current}\n{paragraph}".strip()
            if len(candidate) <= self.chunk_size:
                current = candidate
                continue
            if current:
                result.append(current)
                prefix = current[-self.overlap :]
                current = f"{prefix}\n{paragraph}".strip()
            else:
                result.extend(self._split_window(paragraph))
                current = ""
        if current:
            result.append(current)
        return result

    def _split_window(self, text: str) -> list[str]:
        result: list[str] = []
        start = 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            result.append(text[start:end])
            if end == len(text):
                break
            start = end - self.overlap
        return result

    @staticmethod
    def _clean(text: str) -> str:
        text = text.replace("\x00", "")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
