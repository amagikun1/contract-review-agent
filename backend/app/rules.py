import re

def identify_clauses(text: str) -> dict:
    def extract(pattern):
        return [m.group(0).strip() for m in re.finditer(pattern, text)][:8]
    kind = next((k for k in ['采购','销售','服务'] if k in text[:300]), '其他')
    return {'contract_type': kind+'合同', 'parties': extract(r'(?:甲方|乙方|买方|卖方|委托方|受托方)(?:[（(][^）)\n]*[）)])?[：:][^\n]{2,100}'),
            'amounts': re.findall(r'(?:人民币|￥|¥)?\s*\d[\d,，]*(?:\.\d+)?\s*(?:万元|元)',re.sub(r'(?<=[\d,，])\s*\n\s*(?=[\d,，])','',text))[:8],
            'payment': extract(r'[^\n。]*(?:付款|支付|预付)[^\n。]*'),
            'liability': extract(r'[^\n。]*(?:违约|赔偿|责任)[^\n。]*')}

def check_rules(text: str) -> list[dict]:
    risks=[]
    def add(title,level,quote,reason,suggestion,ids):
        risks.append(dict(id=f'R{len(risks)+1}',title=title,level=level,quote=quote,reason=reason,suggestion=suggestion,rule_ids=ids,missing_clause=not bool(quote),origin='rule'))
    checks=[
        (r'[^\n。]*(?:100[%％]|全额|全部)[^\n。]*(?:预付|支付|付款)|[^\n。]*(?:预付|支付|付款)[^\n。]*(?:100[%％]|全额|全部)', '全额付款的履约保障不足','high','全部款项提前支付可能使付款方缺少履约制约手段，应核实付款节点与担保。','结合交付与验收设置分期付款及尾款，明确退款和履约保障。',['RULE-PAY']),
        (r'[^\n。]*(?:无限责任|一切损失|全部损失)[^\n。]*','赔偿责任范围可能过宽','high','损失范围或责任上限不明确，可能造成责任分配失衡。','明确直接损失范围、合理责任上限和例外，交由人工结合交易核实。',['RULE-LIABILITY','LAW-585']),
        (r'[^\n。]*(?:单方|随时)[^\n。]*(?:变更|修改|终止|解除)[^\n。]*','单方变更或终止权','medium','单方变更可能影响价款、履行范围和交易稳定。','约定双方书面确认、通知期限及终止结算方式。',['RULE-CHANGE'])]
    for pattern,title,level,reason,suggestion,ids in checks:
        match=re.search(pattern,text)
        if match:
            quote=match.group(0).strip()
            # 验收后付清全款不等于预付风险，规则仅提供保守提示。
            if ids==['RULE-PAY'] and re.search(r'验收(?:合格|通过)?后[^\n。]*支付',quote) and not re.search(r'预付|签约|签订',quote):
                continue
            add(title,level,quote,reason,suggestion,ids)
    for pattern,title,reason,suggestion,ids in [
        (r'验收|验货','缺少明确验收条款','未检出验收相关表述，交付质量与付款依据需要人工核实。','补充可验证的验收标准、验收期限和整改机制。',['RULE-ACCEPT','LAW-470']),
        (r'仲裁|管辖|诉讼|人民法院','争议解决条款待补充','未检出明确争议解决路径。','结合交易实际约定可执行的诉讼或仲裁条款。',['RULE-DISPUTE','LAW-470']),
        (r'违约|赔偿','违约救济条款待补充','未检出违约或赔偿安排，需确认履约保障。','明确违约情形、补救期限和损失计算方法。',['LAW-585'])]:
        if not re.search(pattern,text): add(title,'medium','',reason,suggestion,ids)
    return risks
