from app.services.lesson_factory import create_empty_lesson
from app.services.lesson_summary import lesson_content_summary


def test_uses_explicit_lesson_summary_before_document_content() -> None:
    lesson = create_empty_lesson("课程标题")
    lesson.summary = "作者编写的课程简介。"
    lesson.board_document.content_json = {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "板书正文内容。"}]}],
    }

    assert lesson_content_summary(lesson) == "作者编写的课程简介。"


def test_derives_description_from_first_substantive_paragraph() -> None:
    lesson = create_empty_lesson("重命名后的课程标题")
    lesson.board_document.content_json = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "文档内容标题"}]},
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "学习目标"}]},
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "这一段正文介绍了本课程实际要学习的内容、方法与目标。"},
                ],
            },
        ],
    }
    lesson.board_document.content_text = "文档内容标题\n\n学习目标\n\n这一段正文介绍了本课程实际要学习的内容、方法与目标。"

    assert lesson_content_summary(lesson) == "这一段正文介绍了本课程实际要学习的内容、方法与目标。"


def test_returns_empty_description_for_an_empty_lesson() -> None:
    lesson = create_empty_lesson("空白课程")

    assert lesson_content_summary(lesson) == ""
