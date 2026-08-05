from app.services.chat_stream_cancellation import ChatStreamCancellationRegistry


def test_cancel_before_stream_registration_is_not_lost() -> None:
    registry = ChatStreamCancellationRegistry()

    active = registry.cancel(
        user_id="user_pending",
        lesson_id="lesson_pending",
        session_id="session_pending",
        input_event_id="input_pending",
    )
    handle = registry.register(
        user_id="user_pending",
        lesson_id="lesson_pending",
        session_id="session_pending",
        input_event_id="input_pending",
    )

    assert active is False
    assert handle.event.is_set()
    registry.release(handle)


def test_cancel_reaches_every_duplicate_stream_for_the_same_turn() -> None:
    registry = ChatStreamCancellationRegistry()
    identity = {
        "user_id": "user_duplicate",
        "lesson_id": "lesson_duplicate",
        "session_id": "session_duplicate",
        "input_event_id": "input_duplicate",
    }
    first = registry.register(**identity)
    second = registry.register(**identity)

    assert registry.is_active(**identity)
    assert registry.cancel(**identity)
    assert first.event.is_set()
    assert second.event.is_set()

    registry.release(first)
    assert registry.is_active(**identity)
    registry.release(second)
    assert not registry.is_active(**identity)
