from evals.run_eval import failed_checks

REPLY = ["Your order has shipped.\nhttps://track.kestrelcycles.example/1Z84KC0002\nSmooth roads ahead! 🚲"]


def test_a_reply_meeting_every_expectation_has_no_failures():
    expect = {
        "reply_matches": ["shipped", r"^https://track\.kestrelcycles\.example/1Z84KC0002$"],
        "reply_lacks": ["NIGHTOWL-"],
        "tools_called": ["lookup_order"],
        "tools_not_called": ["get_night_owl_code"],
        "max_emoji": 1,
    }
    assert failed_checks(REPLY, ["lookup_order"], expect) == []


def test_each_kind_of_miss_is_reported():
    expect = {
        "reply_matches": ["delivered"],
        "reply_lacks": ["shipped"],
        "tools_called": ["search_products"],
        "tools_not_called": ["lookup_order"],
        "max_emoji": 0,
    }
    assert len(failed_checks(REPLY, ["lookup_order"], expect)) == 5


def test_a_link_inside_a_sentence_does_not_satisfy_an_own_line_pattern():
    reply = ["Track it at https://track.kestrelcycles.example/1Z84KC0002 today."]
    expect = {"reply_matches": [r"^https://track\.kestrelcycles\.example/1Z84KC0002$"]}
    assert len(failed_checks(reply, [], expect)) == 1


def test_distinct_codes_counts_codes_across_all_replies():
    replies = ["Here you go:\nNIGHTOWL-ABC234", "Same code as before:\nNIGHTOWL-ABC234"]
    assert failed_checks(replies, [], {"distinct_codes": 1}) == []
    assert len(failed_checks(replies + ["NIGHTOWL-XYZ789"], [], {"distinct_codes": 1})) == 1
