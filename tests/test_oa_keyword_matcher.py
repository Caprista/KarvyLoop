from karvyloop.oa import ApprovalContext, KeywordMatchResult, match_keywords, normalize_text


def test_normalize_text_handles_fullwidth_case_and_punctuation():
    assert normalize_text("　上海，出差！") == "上海 出差"


def test_all_any_none_keywords_match():
    rule = {"keywords": {"all": ["出差"], "any": ["上海", "杭州"], "none": ["超标"]}}
    context = ApprovalContext(request_title="员工出差申请", request_description="前往上海客户现场")
    result = match_keywords(rule, context)
    assert result.status == "MATCH"
    assert set(result.matched_keywords) == {"出差", "上海"}


def test_missing_text_is_unknown_and_fail_closed():
    result = match_keywords({"keywords": {"all": ["出差"]}}, ApprovalContext())
    assert result.status == "UNKNOWN"
    assert not result.matched_keywords


def test_forbidden_keyword_is_no_match():
    result = match_keywords({"keywords": {"none": ["补贴"]}}, {"request": {"title": "出差补贴"}})
    assert result.status == "UNKNOWN"
    assert result.forbidden_keywords == ("补贴",)


def test_no_keyword_section_is_match():
    assert match_keywords({}, ApprovalContext()).status == "MATCH"


def test_result_validates_status_and_confidence():
    assert KeywordMatchResult("MATCH").confidence == 1.0


def test_condition_groups_compute_hit_rate_and_threshold():
    rule = {"keywords": {"condition_groups": [
        {"all": ["出差"]},
        {"all": ["上海"]},
        {"all": ["客户现场"]},
    ], "match_threshold": 50, "min_groups": 2}}
    context = ApprovalContext(request_title="员工出差申请", request_description="前往上海")
    result = match_keywords(rule, context)
    assert result.status == "MATCH"
    assert result.matched_groups == 2
    assert result.total_groups == 3
    assert result.matched_score == 200
    assert result.score == 200 / 300
    assert result.threshold == 50


def test_condition_groups_below_threshold_requires_human():
    rule = {"keywords": {"condition_groups": [
        {"all": ["出差"]}, {"all": ["上海"]}, {"all": ["客户现场"]},
    ], "match_threshold": 50, "min_groups": 2}}
    result = match_keywords(rule, {"request": {"title": "出差申请"}})
    assert result.status == "NO_MATCH"
    assert result.matched_groups == 1
    assert "命中得分" in result.reason


def test_condition_group_absolute_weights():
    context = {"request": {"title": "出差"}}
    normal = {"keywords": {"condition_groups": [{"all": ["出差"]}]}}
    assert match_keywords(normal, context).status == "MATCH"
    sensitive = {"keywords": {"condition_groups": [{"all": ["出差"], "category": "sensitive"}]}}
    assert match_keywords(sensitive, context).status == "NO_MATCH"
    mixed = {"keywords": {"condition_groups": [{"all": ["出差"]}, {"all": ["出差"], "category": "sensitive"}]}}
    assert match_keywords(mixed, context).status == "MATCH"


def test_condition_groups_support_bare_list_and_forbidden_keyword():
    rule = {"keywords": {"condition_groups": [["出差"], {"none": ["超标"]}],
                          "match_threshold": 1.0}}
    result = match_keywords(rule, {"request": {"title": "出差超标"}})
    assert result.status == "NO_MATCH"
    assert result.forbidden_keywords == ("超标",)
