import unittest

from title_aliases import AliasMatch, match_title_alias, title_alias_terms


class TitleAliasTests(unittest.TestCase):
    def test_initials_keep_the_exact_contiguous_phrase_as_evidence(self):
        title = "Retrieval‑Augmented Generation for Knowledge-Intensive NLP Tasks"
        self.assertEqual(match_title_alias(" rAg ", title),
                         AliasMatch("initials", "Retrieval‑Augmented Generation"))
        self.assertEqual(match_title_alias("ＬＳＴＭ", "Long Short-Term Memory"),
                         AliasMatch("initials", "Long Short-Term Memory"))
        self.assertEqual(match_title_alias("GNN", "Graph Neural Networks: An Introduction"),
                         AliasMatch("initials", "Graph Neural Networks"))
        self.assertEqual(match_title_alias("rag", "retrieval-augmented generation"),
                         AliasMatch("initials", "retrieval-augmented generation"))

    def test_the_printed_name_takes_precedence_and_preserves_original_spelling(self):
        for query, title, source in [
            ("bert", "BERT: Pre-training of Deep Bidirectional Transformers", "BERT"),
            ("roberta", "RoBERTa: A Robustly Optimized Pretraining Approach", "RoBERTa"),
            ("rag", "Retrieval-Augmented Generation (ＲＡＧ)", "ＲＡＧ"),
            ("gpt-3.5", "How GPT-3.5 Solves Problems", "GPT-3.5"),
            ("bert", "BERT: PRE-TRAINING OF DEEP BIDIRECTIONAL TRANSFORMERS", "BERT"),
        ]:
            with self.subTest(query=query, title=title):
                self.assertEqual(match_title_alias(query, title), AliasMatch("explicit", source))

    def test_unrelated_substrings_versions_and_partial_initialisms_do_not_match(self):
        for query, title in [
            ("rag", "GraphRAG: A New Retrieval Approach"),
            ("rag", "Data Storage and Fragment Indexing"),
            ("gpt-3", "Results from GPT-3.5"),
            ("ra", "Retrieval-Augmented Generation"),
            ("rag", "Robust Retrieval-Augmented Generation Systems"),
            ("rgt", "Retrieval-Augmented Generation for Training"),
            ("gan", "Great and Novel Methods"),
            ("the", "THE IMPACT OF MODERN MODELS"),
            ("impact", "THE IMPACT OF MODERN MODELS"),
            ("nim", "New Improved Method"),
            ("abc", "another benchmark comparison"),
            ("deep bidirectional", "BERT: Deep Bidirectional Transformers"),
        ]:
            with self.subTest(query=query, title=title):
                self.assertIsNone(match_title_alias(query, title))

    def test_list_and_match_share_normalized_deduplicated_terms(self):
        title = "Retrieval-Augmented Generation (RAG): Using RAG with GPT-4"
        terms = title_alias_terms(title)
        self.assertEqual(terms, ("rag", "gpt-4"))
        for term in terms:
            self.assertIsNotNone(match_title_alias(term.upper(), title))

    def test_empty_or_missing_values(self):
        for value in (None, "", "  "):
            self.assertEqual(title_alias_terms(value), ())
            self.assertIsNone(match_title_alias(value, "Example Graph Networks"))
            self.assertIsNone(match_title_alias("egn", value))


if __name__ == "__main__":
    unittest.main()
