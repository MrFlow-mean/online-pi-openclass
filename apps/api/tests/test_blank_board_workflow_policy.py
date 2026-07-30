from app.services.blank_board_intake import (
    BOARD_GENERATION_HANDOFF_INSTRUCTIONS,
    BlankBoardTurnDecision,
    evaluate_blank_board_decision,
    force_blank_board_generation_if_requested,
)


def _decision(**updates) -> BlankBoardTurnDecision:
    payload = {
        "intent": "learning_need",
        "teaching_type": "knowledge_point",
        "learning_content": "A focused concept",
        "content_is_specific": True,
        "chatbot_message": "The model produced a contextual reply.",
        "teaching_plan": "Explain the focused concept as one coherent board.",
        "reason": "The learner requested one focused concept.",
    }
    payload.update(updates)
    return BlankBoardTurnDecision.model_validate(payload)


def test_focused_knowledge_board_does_not_require_level_or_scenario() -> None:
    outcome = evaluate_blank_board_decision(
        _decision(),
        previous_requirement=None,
    )

    assert outcome.route == "generate_board"
    assert outcome.ready_for_board is True
    assert outcome.clarification.missing_items == []
    assert [item.title for item in outcome.clarification.checklist] == [
        "learning_content"
    ]


def test_broad_knowledge_board_still_requires_a_focused_content_target() -> None:
    outcome = evaluate_blank_board_decision(
        _decision(
            learning_content="A broad field",
            content_is_specific=False,
            teaching_plan="",
        ),
        previous_requirement=None,
    )

    assert outcome.route == "collect_requirements"
    assert outcome.ready_for_board is False
    assert outcome.clarification.missing_items == ["learning_content"]


def test_practice_artifact_requires_content_level_and_purpose() -> None:
    incomplete = evaluate_blank_board_decision(
        _decision(
            teaching_type="skill_practice",
            current_level="",
            target_scenario="",
            teaching_plan="",
        ),
        previous_requirement=None,
    )
    complete = evaluate_blank_board_decision(
        _decision(
            teaching_type="skill_practice",
            current_level="Current capability evidence",
            target_scenario="Practice purpose",
        ),
        previous_requirement=None,
    )

    assert incomplete.clarification.missing_items == [
        "current_level",
        "target_scenario",
    ]
    assert incomplete.ready_for_board is False
    assert complete.ready_for_board is True


def test_explicit_generate_forces_an_incomplete_requirement_without_inventing_facts() -> None:
    decision = _decision(
        requested_action="generate_board",
        learning_content="A broad field",
        content_is_specific=False,
        current_level="",
        target_scenario="",
        teaching_plan="",
    )
    collecting = evaluate_blank_board_decision(
        decision,
        previous_requirement=None,
    )

    forced = force_blank_board_generation_if_requested(decision, collecting)

    assert forced.route == "generate_board"
    assert forced.ready_for_board is True
    assert forced.clarification.forced_start is True
    assert forced.requirement is not None
    assert forced.requirement.current_level == ""
    assert forced.requirement.target_scenario == ""


def test_generation_handoff_requires_model_to_ask_before_teaching() -> None:
    normalized = " ".join(BOARD_GENERATION_HANDOFF_INSTRUCTIONS.split()).lower()

    assert "asks whether" in normalized
    assert "from the beginning" in normalized
    assert "do not start teaching" in normalized
