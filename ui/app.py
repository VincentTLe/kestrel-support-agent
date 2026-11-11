"""A browser chat for trying the agent by hand: streamlit run ui/app.py

A testing aid, not part of the brief (which asks for a terminal chat). It
wires the same agent as the CLI, so every answer goes through the same tools
and guard, and it shows what the terminal hides behind --debug: each tool
call, each guard decision, and the model calls with their tokens, latency and
cost.
"""

import json
import sys
import tempfile
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run from anywhere

from kestrel.__main__ import DEFAULT_MODEL, build_agent  # noqa: E402

SCENARIOS = Path(__file__).resolve().parent.parent / "evals" / "scenarios.json"


def scenario_prompts() -> dict[str, str]:
    prompts = {}
    for scenario in json.loads(SCENARIOS.read_text(encoding="utf-8")):
        first = scenario["turns"][0]
        prompts[scenario["id"]] = first["say"] if isinstance(first, dict) else first
    return prompts


def as_markdown(text: str) -> str:
    # Keep line breaks, and stop "$129 ... $35" from being read as LaTeX.
    return text.replace("$", "\\$").replace("\n", "  \n")


def start_conversation() -> None:
    try:
        st.session_state.agent = build_agent(st.session_state.now or None, st.session_state.state_file, st.session_state.model)
    except SystemExit as error:  # build_agent exits when the API key is missing
        st.session_state.agent = None
        st.session_state.error = str(error)
        return
    st.session_state.error = None
    st.session_state.turns = []  # one dict per exchange: what was said, the reply and what happened


def send(text: str) -> None:
    agent = st.session_state.agent
    marks = len(agent.trace), len(agent.guard_events), len(agent.llm.calls)
    with st.spinner("Kestrel is typing..."):
        reply = agent.reply(text)
    st.session_state.turns.append({
        "said": text,
        "reply": reply,
        "tools": agent.trace[marks[0]:],
        "guard": agent.guard_events[marks[1]:],
        "calls": agent.llm.calls[marks[2]:],
    })


st.set_page_config(page_title="Kestrel Cycles support", page_icon="🚲")

if "state_file" not in st.session_state:
    # Night Owl codes live for this browser session, across "new conversation".
    st.session_state.state_file = Path(tempfile.mkdtemp()) / "promo_codes.json"
    st.session_state.now = "2026-12-01 21:30"
    st.session_state.model = DEFAULT_MODEL
    start_conversation()

with st.sidebar:
    st.header("Settings")
    st.text_input("Clock (Eastern). Empty = real time", key="now", help='e.g. "2026-12-01 21:30" or "2026-12-01 15:00"')
    st.text_input("OpenRouter model", key="model")
    st.caption("Changing the clock or model takes effect in a new conversation.")
    if st.button("New conversation", use_container_width=True):
        start_conversation()
    if st.button("Forget issued Night Owl codes", use_container_width=True):
        st.session_state.state_file.unlink(missing_ok=True)
        start_conversation()
    show_details = st.toggle("Show tool calls and guard decisions", value=True)

    st.header("Try a scenario")
    prompts = scenario_prompts()
    picked = st.selectbox("First message of an eval scenario", list(prompts), label_visibility="collapsed")
    st.caption(prompts[picked])
    send_picked = st.button("Send this message", use_container_width=True)
    totals = st.container()  # filled after this run's message has been answered

st.title("🚲 Kestrel Cycles support")
if st.session_state.error:
    st.error(st.session_state.error)
    st.stop()
st.caption(f"Clock: {st.session_state.agent.clock.now():%Y-%m-%d %I:%M %p} Eastern · model: {st.session_state.model}")

typed = st.chat_input("Ask about an order, a bike, or the Night Owl promotion")
if typed:
    send(typed)
elif send_picked:
    send(prompts[picked])

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(as_markdown(turn["said"]))
    with st.chat_message("assistant", avatar="🚲"):
        st.markdown(as_markdown(turn["reply"]))
        if show_details and (turn["tools"] or turn["guard"] or turn["calls"]):
            seconds = sum(call["seconds"] for call in turn["calls"])
            with st.expander(f"{len(turn['tools'])} tool call(s), {len(turn['calls'])} model call(s), {seconds:.1f} s"):
                for call in turn["tools"]:
                    st.markdown(f"**{call['tool']}** `{call['arguments']}`")
                    st.json(call["result"], expanded=False)
                for event in turn["guard"]:
                    st.warning(f"Guard {event['action']}: {', '.join(event['rules'])}")
                st.dataframe(turn["calls"], hide_index=True)

calls = st.session_state.agent.llm.calls
if calls:
    with totals:
        st.header("This conversation")
        st.metric("Model calls", len(calls))
        st.metric("Tokens in / out", f"{sum(c['prompt_tokens'] for c in calls):,} / {sum(c['completion_tokens'] for c in calls):,}")
        st.metric("Cost", f"${sum(c['cost'] or 0 for c in calls):.4f}")
