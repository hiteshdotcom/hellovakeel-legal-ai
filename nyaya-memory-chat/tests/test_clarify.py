from app.clarify import _parse_clarify, clarify_gate, render_clarify_text


def test_parse_clarify_false_on_plain_false():
    assert _parse_clarify('{"needs": false}') == {"needs": False}


def test_parse_clarify_extracts_and_caps():
    raw = """```json
    {"needs": true, "preamble": "Need details:",
     "questions": [
       {"q": "Personal law?", "chips": ["Hindu","Muslim","Christian","Sikh","Other"]},
       {"q": "Owned or tenanted?", "chips": ["Owned","Tenanted"]},
       {"q": "Registered deed?", "chips": ["Yes","No"]},
       {"q": "extra fourth?", "chips": ["a"]}
     ]}
    ```"""
    out = _parse_clarify(raw)
    assert out["needs"] is True
    assert out["preamble"] == "Need details:"
    assert len(out["questions"]) == 3                      # capped at 3 questions
    assert out["questions"][0]["chips"] == ["Hindu", "Muslim", "Christian", "Sikh"]  # capped at 4 chips


def test_parse_clarify_malformed_falls_back():
    assert _parse_clarify("not json at all") == {"needs": False}
    assert _parse_clarify('{"needs": true, "questions": "oops"}') == {"needs": False}
    assert _parse_clarify("") == {"needs": False}


async def test_clarify_gate_swallows_llm_error():
    class Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("api down")

    assert await clarify_gate(Boom(), "my father died", []) == {"needs": False}


async def test_clarify_gate_parses_needs():
    class Fake:
        async def complete(self, *a, **k):
            return '{"needs": true, "preamble": "P", "questions": [{"q": "Q?", "chips": ["A", "Not sure"]}]}'

    out = await clarify_gate(Fake(), "q", [])
    assert out["needs"] is True
    assert out["questions"][0]["q"] == "Q?"
    assert out["questions"][0]["chips"] == ["A", "Not sure"]


def test_render_clarify_text():
    txt = render_clarify_text("Need details:", [{"q": "Personal law?", "chips": ["Hindu", "Muslim"]}])
    assert txt.startswith("Need details:")
    assert "1. Personal law? (Hindu / Muslim)" in txt
