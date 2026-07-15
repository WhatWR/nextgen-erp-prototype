from __future__ import annotations

import unittest

from order_intake.ai.rag import KnowledgeIndex, _chunk_article
from order_intake.erpnext_client import ERPNextError


class FakeClient:
    configured = True

    def __init__(self, replies):
        self.replies = {name: list(values) for name, values in replies.items()}
        self.calls = []

    def call_method(self, method, **kwargs):
        self.calls.append((method, kwargs))
        values = self.replies.get(method)
        if not values:
            raise AssertionError(f"unexpected call: {method}")
        value = values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


ARTICLES = {
    "articles": [
        {
            "name": "KA-1",
            "title": "การจัดส่งสินค้า",
            "content": "จัดส่งทุกวันจันทร์ถึงเสาร์ ภายใน 2 วันทำการหลังชำระเงิน",
        },
        {
            "name": "KA-2",
            "title": "ช่องทางชำระเงิน",
            "content": "รับชำระผ่าน PromptPay เท่านั้น สแกน QR จากใบแจ้งหนี้",
        },
    ]
}


class ChunkingTest(unittest.TestCase):
    def test_paragraphs_pack_up_to_chunk_size(self):
        content = "หนึ่ง\n\nสอง\n\n" + ("ก" * 900)
        chunks = _chunk_article("หัวข้อ", content)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 700 for chunk in chunks))

    def test_empty_content_yields_no_chunks(self):
        self.assertEqual(_chunk_article("t", "   "), [])


class KnowledgeIndexTest(unittest.TestCase):
    def test_keyword_search_finds_thai_faq(self):
        client = FakeClient({"nextgen_erp.ai.get_knowledge_articles": [ARTICLES]})
        index = KnowledgeIndex(client)
        results = index.search("จัดส่งกี่วัน")
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "การจัดส่งสินค้า")

    def test_embeddings_are_cached_per_model_and_used_for_ranking(self):
        client = FakeClient({"nextgen_erp.ai.get_knowledge_articles": [ARTICLES]})
        index = KnowledgeIndex(client)
        embed_batches = []

        def embed(texts):
            embed_batches.append(list(texts))
            # First chunk aligned with the query vector, the rest orthogonal.
            return [[1.0, 0.0] if i == 0 else [0.0, 1.0] for i in range(len(texts))]

        first = index.search("คำถาม", embed=embed, embed_key="model-a")
        second = index.search("คำถามอื่น", embed=embed, embed_key="model-a")
        self.assertEqual(first[0]["title"], "การจัดส่งสินค้า")
        self.assertEqual(second[0]["title"], "การจัดส่งสินค้า")
        # chunks embedded once, then only the two queries
        self.assertEqual(len(embed_batches), 3)
        self.assertEqual(len(embed_batches[0]), 2)

    def test_embedding_failure_degrades_to_keyword_ranking(self):
        client = FakeClient({"nextgen_erp.ai.get_knowledge_articles": [ARTICLES]})
        index = KnowledgeIndex(client)

        def broken_embed(texts):
            from order_intake.ai.client import AIError

            raise AIError("no embeddings endpoint")

        results = index.search("จ่ายผ่าน PromptPay ได้ไหม", embed=broken_embed, embed_key="model-a")
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "ช่องทางชำระเงิน")

    def test_erpnext_failure_is_not_fatal(self):
        client = FakeClient(
            {"nextgen_erp.ai.get_knowledge_articles": [ERPNextError("site down")]}
        )
        index = KnowledgeIndex(client)
        self.assertEqual(index.search("จัดส่ง"), [])


if __name__ == "__main__":
    unittest.main()
