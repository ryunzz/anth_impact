from types import SimpleNamespace

import pytest

from rdg.chat import FALLBACK_BETA, MODEL, ChatError, build_call, parse_request, reply
from rdg.server import load_scenarios

SCENARIOS = load_scenarios()


def body(**kw):
    base = {"scenario": "lesne", "state": "after", "selected": None,
            "messages": [{"role": "user", "content": "What breaks?"}]}
    return {**base, **kw}


@pytest.mark.parametrize("bad", [
    "nope",
    body(scenario="x"),
    body(state="sideways"),
    body(messages=[]),
    body(messages=[{"role": "system", "content": "hi"}]),
    body(messages=[{"role": "user", "content": ""}]),
    body(messages=[{"role": "user", "content": "x" * 5000}]),
    body(messages=[{"role": "assistant", "content": "hi"}]),
    body(selected=5),
])
def test_parse_rejects_bad_input(bad):
    with pytest.raises(ChatError):
        parse_request(bad, SCENARIOS)


def test_build_call_is_grounded_and_uses_fallback():
    req = parse_request(body(selected="our-startup#h2"), SCENARIOS)
    call = build_call(req, SCENARIOS)
    assert call["model"] == MODEL
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    system = call["system"][0]["text"]
    assert "our-startup" in system and "leanStands" in system and "Retracted" in system
    assert call["messages"][-1]["content"].startswith("(I'm looking at our-startup#h2.)")
    # original request untouched
    assert req.messages[-1]["content"] == "What breaks?"


def test_system_prompt_is_stable_for_caching():
    req = parse_request(body(), SCENARIOS)
    assert build_call(req, SCENARIOS)["system"] == build_call(req, SCENARIOS)["system"]


class FakeMessages:
    def __init__(self, response):
        self.response, self.calls = response, []

    def create(self, **kw):
        self.calls.append(kw)
        return self.response


def test_reply_returns_text_and_handles_refusal():
    req = parse_request(body(), SCENARIOS)
    ok = FakeMessages(SimpleNamespace(stop_reason="end_turn", content=[
        SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text="h1 breaks.")]))
    assert reply(req, SCENARIOS, ok) == "h1 breaks."
    refused = FakeMessages(SimpleNamespace(stop_reason="refusal", content=[]))
    assert "can't help" in reply(req, SCENARIOS, refused)


def test_missing_credentials_becomes_clear_error():
    class NoKey:
        def create(self, **kw):
            raise TypeError('"Could not resolve authentication method. Expected one of api_key"')

    with pytest.raises(ChatError) as err:
        reply(parse_request(body(), SCENARIOS), SCENARIOS, NoKey())
    assert err.value.status == 503 and "ANTHROPIC_API_KEY" in str(err.value)


def test_read_env_file(tmp_path):
    from rdg.server import read_env_file

    env = tmp_path / ".env"
    env.write_text("# comment\n\nANTHROPIC_API_KEY='abc'\nnot a pair\nOTHER = x=y\n")
    assert read_env_file(env) == {"ANTHROPIC_API_KEY": "abc", "OTHER": "x=y"}
    assert read_env_file(tmp_path / "missing") == {}
