import json
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from backend.app.workflow import build_workflow
from backend.app.config import settings
from backend.app.rag import knowledge

TEXT='采购合同\n甲方：青禾科技有限公司\n乙方：远山设备有限公司\n合同金额人民币100000元。\n甲方签约后支付全部货款。\n乙方承担一切损失。'

def test_real_offline_graph_and_evidence(tmp_dir,monkeypatch):
    monkeypatch.setattr(settings,'dashscope_api_key','')
    p=tmp_dir/'contract.txt';p.write_text(TEXT,encoding='utf-8')
    stages=[]
    result=build_workflow(lambda s,d:stages.append(s)).invoke({'path':str(p),'messages':[]})
    a=result['analysis']
    assert a['mode']=='offline_rules'
    assert {'parse','identify','retrieve','tools','offline','validate','report'}<=set(stages)
    assert len([m for m in result['messages'] if isinstance(m,ToolMessage)])==2
    assert a['risks'] and all(not r['quote'] or r['quote'] in TEXT for r in a['risks'])
    assert all(r['rule_ids'] for r in a['risks'])

def test_vector_retrieval():
    hits=knowledge.search('服务成果交付 知识产权 保密 数据')
    assert any(h['id']=='RULE-SERVICE' for h in hits)
    assert hits[0]['score']>=hits[-1]['score']>0

class ScriptedModel:
    """仅验证 Agent 路由，不代表真实 Qwen 请求。"""
    def __init__(self): self.calls=0
    def bind_tools(self,tools,**kwargs): return self
    def invoke(self,messages):
        self.calls+=1
        if self.calls==1:
            return AIMessage(content='',tool_calls=[{'id':'extra-query','name':'search_knowledge','args':{'query':'全额预付款付款风险'}}])
        assert any(isinstance(m,ToolMessage) and m.tool_call_id=='extra-query' for m in messages)
        return AIMessage(content=json.dumps({'summary':'测试分析','risks':[
            {'title':'预付风险','level':'high','quote':'甲方签约后支付全部货款','reason':'缺少履约保障','suggestion':'分期支付','rule_ids':['RULE-PAY']},
            {'title':'伪造原文','level':'high','quote':'合同没有这句话','rule_ids':['RULE-PAY']},
            {'title':'伪造依据','level':'high','quote':'乙方承担一切损失','rule_ids':['LAW-FAKE']}
        ]},ensure_ascii=False))

def test_agent_tool_loop_and_invalid_evidence_filtered(tmp_dir):
    p=tmp_dir/'contract.txt';p.write_text(TEXT,encoding='utf-8')
    model=ScriptedModel()
    result=build_workflow(llm_override=model).invoke({'path':str(p),'messages':[]})
    assert model.calls==2
    assert any(r['title']=='预付风险' and r['origin']=='llm' for r in result['analysis']['risks'])
    assert not any(r['title'] in ['伪造原文','伪造依据'] for r in result['analysis']['risks'])
    assert len(result['analysis']['warnings'])==2
    assert len(result['analysis']['tool_calls'])==3

def test_pdf_quote_alignment_and_legal_claim_guard():
    from backend.app.workflow import align_quote,unsupported_legal_claim
    assert align_quote('付款100万元','约定付款100\n万元。')=='付款100\n万元'
    assert align_quote('付款200万元','约定付款100\n万元。')==''
    assert unsupported_legal_claim({'reason':'《发票管理办法》第19条要求开具发票'})
    assert not unsupported_legal_claim({'reason':'发票开具时间不明确，建议补充约定'})

class JsonRepairModel:
    """先返回非法 JSON，仅在格式整理调用时返回合法结构（不请求真实模型）。"""
    def __init__(self):
        self.calls=0;self.repairs=0
    def bind_tools(self,tools,**kwargs): return self
    def bind(self,**kwargs): return self
    def invoke(self,messages):
        self.calls+=1
        if any(isinstance(m,SystemMessage) and '只做 JSON 格式整理' in str(m.content) for m in messages):
            self.repairs+=1
            return AIMessage(content=json.dumps({'summary':'格式修复后的测试分析','risks':[
                {'title':'付款节点风险','level':'high','quote':'甲方签约后支付全部货款','missing_clause':False,
                 'reason':'签约即付全部货款，缺少交付与验收约束。','suggestion':'改为按交付和验收节点分期付款。','rule_ids':['RULE-PAY']}
            ]},ensure_ascii=False))
        return AIMessage(content='```json\n{"summary":"草稿","risks":[}\n```')

class LoopingToolModel:
    """每轮都要求调用工具，用于验证第 4 轮的上限保护（不请求真实模型）。"""
    def __init__(self):
        self.calls=0;self.saw_cap_notice=False
    def bind_tools(self,tools,**kwargs): return self
    def bind(self,**kwargs): return self
    def invoke(self,messages):
        self.calls+=1
        if any(isinstance(m,HumanMessage) and '工具查询轮数已到上限' in str(m.content) for m in messages):
            self.saw_cap_notice=True
        return AIMessage(content='',tool_calls=[{'id':f'loop-{self.calls}','name':'search_knowledge','args':{'query':'付款 验收'}}])

def test_model_json_repair_and_tool_round_cap(tmp_dir):
    p=tmp_dir/'contract.txt';p.write_text(TEXT,encoding='utf-8')

    stages=[]
    repair_model=JsonRepairModel()
    result=build_workflow(lambda s,d:stages.append(s),llm_override=repair_model).invoke({'path':str(p),'messages':[]})
    analysis=result['analysis']
    assert 'format' in stages
    assert repair_model.calls==2 and repair_model.repairs==1
    assert analysis['mode']=='qwen'
    assert [r['title'] for r in analysis['risks'] if r['origin']=='llm']==['付款节点风险']
    assert all(not r['quote'] or r['quote'] in TEXT for r in analysis['risks'])

    looping=LoopingToolModel()
    with pytest.raises(ValueError,match='工具调用轮数'):
        build_workflow(llm_override=looping).invoke({'path':str(p),'messages':[]})
    assert looping.calls==4 and looping.saw_cap_notice
