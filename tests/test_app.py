from __future__ import annotations

from io import StringIO

from deepseek_cli.app import MODEL_CHAT, MODEL_REASONER, ChatApplication
from deepseek_cli.gateway import Message, StreamDelta


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def stream_chat(self, model, messages):
        self.calls.append((model, list(messages)))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        yield from response


def input_sequence(*items):
    iterator = iter(items)

    def fake_input(_prompt):
        item = next(iterator)
        if isinstance(item, BaseException):
            raise item
        return item

    return fake_input


def test_multi_turn_history_commits_only_final_answers():
    gateway = FakeGateway(
        [
            [StreamDelta(content="第一")],
            [StreamDelta(content="第二")],
        ]
    )
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("问题一", "问题二", "/quit"),
        output=output,
    )

    app.run()

    assert gateway.calls[0] == (MODEL_CHAT, [Message("user", "问题一")])
    assert gateway.calls[1] == (
        MODEL_CHAT,
        [
            Message("user", "问题一"),
            Message("assistant", "第一"),
            Message("user", "问题二"),
        ],
    )
    assert app.history[-1] == Message("assistant", "第二")


def test_reasoning_is_displayed_but_not_stored():
    gateway = FakeGateway(
        [[StreamDelta(reasoning_content="内部推理"), StreamDelta(content="结论")]]
    )
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("请分析", "/exit"),
        output=output,
    )

    app.run()

    assert "思考过程：内部推理" in output.getvalue()
    assert "回答：结论" in output.getvalue()
    assert app.history == (
        Message("user", "请分析"),
        Message("assistant", "结论"),
    )
    assert all("内部推理" not in item.content for item in app.history)


def test_incomplete_stream_does_not_modify_history():
    def interrupted_stream():
        yield StreamDelta(content="未完成")
        raise KeyboardInterrupt

    gateway = FakeGateway([interrupted_stream()])
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("问题", "/quit"),
        output=output,
    )

    app.run()

    assert app.history == ()
    assert "历史未更改" in output.getvalue()


def test_interrupt_during_finalization_does_not_modify_history():
    class InterruptOnFinalNewline(StringIO):
        def write(self, text):
            if text == "\n":
                raise KeyboardInterrupt
            return super().write(text)

    gateway = FakeGateway([[StreamDelta(content="完成")]])
    output = InterruptOnFinalNewline()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("问题", "/quit"),
        output=output,
    )

    app.run()

    assert app.history == ()
    assert "已中断本次回答" in output.getvalue()


def test_empty_final_answer_does_not_modify_history():
    gateway = FakeGateway(
        [[StreamDelta(reasoning_content="推理"), StreamDelta(content="  ")]]
    )
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("问题", "/quit"),
        output=output,
    )

    app.run()

    assert app.history == ()
    assert "未收到有效回答" in output.getvalue()


def test_commands_switch_models_clear_and_show_help():
    gateway = FakeGateway(
        [
            [StreamDelta(content="A")],
            [StreamDelta(content="B")],
        ]
    )
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence(
            "/help",
            "/model",
            "/model reasoner",
            "问题一",
            "/clear",
            "/model chat",
            "问题二",
            "/quit",
        ),
        output=output,
    )

    app.run()

    assert gateway.calls[0][0] == MODEL_REASONER
    assert gateway.calls[1] == (MODEL_CHAT, [Message("user", "问题二")])
    assert app.model == MODEL_CHAT
    assert app.history == (Message("user", "问题二"), Message("assistant", "B"))
    text = output.getvalue()
    assert "可用命令" in text
    assert f"当前模型：{MODEL_CHAT}" in text
    assert "对话历史已清空" in text


def test_full_model_names_are_accepted_and_history_is_preserved():
    gateway = FakeGateway(
        [
            [StreamDelta(content="A")],
            [StreamDelta(content="B")],
        ]
    )
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence(
            "第一问",
            "/model deepseek-reasoner",
            "第二问",
            "/model deepseek-chat",
            "/quit",
        ),
        output=output,
    )

    app.run()

    assert gateway.calls[0][0] == MODEL_CHAT
    assert gateway.calls[1][0] == MODEL_REASONER
    assert gateway.calls[1][1][0:2] == [
        Message("user", "第一问"),
        Message("assistant", "A"),
    ]
    assert app.model == MODEL_CHAT
    assert "上下文已保留" in output.getvalue()


def test_gateway_error_is_friendly_and_does_not_leak_exception():
    gateway = FakeGateway([RuntimeError("secret raw SDK failure")])
    output = StringIO()
    app = ChatApplication(
        gateway,
        input_fn=input_sequence("问题", "/quit"),
        output=output,
    )

    app.run()

    text = output.getvalue()
    assert "请求失败" in text
    assert "secret raw SDK failure" not in text
    assert app.history == ()


def test_eof_and_keyboard_interrupt_exit_cleanly():
    for signal in (EOFError(), KeyboardInterrupt()):
        output = StringIO()
        app = ChatApplication(
            FakeGateway([]), input_fn=input_sequence(signal), output=output
        )

        app.run()

        assert "已退出" in output.getvalue()
