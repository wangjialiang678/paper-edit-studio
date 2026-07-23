from __future__ import annotations

import unittest

from cutpoint_lab.quality.ai_review import review
from cutpoint_lab.quality.corrections import undo_changeset


def _rows() -> list[dict]:
    return [
        {"id": "sentence_0054", "text": "我们来到现场。"},
        {"id": "sentence_0055", "text": "这是超导的项目。"},
        {"id": "sentence_0056", "text": "下面继续介绍。"},
    ]


def _issue(
    *,
    segment_id: str = "sentence_0055",
    text: str = "超导",
    confidence: float = 0.4,
    status: str = "open",
) -> dict:
    return {
        "id": "low-1",
        "segment_id": segment_id,
        "kind": "low_confidence",
        "span": {"text": text, "token_start": 1, "token_end": 2},
        "confidence": confidence,
        "reason": "低置信",
        "source": "confidence",
        "status": status,
    }


class AiReviewTests(unittest.TestCase):
    def test_high_confidence_ok_resolves_issue_and_appends_review_reason(self):
        issue = _issue()

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "55",
                        "span_text": "超导",
                        "verdict": "ok",
                        "replacement": "",
                        "reason": "上下文语义通顺，原词无误",
                        "confidence": 0.9,
                    }
                ]
            }

        findings, changeset, new_issues = review(
            _rows(),
            [issue],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertEqual(findings[0]["segment_id"], "sentence_0055")
        self.assertEqual(issue["status"], "resolved")
        self.assertEqual(
            issue["reason"],
            "低置信；AI 复核通过：上下文语义通顺，原词无误",
        )
        self.assertIsNone(changeset)
        self.assertEqual(new_issues, [])

    def test_low_confidence_ok_leaves_issue_open(self):
        issue = _issue()

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "sentence_0055",
                        "span_text": "超导",
                        "verdict": "ok",
                        "replacement": "",
                        "reason": "看起来可能无误",
                        "confidence": 0.89,
                    }
                ]
            }

        review(
            _rows(),
            [issue],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["reason"], "低置信")

    def test_ask_user_leaves_low_confidence_issue_open(self):
        issue = _issue()

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "sentence_0055",
                        "span_text": "超导",
                        "verdict": "ask_user",
                        "replacement": "超脑",
                        "reason": "上下文不足，需人工确认",
                        "confidence": 0.95,
                    }
                ]
            }

        _, changeset, new_issues = review(
            _rows(),
            [issue],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertEqual(issue["status"], "open")
        self.assertEqual(issue["reason"], "低置信")
        self.assertIsNone(changeset)
        self.assertEqual(len(new_issues), 1)
        self.assertEqual(new_issues[0]["kind"], "ai_suspect")

    def test_high_confidence_alias_id_auto_fix_builds_undoable_changeset(self):
        rows = _rows()
        original = [dict(row) for row in rows]

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "55",
                        "span_text": "超导",
                        "verdict": "auto_fix",
                        "replacement": "超脑",
                        "reason": "已知机构名",
                        "confidence": 0.95,
                    }
                ]
            }

        findings, changeset, new_issues = review(
            rows,
            [_issue()],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM {{USER_BRIEF}}",
            known_terms=["超脑"],
            corrections_rights=[],
        )

        self.assertEqual(findings[0]["segment_id"], "sentence_0055")
        self.assertEqual(findings[0]["verdict"], "auto_fix")
        self.assertEqual(rows[1]["text"], "这是超脑的项目。")
        self.assertEqual(new_issues, [])
        self.assertIsNotNone(changeset)
        assert changeset is not None
        self.assertEqual(changeset["label"], "AI 自动纠错 1 处")
        restored, undo_report = undo_changeset(rows, changeset)
        self.assertEqual(restored, original)
        self.assertEqual(undo_report["reverted"], 1)

    def test_length_and_confidence_safety_gates_downgrade_to_ask_user(self):
        for replacement, confidence in [
            ("长度相差很多很多", 0.99),
            ("超脑", 0.84),
            ("超脑", 1.2),
        ]:
            with self.subTest(replacement=replacement, confidence=confidence):
                rows = _rows()

                def fake_chat(_system: str, _user: str) -> dict:
                    return {
                        "findings": [
                            {
                                "segment_id": "sentence_0055",
                                "span_text": "超导",
                                "verdict": "auto_fix",
                                "replacement": replacement,
                                "reason": "可能有误",
                                "confidence": confidence,
                            }
                        ]
                    }

                findings, changeset, new_issues = review(
                    rows,
                    [_issue()],
                    chat_json_fn=fake_chat,
                    assemble_prompt_fn=lambda: "SYSTEM",
                    known_terms=[],
                    corrections_rights=[],
                )

                self.assertEqual(findings[0]["verdict"], "ask_user")
                self.assertIsNone(changeset)
                self.assertEqual(rows[1]["text"], "这是超导的项目。")
                self.assertEqual(new_issues[0]["kind"], "ai_suspect")
                self.assertEqual(new_issues[0]["suggestion"], replacement)

    def test_repeated_span_is_downgraded_because_occurrence_is_ambiguous(self):
        rows = [{"id": "sentence_0055", "text": "超导和超导都在这里"}]

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "sentence_0055",
                        "span_text": "超导",
                        "verdict": "auto_fix",
                        "replacement": "超脑",
                        "reason": "近音专名",
                        "confidence": 0.99,
                    }
                ]
            }

        findings, changeset, new_issues = review(
            rows,
            [_issue()],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=["超脑"],
        )

        self.assertEqual(findings[0]["verdict"], "ask_user")
        self.assertIsNone(changeset)
        self.assertEqual(rows[0]["text"], "超导和超导都在这里")
        self.assertEqual(new_issues[0]["suggestion"], "超脑")

    def test_span_mismatch_and_unknown_id_are_rejected(self):
        rows = _rows()

        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "55",
                        "span_text": "超跑",
                        "verdict": "auto_fix",
                        "replacement": "超脑",
                        "reason": "不在输入",
                        "confidence": 0.99,
                    },
                    {
                        "segment_id": "999",
                        "span_text": "超导",
                        "verdict": "auto_fix",
                        "replacement": "超脑",
                        "reason": "未知句",
                        "confidence": 0.99,
                    },
                ]
            }

        findings, changeset, new_issues = review(
            rows,
            [_issue()],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertEqual(findings, [])
        self.assertIsNone(changeset)
        self.assertEqual(new_issues, [])
        self.assertEqual(rows[1]["text"], "这是超导的项目。")

    def test_uncertain_proper_noun_becomes_term_candidate(self):
        def fake_chat(_system: str, _user: str) -> dict:
            return {
                "findings": [
                    {
                        "segment_id": "sentence_0055",
                        "span_text": "超导",
                        "verdict": "ask_user",
                        "replacement": "超脑",
                        "reason": "疑似专有名词，但不确定正确写法",
                        "confidence": 0.7,
                    }
                ]
            }

        _, changeset, new_issues = review(
            _rows(),
            [_issue()],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertIsNone(changeset)
        self.assertEqual(new_issues[0]["kind"], "term_candidate")

    def test_prompt_contains_context_marked_span_confidence_and_known_terms(self):
        captured: list[tuple[str, str]] = []

        def fake_chat(system: str, user: str) -> dict:
            captured.append((system, user))
            return {"findings": []}

        review(
            _rows(),
            [_issue()],
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "理念\n协议\n{{USER_BRIEF}}",
            known_terms=["超脑", "AI"],
            corrections_rights=["超脑", "vibe coding"],
        )

        self.assertEqual(len(captured), 1)
        system, user = captured[0]
        self.assertNotIn("{{USER_BRIEF}}", system)
        self.assertIn("我们来到现场。", user)
        self.assertIn("这是『超导』的项目。", user)
        self.assertIn("下面继续介绍。", user)
        self.assertIn("0.400", user)
        self.assertEqual(user.count("超脑"), 1)
        self.assertIn("vibe coding", user)

    def test_only_open_low_confidence_issues_are_sent_and_chunks_at_40_sentences(self):
        rows = [
            {"id": f"sentence_{index:04d}", "text": f"第{index}个错词。"}
            for index in range(42)
        ]
        issues = [
            _issue(
                segment_id=f"sentence_{index:04d}",
                text="错词",
                status="open" if index < 41 else "ignored",
            )
            for index in range(42)
        ]
        calls: list[str] = []

        def fake_chat(_system: str, user: str) -> dict:
            calls.append(user)
            return {"findings": []}

        review(
            rows,
            issues,
            chat_json_fn=fake_chat,
            assemble_prompt_fn=lambda: "SYSTEM",
            known_terms=[],
            corrections_rights=[],
        )

        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
