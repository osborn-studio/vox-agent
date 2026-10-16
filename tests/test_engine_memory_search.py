"""Unit tests for token-overlap + IDF memory scoring (engine.voice_memory)."""

from engine.voice_memory import query_tokens, rank_nodes, score_node


def _node(title, body="", tags=None):
    return {"title": title, "body": body, "tags": tags or []}


class TestQueryTokens:
    def test_splits_on_whitespace_and_punctuation(self):
        assert query_tokens("Japan, launch!") == ["japan", "launch"]

    def test_cjk_phrase_stays_one_token(self):
        # No segmenter: a CJK run with no interior spaces is a single token.
        assert query_tokens("正式会议纪要") == ["正式会议纪要"]

    def test_empty(self):
        assert query_tokens("   ") == []


class TestScoreNode:
    def test_word_order_independent(self):
        node = _node("Launch in Japan in July")
        # whole-string substring would miss "japan launch"; token overlap hits.
        assert score_node(node, "japan launch") == 1.0

    def test_partial_overlap_scores_fraction(self):
        node = _node("Launch in Japan")
        # 1 of 2 query tokens present ("launch"; "berlin" absent).
        assert score_node(node, "launch berlin") == 0.5

    def test_no_overlap_is_zero(self):
        assert score_node(_node("Launch in Japan"), "zzz nomatch") == 0.0

    def test_matches_body_and_tags(self):
        node = _node("T", body="ship the mobile companion", tags=["roadmap"])
        assert score_node(node, "mobile") == 1.0
        assert score_node(node, "roadmap") == 1.0

    def test_cjk_still_matches(self):
        node = _node("正式会议纪要（中文）", body="会议决定七月发布")
        assert score_node(node, "正式会议纪要") == 1.0

    def test_empty_query_is_zero(self):
        assert score_node(_node("anything"), "   ") == 0.0


class TestRankNodes:
    def test_empty_query_returns_nothing(self):
        assert rank_nodes([_node("anything")], "   ") == []

    def test_ranks_rare_term_above_common_term(self):
        nodes = [
            _node("Launch in Tokyo"),
            _node("Launch in Osaka"),
            _node("Launch in Berlin"),
        ]
        ranked = rank_nodes(nodes, "launch berlin", limit=20)
        assert ranked[0]["node"]["title"] == "Launch in Berlin"
        # "launch" is ubiquitous (low IDF); "berlin" is rare (high IDF)
        assert ranked[0]["score"] > ranked[1]["score"]

    def test_lone_common_term_scores_low_but_returns(self):
        nodes = [_node("Launch A"), _node("Launch B")]
        ranked = rank_nodes(nodes, "launch", limit=20)
        assert len(ranked) == 2
        assert all(r["score"] > 0 for r in ranked)

    def test_no_match_excluded(self):
        ranked = rank_nodes([_node("Launch in Japan")], "berlin only", limit=20)
        assert ranked == []

    def test_cjk_phrase_matches(self):
        nodes = [_node("正式会议纪要（中文）", "会议决定"), _node("Unrelated")]
        ranked = rank_nodes(nodes, "正式会议纪要", limit=20)
        assert ranked[0]["node"]["title"] == "正式会议纪要（中文）"

    def test_limit_caps_results(self):
        nodes = [_node(f"Launch {i}") for i in range(10)]
        assert len(rank_nodes(nodes, "launch", limit=3)) == 3
