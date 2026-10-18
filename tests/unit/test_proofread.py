import json

import pytest

from vtx.llm import INPUT_SHARE, LLMError, estimate_tokens
from vtx.proofread import PROMPT, _ambiguous_quantities, _prompt, _sources, proofread


class FakeClient:
    def __init__(self, reply, context=32000):
        self.reply, self.context, self.calls = reply, context, []

    def chat(self, messages):
        self.calls.append(messages)
        payload = json.loads(messages[-1]["content"].split("输入JSON：\n", 1)[1])
        reply = self.reply(payload) if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return (json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply), {"total_tokens": 13}


def patch(old, new, quote, pointer="/markdown_body", kind="asr"):
    return {"id": pointer, "old": old, "new": new, "evidence": [{"source": kind, "quote": quote}]}


def test_source_backed_operator_patch_and_no_mutation():
    original = {"markdown_body": "伏特乘以安培得到欧姆。"}
    source = "伏特除以安培得到欧姆。"
    client = FakeClient({"patches": [patch(original["markdown_body"], source, source)]})
    result = proofread(client, original, source)
    assert result.doc == {"markdown_body": source}
    assert original["markdown_body"] == "伏特乘以安培得到欧姆。"
    assert result.usage == {"total_tokens": 13}
    assert result.audit["before"] == original
    assert result.audit["after"] == result.doc
    assert result.audit["status"] == "passed"
    assert len(client.calls) == 1
    assert client.calls[0][0]["content"].startswith("SOURCE_BACKED_PROOFREAD_V1")
    json.dumps(result.audit)


def test_recursive_fields_skip_source_metadata_and_allow_different_summary_lengths():
    original = {"points": [{"body": "胶水型号502。", "text": "八个碳叫502。",
                            "quotes": [{"quote": "八个碳叫502。", "source": "八个碳叫502。"}]}]}
    evidence = "八个碳叫508。"
    client = FakeClient({"patches": [patch("502", "508", evidence, "/points/0/text", "reference")]})
    result = proofread(client, original, "ASR八个碳叫502。", evidence)
    assert result.doc["points"][0]["text"] == evidence
    assert result.doc["points"][0]["body"] == original["points"][0]["body"]
    ids = [item["id"] for item in json.loads(client.calls[0][-1]["content"].split("输入JSON：\n", 1)[1])["draft"]]
    assert "/points/0/quotes/0/source" not in ids
    assert result.doc["points"][0]["quotes"][0]["source"] == "八个碳叫502。"


@pytest.mark.parametrize("document", [{"markdown_body": "无需改动。"}, {"points": []}, {}])
def test_valid_empty_patches(document):
    result = proofread(FakeClient({"patches": []}), document, "原始来源。")
    assert result.doc == document
    assert result.audit["status"] == "passed"
    assert result.audit["accepted"] == result.audit["rejected"] == []


@pytest.mark.parametrize("bad,source,reference,reason", [
    (patch("原句", "新句", "不存在的原句"), "来源原句", "", "证据不在"),
    (patch("原句", "508", "502"), "502", "", "数字"),
    (patch("原句", "508和700", "508"), "508；700", "", "数字"),
    (patch("原句", "葛洪", "葛明来了"), "葛明来了", "", "相邻语境"),
    (patch("原句", "新句", "原句", kind="unknown"), "原句", "", "证据不在"),
    (patch("原句", "新句", "原句", pointer="/source"), "原句", "", "未知"),
    ({"id": "/markdown_body", "old": "原句", "new": "新句", "evidence": []}, "原句", "", "没有来源"),
])
def test_rejects_unsupported_patch_with_audit(bad, source, reference, reason):
    client = FakeClient({"patches": [bad]})
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": "原句", "source": "元数据"}, source, reference)
    assert raised.value.code == "LLM_BAD_OUTPUT"
    audit = raised.value.audit
    assert audit["status"] == "failed"
    assert audit["after"] is None
    assert reason in audit["rejected"][0]["reason"]
    assert audit["usage"] == {"total_tokens": 26}
    assert len(client.calls) == 2


def test_name_cannot_borrow_an_unrelated_character():
    source = "我们见到了葛明。洪水之后出门。"
    client = FakeClient({"patches": [patch("葛明", "葛洪", source)]})
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": "我们见到了葛明。"}, source)
    assert "相邻语境" in raised.value.audit["rejected"][0]["reason"]


def test_new_numbers_need_one_evidence_but_existing_numbers_do_not():
    source = "旧数据为502，功率700。"
    reference = "八个碳叫508。"
    original = {"markdown_body": source}
    client = FakeClient({"patches": [patch("502，功率700", "508，功率700", reference, kind="reference")]})
    result = proofread(client, original, source, reference)
    assert result.doc["markdown_body"] == "旧数据为508，功率700。"


def test_equivalent_numeral_spelling_does_not_invent_a_number():
    source = "八个碳叫五零八。"
    client = FakeClient({"patches": [patch("五零八", "508", source)]})
    assert proofread(client, {"markdown_body": source}, source).doc["markdown_body"] == "八个碳叫508。"


def test_presentation_deletion_and_dialogue_paraphrase_with_original_evidence():
    original = "步骤1：试吃。\n我不在这里。你在哪里？"
    client = FakeClient({"patches": [
        patch("步骤1：试吃。\n", "", "步骤1：试吃。"),
        patch("我不在这里。你在哪里？", "有人问在哪里，对方说不在这里。", "我不在这里。你在哪里？"),
    ]})
    result = proofread(client, {"markdown_body": original}, original)
    assert result.doc["markdown_body"] == "有人问在哪里，对方说不在这里。"


@pytest.mark.parametrize("body,patches,reason", [
    ("旧句旧句", [patch("旧句", "", "旧句")], "唯一匹配"),
    ("原句内容", [patch("原句", "", "原句内容"), patch("句内容", "", "原句内容")], "重叠"),
])
def test_ambiguous_or_overlapping_replacements_fail_atomically(body, patches, reason):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": patches}), {"markdown_body": body}, body)
    assert raised.value.audit["after"] is None
    assert reason in raised.value.audit["rejected"][-1]["reason"]


@pytest.mark.parametrize("reply", ["not json", {"patches": {}}, RuntimeError("offline provider failed")])
def test_fault_is_explicit_with_bounded_validation_retry(reply):
    client = FakeClient(reply)
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": "来源"}, "来源")
    assert raised.value.code == "LLM_BAD_OUTPUT"
    assert raised.value.audit["status"] == "failed"
    assert len(client.calls) == (1 if isinstance(reply, Exception) else 2)


def test_long_source_uses_bounded_relevant_excerpts_and_cannot_cite_unseen_text():
    source = "\n\n".join(f"背景信息第{i}段。" + "雨水天气。" * 60 for i in range(80))
    source += "\n\n伏特除以安培得到欧姆。\n\n"
    source += "无关片尾。" * 500
    client = FakeClient({"patches": []}, context=3600)
    result = proofread(client, {"markdown_body": "伏特乘以安培得到欧姆。"}, source)
    assert estimate_tokens("".join(m["content"] for m in client.calls[0])) <= int(client.context * INPUT_SHARE)
    assert any("伏特除以安培" in excerpt for excerpt in result.audit["sources"]["asr"])
    assert source not in result.audit["sources"]["asr"]
    selected = result.audit["sources"]["asr"]
    unseen = next(piece for piece in source.split("\n\n") if not any(piece in chunk for chunk in selected))
    client.reply = {"patches": [patch("伏特乘以安培得到欧姆。", "", unseen)]}
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": "伏特乘以安培得到欧姆。"}, source)
    assert "本次发送" in raised.value.audit["rejected"][0]["reason"]


def test_oversized_draft_fails_without_call_or_truncation():
    client = FakeClient({"patches": []}, context=2000)
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": "不能丢掉的草稿。" * 1000}, "来源。")
    assert raised.value.audit["status"] == "failed"
    assert client.calls == []


def test_full_sources_remain_intact_when_they_fit():
    result = proofread(FakeClient({"patches": []}), {"markdown_body": "内容"}, "完整ASR。", "完整独立字幕。")
    assert result.audit["sources"] == {"asr": ["完整ASR。"], "reference": ["完整独立字幕。"]}


def test_real_probe_paraphrases_do_not_need_identical_source_characters():
    # Replayed from the first live provider probe, not invented synonym examples.
    changes = [
        ("伏特被抓住时，身下是安培", "抓人者喊伏特时，对方提示身下是安培",
         "福特，我抓到你了不？你没有抓到我，你不是福特，你是谁？你看我身下是谁？安培，我身下是安培"),
        ("合起来是“伏特除以安培”", "按伏特除以安培计算", "我俩就是福特厨艺安培"),
        ("牛顿被抓住时，脚下是一平方米地板砖",
         "抓人者喊牛顿、对方却说“你没有抓住牛顿”时，被抓者脚下是一平方米地板砖",
         "牛顿我抓到你了不？你没有抓住牛顿，看我脚下，这是一平方米的地板砖"),
        ("对话说“我俩就是伏特除以安培”", "对话把两者说成伏特除以安培", "我俩就是福特厨艺安培"),
    ]
    original = {"markdown_body": "。\n".join(old for old, new, quote in changes)}
    source = "。".join(quote for old, new, quote in changes)
    result = proofread(FakeClient({"patches": [patch(*change) for change in changes]}), original, source)
    assert result.doc["markdown_body"] == "。\n".join(new for old, new, quote in changes)
    assert len(result.audit["accepted"]) == 4
    assert result.audit["rejected"] == []
    assert result.audit["limits"]


def test_isolated_name_correction_with_full_source_name_is_allowed():
    original = {"markdown_body": "我们见到了葛明。"}
    reference = "人物卡写葛名。"
    result = proofread(FakeClient({"patches": [patch("葛明", "葛名", reference, kind="reference")]}),
                       original, "原转写是葛明。", reference)
    assert result.doc["markdown_body"] == "我们见到了葛名。"


def test_editorial_attribution_label_is_not_an_unconfirmed_person_name():
    old = "文字稿认为ARM能效和优化使离电性能几乎不掉"
    new = "讲述者认为ARM能效和优化使离电性能几乎不掉"
    source = "得益于ARM处理器的超高能效以及优化，基本上没有出现太多性能下降。"
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


def test_real_final9_sentence_paraphrase_does_not_use_short_word_guard():
    old = "当时全国承担垃圾分类的群体主要是数百万拾荒者"
    new = "当时全国承担垃圾分类的群体只有数百万拾荒者"
    source = "当时全国上下承担垃圾分类的只有一个群体，拾荒者。全国数百万拾荒者"
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new
    assert len(result.audit["accepted"]) == 1


def test_real_final12_sourced_role_paraphrase_uses_indefinite_article():
    old = "清华大学温宗国教授团队"
    new = "清华大学一个教授团队"
    source = ("清华大学温宗国教授团队也在这个阶段布局了固废资源化、智能化研究，"
              "并于2016年获得国家科技进步二等奖，为后来垃圾焚烧智能控制系统提供了理论储备。")
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new
    assert result.audit["status"] == "passed"


def test_real_final13_indefinite_role_omits_affiliation_and_source_name():
    old = "清华大学环境学院教授曾指出"
    new = "一位环境学院教授曾指出"
    source = ("清华大学环境学院教授聂永峰曾指出，在起步阶段，"
              "后端设施能力的不足决定了前端分类的成效不可能很高")
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("中科研究所张明研究员介绍", "中科研究所一位研究员介绍", "中科研究所张明研究员介绍该项研究。"),
    ("南方学院研究员表示", "一位研究员表示", "南方学院研究员李明表示需要继续研究。"),
    ("南方学院李明研究员课题组", "一个研究员课题组", "南方学院李明研究员课题组参与该项目。"),
])
def test_sourced_indefinite_roles_share_the_same_omission_structure(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("广州李坑焚烧厂2010年恢复运营时", "广州一家焚烧厂在2010年恢复运营时",
     "广州里坑焚烧厂在2010年恢复运营时，推出了村民驻场监督机制"),
    ("三峰环境是技术突围主力之一。", "一家企业是技术突围主力之一。",
     "三峰环境正是这场技术突围的主力之一"),
    ("光大环保能源有限公司工作人员曾在文章中表示", "一家环保能源企业的工作人员曾在文章中表示",
     "2019年，光大环保能源有限公司的工作人员曾在文章中表示"),
])
def test_real_final14_entity_articles_are_not_new_quantities(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("2026年5月，广东博罗垃圾焚烧发电厂，光大环境自主研发的 ACC 自适应智能燃烧控制系统投入使用",
     "2026年5月，广东博罗的一家垃圾焚烧发电厂，相关企业自主研发的 ACC 自适应智能燃烧控制系统投入使用",
     "2026年5月，广东博罗垃圾焚烧发电厂光大环境自主研发的acc自适应智能燃烧控制系统投入使用"),
    ("在绿色动力武汉工厂，公司联合阿里云打造垃圾焚烧数字化系统",
     "在武汉的一家工厂，该企业联合相关企业打造垃圾焚烧数字化系统",
     "在绿色动力武汉工厂公司联合阿里云打造垃圾焚烧数字化系统"),
    ("中科环保慈溪项目通过连接镇海、慈溪两座焚烧厂的供气管网",
     "慈溪的一个项目通过连接镇海、慈溪两座焚烧厂的供气管网",
     "在中科环保磁溪项目中，通过连接镇海、慈溪两座焚烧厂的供气管网"),
])
def test_real_final15_inserted_articles_and_unchanged_other_quantities(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("总处理规模超每日1.6万吨", "总处理规模超过每日万吨级", "总处理规模超每日1.6 2万吨"),
    ("其中，伟明环保、旺能环境深耕印尼市场，相继拿下巴厘岛等国家级项目",
     "其中，有两家企业深耕印尼市场，相继拿下巴厘岛等国家级项目",
     "其中伟明环保、旺能环境深耕印尼市场，相继拿下巴厘岛、物加四等国家级项目。"),
])
def test_real_final15_quantity_inferences_remain_rejected(old, new, source):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert "数字" in raised.value.audit["rejected"][0]["reason"]


def test_real_final16_association_generalization_is_not_new_quantity():
    old = "芜湖市生态环境保护志愿者协会"
    new = "芜湖一家环保志愿者协会"
    source = ("2023年5月，芜湖市生态环境保护志愿者协会委托第三方机构"
              "对垃圾焚烧厂的监测数据进行了整理分析")
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("芜湖市生态环境保护志愿者协会委托第三方机构整理分析焚烧厂监测数据",
     "芜湖一家环保志愿者协会委托第三方机构整理分析焚烧厂监测数据",
     "芜湖市生态环境保护志愿者协会委托第三方机构对垃圾焚烧厂的监测数据进行了整理分析"),
    ("重庆大学通过校企合作研发垃圾焚烧烟气高分子脱硝技术",
     "重庆一所高校通过校企合作研发垃圾焚烧烟气高分子脱硝技术",
     "重庆大学通过校企合作，研发出垃圾焚烧烟气高分子脱硝技术"),
])
def test_real_final18_article_grounding_uses_entity_phrase_not_sentence_word_order(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


def test_entity_article_still_needs_that_entity_in_evidence():
    old = "重庆大学通过校企合作研发垃圾焚烧烟气高分子脱硝技术"
    new = "重庆一所高校通过校企合作研发垃圾焚烧烟气高分子脱硝技术"
    source = "南方学院通过校企合作，研发出垃圾焚烧烟气高分子脱硝技术"
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert raised.value.audit["after"] is None


def test_short_institution_cannot_borrow_one_character_mismatch():
    old = "北方大学研究该项技术"
    new = "一所高校研究该项技术"
    source = "南方大学研究该项技术"
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert raised.value.audit["after"] is None


@pytest.mark.parametrize("old,new,source", [
    ("清华大学环境学院教授", "一位环境领域教授", "清华大学环境学院教授聂永峰曾指出"),
    ("清华大学一个研究团队", "一个高校研究团队", "清华大学温宗国教授团队也在这个阶段布局了固废资源化、智能化研究"),
])
def test_real_final20_role_modifier_and_unchanged_classifier_quantity(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


def test_rejected_attempt_is_corrected_with_identical_draft_sources_and_total_usage():
    original = {"markdown_body": "旧句。另句。"}
    source = "旧句。另句。"
    replies = [
        {"patches": [patch("旧句。", "删改。", "不存在的证据"), patch("另句。", "", "另句。")]},
        {"patches": [patch("旧句。", "", "旧句。")]},
    ]
    client = FakeClient(lambda payload: replies.pop(0))
    result = proofread(client, original, source)
    assert result.doc["markdown_body"] == "另句。"  # No first-attempt valid patch leaks.
    assert original["markdown_body"] == source
    assert payloads(client)[0] == payloads(client)[1]
    assert result.usage == {"total_tokens": 26}
    batch = result.audit["batches"][0]
    assert batch["usage"] == result.usage
    assert [a["status"] for a in batch["attempts"]] == ["rejected", "passed"]
    assert batch["attempts"][0]["raw"]
    assert batch["attempts"][0]["accepted"]
    assert result.audit["rejected"][0]["reason"].startswith("证据不在")
    assert len(result.audit["accepted"]) == 1
    assert "重新返回完整且独立的补丁集" in client.calls[1][-1]["content"]
    for call in client.calls:
        assert estimate_tokens(call[-1]["content"]) <= int(client.context * INPUT_SHARE)


def test_second_rejected_attempt_fails_atomically_with_both_audited():
    original = {"markdown_body": "旧句。另句。"}
    client = FakeClient({"patches": [patch("旧句。", "", "未发送证据"), patch("另句。", "", "另句。")]})
    with pytest.raises(LLMError) as raised:
        proofread(client, original, "旧句。另句。")
    audit = raised.value.audit
    assert len(client.calls) == 2
    assert audit["after"] is None
    assert audit["accepted"] == []
    assert len(audit["rejected"]) == 2
    assert len(audit["batches"][0]["attempts"]) == 2
    assert audit["usage"] == {"total_tokens": 26}
    assert original == {"markdown_body": "旧句。另句。"}


def test_correction_can_return_empty_set_without_hiding_first_rejection():
    replies = [{"patches": [patch("旧句。", "", "未发送证据")]}, {"patches": []}]
    result = proofread(FakeClient(lambda payload: replies.pop(0)), {"markdown_body": "旧句。"}, "旧句。")
    assert result.doc == {"markdown_body": "旧句。"}
    assert result.audit["status"] == "passed"
    assert result.audit["rejected"]
    assert result.audit["accepted"] == []


def test_oversized_rejected_reply_is_audited_but_feedback_is_bounded():
    bad = {"patches": [patch("旧句。", "", "未发送证据" * 10000)]}
    replies = [bad, {"patches": []}]
    client = FakeClient(lambda payload: replies.pop(0), context=16000)
    result = proofread(client, {"markdown_body": "旧句。"}, "旧句。")
    assert len(result.audit["batches"][0]["attempts"][0]["raw"]) > 50000
    assert payloads(client)[0] == payloads(client)[1]
    assert estimate_tokens(client.calls[1][-1]["content"]) <= int(client.context * INPUT_SHARE)


def test_ambiguous_quantity_empty_patches_retry_to_qualitative_and_keep_other_count():
    source = "仅1至8月，全国就发生10一起重金属污染事件，9起是血铅事件。"
    original = {"markdown_body": "全国发生10起重金属污染事件，其中9起是血铅事件。"}
    replies = [{"patches": []}, {"patches": [patch("10起重金属污染事件", "多起重金属污染事件", source)]}]
    client = FakeClient(lambda payload: replies.pop(0))
    result = proofread(client, original, source)
    assert result.doc["markdown_body"] == "全国发生多起重金属污染事件，其中9起是血铅事件。"
    assert original["markdown_body"].startswith("全国发生10起")
    assert payloads(client)[0] == payloads(client)[1]
    assert "含混混写数量" in result.audit["rejected"][0]["reason"]
    assert result.audit["batches"][0]["attempts"][0]["accepted"] == []
    assert result.usage == {"total_tokens": 26}


@pytest.mark.parametrize("number", ["10", "11", "十一"])
def test_ambiguous_quantity_twice_definite_fails_atomically(number):
    source = "全国发生10一起重金属污染事件，9起是血铅事件。"
    original = {"markdown_body": f"全国发生{number}起重金属污染事件。"}
    client = FakeClient({"patches": []})
    with pytest.raises(LLMError) as raised:
        proofread(client, original, source)
    audit = raised.value.audit
    assert len(client.calls) == 2
    assert audit["after"] is None and audit["status"] == "failed"
    assert len(audit["rejected"]) == 2
    assert audit["usage"] == {"total_tokens": 26}


def test_ambiguous_quantity_checks_candidate_after_successful_patch():
    source = "全国发生10一起重金属污染事件。"
    original = {"markdown_body": "全国发生多起重金属污染事件。"}
    client = FakeClient({"patches": [patch("多起重金属污染事件", "10起重金属污染事件", source)]})
    with pytest.raises(LLMError) as raised:
        proofread(client, original, source)
    assert "含混混写数量" in raised.value.audit["rejected"][0]["reason"]
    assert raised.value.audit["after"] is None


@pytest.mark.parametrize("reference,extra_asr", [
    ("全国发生11起重金属污染事件。", ""),
    ("", "更正：全国发生11起重金属污染事件。"),
])
def test_ambiguous_quantity_can_use_clear_sent_reference_or_asr(reference, extra_asr):
    original = {"markdown_body": "全国发生11起重金属污染事件。"}
    client = FakeClient({"patches": []})
    result = proofread(client, original, "全国发生10一起重金属污染事件。" + extra_asr, reference)
    assert result.doc == original
    assert len(client.calls) == 1


def test_legal_magnitude_and_unrelated_noun_counts_are_not_ambiguous_leaks():
    source = "总处理规模每日1.6万吨。发生10一起重金属污染事件，9起血铅事件。"
    original = {"markdown_body": "总处理规模每日1.6万吨；9起血铅事件。"}
    client = FakeClient({"patches": []})
    result = proofread(client, original, source)
    assert result.doc == original
    assert result.audit["rejected"] == []
    assert len(client.calls) == 1


def test_ambiguous_quantity_window_is_required_before_relevance_spends_budget():
    source = ("垃圾焚烧行业需要结合区域供需分析，新增设施与收运能力存在差异。\n\n" * 80 +
              "仅1至8月，全国就发生10一起重金属污染事件，9起是血铅事件。")
    draft = [{"id": "/markdown_body", "text": "垃圾焚烧行业需要结合区域供需分析。全国发生10起重金属污染事件。",
              "start": 0, "end": 40}]
    budget = estimate_tokens(_prompt(draft, {"asr": [], "reference": []})) + 350
    sources = _sources(source, "", draft, budget)
    assert any("10一起重金属污染事件" in window for window in sources["asr"])
    assert estimate_tokens(_prompt(draft, sources)) <= budget
    assert _ambiguous_quantities(draft, sources, [])[0]["candidate"] == "10起重金属污染事件"


def test_required_ambiguous_window_cannot_be_silently_skipped_when_budget_is_too_small():
    source = "大量无关背景。" * 200 + "全国发生10一起重金属污染事件。"
    draft = [{"id": "/markdown_body", "text": "全国发生10起重金属污染事件。", "start": 0, "end": 17}]
    budget = estimate_tokens(_prompt(draft, {"asr": [], "reference": []})) + 5
    with pytest.raises(ValueError, match="必要来源窗口超出校对预算"):
        _sources(source, "", draft, budget)


def test_required_quantity_window_crossing_split_boundary_is_sent_contiguously(monkeypatch):
    import vtx.proofread as module

    windows = ["全国发生10一", "起重金属污染事件。", "其他行业背景。" * 300]
    monkeypatch.setattr(module, "split_text", lambda text, budget: windows)
    draft = [{"id": "/markdown_body", "text": "全国发生10起重金属污染事件。", "start": 0, "end": 17}]
    budget = estimate_tokens(_prompt(draft, {"asr": windows[:2], "reference": []})) + 20
    sources = _sources("".join(windows), "", draft, budget)
    assert sources["asr"] == ["".join(windows[:2])]
    assert _ambiguous_quantities(draft, sources, [])


def test_all_required_quantity_windows_must_fit_the_same_budget(monkeypatch):
    import vtx.proofread as module

    windows = ["背景。" * 40 + "发生10一起重金属污染事件。", "其他行业。" * 200,
               "背景。" * 40 + "新增20一个工厂项目。"]
    monkeypatch.setattr(module, "split_text", lambda text, budget: windows)
    draft = [{"id": "/markdown_body", "text": "发生10起重金属污染事件，新增20个工厂项目。",
              "start": 0, "end": 26}]
    budget = max(estimate_tokens(_prompt(draft, {"asr": [window], "reference": []}))
                 for window in (windows[0], windows[2])) + 10
    with pytest.raises(ValueError, match="必要来源窗口超出校对预算"):
        _sources("".join(windows), "", draft, budget)


@pytest.mark.parametrize("old,new,source", [
    ("温宗国教授团队", "一个教授团队", "温宗国教授团队获奖。"),
    ("听说温宗国教授团队", "听说一个教授团队", "听说温宗国教授团队获奖。"),
    ("张明医院为居民服务。", "一所医院为居民服务。", "张明医院为居民服务。"),
])
def test_sourced_entity_generalization_needs_no_special_affiliation_prefix(old, new, source):
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("两家企业承担1个项目", "一家企业承担1个项目", "两家企业承担1个项目"),
    ("两家企业和一家学校合作", "一家企业和学校合作", "两家企业和一家学校合作"),
    ("2家企业共用1个设备", "1家企业共用1个设备", "2家企业共用1个设备"),
    ("广州李坑焚烧厂2010年恢复运营时", "广州一家焚烧厂在2011年恢复运营时",
     "广州里坑焚烧厂在2010年恢复运营时，推出了村民驻场监督机制"),
    ("仅光大公司参与项目", "仅一家企业参与项目", "仅光大公司参与项目"),
    ("光大公司参与项目", "一家设备参与项目", "光大公司参与项目"),
    ("实验设备由教授介绍", "一个设备由教授介绍", "实验设备由教授介绍"),
    ("光大公司与两家企业和一家学校合作", "一家企业与一家企业和学校合作",
     "光大公司与两家企业和一家学校合作"),
])
def test_entity_articles_do_not_waive_real_quantities_or_unrelated_nouns(old, new, source):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert raised.value.audit["after"] is None


@pytest.mark.parametrize("old,new,source", [
    ("清华大学环境学院教授曾指出", "一位环境学院教授曾指出", "南方大学环境学院教授聂永峰曾指出，现有能力不足。"),
    ("清华大学环境学院教授曾指出", "一位环境学院研究员曾指出", "清华大学环境学院教授聂永峰曾指出，现有能力不足。"),
    ("清华大学环境学院教授曾指出", "两位环境学院教授曾指出", "清华大学环境学院教授聂永峰曾指出，现有能力不足。"),
    ("仅清华大学环境学院教授曾指出", "仅一位环境学院教授曾指出", "仅清华大学环境学院教授聂永峰曾指出，现有能力不足。"),
    ("清华大学科研项目", "一项项目", "清华大学科研项目已完成。"),
    ("清华大学环境学院教授曾指出", "一位环境学院教授2017年曾指出", "清华大学环境学院教授聂永峰2016年曾指出，现有能力不足。"),
])
def test_role_omission_does_not_waive_identity_role_or_count_changes(old, new, source):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert raised.value.audit["after"] is None


def test_sourced_role_paraphrase_is_not_specific_to_one_name_or_institution():
    old = "中科研究所张明研究员课题组"
    new = "中科研究所一个研究员课题组"
    source = old + "参与了该项研究。"
    result = proofread(FakeClient({"patches": [patch(old, new, source)]}),
                       {"markdown_body": old}, source)
    assert result.doc["markdown_body"] == new


@pytest.mark.parametrize("old,new,source", [
    ("清华大学温宗国教授团队", "清华大学一个教授团队", "不相关的大学教授团队在2016年获奖。"),
    ("清华大学两个教授团队", "清华大学一个教授团队", "清华大学两个教授团队获奖。"),
    ("清华大学二个教授团队", "清华大学一个教授团队", "清华大学二个教授团队获奖。"),
    ("清华大学2个教授团队", "清华大学1个教授团队", "清华大学2个教授团队获奖。"),
    ("清华大学一个教授团队", "清华大学两个教授团队", "清华大学一个教授团队获奖。"),
    ("清华大学1个教授团队", "清华大学2个教授团队", "清华大学1个教授团队获奖。"),
    ("清华大学温宗国教授团队", "清华大学1个教授团队", "清华大学温宗国教授团队获奖。"),
    ("清华大学温宗国教授团队", "清华大学二个教授团队", "清华大学温宗国教授团队获奖。"),
    ("只有清华大学温宗国教授团队", "只有清华大学一个教授团队", "只有清华大学温宗国教授团队获奖。"),
    ("仅清华大学温宗国教授团队", "仅清华大学一个教授团队", "仅清华大学温宗国教授团队获奖。"),
    ("共清华大学温宗国教授团队", "共清华大学一个教授团队", "共清华大学温宗国教授团队获奖。"),
    ("第清华大学温宗国教授团队", "第清华大学一个教授团队", "第清华大学温宗国教授团队获奖。"),
    ("清华大学温宗国教授团队版本", "清华大学一个教授团队版本", "清华大学温宗国教授团队版本。"),
    ("清华大学温宗国教授团队", "清华大学一个教授团队2017年获奖", "清华大学温宗国教授团队2016年获奖。"),
    ("清华大学温宗国教授团队", "清华大学一个设备", "清华大学温宗国教授团队。"),
    ("型号甲", "型号1", "型号甲。"),
    ("版本甲", "版本一", "版本甲。"),
    ("2016年获奖", "2017年获奖", "2016年获奖。"),
    ("数量未知", "数量1", "数量未知。"),
])
def test_indefinite_role_does_not_disable_fact_number_guard(old, new, source):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert any(word in raised.value.audit["rejected"][0]["reason"] for word in ("数字", "相邻语境"))
    assert raised.value.audit["after"] is None


@pytest.mark.parametrize("old,new,source", [
    ("葛明", "王洪", "葛明说王老师知道洪水"),
    ("我们见到了葛明。", "我们见到了葛洪。", "我们见到了葛明。洪水之后出门。"),
])
def test_short_patch_and_single_character_name_change_still_require_context(old, new, source):
    with pytest.raises(LLMError) as raised:
        proofread(FakeClient({"patches": [patch(old, new, source)]}),
                  {"markdown_body": old}, source)
    assert "相邻语境" in raised.value.audit["rejected"][0]["reason"]
    assert raised.value.audit["after"] is None


def long_draft():
    # Reproduce the live failure's 7,317-character draft and 16k context without
    # depending on local acceptance artifacts or making any provider requests.
    paragraphs = [f"第{i}段：垃圾焚烧行业需要结合区域供需分析。" +
                  "新增设施与垃圾收运能力之间存在差异，应分别考察各地实际情况。" * 5 + "\n\n"
                  for i in range(45)]
    tail = "末段垃圾焚烧厂数量为1130座。"
    body = "".join(paragraphs)
    body = body[:7317 - len(tail) - 3] + "。\n\n" + tail
    assert len(body) == 7317
    return body, tail


def payloads(client):
    return [json.loads(call[-1]["content"].split("输入JSON：\n", 1)[1]) for call in client.calls]


def assert_complete_coverage(result, original):
    for pointer, text in original.items():
        snippets = [item for batch in result.audit["batches"] for item in batch["draft"]
                    if item["id"] == "/" + pointer]
        assert "".join(item["text"] for item in snippets) == text
        offset = 0
        for item in snippets:
            assert item["start"] == offset
            offset = item["end"]
            assert item["text"] == text[item["start"]:item["end"]]
        assert offset == len(text)


def test_7317_character_draft_at_16k_checks_tail_and_covers_every_character():
    body, tail = long_draft()
    correct = "末段垃圾焚烧厂数量为1137座。"
    source = body.replace(tail, correct)

    def reply(payload):
        if any(tail in item["text"] for item in payload["draft"]):
            assert any(correct in excerpt for excerpt in payload["sources"]["asr"])
            return {"patches": [patch(tail, correct, correct)]}
        return {"patches": []}

    original = {"markdown_body": body}
    client = FakeClient(reply, context=16000)
    result = proofread(client, original, source)
    assert 1 < len(client.calls) <= 6
    assert result.doc["markdown_body"] == body.replace(tail, correct)
    assert original["markdown_body"] == body
    assert_complete_coverage(result, original)
    assert result.usage == {"total_tokens": 13 * len(client.calls)}
    for call, batch in zip(client.calls, result.audit["batches"]):
        assert estimate_tokens(call[-1]["content"]) <= int(16000 * INPUT_SHARE)
        assert batch["status"] == "passed"
        assert batch["sources"] == json.loads(call[-1]["content"].split("输入JSON：\n", 1)[1])["sources"]
    json.dumps(result.audit)


def test_cannot_cite_source_sent_to_another_batch():
    body, tail = long_draft()
    source = body + "\n\n" + "其他气候背景。" * 1000
    baseline = FakeClient({"patches": []}, context=16000)
    proofread(baseline, {"markdown_body": body}, source)
    first, last = payloads(baseline)[0], payloads(baseline)[-1]
    # Evidence is real and selected elsewhere, but absent from the tail call.
    unseen = next(excerpt for excerpt in first["sources"]["asr"]
                  if not any(excerpt in sent for sent in last["sources"]["asr"]))

    def reply(payload):
        return {"patches": [patch(tail, "", unseen)]} if any(tail in item["text"] for item in payload["draft"]) else {"patches": []}

    client = FakeClient(reply, context=16000)
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": body}, source)
    assert "本次发送" in raised.value.audit["rejected"][0]["reason"]
    assert raised.value.audit["after"] is None


def test_batch_patch_cannot_modify_another_fragment_and_global_old_stays_unique():
    body, tail = long_draft()
    for old, reason in [(tail, "本批发送"), ("新增设施", "唯一匹配")]:
        client = FakeClient({"patches": [patch(old, "", tail)]}, context=16000)
        with pytest.raises(LLMError) as raised:
            proofread(client, {"markdown_body": body}, body)
        assert reason in raised.value.audit["rejected"][0]["reason"]
        assert len(client.calls) == 2
        assert raised.value.audit["after"] is None


def test_later_provider_failure_discards_prior_patches_and_keeps_usage():
    body, tail = long_draft()
    first_sentence = body.split("。", 1)[0] + "。"
    original = {"markdown_body": body}
    seen = 0

    def reply(payload):
        nonlocal seen
        seen += 1
        if seen == 1:
            return {"patches": [patch(first_sentence, "", first_sentence)]}
        return RuntimeError("second batch offline")

    client = FakeClient(reply, context=16000)
    with pytest.raises(LLMError) as raised:
        proofread(client, original, body)
    audit = raised.value.audit
    assert len(client.calls) == 2
    assert audit["accepted"]
    assert audit["after"] is None and original == {"markdown_body": body}
    assert audit["usage"] == {"total_tokens": 13}
    assert audit["batches"][1]["status"] == "failed"


def test_more_than_six_batches_fails_before_any_calls_without_partial_coverage():
    body, tail = long_draft()
    client = FakeClient({"patches": []}, context=16000)
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": body * 5}, body * 5)
    assert "6批" in raised.value.audit["error"]
    assert client.calls == []
    assert raised.value.audit["after"] is None


def test_long_paragraph_splits_on_sentences_and_preserves_whitespace():
    body, tail = long_draft()
    original = {"markdown_body": body.replace("\n\n", "  ")}
    client = FakeClient({"patches": []}, context=16000)
    result = proofread(client, original, body)
    assert 1 < len(client.calls) <= 6
    assert_complete_coverage(result, original)
    for batch in result.audit["batches"][:-1]:
        assert batch["draft"][-1]["text"].endswith("。")


def test_a_patch_cannot_cross_batch_boundary_even_with_real_evidence():
    body, tail = long_draft()
    baseline = FakeClient({"patches": []}, context=16000)
    result = proofread(baseline, {"markdown_body": body}, body)
    first, second = result.audit["batches"][:2]
    boundary = first["draft"][-1]["end"]
    old = body[boundary - 25:boundary + 25]
    assert body.count(old) == 1
    quote = first["sources"]["asr"][0]
    client = FakeClient({"patches": [patch(old, "", quote)]}, context=16000)
    with pytest.raises(LLMError) as raised:
        proofread(client, {"markdown_body": body}, body)
    assert "本批发送" in raised.value.audit["rejected"][0]["reason"]
    assert len(client.calls) == 2
    assert second["draft"][0]["start"] == boundary


def test_multiple_fields_are_covered_without_source_metadata():
    body, tail = long_draft()
    original = {"title": "行业观察\n", "markdown_body": body, "source": "不得校对的元数据"}
    client = FakeClient({"patches": []}, context=16000)
    result = proofread(client, original, body)
    assert_complete_coverage(result, {"title": original["title"], "markdown_body": body})
    assert all(item["id"] != "/source" for batch in result.audit["batches"] for item in batch["draft"])
    assert result.doc == original
